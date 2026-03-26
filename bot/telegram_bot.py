"""
Telegram bot with inline buttons for DEX copy trading
"""
import logging
import asyncio
import aiohttp
from typing import Dict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler
)
from config import TELEGRAM_BOT_TOKEN, BIRDEYE_API_KEY, BIRDEYE_API_URL, JUPITER_QUOTE_TIMEOUT
from data.database import db
from chains.solana.wallet import SolanaWallet, encrypt_private_key, decrypt_private_key
from chains.solana.dex_swaps import swapper
from chains.solana.spl_tokens import token_manager
from trading.copy_trader import copy_trader
from utils.chain_detector import is_solana_address
from chains.solana.vanity_wallet import vanity_generator
from wallet.encryption import encryption
from trading.risk_manager import risk_manager
from data.analytics import analytics
from trading.mev_protection import mev_protection
from wallet.hardware_wallet import hw_connector
from utils.notifications import notification_engine
from bot.admin_panel import admin_panel
from trading.token_analyzer import token_analyzer
from trading.smart_trader import smart_trader

logger = logging.getLogger(__name__)

# Transaction monitoring cache
_tx_monitor_cache: Dict[str, Dict] = {}  # tx_hash -> {user_id, amount, token, start_time}

# ── Popular tokens for quick-select ──────────────────────────────────────────
POPULAR_SOL_TOKENS = {
    "USDC":  "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT":  "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "BONK":  "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "JUP":   "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "WIF":   "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "PYTH":  "HZ1JovNiVvGqNLPQWBn3t3fF7BpB1V1L7qFH1HUCKpd4",
    "RAY":   "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
    "MSOL":  "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
    "RENDER":"rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof",
    "HNT":   "hntyVP6YFm1Hg25TN9WGLqM12b8TQmcknKrdu1oxWux",
}


# ── Live whale discovery via Birdeye top-traders API ─────────────────────────
# Cache: (timestamp, list_of_dicts) — refreshed every hour
_whale_cache: tuple = (0.0, [])

async def fetch_top_traders(limit: int = 20) -> list:
    """Fetch top Solana traders from Birdeye leaderboard.

    Returns a list of dicts: {address, pnl_usd, volume_usd, trade_count}
    sorted by 24-hour PnL descending.
    Falls back to an empty list on API failure.
    """
    import time as _time
    global _whale_cache
    cached_at, cached_data = _whale_cache
    if _time.time() - cached_at < 3600 and cached_data:   # 1-hour TTL
        return cached_data

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{BIRDEYE_API_URL}/trader/gainers-losers",
                params={
                    "type":   "24h",
                    "sort_by": "PnL",
                    "sort_type": "desc",
                    "offset": 0,
                    "limit":  limit,
                },
                headers={"X-API-KEY": BIRDEYE_API_KEY, "x-chain": "solana"},
                timeout=aiohttp.ClientTimeout(total=JUPITER_QUOTE_TIMEOUT),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("data", {}).get("items", [])
                    traders = [
                        {
                            "address":     t.get("address", ""),
                            "pnl_usd":     float(t.get("PnL", 0) or 0),
                            "volume_usd":  float(t.get("volume", 0) or 0),
                            "trade_count": int(t.get("trade", 0) or 0),
                        }
                        for t in items
                        if t.get("address")
                    ]
                    _whale_cache = (_time.time(), traders)
                    return traders
                logger.warning(f"Birdeye gainers HTTP {resp.status}")
    except Exception as e:
        logger.error(f"fetch_top_traders error: {e}")
    return []


# Conversation states
(START, MENU, IMPORT_KEY, ADD_WALLET, SWAP_SELECT, SWAP_AMOUNT, CONFIRM_SWAP,
 VANITY_PREFIX, VANITY_POSITION, VANITY_CASE, VANITY_DIFFICULTY, STOPLOS_AMOUNT, TAKE_PROFIT_PERCENT,
 ANALYTICS_TYPE, HARDWARE_WALLET_SELECT, SELL_AMOUNT,
 ADMIN_MENU, ADMIN_USERS, ADMIN_MASTER_PASSWORD, ADMIN_WALLET_ACTION,
 SMART_TRADE, TRADE_PERCENT_SELECT, SMART_TOKEN_INPUT,
 CREATE_WALLET, WALLET_TRADING_CHOICE, SEND_AMOUNT, RECEIVE_MENU,
 # Extended swap states
 SWAP_TOKEN_INPUT, SWAP_OUTPUT_TOKEN,
 # Smart Trader v2
 AUTO_TRADE_PERCENT, BLACKLIST_TOKEN_INPUT,
 # Auto Smart Trade
 AUTO_SMART_PERCENT,
 # Smart Trade Settings
 ST_SETTINGS_INPUT,
 # Holdings view
 HOLDINGS_VIEW, HOLDINGS_SELL_CONFIRM) = range(35)


class TelegramBot:
    """Telegram bot interface"""

    def __init__(self):
        self.wallet_manager = SolanaWallet()
        self.import_keys = {}  # Temporary storage for import flow

    async def _safe_edit_message(self, update, text, **kwargs):
        """Safely edit message, ignoring 'message is not modified' errors."""
        try:
            if update.callback_query:
                await update.callback_query.edit_message_text(text, **kwargs)
            elif update.message:
                await update.message.edit_text(text, **kwargs)
        except Exception as e:
            if "message is not modified" not in str(e) and "message can't be edited" not in str(e):
                logger.warning(f"Message edit error: {e}")
                # Try sending new message instead
                try:
                    if update.message:
                        await update.message.reply_text(text, **kwargs)
                except:
                    pass

    async def _safe_send_message(self, update, text, **kwargs):
        """Send or edit message safely."""
        try:
            if update.callback_query:
                await update.callback_query.edit_message_text(text, **kwargs)
            else:
                await update.message.reply_text(text, **kwargs)
        except Exception as e:
            if "message is not modified" not in str(e):
                logger.warning(f"Message error: {e}")

    async def _monitor_transaction(self, tx_hash: str, user_id: int, context) -> bool:
        """Monitor transaction status and notify user when confirmed."""
        try:
            import time
            start_time = time.time()
            status_msg = "⏳ **Transaction Pending**\n\n"
            status_msg += f"TX: `{tx_hash[:40]}...`\n\n"
            status_msg += "_Waiting for confirmation on Solana..._\n"
            status_msg += "_This usually takes 5-30 seconds._"
            
            # Send initial pending message
            await context.bot.send_message(
                chat_id=user_id,
                text=status_msg,
                parse_mode='Markdown'
            )
            
            # Poll for confirmation (max 60 seconds)
            while time.time() - start_time < 60:
                await asyncio.sleep(3)  # Check every 3 seconds
                
                try:
                    payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignatureStatuses",
                        "params": [[tx_hash], {"searchTransactionHistory": False}]
                    }
                    
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            SOLANA_RPC_URL,
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=10)
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                statuses = data.get('result', {}).get('value', [])
                                
                                if statuses and statuses[0]:
                                    status = statuses[0]
                                    if status.get('confirmationStatus') == 'confirmed' or \
                                       status.get('confirmationStatus') == 'finalized':
                                        # Check for errors
                                        if status.get('err'):
                                            await context.bot.send_message(
                                                chat_id=user_id,
                                                text=f"❌ **Transaction Failed!**\n\n"
                                                     f"TX: `{tx_hash[:40]}...`\n\n"
                                                     f"The transaction was rejected by the network.\n"
                                                     f"Your funds are safe - no changes were made.\n\n"
                                                     f"View on Solscan: https://solscan.io/tx/{tx_hash}",
                                                parse_mode='Markdown'
                                            )
                                            return False
                                        else:
                                            await context.bot.send_message(
                                                chat_id=user_id,
                                                text=f"✅ **Transaction Confirmed!**\n\n"
                                                     f"TX: `{tx_hash[:40]}...`\n\n"
                                                     f"Your transaction has been confirmed on Solana!\n\n"
                                                     f"View on Solscan: https://solscan.io/tx/{tx_hash}",
                                                parse_mode='Markdown'
                                            )
                                            return True
                except Exception as e:
                    logger.debug(f"TX monitor error: {e}")
                    continue
            
            # Timeout
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⏱️ **Transaction Status Unknown**\n\n"
                     f"TX: `{tx_hash[:40]}...`\n\n"
                     f"The transaction is still pending. It may confirm soon.\n"
                     f"Check Solscan for the latest status:\n"
                     f"https://solscan.io/tx/{tx_hash}",
                parse_mode='Markdown'
            )
            return False
            
        except Exception as e:
            logger.error(f"TX monitor failed: {e}")
            return False
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Start command - works for both new users and existing users"""
        user_id = update.effective_user.id

        # Check if user exists
        user = db.get_user(user_id)

        if not user:
            keyboard = [
                [InlineKeyboardButton("🔐 Import Existing Wallet", callback_data="import_key")],
                [InlineKeyboardButton("✨ Create New Wallet", callback_data="create_wallet")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Handle both message and callback query
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    "👋 Welcome to **Solana DEX Copy Trader**!\n\n"
                    "This bot lets you:\n"
                    "✅ Swap tokens on Solana via Jupiter\n"
                    "✅ Copy trades from whale wallets on Solana\n"
                    "✅ Smart token analyzer with auto-trade\n\n"
                    "Get started by creating or importing a wallet:",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            elif update.message:
                await update.message.reply_text(
                    "👋 Welcome to **Solana DEX Copy Trader**!\n\n"
                    "This bot lets you:\n"
                    "✅ Swap tokens on Solana via Jupiter\n"
                    "✅ Copy trades from whale wallets on Solana\n"
                    "✅ Smart token analyzer with auto-trade\n\n"
                    "Get started by creating or importing a wallet:",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            return MENU
        else:
            # Existing user - show main menu
            if update.callback_query:
                await self.show_main_menu(update, context)
            elif update.message:
                await update.message.reply_text("🤖 **Main Menu**\n\nSelect an option below:",
                                                parse_mode='Markdown')
                await self.show_main_menu(update, context)
            return MENU
    
    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show main menu"""
        # Answer any pending callback query first to kill the spinner
        if update.callback_query:
            await update.callback_query.answer()

        user_id = update.effective_user.id
        user = db.get_user(user_id)
        if not user:
            return

        try:
            sol_balance = await asyncio.to_thread(
                self.wallet_manager.get_balance, user['wallet_address']
            ) or 0
        except Exception:
            sol_balance = 0

        # --- Build balance block ---
        balance_text = (
            f"🟣 Solana: `{sol_balance:.4f} SOL`\n"
            f"  📮 `{user['wallet_address'][:22]}...`\n"
        )

        keyboard = [
            [InlineKeyboardButton("💱 Swap", callback_data="swap"),
             InlineKeyboardButton("📤 Send", callback_data="send_tokens"),
             InlineKeyboardButton("📥 Receive", callback_data="receive")],
            [InlineKeyboardButton("🐋 Copy Trade", callback_data="copy_trade"),
             InlineKeyboardButton("🤖 Smart Analyzer", callback_data="smart_trade")],
            [InlineKeyboardButton("📊 My Holdings", callback_data="my_holdings"),
             InlineKeyboardButton("📂 Positions", callback_data="active_positions")],
            [InlineKeyboardButton("⚠️ Risk", callback_data="risk_mgmt")],
            [InlineKeyboardButton("📊 Analytics", callback_data="analytics"),
             InlineKeyboardButton("🔧 Tools", callback_data="tools"),
             InlineKeyboardButton("⚙️", callback_data="settings")],
        ]

        if admin_panel.is_admin(user_id):
            keyboard.append([InlineKeyboardButton("🛡️ Admin Panel", callback_data="admin_panel")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        text = f"🤖 **Main Menu**\n\n{balance_text}"

        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    
    # Create wallet flow
    async def create_wallet_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show chain selection for wallet creation."""
        query = update.callback_query
        await query.answer()

        keyboard = [
            [InlineKeyboardButton("🟣 Create Solana Wallet", callback_data="create_sol_wallet")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")],
        ]
        await query.edit_message_text(
            "✨ **Create New Wallet**\n\n"
            "Create a new Solana wallet to get started.\n\n"
            "🟣 **Solana** — Jupiter, Raydium, Orca",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return WALLET_TRADING_CHOICE

    async def create_sol_wallet_callback(self, update: Update,
                                          context: ContextTypes.DEFAULT_TYPE) -> int:
        """Generate a new Solana wallet and register the user."""
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id

        try:
            public_key, secret_key = self.wallet_manager.generate_keypair()
            encrypted_key = encrypt_private_key(secret_key)
            db.add_user(user_id, public_key, encrypted_key, public_key)

            await query.edit_message_text(
                "✅ **Solana Wallet Created!**\n\n"
                f"📮 Address: `{public_key}`\n\n"
                f"🔑 Private Key (base58):\n`{secret_key}`\n\n"
                "⚠️ **SAVE YOUR PRIVATE KEY NOW** — it won't be shown again!\n"
                "Anyone with this key controls your wallet.",
                parse_mode='Markdown'
            )
            await self.show_main_menu(update, context)
            return MENU
        except Exception as e:
            logger.error(f"Error creating Solana wallet: {e}")
            await query.edit_message_text(f"❌ Failed to create wallet: {e}")
            return MENU


    # Import wallet flow
    async def import_key_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start import key flow"""
        keyboard = [
            [InlineKeyboardButton("✨ Create New Wallet Instead", callback_data="create_wallet")],
            [InlineKeyboardButton("❌ Cancel", callback_data="back_menu")],
        ]
        await update.callback_query.edit_message_text(
            "🔐 **Import Solana Wallet**\n\n"
            "Send your Solana private key (base58 format):\n\n"
            "⚠️ **SECURITY WARNING:** Never share your private key!\n"
            "It will be encrypted and stored securely.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return IMPORT_KEY
    
    async def handle_private_key(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle private key import"""
        user_id = update.effective_user.id
        private_key = update.message.text.strip()
        
        # Validate key
        keypair = self.wallet_manager.import_keypair(private_key)
        if not keypair:
            await update.message.reply_text("❌ Invalid private key format!")
            return IMPORT_KEY
        
        public_key = str(keypair.pubkey())
        encrypted_key = encrypt_private_key(private_key)
        
        # Save to database
        db.add_user(user_id, public_key, encrypted_key, public_key)
        
        await update.message.reply_text(
            f"✅ Wallet imported successfully!\n\n"
            f"📮 Address: `{public_key}`",
            parse_mode='Markdown'
        )
        
        await self.show_main_menu(update, context)
        return MENU
    
    # ──────────────────────────────────────────────────────────────────────────
    # Solana Swap Flow
    # ──────────────────────────────────────────────────────────────────────────

    async def _fetch_token_info(self, token_mint: str) -> Optional[Dict]:
        """Fetch token metadata from multiple sources."""
        token_info = {
            'mint': token_mint,
            'symbol': 'Unknown',
            'name': 'Unknown Token',
            'decimals': 9,
            'verified': False,
            'logo': None,
            'price_usd': 0.0,
        }
        
        try:
            # Try Jupiter metadata first
            metadata = await token_manager.get_token_metadata(token_mint)
            if metadata:
                token_info['symbol'] = metadata.get('symbol', token_info['symbol'])
                token_info['name'] = metadata.get('name', token_info['name'])
                token_info['decimals'] = metadata.get('decimals', token_info['decimals'])
                token_info['verified'] = metadata.get('verified', False)
                token_info['logo'] = metadata.get('logoURI')
            
            # Fallback to Birdeye if symbol still unknown
            if token_info['symbol'] == 'Unknown':
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{BIRDEYE_API_URL}/defi/token_metadata",
                        params={'address': token_mint},
                        headers={'X-API-KEY': BIRDEYE_API_KEY, 'x-chain': 'solana'},
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get('success'):
                                token_data = data.get('data', {})
                                token_info['symbol'] = token_data.get('symbol', token_info['symbol'])
                                token_info['name'] = token_data.get('name', token_info['name'])
                                token_info['decimals'] = token_data.get('decimals', token_info['decimals'])
                                token_info['verified'] = token_data.get('verified', False)
            
            # Fetch price from Birdeye
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{BIRDEYE_API_URL}/defi/price",
                        params={'address': token_mint},
                        headers={'X-API-KEY': BIRDEYE_API_KEY, 'x-chain': 'solana'},
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get('success'):
                                token_info['price_usd'] = float(data.get('data', {}).get('value', 0))
            except:
                pass  # Price fetch failed, keep 0.0
                
        except Exception as e:
            logger.debug(f"Error fetching token info for {token_mint}: {e}")
        
        return token_info

    def _sol_token_keyboard(self, prefix: str = "soltok") -> InlineKeyboardMarkup:
        """Build a quick-pick keyboard of popular Solana tokens."""
        symbols = list(POPULAR_SOL_TOKENS.keys())
        rows = []
        for i in range(0, len(symbols), 3):
            rows.append([
                InlineKeyboardButton(sym, callback_data=f"{prefix}_{sym}")
                for sym in symbols[i:i+3]
            ])
        rows.append([InlineKeyboardButton("📝 Custom Address", callback_data=f"{prefix}_custom")])
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="back_menu")])
        return InlineKeyboardMarkup(rows)

    async def swap_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Solana swap — direction selection."""
        user_id = update.effective_user.id
        user = db.get_user(user_id)
        sol_bal = self.wallet_manager.get_balance(user['wallet_address']) or 0 if user else 0

        keyboard = [
            [InlineKeyboardButton("SOL → Token", callback_data="swap_sol_to_token")],
            [InlineKeyboardButton("Token → SOL", callback_data="swap_token_to_sol")],
            [InlineKeyboardButton("Token → Token", callback_data="swap_token_to_token")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")],
        ]
        await update.callback_query.edit_message_text(
            f"💱 **Swap Tokens — Solana**\n\n"
            f"Balance: `{sol_bal:.4f} SOL`\n\n"
            f"Select swap direction:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return SWAP_SELECT

    async def swap_amount_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Store swap direction and show token picker."""
        direction = update.callback_query.data  # e.g. swap_sol_to_token
        context.user_data['sol_swap_direction'] = direction
        context.user_data.pop('sol_input_token', None)
        context.user_data.pop('sol_output_token', None)

        if direction == "swap_sol_to_token":
            await update.callback_query.edit_message_text(
                "🔍 **Select token to BUY with SOL:**\n\n"
                "Pick a popular token or enter a custom contract address:",
                reply_markup=self._sol_token_keyboard("soltok"),
                parse_mode='Markdown'
            )
        elif direction == "swap_token_to_sol":
            await update.callback_query.edit_message_text(
                "🔍 **Select token to SELL for SOL:**\n\n"
                "Pick a popular token or enter a custom contract address:",
                reply_markup=self._sol_token_keyboard("soltok"),
                parse_mode='Markdown'
            )
        else:  # token_to_token
            await update.callback_query.edit_message_text(
                "🔍 **Select INPUT token (token you're selling):**\n\n"
                "Pick a popular token or enter a custom contract address:",
                reply_markup=self._sol_token_keyboard("soltok"),
                parse_mode='Markdown'
            )
        return SWAP_TOKEN_INPUT

    async def sol_token_pick_callback(self, update: Update,
                                       context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle a quick-pick Solana token selection."""
        data = update.callback_query.data  # e.g. soltok_USDC or soltok_custom
        await update.callback_query.answer()
        symbol = data.replace("soltok_", "")

        if symbol == "custom":
            keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="back_menu")]]
            await update.callback_query.edit_message_text(
                "📝 Enter the Solana token contract address (base58):",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return SWAP_TOKEN_INPUT  # wait for text message

        mint = POPULAR_SOL_TOKENS.get(symbol)
        if not mint:
            await update.callback_query.edit_message_text("❌ Unknown token symbol.")
            return MENU

        return await self._sol_token_selected(update, context, mint, symbol)

    async def handle_sol_custom_token(self, update: Update,
                                       context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle manually-typed Solana token address with token info confirmation."""
        addr = update.message.text.strip()
        
        if not self.wallet_manager.validate_address(addr):
            keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="back_menu")]]
            await update.message.reply_text(
                "❌ Invalid Solana address format. Please try again:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return SWAP_TOKEN_INPUT
        
        # Fetch token info
        await update.callback_query.answer() if update.callback_query else None
        msg = await update.message.reply_text("🔍 Fetching token details...")
        token_info = await self._fetch_token_info(addr)
        
        # Check if token appears to be valid (has symbol from a known source)
        if token_info['symbol'] == 'Unknown':
            keyboard = [
                [InlineKeyboardButton("✅ Swap Anyway (High Risk)", callback_data="confirm_custom_input")],
                [InlineKeyboardButton("❌ Cancel", callback_data="back_menu")],
            ]
            context.user_data['pending_token_address'] = addr
            await msg.edit_text(
                "⚠️ **Unknown Token - High Risk!**\n\n"
                f"Token Address: `{addr[:30]}...`\n\n"
                "This token was not found in Jupiter or Birdeye databases.\n"
                "It could be:\n"
                "• A brand new token (launched <5min ago)\n"
                "• A scam/fake token\n"
                "• A token with no metadata\n\n"
                "⚠️ **WARNING:** You may lose ALL funds if this is a scam!\n\n"
                "Proceed only if you're absolutely sure:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return CONFIRM_SWAP
        else:
            # Show token confirmation
            verified_badge = "✅" if token_info['verified'] else "⚠️"
            keyboard = [
                [InlineKeyboardButton("✅ Confirm Token", callback_data="confirm_custom_input")],
                [InlineKeyboardButton("❌ Cancel", callback_data="back_menu")],
            ]
            context.user_data['pending_token_address'] = addr
            context.user_data['pending_token_info'] = token_info
            
            await msg.edit_text(
                f"🪙 **Token Confirmation**\n\n"
                f"{verified_badge} **{token_info['symbol']}**\n"
                f"📛 Name: `{token_info['name']}`\n"
                f"🔑 Mint: `{addr}`\n"
                f"🔢 Decimals: `{token_info['decimals']}`\n"
                f"{'✅ Verified on Jupiter' if token_info['verified'] else '⚠️ Not verified - trade carefully'}\n\n"
                f"⚠️ **Always verify:**\n"
                f"• Check the mint address matches expected token\n"
                f"• Verify on CoinGecko/DexScreener\n"
                f"• Never trust blindly!\n\n"
                f"Confirm this is the correct token:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return CONFIRM_SWAP

    async def _sol_token_selected(self, update, context, mint: str, label: str) -> int:
        """Store the selected token and advance to next step."""
        direction = context.user_data.get('sol_swap_direction', 'swap_sol_to_token')

        if direction == "swap_token_to_token" and 'sol_input_token' not in context.user_data:
            # Need output token next
            context.user_data['sol_input_token'] = mint
            context.user_data['sol_input_label'] = label
            msg = (
                f"✅ Input token set: **{label}**\n\n"
                "🔍 Now select the OUTPUT token (token you'll receive):"
            )
            kb = self._sol_token_keyboard("soltok_out")
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(msg, reply_markup=kb, parse_mode='Markdown')
            else:
                await update.message.reply_text(msg, reply_markup=kb, parse_mode='Markdown')
            return SWAP_OUTPUT_TOKEN
        else:
            if direction == "swap_sol_to_token":
                context.user_data['sol_output_token'] = mint
                context.user_data['sol_output_label'] = label
                context.user_data['sol_input_token'] = None  # means native SOL
            elif direction == "swap_token_to_sol":
                context.user_data['sol_input_token'] = mint
                context.user_data['sol_input_label'] = label
                context.user_data['sol_output_token'] = None  # means native SOL
            else:
                # token→token, output token
                context.user_data['sol_output_token'] = mint
                context.user_data['sol_output_label'] = label

            # Ask for amount
            direction = context.user_data.get('sol_swap_direction')
            if direction == "swap_sol_to_token":
                prompt = f"💰 How much **SOL** to spend?\n(e.g. `0.5`)"
            elif direction == "swap_token_to_sol":
                in_label = context.user_data.get('sol_input_label', 'tokens')
                prompt = f"💰 How many **{in_label}** to sell?\n(e.g. `100`)"
            else:
                in_label = context.user_data.get('sol_input_label', 'tokens')
                prompt = f"💰 How many **{in_label}** to sell?\n(e.g. `100`)"

            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(prompt, parse_mode='Markdown')
            else:
                await update.message.reply_text(prompt, parse_mode='Markdown')
            return SWAP_AMOUNT

    async def sol_output_token_pick_callback(self, update: Update,
                                              context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle output token quick-pick for token→token swaps."""
        data = update.callback_query.data  # e.g. soltok_out_USDC
        await update.callback_query.answer()
        symbol = data.replace("soltok_out_", "")

        if symbol == "custom":
            keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="back_menu")]]
            await update.callback_query.edit_message_text(
                "📝 Enter the OUTPUT token contract address (base58):",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return SWAP_OUTPUT_TOKEN

        mint = POPULAR_SOL_TOKENS.get(symbol)
        if not mint:
            await update.callback_query.edit_message_text("❌ Unknown token.")
            return MENU
        return await self._sol_token_selected(update, context, mint, symbol)

    async def handle_sol_custom_output_token(self, update: Update,
                                              context: ContextTypes.DEFAULT_TYPE) -> int:
        """Manually-typed output token address for token→token with confirmation."""
        addr = update.message.text.strip()
        
        if not self.wallet_manager.validate_address(addr):
            keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="back_menu")]]
            await update.message.reply_text("❌ Invalid address. Try again:",
                                            reply_markup=InlineKeyboardMarkup(keyboard))
            return SWAP_OUTPUT_TOKEN
        
        # Fetch token info
        await update.callback_query.answer() if update.callback_query else None
        msg = await update.message.reply_text("🔍 Fetching token details...")
        token_info = await self._fetch_token_info(addr)
        
        # Check if token appears to be valid
        if token_info['symbol'] == 'Unknown':
            keyboard = [
                [InlineKeyboardButton("✅ Swap Anyway (High Risk)", callback_data="confirm_custom_output")],
                [InlineKeyboardButton("❌ Cancel", callback_data="back_menu")],
            ]
            context.user_data['pending_token_address'] = addr
            await msg.edit_text(
                "⚠️ **Unknown Token - High Risk!**\n\n"
                f"Token Address: `{addr[:30]}...`\n\n"
                "This token was not found in Jupiter or Birdeye databases.\n"
                "It could be:\n"
                "• A brand new token\n"
                "• A scam/fake token\n"
                "• A token with no metadata\n\n"
                "⚠️ **WARNING:** You may lose ALL funds!\n\n"
                "Proceed only if you're sure:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return CONFIRM_SWAP
        else:
            verified_badge = "✅" if token_info['verified'] else "⚠️"
            keyboard = [
                [InlineKeyboardButton("✅ Confirm Token", callback_data="confirm_custom_output")],
                [InlineKeyboardButton("❌ Cancel", callback_data="back_menu")],
            ]
            context.user_data['pending_token_address'] = addr
            context.user_data['pending_token_info'] = token_info
            
            await msg.edit_text(
                f"🪙 **Token Confirmation**\n\n"
                f"{verified_badge} **{token_info['symbol']}**\n"
                f"📛 Name: `{token_info['name']}`\n"
                f"🔑 Mint: `{addr}`\n"
                f"🔢 Decimals: `{token_info['decimals']}`\n"
                f"{'✅ Verified on Jupiter' if token_info['verified'] else '⚠️ Not verified'}\n\n"
                f"Confirm this is the correct output token:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return CONFIRM_SWAP

    async def handle_swap_amount(self, update: Update,
                                  context: ContextTypes.DEFAULT_TYPE) -> int:
        """Amount entered — fetch Jupiter quote and show swap preview with balance check."""
        try:
            amount = float(update.message.text.strip())
        except ValueError:
            keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="back_menu")]]
            await update.message.reply_text("❌ Enter a valid number (e.g. `0.5`):",
                                            reply_markup=InlineKeyboardMarkup(keyboard),
                                            parse_mode='Markdown')
            return SWAP_AMOUNT

        if amount <= 0:
            keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="back_menu")]]
            await update.message.reply_text("❌ Amount must be greater than 0.",
                                            reply_markup=InlineKeyboardMarkup(keyboard))
            return SWAP_AMOUNT

        # ── LARGE TRADE WARNING ───────────────────────────────────────────────
        if amount >= 5.0:  # 5 SOL or more
            keyboard = [
                [InlineKeyboardButton("✅ Yes, Proceed", callback_data="large_trade_confirm")],
                [InlineKeyboardButton("❌ Cancel", callback_data="back_menu")],
            ]
            context.user_data['sol_swap_amount'] = amount
            context.user_data['sol_swap_direction'] = context.user_data.get('sol_swap_direction', 'swap_sol_to_token')
            await update.message.reply_text(
                f"⚠️ **LARGE TRANSACTION WARNING**\n\n"
                f"You're about to swap **{amount} SOL**\n\n"
                f"This is a significant amount. Please double-check:\n"
                f"• You're sending to the correct token\n"
                f"• You understand the price impact\n"
                f"• You're okay with the risk\n\n"
                f"**Proceed with caution!**",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return CONFIRM_SWAP
        
        context.user_data['sol_swap_amount'] = amount
        direction = context.user_data.get('sol_swap_direction', 'swap_sol_to_token')
        from config import WSOL_MINT

        # Resolve mints
        if direction == "swap_sol_to_token":
            input_mint = WSOL_MINT
            output_mint = context.user_data.get('sol_output_token', '')
            in_label = "SOL"
            out_label = context.user_data.get('sol_output_label', output_mint[:8])
        elif direction == "swap_token_to_sol":
            input_mint = context.user_data.get('sol_input_token', '')
            output_mint = WSOL_MINT
            in_label = context.user_data.get('sol_input_label', input_mint[:8])
            out_label = "SOL"
        else:
            input_mint = context.user_data.get('sol_input_token', '')
            output_mint = context.user_data.get('sol_output_token', '')
            in_label = context.user_data.get('sol_input_label', input_mint[:8])
            out_label = context.user_data.get('sol_output_label', output_mint[:8])

        if not input_mint or not output_mint:
            await update.message.reply_text("❌ Token not set. Please restart the swap.")
            return MENU

        # ── BALANCE VERIFICATION (BEFORE QUOTE) ───────────────────────────────
        user_id = update.effective_user.id
        user = db.get_user(user_id)
        
        if not user:
            await update.message.reply_text("❌ User not found. Please restart the bot.")
            return MENU
        
        wallet_addr = user['wallet_address']
        
        # Check input token balance FIRST before fetching quote
        if input_mint == WSOL_MINT:
            # SOL balance check
            sol_balance = await asyncio.to_thread(
                self.wallet_manager.get_balance, wallet_addr
            ) or 0
            
            if amount > sol_balance:
                keyboard = [
                    [InlineKeyboardButton("❌ Cancel", callback_data="back_menu")],
                ]
                await update.message.reply_text(
                    f"❌ **Insufficient SOL Balance!**\n\n"
                    f"Required: `{amount} SOL`\n"
                    f"Available: `{sol_balance:.4f} SOL`\n\n"
                    f"Please deposit more SOL or use a smaller amount.",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
                return SWAP_AMOUNT
        else:
            # Token balance check
            try:
                token_bal = await token_manager.get_token_balance(wallet_addr, input_mint)
                token_balance = token_bal.get('amount', 0) if token_bal else 0
                
                if token_balance <= 0:
                    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="back_menu")]]
                    await update.message.reply_text(
                        f"❌ **No Token Balance!**\n\n"
                        f"You don't own any **{in_label}** tokens.\n"
                        f"Token: `{input_mint[:30]}...`\n\n"
                        f"Please acquire some tokens first or select a different token.",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode='Markdown'
                    )
                    return SWAP_AMOUNT
                
                if amount > token_balance:
                    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="back_menu")]]
                    await update.message.reply_text(
                        f"❌ **Insufficient Token Balance!**\n\n"
                        f"Required: `{amount} {in_label}`\n"
                        f"Available: `{token_balance:.4f} {in_label}`\n\n"
                        f"Please use a smaller amount.",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode='Markdown'
                    )
                    return SWAP_AMOUNT
            except Exception as e:
                logger.warning(f"Token balance check failed: {e}")
                # Continue without balance check if API fails
        
        # ── GET QUOTE (after balance verified) ────────────────────────────────
        msg = await update.message.reply_text("⏳ Fetching best quote from Jupiter...")
        
        quote = await swapper.get_jupiter_price(input_mint, output_mint, amount)
        if not quote:
            await msg.edit_text(
                "❌ No quote available. The token may have low liquidity.\n"
                "Try a smaller amount or a different token."
            )
            return MENU

        raw_out = quote.get('price', 0)
        impact = float(quote.get('priceImpact', 0))

        # Store for execution
        context.user_data['sol_input_mint'] = input_mint
        context.user_data['sol_output_mint'] = output_mint
        context.user_data['sol_quote_output'] = raw_out
        context.user_data['sol_quote_impact'] = impact

        # Impact warning
        impact_warn = ""
        if impact > 5:
            impact_warn = "\n⚠️ HIGH price impact — consider smaller amount!"
        elif impact > 2:
            impact_warn = "\n⚠️ Moderate price impact."

        keyboard = [
            [InlineKeyboardButton("✅ Confirm Swap (Jupiter)", callback_data="confirm_sol_swap_jupiter")],
            [InlineKeyboardButton("❌ Cancel", callback_data="back_menu")],
        ]
        await msg.edit_text(
            f"💱 **Swap Preview — Solana**\n\n"
            f"You send: `{amount}` {in_label}\n"
            f"You receive: ~`{raw_out:.6f}` {out_label}\n"
            f"Price Impact: `{impact:.3f}%`{impact_warn}\n"
            f"DEX: Jupiter (best rate)\n\n"
            f"Confirm your swap:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return CONFIRM_SWAP

    async def large_trade_confirm_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle large trade confirmation - proceed with balance check and quote."""
        await update.callback_query.answer()
        # Continue with the normal swap amount handling
        # The handle_swap_amount method will continue from where we left off
        return await self.handle_swap_amount_continued(update, context)

    async def handle_swap_amount_continued(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Continue swap flow after large trade confirmation - calls main logic."""
        # Get saved values from context
        amount = context.user_data.get('sol_swap_amount', 0)
        direction = context.user_data.get('sol_swap_direction', 'swap_sol_to_token')
        
        # Get user info
        user_id = update.effective_user.id if hasattr(update, 'effective_user') else update.callback_query.message.chat.id
        user = db.get_user(user_id)
        
        if not user:
            await update.callback_query.edit_message_text("❌ User not found.")
            return MENU
        
        # Continue with balance check and quote
        from config import WSOL_MINT
        
        # Resolve mints
        if direction == "swap_sol_to_token":
            input_mint = WSOL_MINT
            output_mint = context.user_data.get('sol_output_token', '')
            in_label = "SOL"
            out_label = context.user_data.get('sol_output_label', output_mint[:8])
        elif direction == "swap_token_to_sol":
            input_mint = context.user_data.get('sol_input_token', '')
            output_mint = WSOL_MINT
            in_label = context.user_data.get('sol_input_label', input_mint[:8])
            out_label = "SOL"
        else:
            input_mint = context.user_data.get('sol_input_token', '')
            output_mint = context.user_data.get('sol_output_token', '')
            in_label = context.user_data.get('sol_input_label', input_mint[:8])
            out_label = context.user_data.get('sol_output_label', output_mint[:8])

        if not input_mint or not output_mint:
            await update.callback_query.edit_message_text("❌ Token not set. Please restart.")
            return MENU
        
        # Balance check
        wallet_addr = user['wallet_address']
        await update.callback_query.edit_message_text("⏳ Checking balance and fetching quote...")
        
        # SOL balance check
        if input_mint == WSOL_MINT:
            sol_balance = await asyncio.to_thread(self.wallet_manager.get_balance, wallet_addr) or 0
            if amount > sol_balance:
                await update.callback_query.edit_message_text(
                    f"❌ **Insufficient SOL Balance!**\n\n"
                    f"Required: `{amount} SOL`\n"
                    f"Available: `{sol_balance:.4f} SOL`",
                    parse_mode='Markdown'
                )
                return SWAP_AMOUNT
        else:
            # Token balance check
            try:
                token_bal = await token_manager.get_token_balance(wallet_addr, input_mint)
                token_balance = token_bal.get('amount', 0) if token_bal else 0
                if token_balance <= 0:
                    await update.callback_query.edit_message_text(
                        f"❌ **No Token Balance!**\n\nYou don't own any **{in_label}** tokens.",
                        parse_mode='Markdown'
                    )
                    return SWAP_AMOUNT
                if amount > token_balance:
                    await update.callback_query.edit_message_text(
                        f"❌ **Insufficient Token Balance!**",
                        parse_mode='Markdown'
                    )
                    return SWAP_AMOUNT
            except:
                pass
        
        # Get quote
        quote = await swapper.get_jupiter_price(input_mint, output_mint, amount)
        if not quote:
            await update.callback_query.edit_message_text(
                "❌ No quote available. Try smaller amount."
            )
            return MENU
        
        # Show preview (same as handle_swap_amount)
        raw_out = quote.get('price', 0)
        impact = float(quote.get('priceImpact', 0))
        
        context.user_data['sol_input_mint'] = input_mint
        context.user_data['sol_output_mint'] = output_mint
        context.user_data['sol_quote_output'] = raw_out
        context.user_data['sol_quote_impact'] = impact
        
        impact_warn = ""
        if impact > 5:
            impact_warn = "\n⚠️ HIGH price impact!"
        
        keyboard = [
            [InlineKeyboardButton("✅ Confirm Swap", callback_data="confirm_sol_swap_jupiter")],
            [InlineKeyboardButton("❌ Cancel", callback_data="back_menu")],
        ]
        
        await update.callback_query.edit_message_text(
            f"💱 **Swap Preview**\n\n"
            f"Send: `{amount}` {in_label}\n"
            f"Receive: ~`{raw_out:.6f}` {out_label}\n"
            f"Impact: `{impact:.3f}%`{impact_warn}\n\n"
            f"Confirm:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        
        return CONFIRM_SWAP

    async def confirm_custom_token_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle confirmation of custom token (input or output)."""
        await update.callback_query.answer()
        data = update.callback_query.data  # confirm_custom_input or confirm_custom_output
        
        addr = context.user_data.get('pending_token_address')
        token_info = context.user_data.get('pending_token_info', {})
        
        if not addr:
            await update.callback_query.edit_message_text("❌ Error: No token address found. Please restart.")
            return MENU
        
        direction = context.user_data.get('sol_swap_direction', 'swap_sol_to_token')
        
        if data == "confirm_custom_input":
            # Confirm as input token
            context.user_data['sol_input_token'] = addr
            context.user_data['sol_input_label'] = token_info.get('symbol', addr[:8])
            
            if direction == "swap_token_to_token":
                # Need output token next
                keyboard = self._sol_token_keyboard("soltok_out")
                await update.callback_query.edit_message_text(
                    f"✅ Input token set: **{token_info.get('symbol', addr[:8])}**\n\n"
                    f"🔍 Now select the OUTPUT token:",
                    reply_markup=keyboard,
                    parse_mode='Markdown'
                )
                return SWAP_OUTPUT_TOKEN
            else:
                # SOL → token, proceed to amount
                context.user_data['sol_output_token'] = addr
                context.user_data['sol_output_label'] = token_info.get('symbol', addr[:8])
                await update.callback_query.edit_message_text(
                    f"💰 How much **SOL** to spend?\n(e.g. `0.5`)",
                    parse_mode='Markdown'
                )
                return SWAP_AMOUNT
        else:
            # confirm_custom_output - Confirm as output token
            context.user_data['sol_output_token'] = addr
            context.user_data['sol_output_label'] = token_info.get('symbol', addr[:8])
            
            # Now ask for amount
            in_label = context.user_data.get('sol_input_label', 'tokens')
            await update.callback_query.edit_message_text(
                f"💰 How many **{in_label}** to sell?\n(e.g. `100`)",
                parse_mode='Markdown'
            )
            return SWAP_AMOUNT

    async def confirm_swap(self, update: Update,
                            context: ContextTypes.DEFAULT_TYPE) -> int:
        """Execute the confirmed Solana swap."""
        user_id = update.effective_user.id
        data = update.callback_query.data  # confirm_sol_swap_jupiter
        dex = data.replace("confirm_sol_swap_", "")  # jupiter

        amount = context.user_data.get('sol_swap_amount', 0)
        input_mint = context.user_data.get('sol_input_mint', '')
        output_mint = context.user_data.get('sol_output_mint', '')
        expected_out = context.user_data.get('sol_quote_output', 0)
        impact = context.user_data.get('sol_quote_impact', 0)

        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            f"⏳ Executing swap on **Jupiter**...", parse_mode='Markdown'
        )

        try:
            # Decrypt user keypair for on-chain signing
            user = db.get_user(user_id)
            keypair = None
            if user and user.get('encrypted_private_key'):
                raw_key = decrypt_private_key(user['encrypted_private_key'])
                if raw_key:
                    keypair = self.wallet_manager.import_keypair(raw_key)

            if not keypair:
                await update.callback_query.edit_message_text(
                    "❌ Could not load wallet key. Please re-import your wallet."
                )
                await self.show_main_menu(update, context)
                return MENU

            fee_map = {'low': 1_000, 'auto': None, 'high': 50_000, 'turbo': 200_000}
            priority_override = fee_map.get(context.user_data.get('priority_level', 'auto'))
            result = await swapper.execute_swap(input_mint, output_mint, amount, dex, keypair=keypair, priority_override=priority_override)

            if result and result.get('status') == 'confirmed':
                tx_hash = result.get('tx_hash', result.get('signature', 'pending'))
                actual_out = result.get('expectedOutput', result.get('outputAmount', expected_out))

                db.add_trade(
                    user_id=user_id,
                    input_mint=input_mint,
                    output_mint=output_mint,
                    input_amount=amount,
                    output_amount=actual_out,
                    dex=dex,
                    price=actual_out / amount if amount > 0 else 0,
                    slippage=impact,
                    tx_hash=str(tx_hash) if tx_hash else 'pending',
                    chain='solana',
                )
                await update.callback_query.edit_message_text(
                    f"✅ **Swap Submitted!**\n\n"
                    f"Sent: `{amount}` tokens\n"
                    f"Received: ~`{actual_out:.6f}` tokens\n"
                    f"DEX: {dex.upper()}\n"
                    f"TX: `{str(tx_hash)[:30] if tx_hash else 'pending'}...`\n\n"
                    f"📡 _Monitoring transaction..._",
                    parse_mode='Markdown'
                )
                
                # Start transaction monitoring in background
                asyncio.create_task(
                    self._monitor_transaction(str(tx_hash), user_id, context)
                )
            else:
                await update.callback_query.edit_message_text(
                    "❌ Swap failed. Please check your balance and try again."
                )
        except Exception as e:
            logger.error(f"Swap execution error: {e}")
            await update.callback_query.edit_message_text(f"❌ Error: {e}")

        await self.show_main_menu(update, context)
        return MENU
    
    # =========================================================================
    # Smart Trader v2 – Hub + all sub-handlers
    # =========================================================================

    async def smart_trade_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Smart Trader v2 hub — shows all options."""
        user_id = update.effective_user.id
        user = db.get_user(user_id)
        current_percent = user.get('trade_percent', 20.0) if user else 20.0

        is_copy_on  = smart_trader.is_auto_trading(user_id)
        is_smart_on = smart_trader.is_auto_smart_trading(user_id)

        copy_label  = "🟢 Auto Copy: ON  — tap to stop"  if is_copy_on  else "🔴 Auto Copy: OFF — tap to start"
        smart_label = "🟢 Auto Smart: ON — tap to stop"  if is_smart_on else "🔴 Auto Smart: OFF — tap to start"

        keyboard = [
            [InlineKeyboardButton("🔍 Analyze & Trade (Manual)", callback_data="st_manual")],
            [InlineKeyboardButton("💡 Token Suggestions",        callback_data="st_suggestions")],
            [InlineKeyboardButton(copy_label,  callback_data="st_auto_copy_toggle")],
            [InlineKeyboardButton(smart_label, callback_data="st_auto_smart_toggle")],
            [InlineKeyboardButton("🚫 Blacklist a Token",        callback_data="st_blacklist")],
            [InlineKeyboardButton("📊 Open Positions",           callback_data="active_positions")],
            [InlineKeyboardButton("🔙 Back",                     callback_data="back_menu")],
        ]
        await update.callback_query.edit_message_text(
            f"🤖 **Smart Trader v2**\n\n"
            f"Trade %: `{current_percent}%` per position\n\n"
            f"*🐋 Auto Copy Trade*\n"
            f"  Ranks whale wallets by win rate × avg profit.\n"
            f"  Auto-pauses underperformers. Re-ranks every 6h.\n\n"
            f"*🤖 Auto Smart Trade*\n"
            f"  Scans DexScreener + Birdeye every 30 min.\n"
            f"  Buys top momentum tokens. Sells at TP ladder.\n"
            f"  Trailing stop after first TP. Re-buys if still hot.\n\n"
            f"Max open positions: 8 | Hard stop: -20%\n"
            f"TP ladder: +30% → +60% → +100%",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return SMART_TRADE

    async def st_manual_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Manual analyze-and-trade: pick trade %."""
        user_id = update.effective_user.id
        user = db.get_user(user_id)
        current_percent = user.get('trade_percent', 20.0) if user else 20.0

        keyboard = [
            [InlineKeyboardButton("5%  Conservative", callback_data="trade_5"),
             InlineKeyboardButton("10%  Safe", callback_data="trade_10")],
            [InlineKeyboardButton("15%  Balanced", callback_data="trade_15"),
             InlineKeyboardButton("20%  Standard", callback_data="trade_20")],
            [InlineKeyboardButton("25%  Moderate", callback_data="trade_25"),
             InlineKeyboardButton("30%  Aggressive", callback_data="trade_30")],
            [InlineKeyboardButton("40%  Very Aggressive", callback_data="trade_40"),
             InlineKeyboardButton("50%  Max", callback_data="trade_50")],
            [InlineKeyboardButton("🔙 Back", callback_data="smart_trade")],
        ]
        await update.callback_query.edit_message_text(
            f"🔍 **Analyze & Trade — Manual Mode**\n\n"
            f"Current trade %: `{current_percent}%`\n\n"
            f"Select how much of your balance to use per trade,\n"
            f"then paste a token contract address.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return TRADE_PERCENT_SELECT

    async def handle_trade_percent(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle trade % selection"""
        user_id = update.effective_user.id
        callback_data = update.callback_query.data

        # Extract percentage
        percent_str = callback_data.replace("trade_", "")
        try:
            percent = float(percent_str)
            # Save to user preferences
            db.update_user_trade_percent(user_id, percent)
            context.user_data['selected_trade_percent'] = percent
        except:
            percent = 20.0

        # Now ask for token address
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="back_menu")]]
        await update.callback_query.edit_message_text(
            f"✅ Trade amount set to {percent}%\n\n"
            f"📝 Send the token contract address to analyze and trade:\n\n"
            f"Example: `DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263`",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return SMART_TOKEN_INPUT

    # ── Auto-trading ──────────────────────────────────────────────────────────

    async def st_auto_copy_toggle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Toggle auto-copy trading on or off."""
        user_id = update.effective_user.id
        query = update.callback_query
        await query.answer()

        is_auto_on = smart_trader.is_auto_trading(user_id)

        if is_auto_on:
            smart_trader.stop_auto_trading(user_id)
            await query.edit_message_text(
                "🔴 **Auto-Trading Stopped**\n\n"
                "The background scanner has been paused.\n"
                "Your open positions continue to be monitored.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back to Smart Trader", callback_data="smart_trade")]
                ]),
                parse_mode='Markdown'
            )
            return SMART_TRADE

        # Starting — ask for % to use per auto-trade
        user = db.get_user(user_id)
        current_percent = user.get('trade_percent', 20.0) if user else 20.0
        keyboard = [
            [InlineKeyboardButton("5%  Conservative", callback_data="at_5"),
             InlineKeyboardButton("10%  Safe", callback_data="at_10")],
            [InlineKeyboardButton("15%  Balanced", callback_data="at_15"),
             InlineKeyboardButton("20%  Standard", callback_data="at_20")],
            [InlineKeyboardButton("25%  Moderate", callback_data="at_25"),
             InlineKeyboardButton("30%  Aggressive", callback_data="at_30")],
            [InlineKeyboardButton(f"Use current ({current_percent}%)", callback_data=f"at_{int(current_percent)}")],
            [InlineKeyboardButton("🔙 Cancel", callback_data="smart_trade")],
        ]
        await query.edit_message_text(
            "🟢 **Start Auto-Trading**\n\n"
            "The bot will scan DexScreener + Birdeye every 3 minutes,\n"
            "score each token, and buy the best ones automatically.\n\n"
            "Min momentum score: **65/100**\n"
            "Max risk score allowed: **55/100**\n"
            "TP ladder: **+30% → +60% → +100%**\n"
            "Hard stop-loss: **-20%**\n\n"
            "Select % of balance to use per auto-trade:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return AUTO_TRADE_PERCENT

    async def handle_auto_trade_percent(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle auto-trade % selection and start the loop."""
        user_id = update.effective_user.id
        query = update.callback_query
        await query.answer()

        pct_str = query.data.replace("at_", "")
        try:
            pct = float(pct_str)
        except Exception:
            pct = 20.0

        await smart_trader.start_auto_trading(user_id, max_trades_per_cycle=2, trade_percent=pct)

        await query.edit_message_text(
            f"✅ **Auto-Trading Started!**\n\n"
            f"Trade size: `{pct}%` per position\n"
            f"Scan interval: every 3 minutes\n"
            f"Max open positions: 8\n\n"
            f"The bot is now scanning for high-momentum tokens.\n"
            f"You'll be notified when a trade is executed.\n\n"
            f"Tap _Auto-Trade_ again to stop at any time.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Smart Trader", callback_data="smart_trade")]
            ]),
            parse_mode='Markdown'
        )
        return SMART_TRADE

    # ── Auto Smart Trade toggle ────────────────────────────────────────────────

    async def st_auto_smart_toggle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Toggle auto-smart trading on or off."""
        user_id = update.effective_user.id
        query = update.callback_query
        await query.answer()

        if smart_trader.is_auto_smart_trading(user_id):
            smart_trader.stop_auto_smart_trading(user_id)
            await query.edit_message_text(
                "🔴 **Auto Smart Trade Stopped**\n\n"
                "The token scanner has been paused.\n"
                "Open positions continue to be monitored.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data="smart_trade")
                ]]),
                parse_mode='Markdown'
            )
            return SMART_TRADE

        user = db.get_user(user_id)
        current_percent = user.get('trade_percent', 10.0) if user else 10.0
        keyboard = [
            [InlineKeyboardButton("5%  Conservative", callback_data="as_5"),
             InlineKeyboardButton("10%  Safe",         callback_data="as_10")],
            [InlineKeyboardButton("15%  Balanced",     callback_data="as_15"),
             InlineKeyboardButton("20%  Standard",     callback_data="as_20")],
            [InlineKeyboardButton(f"Use current ({current_percent}%)", callback_data=f"as_{int(current_percent)}")],
            [InlineKeyboardButton("🔙 Cancel",          callback_data="smart_trade")],
        ]
        await query.edit_message_text(
            "🟢 **Start Auto Smart Trade**\n\n"
            "The bot will scan DexScreener + Birdeye every 30 min,\n"
            "score each token, and buy the best ones automatically.\n\n"
            "• Min momentum score: 65/100\n"
            "• Max risk score: 55/100\n"
            "• Trailing stop after first TP\n"
            "• Hard stop-loss: -20%\n"
            "• Re-buys if token still hot after exit\n\n"
            "Select trade % per position:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return AUTO_SMART_PERCENT

    async def handle_auto_smart_percent(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle auto-smart trade % selection — then ask for max positions."""
        user_id = update.effective_user.id
        query = update.callback_query
        await query.answer()

        pct_str = query.data.replace("as_", "")
        try:
            pct = float(pct_str)
        except Exception:
            pct = 10.0

        keyboard = [
            [InlineKeyboardButton("2 positions", callback_data=f"asp_2_{int(pct)}"),
             InlineKeyboardButton("4 positions", callback_data=f"asp_4_{int(pct)}")],
            [InlineKeyboardButton("6 positions", callback_data=f"asp_6_{int(pct)}"),
             InlineKeyboardButton("8 positions", callback_data=f"asp_8_{int(pct)}")],
            [InlineKeyboardButton("🔙 Cancel",   callback_data="smart_trade")],
        ]
        await query.edit_message_text(
            f"Trade size set to `{pct}%` per position.\n\n"
            f"Select max simultaneous positions:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return AUTO_SMART_PERCENT

    async def handle_auto_smart_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle max positions selection and start auto-smart trading."""
        user_id = update.effective_user.id
        query = update.callback_query
        await query.answer()

        # callback_data = "asp_{positions}_{pct}"
        parts = query.data.split('_')
        try:
            max_pos = int(parts[1])
            pct     = float(parts[2])
        except Exception:
            max_pos, pct = 4, 10.0

        await smart_trader.start_auto_smart_trading(user_id, trade_percent=pct, max_positions=max_pos)

        await query.edit_message_text(
            f"✅ **Auto Smart Trade Started!**\n\n"
            f"Trade size: `{pct}%` per position\n"
            f"Max positions: `{max_pos}`\n"
            f"Scan interval: every 30 minutes\n\n"
            f"The bot is scanning for high-momentum tokens.\n"
            f"You'll be notified when trades execute.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back to Smart Trader", callback_data="smart_trade")
            ]]),
            parse_mode='Markdown'
        )
        return SMART_TRADE

    # ── Suggestions ───────────────────────────────────────────────────────────

    async def st_suggestions_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show token suggestions from the last auto cycle, or do a fresh scan."""
        user_id = update.effective_user.id
        query = update.callback_query
        await query.answer()

        await query.edit_message_text(
            "💡 **Scanning for top tokens…**\n\n"
            "Fetching DexScreener + Birdeye trending lists,\n"
            "scoring momentum, safety-checking top candidates.\n\n"
            "⏳ Please wait ~5 seconds…",
            parse_mode='Markdown'
        )

        try:
            suggestions = await asyncio.wait_for(
                smart_trader.get_suggestions(user_id, chain='solana'),
                timeout=12
            )
        except asyncio.TimeoutError:
            suggestions = smart_trader.get_cached_suggestions(user_id)
            if not suggestions:
                await query.edit_message_text(
                    "⏱ **Scan timed out.**\n\n"
                    "APIs are slow right now. Try again in a moment.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Back", callback_data="smart_trade")
                    ]]),
                    parse_mode='Markdown'
                )
                return SMART_TRADE
        except Exception as e:
            logger.error(f"Suggestions error: {e}")
            suggestions = []

        if not suggestions:
            await query.edit_message_text(
                "😕 **No strong suggestions right now.**\n\n"
                "Market conditions may be low-volatility.\n"
                "Try again in a few minutes or enable Auto-Trade\n"
                "so the bot catches the next opportunity automatically.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="smart_trade")]
                ]),
                parse_mode='Markdown'
            )
            return SMART_TRADE

        lines = ["💡 **Top Token Suggestions**\n"]
        keyboard_rows = []
        for i, tok in enumerate(suggestions[:5], 1):
            sym = tok.get('symbol', '?')
            addr = tok.get('address', '')
            mom = tok.get('momentum_score', 0)
            pc = tok.get('price_change_24h', 0)
            vol = tok.get('volume_24h', 0)
            lines.append(
                f"`{i}.` **{sym}**\n"
                f"   Momentum: `{mom}/100`  |  24h: `{pc:+.1f}%`\n"
                f"   Vol: `${vol:,.0f}`\n"
                f"   `{addr[:22]}…`\n"
            )
            keyboard_rows.append([
                InlineKeyboardButton(
                    f"Buy {sym} (#{i})",
                    callback_data=f"st_buy_{addr[:40]}"
                )
            ])
        keyboard_rows.append([InlineKeyboardButton("🔙 Back", callback_data="smart_trade")])

        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(keyboard_rows),
            parse_mode='Markdown'
        )
        return SMART_TRADE

    async def st_buy_suggested_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Buy a token from the suggestions list."""
        user_id = update.effective_user.id
        query = update.callback_query
        await query.answer()

        token_address = query.data.replace("st_buy_", "")
        user = db.get_user(user_id)
        trade_pct = user.get('trade_percent', 20.0) if user else 20.0

        await query.edit_message_text(
            f"🚀 **Executing trade…**\n\n"
            f"Token: `{token_address[:22]}…`\n"
            f"Trade %: `{trade_pct}%`\n\n"
            f"Running safety check + momentum score…",
            parse_mode='Markdown'
        )

        result = await smart_trader.analyze_and_trade(
            user_id=user_id,
            token_address=token_address,
            user_trade_percent=trade_pct,
            dex="jupiter"
        )

        status = result.get('status', 'ERROR')
        if status == 'SUCCESS':
            text = (
                f"✅ **Trade Executed!**\n\n"
                f"Token: `{token_address[:22]}…`\n"
                f"Spent: `{result.get('trade_amount_sol', 0):.4f} SOL`\n"
                f"Momentum: `{result.get('momentum_score', 0)}/100`\n"
                f"Risk: `{result.get('risk_assessment', {}).get('risk_score', 0):.0f}/100`\n"
                f"TX: `{str(result.get('tx_signature',''))[:24]}…`\n\n"
                f"TP ladder active: +30% → +60% → +100%\n"
                f"Stop-loss: -20%"
            )
        elif status == 'REJECTED':
            text = (
                f"🚫 **Token Rejected**\n\n"
                f"Reason: {result.get('reason', 'Too risky')}\n"
                f"Risk score: `{result.get('risk_assessment', {}).get('risk_score', 0):.0f}/100`"
            )
        elif status == 'BLACKLISTED':
            text = "🚫 That token is on your blacklist."
        else:
            text = f"❌ Trade failed: {result.get('error', status)}"

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💡 More Suggestions", callback_data="st_suggestions")],
                [InlineKeyboardButton("🔙 Smart Trader", callback_data="smart_trade")],
            ]),
            parse_mode='Markdown'
        )
        return SMART_TRADE

    # ── Blacklist ─────────────────────────────────────────────────────────────

    async def st_blacklist_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Ask for a token address to blacklist."""
        query = update.callback_query
        await query.answer()

        # Show current blacklist for this user
        user_id = update.effective_user.id
        bl = smart_trader._blacklist.get(user_id, set())
        bl_text = "\n".join(f"• `{a[:22]}…`" for a in list(bl)[:5]) if bl else "_None_"

        await query.edit_message_text(
            f"🚫 **Token Blacklist**\n\n"
            f"Blacklisted tokens are never traded by Smart Trader.\n\n"
            f"*Current blacklist:*\n{bl_text}\n\n"
            f"Send a token contract address to add/remove it:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="smart_trade")]
            ]),
            parse_mode='Markdown'
        )
        return BLACKLIST_TOKEN_INPUT

    async def handle_blacklist_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Toggle a token on/off the blacklist."""
        user_id = update.effective_user.id
        token = update.message.text.strip()

        bl = smart_trader._blacklist.setdefault(user_id, set())
        if token in bl:
            smart_trader.remove_from_blacklist(user_id, token)
            action = "removed from"
        else:
            smart_trader.blacklist_token(user_id, token)
            action = "added to"

        keyboard = [[InlineKeyboardButton("🔙 Smart Trader", callback_data="smart_trade")]]
        await update.message.reply_text(
            f"✅ `{token[:22]}…` {action} your blacklist.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return SMART_TRADE
    
    async def handle_trade_percent(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle trade % selection"""
        user_id = update.effective_user.id
        callback_data = update.callback_query.data
        
        # Extract percentage
        percent_str = callback_data.replace("trade_", "")
        try:
            percent = float(percent_str)
            # Save to user preferences
            db.update_user_trade_percent(user_id, percent)
            context.user_data['selected_trade_percent'] = percent
        except:
            percent = 20.0
        
        # Now ask for token address
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="back_menu")]]
        await update.callback_query.edit_message_text(
            f"✅ Trade amount set to {percent}%\n\n"
            f"📝 Send the token contract address to analyze and trade:\n\n"
            f"Example: `DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263`",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return SMART_TOKEN_INPUT
    
    async def handle_smart_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle token address input and start smart analysis/trade"""
        user_id = update.effective_user.id
        token_address = update.message.text.strip()
        selected_percent = context.user_data.get('selected_trade_percent', 20.0)
        
        # Validate token address — Solana base58 address (32-44 chars)
        if not is_solana_address(token_address):
            await update.message.reply_text(
                "❌ Invalid token address format!\n\n"
                "Send a Solana token address (base58, 32-44 chars).",
                parse_mode='Markdown'
            )
            return SMART_TOKEN_INPUT
        
        # Show analyzing message
        msg = await update.message.reply_text(
            f"🔍 **Analyzing token...**\n\n"
            f"Token: `{token_address[:20]}`\n"
            f"Trade %: {selected_percent}%\n\n"
            f"Checking:\n"
            f"• Contract security\n"
            f"• Liquidity pools\n"
            f"• Holder distribution\n"
            f"• Mint/Freeze authorities\n"
            f"• Honeypot detection\n"
            f"• Volume/Market cap ratio\n"
            f"• Social presence\n"
            f"• Dev activity\n\n"
            f"⏳ This may take 30-60 seconds...",
            parse_mode='Markdown'
        )
        
        detected_chain = 'solana'
        native_sym = 'SOL'

        try:
            # Run smart trade analysis in background
            result = await smart_trader.analyze_and_trade(
                user_id=user_id,
                token_address=token_address,
                user_trade_percent=selected_percent,
                dex="jupiter",
                chain=detected_chain
            )

            # Update message with results
            if result.get('status') == 'SUCCESS':
                risk_score = result.get('risk_assessment', {}).get('risk_score', 0)
                tx_sig = result.get('tx_signature', 'N/A')

                await msg.edit_text(
                    f"✅ **Trade Executed Successfully!**\n\n"
                    f"Chain: {detected_chain.upper()}\n"
                    f"Token: `{token_address[:20]}`\n"
                    f"Amount: {result.get('trade_amount_sol', 0):.4f} {native_sym}\n"
                    f"Momentum: {result.get('momentum_score', 0)}/100\n"
                    f"Risk Score: {risk_score:.1f}/100\n"
                    f"TX: `{str(tx_sig)[:20]}...`\n\n"
                    f"📊 Position monitoring started\n"
                    f"🎯 TP ladder: +30% → +60% → +100%\n"
                    f"🛑 Stop-loss: -20%",
                    parse_mode='Markdown'
                )
            elif result.get('status') == 'REJECTED':
                reason = result.get('reason', 'Unknown')
                risk_score = result.get('risk_assessment', {}).get('risk_score', 0)

                await msg.edit_text(
                    f"🚫 **Token Analysis Rejected**\n\n"
                    f"Reason: {reason}\n"
                    f"Risk Score: {risk_score:.1f}/100\n\n"
                    f"This token is not safe to trade right now.\n"
                    f"Check back later or try a different token.",
                    parse_mode='Markdown'
                )
            elif result.get('status') == 'INSUFFICIENT_BALANCE':
                await msg.edit_text(
                    f"💸 **Insufficient Balance**\n\n"
                    f"{result.get('error', 'Not enough funds')}\n\n"
                    f"Current balance: {result.get('wallet_balance', 0):.4f} {native_sym}",
                    parse_mode='Markdown'
                )
            else:
                error_msg = result.get('error', 'Unknown error')
                await msg.edit_text(
                    f"❌ **Trade Failed**\n\n"
                    f"Error: {error_msg}\n\n"
                    f"Please try again or contact support.",
                    parse_mode='Markdown'
                )
        
        except Exception as e:
            logger.error(f"Error in smart trade: {e}")
            await msg.edit_text(
                f"❌ **Error**\n\n"
                f"Exception: {str(e)}\n\n"
                f"Please try again.",
                parse_mode='Markdown'
            )
        
        await self.show_main_menu(update, context)
        return MENU
    
    # Copy trading flow
    async def copy_trade_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Copy trading menu (Solana)"""
        keyboard = [
            [InlineKeyboardButton("➕ Add Wallet to Watch", callback_data="add_watch_wallet")],
            [InlineKeyboardButton("👁️ View Watched Wallets", callback_data="view_watched")],
            [InlineKeyboardButton("🐋 Suggested Whales", callback_data="suggested_whales")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.edit_message_text(
            "🐋 **Copy Trading — Solana**\n\nWhat would you like to do?",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    # ── Suggested whale wallets ────────────────────────────────────────────────

    async def suggested_whales_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show top Solana traders from Birdeye leaderboard with live balances."""
        await update.callback_query.answer()
        user_id = update.effective_user.id

        await update.callback_query.edit_message_text(
            "⏳ Loading top Solana traders...", parse_mode='Markdown'
        )

        # Fetch top traders + user's current watch list
        traders, watched = await asyncio.gather(
            fetch_top_traders(limit=20),
            asyncio.to_thread(db.get_watched_wallets, user_id),
        )
        watched_addrs = {w['wallet_address'] for w in watched}

        if not traders:
            await update.callback_query.edit_message_text(
                "⚠️ Could not load trader leaderboard right now.\n"
                "Check your Birdeye API key or try again later.",
                parse_mode='Markdown'
            )
            return

        # Fetch SOL balances in parallel
        async def _get_bal(addr: str) -> float:
            try:
                return await asyncio.to_thread(self.wallet_manager.get_balance, addr) or 0.0
            except Exception:
                return 0.0

        balances = await asyncio.gather(*[_get_bal(t['address']) for t in traders])

        # For already-watched wallets pull copy-performance stats from DB
        def _my_stats(addr: str) -> str:
            if addr not in watched_addrs:
                return ""
            records = db.get_copy_performance(user_id, addr, limit=20)
            closed = [r for r in records if r.get('status') == 'closed'
                      and r.get('user_profit_percent') is not None]
            if not closed:
                return "  👁 Watching"
            avg = sum(r['user_profit_percent'] for r in closed) / len(closed)
            wins = sum(1 for r in closed if r['user_profit_percent'] > 0)
            return f"  ✅ {wins}/{len(closed)} wins · avg {avg:+.1f}%"

        lines = []
        keyboard = []
        for i, (t, bal) in enumerate(zip(traders, balances), start=1):
            addr       = t['address']
            pnl        = t['pnl_usd']
            vol        = t['volume_usd']
            trades     = t['trade_count']
            bal_str    = f"{bal:.2f} SOL" if bal >= 0.01 else "< 0.01 SOL"
            pnl_str    = f"+${pnl:,.0f}" if pnl >= 0 else f"-${abs(pnl):,.0f}"
            my_stats   = _my_stats(addr)
            short_addr = f"`{addr[:16]}…`"

            lines.append(
                f"**#{i}** {short_addr}\n"
                f"  💰 {bal_str}  |  24h PnL: **{pnl_str}**\n"
                f"  Vol: ${vol:,.0f}  |  Trades: {trades}{my_stats}"
            )
            already = addr in watched_addrs
            btn_label = f"✓ Watching #{i}" if already else f"➕ Add #{i}"
            keyboard.append([InlineKeyboardButton(btn_label, callback_data=f"add_sol_whale_{addr}")])

        keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data="suggested_whales")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="copy_trade")])

        await update.callback_query.edit_message_text(
            "🐋 **Top Solana Traders — Live Leaderboard**\n"
            "_(Birdeye · sorted by 24h PnL · refreshes hourly)_\n\n"
            + "\n\n".join(lines),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    async def add_sol_whale_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """One-tap add a suggested SOL whale to watch list."""
        await update.callback_query.answer()
        user_id = update.effective_user.id
        addr = update.callback_query.data[len("add_sol_whale_"):]

        # Label from cache if available, else short address
        _, cached_traders = _whale_cache
        rank = next((i + 1 for i, t in enumerate(cached_traders) if t['address'] == addr), None)
        label = f"Top Trader #{rank}" if rank else addr[:20] + "..."

        db.add_watched_wallet(user_id, addr)
        await copy_trader.start_monitoring_for_user(user_id)

        await update.callback_query.edit_message_text(
            f"✅ **Whale Added!**\n\n"
            f"🐋 {label}\n"
            f"📮 `{addr}`\n\n"
            f"Copy trades will execute automatically on Solana DEXs.",
            parse_mode='Markdown'
        )
        await self.show_main_menu(update, context)
        return MENU

    # ── Send / Receive / Settings ──────────────────────────────────────────────

    async def send_tokens_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send entry — straight to SOL send."""
        await update.callback_query.answer()
        return await self._show_send_sol(update, context)

    async def send_from_sol_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User chose to send from Solana wallet."""
        await update.callback_query.answer()
        return await self._show_send_sol(update, context)

    async def _show_send_sol(self, update, context):
        """Show Solana send screen — ask for recipient address."""
        user_id = update.effective_user.id
        user = db.get_user(user_id)
        sol_bal = self.wallet_manager.get_balance(user['wallet_address']) or 0
        context.user_data['send_chain'] = 'solana'

        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="back_menu")]]
        text = (
            f"📤 **Send SOL**\n\n"
            f"From: `{user['wallet_address'][:20]}...`\n"
            f"Balance: `{sol_bal:.6f} SOL`\n\n"
            f"Enter the **recipient Solana address**:"
        )
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
            )
        return SEND_AMOUNT

    async def _route_send_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Route incoming text in SEND_AMOUNT state to address or amount handler."""
        if context.user_data.get('send_step') == 'amount':
            return await self.handle_send_amount(update, context)
        return await self.handle_send_address(update, context)

    async def handle_send_address(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Step 2 — user typed recipient address; now ask for amount."""
        address = update.message.text.strip()
        unit = 'SOL'

        valid = self.wallet_manager.validate_address(address)

        if not valid:
            keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="back_menu")]]
            await update.message.reply_text(
                "❌ Invalid Solana address. Try again:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return SEND_AMOUNT

        context.user_data['send_to_address'] = address
        context.user_data['send_step'] = 'amount'

        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="back_menu")]]
        await update.message.reply_text(
            f"💰 **How much {unit} to send?**\n\n"
            f"To: `{address}`\n\n"
            f"Enter amount (e.g. `0.1`):",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return SEND_AMOUNT

    async def handle_send_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Step 3 — user typed amount; show confirmation."""
        to_address = context.user_data.get('send_to_address', '')
        user_id = update.effective_user.id

        try:
            amount = float(update.message.text.strip())
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Enter a valid positive number (e.g. `0.1`):", parse_mode='Markdown')
            return SEND_AMOUNT

        unit = 'SOL'
        context.user_data['send_amount'] = amount
        context.user_data['send_step'] = 'confirm'

        # Balance check
        user = db.get_user(user_id)
        bal = self.wallet_manager.get_balance(user['wallet_address']) or 0
        from_addr = user['wallet_address']

        if amount > bal:
            await update.message.reply_text(
                f"❌ Insufficient balance.\n"
                f"You have `{bal:.6f} {unit}` but tried to send `{amount} {unit}`.",
                parse_mode='Markdown'
            )
            return SEND_AMOUNT

        keyboard = [
            [InlineKeyboardButton(f"✅ Confirm Send {amount} {unit}", callback_data="confirm_send")],
            [InlineKeyboardButton("❌ Cancel", callback_data="back_menu")],
        ]
        await update.message.reply_text(
            f"📤 **Confirm Send**\n\n"
            f"From: `{from_addr[:20]}...`\n"
            f"To: `{to_address[:20]}...`\n"
            f"Amount: `{amount} {unit}`\n"
            f"Network: Solana\n\n"
            f"⚠️ Double-check the address — transactions cannot be reversed.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return SEND_AMOUNT

    async def confirm_send_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Execute the send after user confirms."""
        await update.callback_query.answer()
        user_id = update.effective_user.id
        to_address = context.user_data.get('send_to_address', '')
        amount = context.user_data.get('send_amount', 0)
        unit = 'SOL'

        await update.callback_query.edit_message_text(f"⏳ Sending {amount} {unit}...")

        try:
            user = db.get_user(user_id)
            
            # Decrypt private key for signing
            keypair = None
            if user and user.get('encrypted_private_key'):
                raw_key = decrypt_private_key(user['encrypted_private_key'])
                if raw_key:
                    keypair = self.wallet_manager.import_keypair(raw_key)
            
            if not keypair:
                await update.callback_query.edit_message_text(
                    "❌ **Could not load wallet key**\n\n"
                    "Please re-import your wallet to enable sending.",
                    parse_mode='Markdown'
                )
                await self.show_main_menu(update, context)
                return MENU
            
            # Send SOL
            result = self.wallet_manager.send_sol(
                from_wallet_address=user['wallet_address'],
                to_address=to_address,
                amount=amount,
                keypair=keypair
            )

            if result and result.get('success'):
                tx = result.get('tx_hash', result.get('signature', 'pending'))
                await update.callback_query.edit_message_text(
                    f"✅ **Sent!**\n\n"
                    f"Amount: `{amount} {unit}`\n"
                    f"To: `{to_address}`\n"
                    f"TX: `{str(tx)[:40]}...`\n\n"
                    f"📡 _Monitoring transaction..._",
                    parse_mode='Markdown'
                )
                
                # Start transaction monitoring in background
                asyncio.create_task(
                    self._monitor_transaction(str(tx), user_id, context)
                )
            else:
                err = result.get('error', 'Unknown error') if result else 'Failed to send transaction'
                await update.callback_query.edit_message_text(
                    f"⚠️ **Send Failed**\n\n"
                    f"Reason: {err}\n\n"
                    f"Please try again or check:\n"
                    f"• Sufficient SOL balance\n"
                    f"• Valid recipient address\n"
                    f"• Network congestion",
                    parse_mode='Markdown'
                )
        except Exception as e:
            logger.error(f"Send error: {e}")
            await update.callback_query.edit_message_text(
                f"❌ **Error**: {str(e)}\n\n"
                f"Please try again or use an external wallet.",
                parse_mode='Markdown'
            )

        # Clear send state
        for k in ('send_chain', 'send_to_address', 'send_amount', 'send_step'):
            context.user_data.pop(k, None)

        await self.show_main_menu(update, context)
        return MENU

    async def receive_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show receive address."""
        await update.callback_query.answer()
        user_id = update.effective_user.id
        user = db.get_user(user_id)

        text = (
            f"📥 **Receive Funds**\n\n"
            f"🟣 **Solana Address:**\n`{user['wallet_address']}`\n\n"
            f"Share your address to receive tokens. Always double-check before sending."
        )

        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
        )
        return MENU

    async def settings_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Settings menu."""
        await update.callback_query.answer()
        user_id = update.effective_user.id
        user = db.get_user(user_id)
        trade_pct = user.get('trade_percent', 20.0) if user else 20.0
        sol_addr  = (user.get('wallet_address') or 'Not set') if user else 'Not set'

        # Load live smart trade settings for display
        s = smart_trader._get_user_smart_settings(user_id)
        sl   = abs(float(s['hard_stop_loss'])) * 100
        tr   = float(s['trailing_stop_pct'])  * 100
        mh   = float(s['max_hold_hours'])
        tp1  = float(s['tp1_threshold']) * 100
        tp2  = float(s['tp2_threshold']) * 100
        tp3  = float(s['tp3_threshold']) * 100
        msc  = int(s['auto_min_score'])
        mpos = int(s['max_positions'])

        text = (
            f"⚙️ **Settings**\n\n"
            f"**Chain:** 🟣 Solana\n"
            f"**Default Trade %:** `{trade_pct}%`\n"
            f"🟣 Wallet: `{sol_addr[:20]}{'...' if len(sol_addr) > 20 else ''}`\n\n"
            f"**Smart Trade Settings:**\n"
            f"• Hard Stop-Loss: `{sl:.0f}%`\n"
            f"• Trailing Stop: `{tr:.0f}%` from peak\n"
            f"• Max Hold: `{mh:.0f}h`\n"
            f"• TP Ladder: `+{tp1:.0f}% → +{tp2:.0f}% → +{tp3:.0f}%`\n"
            f"• Min Momentum Score: `{msc}/100`\n"
            f"• Max Positions: `{mpos}`"
        )

        keyboard = [
            [InlineKeyboardButton("🎯 Smart Trade Settings", callback_data="st_settings")],
            [InlineKeyboardButton("📊 Change Trade %", callback_data="smart_trade")],
            [InlineKeyboardButton("💱 Slippage Tolerance", callback_data="slippage_settings")],
            [InlineKeyboardButton("🔑 View Private Key", callback_data="view_private_key")],
            [InlineKeyboardButton("🔑 Import Wallet", callback_data="import_key")],
            [InlineKeyboardButton("📥 My Addresses (Receive)", callback_data="receive")],
            [InlineKeyboardButton("⚠️ Risk Management", callback_data="risk_mgmt")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")],
        ]
        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return MENU

    async def slippage_settings_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Slippage tolerance settings."""
        await update.callback_query.answer()
        user_id = update.effective_user.id
        
        # Get current slippage setting (default 2.0%)
        current_slippage = db.get_user_setting(user_id, 'slippage_tolerance', 2.0)
        
        text = (
            f"💱 **Slippage Tolerance**\n\n"
            f"Current: `{current_slippage}%`\n\n"
            f"Slippage is the maximum price movement you're willing to accept.\n\n"
            f"**Recommendations:**\n"
            f"• 0.5% - Stablecoins (USDC, USDT)\n"
            f"• 1-2% - Large cap tokens (SOL, JUP, RAY)\n"
            f"• 3-5% - Mid cap tokens\n"
            f"• 5-10% - New/meme tokens\n\n"
            f"⚠️ Higher slippage = More failed trades but better fill rate\n"
            f"⚠️ Lower slippage = Less price impact but more failed TX"
        )
        
        keyboard = [
            [InlineKeyboardButton(f"{'✅' if current_slippage == 0.5 else '○'} 0.5% (Stablecoins)", 
                                  callback_data="slip_0_5")],
            [InlineKeyboardButton(f"{'✅' if current_slippage == 1.0 else '○'} 1% (Low)", 
                                  callback_data="slip_1_0")],
            [InlineKeyboardButton(f"{'✅' if current_slippage == 2.0 else '○'} 2% (Default)", 
                                  callback_data="slip_2_0")],
            [InlineKeyboardButton(f"{'✅' if current_slippage == 5.0 else '○'} 5% (High)", 
                                  callback_data="slip_5_0")],
            [InlineKeyboardButton("📝 Custom Value", callback_data="slip_custom")],
            [InlineKeyboardButton("🔙 Back", callback_data="settings")],
        ]
        
        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return MENU

    async def slippage_select_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle slippage preset selection."""
        await update.callback_query.answer()
        user_id = update.effective_user.id
        data = update.callback_query.data
        
        slippage_map = {
            'slip_0_5': 0.5,
            'slip_1_0': 1.0,
            'slip_2_0': 2.0,
            'slip_5_0': 5.0,
        }
        
        slippage = slippage_map.get(data, 2.0)
        db.set_user_setting(user_id, 'slippage_tolerance', slippage)
        
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Slippage", callback_data="slippage_settings")],
        ]
        
        await update.callback_query.edit_message_text(
            f"✅ **Slippage Updated!**\n\n"
            f"New slippage tolerance: `{slippage}%`\n\n"
            f"This will apply to all future swaps.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return MENU

    async def view_private_key_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Display the user's decrypted private key with security warning."""
        await update.callback_query.answer()
        user_id = update.effective_user.id
        user = db.get_user(user_id)
        
        if not user or not user.get('encrypted_private_key'):
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="settings")]]
            await update.callback_query.edit_message_text(
                "❌ **No Private Key Found**\n\n"
                "Your wallet doesn't have a stored private key.\n"
                "You may need to re-import your wallet.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return MENU
        
        try:
            # Decrypt the private key
            private_key = decrypt_private_key(user['encrypted_private_key'])
            wallet_address = user.get('wallet_address', 'Unknown')
            
            if not private_key:
                raise Exception("Failed to decrypt private key")
            
            keyboard = [
                [InlineKeyboardButton("🔙 Back to Settings", callback_data="settings")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="back_menu")],
            ]
            
            await update.callback_query.edit_message_text(
                "🔑 **Your Private Key**\n\n"
                f"📮 Wallet: `{wallet_address}`\n\n"
                f"🔐 Private Key (base58):\n"
                f"```\n{private_key}\n```\n\n"
                "⚠️ **SECURITY WARNING:**\n"
                "• NEVER share this key with anyone!\n"
                "• Store it in a secure location (password manager, hardware wallet, paper)\n"
                "• Anyone with this key can steal ALL your funds\n"
                "• The bot cannot recover it if you lose it\n\n"
                "📋 _Tap and hold to copy_",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Error decrypting private key for user {user_id}: {e}")
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="settings")]]
            await update.callback_query.edit_message_text(
                f"❌ **Error Decrypting Key**\n\n"
                f"Failed to decrypt your private key.\n"
                f"Error: {str(e)}\n\n"
                "Please contact support or re-import your wallet.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        
        return MENU

    async def st_settings_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Smart Trade Settings — lets users adjust all engine parameters."""
        await update.callback_query.answer()
        user_id = update.effective_user.id
        s = smart_trader._get_user_smart_settings(user_id)

        sl   = abs(float(s['hard_stop_loss'])) * 100
        tr   = float(s['trailing_stop_pct'])  * 100
        mh   = float(s['max_hold_hours'])
        tp1  = float(s['tp1_threshold']) * 100
        tp1f = float(s['tp1_fraction'])  * 100
        tp2  = float(s['tp2_threshold']) * 100
        tp2f = float(s['tp2_fraction'])  * 100
        tp3  = float(s['tp3_threshold']) * 100
        msc  = int(s['auto_min_score'])
        mpos = int(s['max_positions'])

        text = (
            f"🎯 **Smart Trade Settings**\n\n"
            f"Tap any setting to change it.\n\n"
            f"**Stop-Loss & Trailing:**\n"
            f"• Hard Stop-Loss: `{sl:.0f}%` → tap to change\n"
            f"• Trailing Stop: `{tr:.0f}%` from peak\n\n"
            f"**Take-Profit Ladder:**\n"
            f"• TP1: +`{tp1:.0f}%` → sell `{tp1f:.0f}%` of position\n"
            f"• TP2: +`{tp2:.0f}%` → sell `{tp2f:.0f}%` of remainder\n"
            f"• TP3: +`{tp3:.0f}%` → sell all remaining\n\n"
            f"**Auto-Trade:**\n"
            f"• Min Momentum Score: `{msc}/100`\n"
            f"• Max Open Positions: `{mpos}`\n"
            f"• Max Hold Time: `{mh:.0f}h`"
        )
        keyboard = [
            [InlineKeyboardButton(f"🛑 Hard Stop-Loss ({sl:.0f}%)", callback_data="sts_stop_loss"),
             InlineKeyboardButton(f"📉 Trailing Stop ({tr:.0f}%)", callback_data="sts_trailing")],
            [InlineKeyboardButton(f"💰 TP1 (+{tp1:.0f}%)", callback_data="sts_tp1"),
             InlineKeyboardButton(f"💰 TP2 (+{tp2:.0f}%)", callback_data="sts_tp2"),
             InlineKeyboardButton(f"💰 TP3 (+{tp3:.0f}%)", callback_data="sts_tp3")],
            [InlineKeyboardButton(f"🎯 Min Score ({msc})", callback_data="sts_min_score"),
             InlineKeyboardButton(f"📦 Max Positions ({mpos})", callback_data="sts_max_pos")],
            [InlineKeyboardButton(f"⏰ Max Hold ({mh:.0f}h)", callback_data="sts_max_hold")],
            [InlineKeyboardButton("🔄 Reset to Defaults", callback_data="sts_reset")],
            [InlineKeyboardButton("🔙 Back", callback_data="settings")],
        ]
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
        )
        return MENU

    async def st_settings_action_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle tap on a specific smart setting — prompt for new value."""
        await update.callback_query.answer()
        action = update.callback_query.data  # e.g. "sts_stop_loss"
        context.user_data['st_settings_action'] = action

        prompts = {
            'sts_stop_loss':  ("🛑 **Hard Stop-Loss**\n\nEnter % (e.g. `20` for -20%).\nRange: 5–50%", "5–50"),
            'sts_trailing':   ("📉 **Trailing Stop Drawdown**\n\nEnter % from peak (e.g. `15`).\nRange: 5–40%", "5–40"),
            'sts_tp1':        ("💰 **TP1 Threshold**\n\nEnter profit % to trigger first take-profit (e.g. `30`).\nRange: 10–200%", "10–200"),
            'sts_tp2':        ("💰 **TP2 Threshold**\n\nEnter profit % to trigger second take-profit (e.g. `60`).\nMust be higher than TP1.", "10–500"),
            'sts_tp3':        ("💰 **TP3 Threshold**\n\nEnter profit % to trigger final exit (e.g. `100`).\nMust be higher than TP2.", "10–1000"),
            'sts_min_score':  ("🎯 **Min Momentum Score**\n\nAuto-Smart Trade only buys tokens above this score.\nRange: 40–90", "40–90"),
            'sts_max_pos':    ("📦 **Max Open Positions**\n\nMaximum smart positions open at once.\nRange: 1–20", "1–20"),
            'sts_max_hold':   ("⏰ **Max Hold Time (hours)**\n\nAuto-exit position after this many hours.\nRange: 1–168 (1 week)", "1–168"),
        }
        if action == 'sts_reset':
            user_id = update.effective_user.id
            for key in ('hard_stop_loss', 'trailing_stop_pct', 'max_hold_hours',
                        'auto_min_score', 'max_positions',
                        'tp1_threshold', 'tp1_fraction',
                        'tp2_threshold', 'tp2_fraction',
                        'tp3_threshold', 'tp3_fraction'):
                try:
                    conn = db.get_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        'DELETE FROM user_settings WHERE user_id = ? AND setting_key = ?',
                        (user_id, key)
                    )
                    conn.commit()
                    conn.close()
                except Exception:
                    pass
            await update.callback_query.edit_message_text(
                "✅ **Smart Trade settings reset to defaults.**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="st_settings")]
                ]),
                parse_mode='Markdown'
            )
            return MENU

        prompt, rng = prompts.get(action, ("Enter new value:", ""))
        await update.callback_query.edit_message_text(
            f"{prompt}\n\n_(Range: {rng})_",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="st_settings")]
            ]),
            parse_mode='Markdown'
        )
        return ST_SETTINGS_INPUT

    async def handle_st_settings_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Save the new smart setting value entered by the user."""
        user_id = update.effective_user.id
        action  = context.user_data.get('st_settings_action', '')
        raw     = update.message.text.strip().replace('%', '').replace('h', '')

        try:
            val = float(raw)
        except ValueError:
            await update.message.reply_text("❌ Invalid number. Please try again.")
            return ST_SETTINGS_INPUT

        key_map = {
            'sts_stop_loss': ('hard_stop_loss',    lambda v: -abs(v) / 100,  5, 50),
            'sts_trailing':  ('trailing_stop_pct',  lambda v: v / 100,        5, 40),
            'sts_tp1':       ('tp1_threshold',      lambda v: v / 100,        10, 200),
            'sts_tp2':       ('tp2_threshold',      lambda v: v / 100,        10, 500),
            'sts_tp3':       ('tp3_threshold',      lambda v: v / 100,        10, 1000),
            'sts_min_score': ('auto_min_score',     lambda v: int(v),         40, 90),
            'sts_max_pos':   ('max_positions',      lambda v: int(v),         1,  20),
            'sts_max_hold':  ('max_hold_hours',     lambda v: v,              1,  168),
        }
        if action not in key_map:
            await update.message.reply_text("❌ Unknown setting.")
            return MENU

        db_key, transform, lo, hi = key_map[action]
        if not (lo <= val <= hi):
            await update.message.reply_text(f"❌ Value must be between {lo} and {hi}.")
            return ST_SETTINGS_INPUT

        db.set_user_setting(user_id, db_key, transform(val))

        labels = {
            'sts_stop_loss': f"Hard Stop-Loss set to **-{val:.0f}%**",
            'sts_trailing':  f"Trailing Stop set to **{val:.0f}%** from peak",
            'sts_tp1':       f"TP1 threshold set to **+{val:.0f}%**",
            'sts_tp2':       f"TP2 threshold set to **+{val:.0f}%**",
            'sts_tp3':       f"TP3 threshold set to **+{val:.0f}%**",
            'sts_min_score': f"Min momentum score set to **{int(val)}/100**",
            'sts_max_pos':   f"Max positions set to **{int(val)}**",
            'sts_max_hold':  f"Max hold time set to **{val:.0f}h**",
        }
        await update.message.reply_text(
            f"✅ {labels.get(action, 'Setting saved.')}\n\nApplies to all new positions.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎯 Back to Smart Settings", callback_data="st_settings")],
                [InlineKeyboardButton("🔙 Main Menu", callback_data="back_menu")],
            ]),
            parse_mode='Markdown'
        )
        return MENU

    # ── Solana watched wallets ─────────────────────────────────────────────────

    async def view_watched_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List watched Solana wallets."""
        await update.callback_query.answer()
        user_id = update.effective_user.id
        wallets = [w for w in db.get_watched_wallets(user_id)
                   if w.get('chain', 'solana') == 'solana']

        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="copy_trade")]]
        if not wallets:
            await update.callback_query.edit_message_text(
                "🟣 No Solana wallets being watched yet.\nAdd one from the copy trading menu!",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return MENU

        text = "🟣 **Solana Watched Wallets**\n\n"
        for w in wallets:
            status = "⏸️ Paused" if w.get('is_paused') else "✅ Active"
            text += (
                f"• `{w['wallet_address'][:20]}...`\n"
                f"  Scale: {w.get('copy_scale', 1.0)}x | Status: {status}\n\n"
            )
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
        )
        return MENU

    # ── Copy stats ─────────────────────────────────────────────────────────────

    async def active_positions_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Show all currently open positions across all chains."""
        await update.callback_query.answer()
        user_id = update.effective_user.id
        user = db.get_user(user_id)
        if not user:
            await update.callback_query.edit_message_text("❌ No account found.")
            return MENU

        positions = db.get_all_open_positions(user['user_id'])
        copy_pos = positions.get('copy', [])
        smart_pos = positions.get('smart', [])

        total_open = len(copy_pos) + len(smart_pos)

        lines = [f"📂 **Open Positions** ({total_open} active)\n"]

        # ── Copy trade positions ──────────────────────────────────────────
        if copy_pos:
            lines.append("🐋 **Copy Trades:**")
            for p in copy_pos:
                token = p.get('token_address', '')
                short_token = token[:10] + "..." if len(token) > 10 else token
                whale = p.get('watched_wallet', '')
                short_whale = whale[:10] + "..." if len(whale) > 10 else whale
                entry = p.get('user_entry_price', 0) or 0
                spent = p.get('sol_spent', 0) or 0
                token_amt = p.get('token_amount', 0) or 0
                opened = p.get('opened_at', '')[:16] if p.get('opened_at') else 'unknown'
                lines.append(
                    f"  • `{short_token}`\n"
                    f"    Whale: `{short_whale}`\n"
                    f"    Entry: {entry:.8f}  |  Spent: {spent:.4f}\n"
                    f"    Tokens held: {token_amt:.2f}\n"
                    f"    Opened: {opened}"
                )
        else:
            lines.append("🐋 No open copy trades.")

        lines.append("")

        # ── Smart trade positions ─────────────────────────────────────────
        if smart_pos:
            lines.append("🤖 **Smart Trades:**")
            for p in smart_pos:
                token = p.get('token_address', '')
                short_token = token[:10] + "..." if len(token) > 10 else token
                entry = p.get('entry_price', 0) or 0
                spent = p.get('sol_spent', 0) or 0
                token_amt = p.get('token_amount', 0) or 0
                dex = p.get('dex', '?')
                opened = p.get('created_at', '')[:16] if p.get('created_at') else 'unknown'
                lines.append(
                    f"  • `{short_token}`\n"
                    f"    DEX: {dex}  |  Entry: {entry:.8f}\n"
                    f"    Spent: {spent:.4f}  |  Tokens: {token_amt:.2f}\n"
                    f"    Opened: {opened}"
                )
        else:
            lines.append("🤖 No open smart trades.")

        keyboard = [
            [InlineKeyboardButton("🔄 Refresh", callback_data="active_positions"),
             InlineKeyboardButton("🔙 Back", callback_data="analytics")],
        ]
        await update.callback_query.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return MENU

    async def my_holdings_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Display all token holdings in user's wallet with current values."""
        await update.callback_query.answer()
        user_id = update.effective_user.id
        user = db.get_user(user_id)
        
        if not user:
            await update.callback_query.edit_message_text("❌ User not found.")
            return MENU
        
        wallet_addr = user['wallet_address']
        msg = await update.callback_query.edit_message_text("🔍 Scanning wallet for tokens...")
        
        try:
            # Get SOL balance
            sol_balance = await asyncio.to_thread(
                self.wallet_manager.get_balance, wallet_addr
            ) or 0
            
            # Get all SPL token balances
            tokens = await token_manager.get_all_token_balances(wallet_addr)
            
            # Filter out zero balances and sort by value
            holdings = []
            for token in tokens:
                if token.get('amount', 0) > 0:
                    holdings.append(token)
            
            if not holdings and sol_balance == 0:
                keyboard = [
                    [InlineKeyboardButton("💱 Make First Swap", callback_data="swap")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_menu")],
                ]
                await msg.edit_text(
                    "📊 **My Holdings**\n\n"
                    "Your wallet is currently empty.\n\n"
                    "Use the swap feature to acquire your first tokens!",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
                return MENU
            
            # Build holdings display with USD values
            lines = ["📊 **My Holdings**\n\n"]
            keyboard_rows = []
            total_usd = 0.0
            
            # Get SOL price for USD conversion
            sol_price_usd = 0.0
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{BIRDEYE_API_URL}/defi/price",
                        params={'address': 'So11111111111111111111111111111111111111112'},
                        headers={'X-API-KEY': BIRDEYE_API_KEY, 'x-chain': 'solana'},
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get('success'):
                                sol_price_usd = float(data.get('data', {}).get('value', 0))
            except:
                pass
            
            # Show SOL balance first with USD value
            if sol_balance > 0:
                sol_usd_value = sol_balance * sol_price_usd
                total_usd += sol_usd_value
                if sol_price_usd > 0:
                    lines.append(f"🟣 **SOL**: `{sol_balance:.4f}` ≈ `${sol_usd_value:.2f}`\n\n")
                else:
                    lines.append(f"🟣 **SOL**: `{sol_balance:.4f}`\n\n")

            # Show token holdings with USD values
            if holdings:
                lines.append("🪙 **Tokens:**\n")
                for token in holdings[:15]:  # Limit to 15 tokens
                    mint = token.get('mint', '')
                    amount = token.get('amount', 0)
                    decimals = token.get('decimals', 9)
                    short_mint = f"{mint[:8]}...{mint[-6:]}" if len(mint) > 14 else mint
                    
                    # Fetch token price
                    token_info = await self._fetch_token_info(mint)
                    price_usd = token_info.get('price_usd', 0)
                    symbol = token_info.get('symbol', short_mint)
                    token_usd_value = amount * price_usd
                    total_usd += token_usd_value
                    
                    if price_usd > 0:
                        lines.append(f"• **{symbol}**: `{amount:.4f}` ≈ `${token_usd_value:.2f}`\n")
                    else:
                        lines.append(f"• `{amount:.4f}` {symbol}\n")
                    lines.append(f"  Mint: `{short_mint}`\n")

                    # Add sell and send buttons for each token (if not a tiny amount)
                    if amount > 0.001:
                        keyboard_rows.append([
                            InlineKeyboardButton(
                                f"💰 Sell ({amount:.2f})",
                                callback_data=f"hold_sell_{mint}"
                            ),
                            InlineKeyboardButton(
                                f"📤 Send",
                                callback_data=f"hold_send_{mint}"
                            )
                        ])
            
            # Add total portfolio value
            lines.insert(1, f"**Portfolio Value**: `${total_usd:.2f}`\n\n")
            
            # Add action buttons
            keyboard_rows.append([
                InlineKeyboardButton("🔄 Refresh", callback_data="my_holdings"),
                InlineKeyboardButton("🔙 Back", callback_data="back_menu"),
            ])
            
            await msg.edit_text(
                "".join(lines),
                reply_markup=InlineKeyboardMarkup(keyboard_rows),
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Error fetching holdings: {e}")
            keyboard = [
                [InlineKeyboardButton("🔄 Retry", callback_data="my_holdings")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_menu")],
            ]
            await msg.edit_text(
                f"❌ **Error Loading Holdings**\n\n"
                f"Failed to fetch token balances: {str(e)}\n\n"
                f"Please try again or check your wallet on Solscan.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        
        return HOLDINGS_VIEW

    async def holdings_sell_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show sell confirmation for a specific token holding."""
        await update.callback_query.answer()
        user_id = update.effective_user.id
        data = update.callback_query.data  # hold_sell_MINTADDRESS
        
        mint = data.replace("hold_sell_", "")
        user = db.get_user(user_id)
        
        if not user:
            await update.callback_query.edit_message_text("❌ User not found.")
            return MENU
        
        wallet_addr = user['wallet_address']
        
        # Fetch token info and balance
        msg = await update.callback_query.edit_message_text("🔍 Fetching token details...")
        
        try:
            token_info = await self._fetch_token_info(mint)
            token_bal = await token_manager.get_token_balance(wallet_addr, mint)
            
            if not token_bal or token_bal.get('amount', 0) <= 0:
                await msg.edit_text(
                    "❌ **No Balance Found**\n\n"
                    f"You don't have any tokens in your wallet.\n"
                    f"Token: `{mint[:30]}...`",
                    parse_mode='Markdown'
                )
                return HOLDINGS_VIEW
            
            balance = token_bal.get('amount', 0)
            symbol = token_info.get('symbol', mint[:8])
            
            # Store for confirmation
            context.user_data['hold_sell_mint'] = mint
            context.user_data['hold_sell_balance'] = balance
            context.user_data['hold_sell_symbol'] = symbol
            
            keyboard = [
                [InlineKeyboardButton("💰 Sell 25%", callback_data=f"hold_sell_pct_25")],
                [InlineKeyboardButton("💰 Sell 50%", callback_data=f"hold_sell_pct_50")],
                [InlineKeyboardButton("💰 Sell 100%", callback_data=f"hold_sell_pct_100")],
                [InlineKeyboardButton("📝 Custom Amount", callback_data="hold_sell_custom")],
                [InlineKeyboardButton("❌ Cancel", callback_data="my_holdings")],
            ]
            
            await msg.edit_text(
                f"💱 **Sell Tokens**\n\n"
                f"Token: **{symbol}**\n"
                f"Balance: `{balance:.6f}`\n"
                f"Mint: `{mint[:30]}...`\n\n"
                f"Select how much to sell:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Error in holdings sell: {e}")
            await msg.edit_text(
                f"❌ **Error**\n\nFailed to fetch token info: {str(e)}",
                parse_mode='Markdown'
            )
        
        return HOLDINGS_SELL_CONFIRM

    async def holdings_send_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show send screen for a specific token holding."""
        await update.callback_query.answer()
        user_id = update.effective_user.id
        data = update.callback_query.data  # hold_send_MINTADDRESS
        
        mint = data.replace("hold_send_", "")
        user = db.get_user(user_id)
        
        if not user:
            await update.callback_query.edit_message_text("❌ User not found.")
            return MENU
        
        wallet_addr = user['wallet_address']
        
        # Fetch token info and balance
        msg = await update.callback_query.edit_message_text("🔍 Fetching token details...")
        
        try:
            token_info = await self._fetch_token_info(mint)
            token_bal = await token_manager.get_token_balance(wallet_addr, mint)
            
            if not token_bal or token_bal.get('amount', 0) <= 0:
                await msg.edit_text(
                    "❌ **No Balance Found**\n\n"
                    f"You don't have any tokens in your wallet.\n"
                    f"Token: `{mint[:30]}...`",
                    parse_mode='Markdown'
                )
                return HOLDINGS_VIEW
            
            balance = token_bal.get('amount', 0)
            symbol = token_info.get('symbol', mint[:8])
            decimals = token_info.get('decimals', 9)
            
            # Store for send flow
            context.user_data['hold_send_mint'] = mint
            context.user_data['hold_send_balance'] = balance
            context.user_data['hold_send_symbol'] = symbol
            context.user_data['hold_send_decimals'] = decimals
            context.user_data['send_step'] = 'token_address'
            
            keyboard = [
                [InlineKeyboardButton("❌ Cancel", callback_data="my_holdings")],
            ]
            
            await msg.edit_text(
                f"📤 **Send {symbol}**\n\n"
                f"Balance: `{balance:.6f} {symbol}`\n"
                f"Mint: `{mint[:30]}...`\n\n"
                f"⚠️ **Note:** SPL token transfers may require using\n"
                f"an external wallet like Phantom for full support.\n\n"
                f"Enter the **recipient Solana address**:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Error in holdings send: {e}")
            await msg.edit_text(
                f"❌ **Error**\n\nFailed to fetch token info: {str(e)}",
                parse_mode='Markdown'
            )
        
        return SEND_AMOUNT

    async def copy_stats_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show copy trading performance stats."""
        await update.callback_query.answer()
        user_id = update.effective_user.id
        user = db.get_user(user_id)
        stats = db.get_user_stats(user_id)
        copy_trades = stats.get('copy_trades', 0)
        total_volume = stats.get('total_volume', 0)

        # Closed copy performance summary
        perf_rows = db.get_copy_performance(user['user_id'] if user else 0, limit=50) if user else []
        closed = [r for r in perf_rows if r.get('status') == 'closed']
        wins = [r for r in closed if (r.get('user_profit_percent') or 0) > 0]
        avg_profit = (sum(r.get('user_profit_percent', 0) for r in closed) / len(closed)) if closed else 0
        win_rate = (len(wins) / len(closed) * 100) if closed else 0

        keyboard = [
            [InlineKeyboardButton("📂 Open Positions", callback_data="active_positions")],
            [InlineKeyboardButton("🔙 Back", callback_data="analytics")],
        ]
        await update.callback_query.edit_message_text(
            f"🐋 **Copy Trade Stats**\n\n"
            f"Total Copy Trades: {copy_trades}\n"
            f"Closed Positions: {len(closed)}\n"
            f"Win Rate: {win_rate:.1f}%\n"
            f"Avg Profit: {avg_profit:.1f}%\n"
            f"Total Copy Volume: {total_volume:.4f} SOL\n\n"
            f"Tap **Open Positions** to see what's running now.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return MENU

    # ── Risk management sub-handlers ───────────────────────────────────────────

    async def set_takeprofit_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set take-profit."""
        await update.callback_query.answer()
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="risk_mgmt")]]
        await update.callback_query.edit_message_text(
            "🎯 **Set Take-Profit**\n\n"
            "Enter take-profit percentage (e.g., `30` for 30% gain):\n\n"
            "The bot will auto-sell when this target is reached.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return TAKE_PROFIT_PERCENT

    async def handle_takeprofit_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle take-profit amount input."""
        raw = update.message.text.strip().replace('%', '')
        try:
            tp_percent = float(raw)
            if not (0 < tp_percent <= 1000):
                raise ValueError("out of range")
        except ValueError:
            await update.message.reply_text(
                "❌ Please enter a number between 1 and 1000 (e.g. `30` for 30%).",
                parse_mode='Markdown'
            )
            return TAKE_PROFIT_PERCENT

        context.user_data['takeprofit_percent'] = tp_percent
        try:
            db.update_user_setting(update.effective_user.id, 'take_profit_percent', tp_percent)
        except Exception:
            pass

        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]
        await update.message.reply_text(
            f"✅ **Take-Profit set to {tp_percent}%**\n\n"
            f"Auto-sell will trigger when a position gains +{tp_percent}% from entry.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return MENU

    async def trailing_stop_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show trailing stop info."""
        await update.callback_query.answer()
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="risk_mgmt")]]
        await update.callback_query.edit_message_text(
            "📈 **Trailing Stop**\n\n"
            "The copy trader uses a built-in trailing stop:\n\n"
            "• Hard stop-loss: **-20%** from entry\n"
            "• Partial exit: **+30%** (sell 50%)\n"
            "• Trailing stop: **-15%** from peak\n"
            "• Time-decay exit: after **24 hours**\n\n"
            "These apply automatically to all copy trades.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return MENU

    async def view_orders_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show active risk orders."""
        await update.callback_query.answer()
        user_id = update.effective_user.id
        orders = risk_manager.get_active_orders(user_id) if hasattr(risk_manager, 'get_active_orders') else []
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="risk_mgmt")]]
        if not orders:
            await update.callback_query.edit_message_text(
                "👀 **Active Orders**\n\nNo active risk orders.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
            text = "👀 **Active Orders**\n\n"
            for o in orders:
                text += f"• {o.get('type', '?')} @ {o.get('price', 0):.8f}\n"
            await update.callback_query.edit_message_text(
                text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
            )
        return MENU

    # ── Tools sub-handlers ─────────────────────────────────────────────────────

    async def manage_wallets_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show wallet management options."""
        await update.callback_query.answer()
        user_id = update.effective_user.id
        user = db.get_user(user_id)

        text = "👁️ **Wallet Management**\n\n"
        if user and user.get('wallet_address'):
            addr = user['wallet_address']
            balance = await asyncio.to_thread(self.wallet_manager.get_balance, addr) or 0
            text += (
                f"🟣 **Solana Wallet**\n"
                f"  Address: `{addr}`\n"
                f"  Balance: `{balance:.4f} SOL`\n"
            )

        keyboard = [
            [InlineKeyboardButton("🔑 Replace Wallet (Import Key)", callback_data="import_key")],
            [InlineKeyboardButton("📋 Copy Address", callback_data="receive")],
            [InlineKeyboardButton("🔙 Back", callback_data="tools")],
        ]
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
        )
        return MENU

    async def hardware_wallet_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show hardware wallet connection info."""
        await update.callback_query.answer()
        providers = hw_connector.get_supported_wallets() if hasattr(hw_connector, 'get_supported_wallets') else []
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="tools")]]
        text = "🔑 **Hardware Wallet**\n\n"
        if providers:
            text += "Supported devices:\n" + "\n".join(f"• {p}" for p in providers)
        else:
            text += "Hardware wallet integration is available via Ledger/Trezor.\nConnect your device and import the public key."
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
        )
        return MENU

    # ── Notification action handlers ───────────────────────────────────────────

    async def notification_hold_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle 'hold' action on loss warning."""
        await update.callback_query.answer("💪 Holding position!")
        keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="back_menu")]]
        await update.callback_query.edit_message_text(
            "💪 **Holding Position**\n\nYou've chosen to hold and wait for recovery.\n"
            "The bot will continue monitoring this position.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return MENU

    async def notification_ride_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle 'let it ride' action on aging position."""
        await update.callback_query.answer("🚀 Riding the wave!")
        keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="back_menu")]]
        await update.callback_query.edit_message_text(
            "🚀 **Letting It Ride**\n\nYou've chosen to keep holding.\n"
            "The bot will continue monitoring for the trailing stop.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return MENU

    async def notification_tp_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle 'set take-profit' from notification."""
        await update.callback_query.answer()
        keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="back_menu")]]
        await update.callback_query.edit_message_text(
            "🎯 **Take-Profit**\n\nAuto-sell targets are managed by the copy trader.\n"
            "The bot will sell at +30% profit automatically.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return MENU

    async def notification_view_pos_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle 'view position' from notification."""
        await update.callback_query.answer()
        keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="back_menu")]]
        await update.callback_query.edit_message_text(
            "📊 **Position Details**\n\nCheck your open positions via Analytics → Copy Trade Stats.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return MENU

    async def add_watch_wallet_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Add wallet to watch"""
        keyboard = [
            [InlineKeyboardButton("🐋 See Suggested Whales", callback_data="suggested_whales")],
            [InlineKeyboardButton("❌ Cancel", callback_data="back_menu")],
        ]
        await update.callback_query.edit_message_text(
            "🟣 **Add Solana Wallet to Watch**\n\n"
            "Enter the wallet address you want to copy-trade:\n\n"
            "(Must be a valid Solana address)",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ADD_WALLET
    
    async def handle_watch_wallet(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle wallet addition"""
        user_id = update.effective_user.id
        wallet_address = update.message.text.strip()
        
        if not self.wallet_manager.validate_address(wallet_address):
            await update.message.reply_text("❌ Invalid Solana address!")
            return ADD_WALLET
        
        db.add_watched_wallet(user_id, wallet_address)
        
        await update.message.reply_text(
            f"✅ Wallet added!\n\n"
            f"👁️ Now watching: `{wallet_address}`\n"
            f"🔄 Copy trades will execute automatically",
            parse_mode='Markdown'
        )
        
        await self.show_main_menu(update, context)
        return MENU
    
    # View trades
    async def view_trades_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show recent trades"""
        user_id = update.effective_user.id
        trades = db.get_user_trades(user_id, limit=10)
        stats = db.get_user_stats(user_id)
        
        text = (
            f"📊 **Your Trading Stats**\n\n"
            f"Total Trades: {stats['total_trades']}\n"
            f"Copy Trades: {stats['copy_trades']}\n"
            f"Total Volume: {stats['total_volume']:.2f} SOL\n\n"
            f"**Recent Trades:**\n"
        )
        
        if trades:
            for trade in trades[:5]:
                text += (
                    f"• {trade['input_amount']:.4f} → {trade['output_amount']:.4f} "
                    f"({trade['dex']})\n"
                )
        else:
            text += "No trades yet"
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    # Risk Management
    async def risk_mgmt_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Risk management menu"""
        user_id = update.effective_user.id
        user = db.get_user(user_id)
        sl  = user.get('stop_loss_percent', 0) if user else 0
        tp  = user.get('take_profit_percent', 0) if user else 0

        keyboard = [
            [InlineKeyboardButton("🛑 Set Stop-Loss", callback_data="set_stoploss")],
            [InlineKeyboardButton("🎯 Set Take-Profit", callback_data="set_takeprofit")],
            [InlineKeyboardButton("📈 Trailing Stop", callback_data="trailing_stop")],
            [InlineKeyboardButton("👀 View Orders", callback_data="view_orders")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")]
        ]
        sl_line = f"Stop-Loss: `{sl}%`" if sl else "Stop-Loss: _not set_"
        tp_line = f"Take-Profit: `{tp}%`" if tp else "Take-Profit: _not set_"
        await update.callback_query.edit_message_text(
            f"⚠️ **Risk Management**\n\n"
            f"{sl_line}\n{tp_line}\n\n"
            f"These apply as defaults for all new trades.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return MENU
    
    async def set_stoploss_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set stop-loss"""
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="risk_mgmt")]]
        await update.callback_query.edit_message_text(
            "🛑 **Set Stop-Loss**\n\n"
            "Enter stop-loss percentage (e.g., `5` for 5% below entry):",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return STOPLOS_AMOUNT
    
    async def handle_stoploss_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle stop-loss amount — saves as user default."""
        raw = update.message.text.strip().replace('%', '')
        try:
            sl_percent = float(raw)
            if not (0 < sl_percent <= 100):
                raise ValueError("out of range")
        except ValueError:
            await update.message.reply_text(
                "❌ Please enter a number between 1 and 100 (e.g. `15` for 15%).",
                parse_mode='Markdown'
            )
            return STOPLOS_AMOUNT

        context.user_data['stoploss_percent'] = sl_percent

        # Persist as user default in DB
        try:
            db.update_user_setting(update.effective_user.id, 'stop_loss_percent', sl_percent)
        except Exception:
            pass  # non-fatal — setting is still live in context

        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]
        await update.message.reply_text(
            f"✅ **Stop-Loss set to {sl_percent}%**\n\n"
            f"All new trades will automatically stop out at -{sl_percent}% from entry.\n"
            f"Existing open positions are not affected.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return MENU
    
    # Analytics
    async def analytics_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show analytics"""
        user_id = update.effective_user.id
        try:
            metrics = analytics.calculate_performance_metrics(user_id)
        except Exception:
            metrics = {}

        text = (
            f"📈 **Performance Analytics**\n\n"
            f"Total Trades: {metrics.get('total_trades', 0)}\n"
            f"Win Rate: {metrics.get('win_rate', 0.0):.1f}%\n"
            f"Winning Trades: {metrics.get('winning_trades', 0)}\n"
            f"Losing Trades: {metrics.get('losing_trades', 0)}\n"
            f"Profit Factor: {metrics.get('profit_factor', 0.0):.2f}\n"
            f"Max Drawdown: ${metrics.get('max_drawdown', 0.0):.2f}\n"
            f"Avg Profit/Trade: ${metrics.get('avg_profit_per_trade', 0.0):.2f}"
        )
        
        keyboard = [
            [InlineKeyboardButton("📂 Open Positions", callback_data="active_positions")],
            [InlineKeyboardButton("📊 Daily Report", callback_data="daily_report")],
            [InlineKeyboardButton("🐋 Copy Trade Stats", callback_data="copy_stats")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def daily_report_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Generate daily report"""
        user_id = update.effective_user.id
        report = analytics.generate_daily_report(user_id)
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            report,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    # Tools menu (Wallets & Vanity)
    async def tools_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Tools menu"""
        await update.callback_query.answer()
        keyboard = [
            [InlineKeyboardButton("✨ Create Vanity Wallet", callback_data="create_vanity")],
            [InlineKeyboardButton("👁️ Manage Wallets", callback_data="manage_wallets")],
            [InlineKeyboardButton("⚡ Priority Fees", callback_data="priority_fees")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.edit_message_text(
            "🔧 **Tools**",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def create_vanity_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Create vanity wallet"""
        await update.callback_query.answer()
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="tools")]]
        await update.callback_query.edit_message_text(
            "✨ **Vanity Wallet Generator**\n\n"
            "Enter prefix (1-6 characters, base58 only):\n"
            "Example: `elite`, `moon`, `sol`\n\n"
            "⚠️ Warning: Longer prefixes take much longer to generate",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return VANITY_PREFIX
    
    async def handle_vanity_prefix(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle vanity prefix"""
        prefix = update.message.text.strip()
        
        if len(prefix) > 6:
            await update.message.reply_text("❌ Prefix too long (max 6 characters)")
            return VANITY_PREFIX
        
        context.user_data['vanity_prefix'] = prefix

        keyboard = [
            [InlineKeyboardButton("🔤 Match at Start", callback_data="vanity_pos_start")],
            [InlineKeyboardButton("🔚 Match at End", callback_data="vanity_pos_end")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            "Where should the vanity string match?",
            reply_markup=reply_markup,
        )
        return VANITY_POSITION

    async def handle_vanity_position(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle vanity match position (start/end)."""
        await update.callback_query.answer()

        callback_data = update.callback_query.data
        match_position = "start" if callback_data.endswith("start") else "end"
        context.user_data["vanity_match_position"] = match_position
        
        keyboard = [
            [InlineKeyboardButton("🔠 Case Sensitive", callback_data="case_yes")],
            [InlineKeyboardButton("🔡 Case Insensitive", callback_data="case_no")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.edit_message_text(
            "Should the vanity match be case-sensitive?",
            reply_markup=reply_markup,
        )
        return VANITY_CASE

    async def handle_vanity_case(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle vanity case-sensitivity choice."""
        await update.callback_query.answer()

        callback_data = update.callback_query.data
        context.user_data["vanity_case_sensitive"] = True if callback_data == "case_yes" else False

        keyboard = [
            [InlineKeyboardButton("⚡ Easy (3 chars, ~1min)", callback_data="diff_3")],
            [InlineKeyboardButton("🔥 Medium (4 chars, ~30min)", callback_data="diff_4")],
            [InlineKeyboardButton("💪 Hard (5 chars, ~2hrs)", callback_data="diff_5")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.edit_message_text(
            "Select difficulty level:",
            reply_markup=reply_markup,
        )
        return VANITY_DIFFICULTY
    
    async def handle_vanity_generation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Generate vanity wallet"""
        difficulty = int(update.callback_query.data.split('_')[1])
        prefix = context.user_data['vanity_prefix']
        match_position = context.user_data.get("vanity_match_position", "start")
        case_sensitive = context.user_data.get("vanity_case_sensitive", True)
        
        await update.callback_query.edit_message_text(
            f"🎲 Generating vanity wallet...\n"
            f"Prefix: {prefix}\n"
            f"Match: {match_position}\n"
            f"Case: {'sensitive' if case_sensitive else 'insensitive'}\n"
            f"Difficulty: {difficulty}\n\n"
            f"This may take a while..."
        )
        
        try:
            pub_key, secret_key, diff = await vanity_generator.generate_vanity_wallet(
                prefix,
                difficulty,
                match_position=match_position,
                case_sensitive=case_sensitive,
            )

            user = db.get_user(update.effective_user.id) or {}
            internal_user_id = user.get("user_id", update.effective_user.id)

            # Store in database
            encrypted_key = encryption.encrypt(secret_key)
            db.add_vanity_wallet(
                internal_user_id,
                pub_key,
                prefix,
                diff,
                encrypted_key,
                match_position=match_position,
                case_sensitive=case_sensitive,
            )

            is_admin = bool(user.get("is_admin"))

            if is_admin:
                await update.callback_query.edit_message_text(
                    f"✨ **Vanity Wallet Created!**\n\n"
                    f"📮 Address:\n`{pub_key}`\n\n"
                    f"🔑 Private Key (base58):\n`{secret_key}`\n\n"
                    f"⚠️ Anyone with this key controls your wallet.",
                    parse_mode='Markdown',
                )
            else:
                await update.callback_query.edit_message_text(
                    f"✨ Vanity Wallet Created!**\n\n"
                    f"📮 Address:\n`{pub_key}`\n\n"
                    f"🔑 Private Key:\n`{secret_key}`\n\n"
                    f"⚠️ Anyone with this key controls your wallet.",

                    parse_mode='Markdown',
                )
        except Exception as e:
            logger.error(f"Vanity generation error: {e}")
            await update.callback_query.edit_message_text(
                f"❌ Error generating wallet: {str(e)}"
            )
        
        await self.show_main_menu(update, context)
        return MENU
    
    async def priority_fees_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Priority fee level selector."""
        await update.callback_query.answer()
        current = context.user_data.get('priority_level', 'auto')
        labels = {
            'low':   '🐢 Low    (~1k μL) — cheapest',
            'auto':  '⚡ Auto   (75th %) — default',
            'high':  '🚀 High  (~50k μL) — fast',
            'turbo': '🔥 Turbo (200k μL) — fastest',
        }

        def mark(k):
            return f"✅ {labels[k]}" if k == current else labels[k]

        keyboard = [
            [InlineKeyboardButton(mark('low'),   callback_data="pf_low")],
            [InlineKeyboardButton(mark('auto'),  callback_data="pf_auto")],
            [InlineKeyboardButton(mark('high'),  callback_data="pf_high")],
            [InlineKeyboardButton(mark('turbo'), callback_data="pf_turbo")],
            [InlineKeyboardButton("🔙 Back", callback_data="tools")],
        ]
        await update.callback_query.edit_message_text(
            "⚡ **Priority Fees**\n\n"
            "Higher priority fees make your transactions confirm faster\n"
            "but cost slightly more in SOL.\n\n"
            f"Current setting: **{current.upper()}**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return MENU

    async def handle_priority_fee_select(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Store chosen priority fee level."""
        await update.callback_query.answer()
        level = update.callback_query.data.replace("pf_", "")  # low/auto/high/turbo
        context.user_data['priority_level'] = level
        fee_map = {'low': 1_000, 'auto': None, 'high': 50_000, 'turbo': 200_000}
        fee_display = {
            'low': '~1,000 microlamports',
            'auto': '75th-percentile (varies)',
            'high': '~50,000 microlamports',
            'turbo': '~200,000 microlamports',
        }
        await update.callback_query.edit_message_text(
            f"✅ Priority fee set to **{level.upper()}**\n\n"
            f"Fee: {fee_display[level]}\n\n"
            f"This will apply to all swaps in this session.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Tools", callback_data="tools")]
            ]),
            parse_mode='Markdown'
        )
        return MENU
    
    async def back_to_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Go back to main menu"""
        await self.show_main_menu(update, context)
        return MENU
    
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Cancel conversation"""
        await update.message.reply_text("❌ Cancelled")
        return ConversationHandler.END
    
    # Admin panel methods
    
    async def admin_panel_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin panel menu"""
        user_id = update.effective_user.id
        
        if not admin_panel.is_admin(user_id):
            await update.callback_query.answer("❌ Not authorized", show_alert=True)
            return MENU
        
        await update.callback_query.answer()
        stats = admin_panel.get_bot_stats()

        keyboard = [
            [InlineKeyboardButton("👥 Manage Users", callback_data="admin_users")],
            [InlineKeyboardButton("💼 Wallet Management", callback_data="admin_wallets")],
            [InlineKeyboardButton("📊 Bot Statistics", callback_data="admin_stats")],
            [InlineKeyboardButton("🔐 Decrypt Keys", callback_data="admin_decrypt")],
            [InlineKeyboardButton("📈 Generate Report", callback_data="admin_report")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        text = (
            f"🛡️ **ADMIN PANEL**\n\n"
            f"**Bot Statistics:**\n"
            f"  👥 Users: {stats.get('total_users', 0)}\n"
            f"  👨‍💼 Admins: {stats.get('total_admins', 0)}\n"
            f"  🔄 Trades: {stats.get('total_trades', 0)}\n"
            f"  💰 Total Profit: ${stats.get('total_profit', 0):.2f}\n"
            f"  🎯 Risk Orders: {stats.get('active_risk_orders', 0)}\n"
            f"  🐋 Copy Targets: {stats.get('copy_trading_targets', 0)}"
        )

        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        return ADMIN_MENU
    
    async def admin_users_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List all users"""
        await update.callback_query.answer()
        users = admin_panel.get_all_users()
        
        if not users:
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]
            await update.callback_query.edit_message_text(
                "No users found",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return ADMIN_MENU
        
        text = "👥 **ALL USERS**\n\n"
        for user in users[:10]:  # Show first 10
            admin_badge = "👨‍💼" if user.get('is_admin') else "📱"
            text += (
                f"{admin_badge} User: {user['telegram_id']}\n"
                f"  • Address: `{user['wallet_address'][:16]}...`\n"
                f"  • Created: {user['created_at'][:10]}\n\n"
            )
        
        if len(users) > 10:
            text += f"\n... and {len(users) - 10} more users"
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]
        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return ADMIN_MENU
    
    async def admin_wallets_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Wallet management"""
        await update.callback_query.answer()
        keyboard = [
            [InlineKeyboardButton("📋 List All Wallets", callback_data="admin_list_wallets")],
            [InlineKeyboardButton("💰 View Balance", callback_data="admin_view_balance")],
            [InlineKeyboardButton("📈 View Profit", callback_data="admin_view_profit")],
            [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = "💼 **Wallet Management**\n\nSelect an option:"
        
        await update.callback_query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return ADMIN_WALLET_ACTION
    
    async def admin_decrypt_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Decrypt private key"""
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "🔐 Enter master password to decrypt keys:\n"
            "(Type password or /cancel to exit)"
        )
        return ADMIN_MASTER_PASSWORD
    
    async def handle_admin_decrypt(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle decryption request"""
        user_id = update.effective_user.id
        password = update.message.text
        target_user_id = context.user_data.get('target_user_id', user_id)
        
        # Decrypt the key
        decrypted_key = admin_panel.decrypt_wallet_key(target_user_id, password)
        
        if not decrypted_key:
            await update.message.reply_text("❌ Incorrect password or key not found")
            return ADMIN_MASTER_PASSWORD
        
        # Show key preview for security
        text = (
            f"🔑 **Decrypted Private Key**\n\n"
            f"User: {target_user_id}\n"
            f"Key: `{decrypted_key}`\n\n"
            f"⚠️ **SECURITY WARNING:** Never share this key!"
        )
        
        await update.message.reply_text(text, parse_mode='Markdown')
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel")]]
        await update.message.reply_text(
            "Back to admin panel?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ADMIN_MENU
    
    async def admin_stats_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show bot statistics"""
        await update.callback_query.answer()
        stats = admin_panel.get_bot_stats()

        total_profit = stats.get('total_profit', 0)
        total_trades = stats.get('total_trades', 0)
        text = (
            f"📊 **Bot Statistics**\n\n"
            f"**Users:**\n"
            f"  👥 Total: {stats.get('total_users', 0)}\n"
            f"  👨‍💼 Admins: {stats.get('total_admins', 0)}\n\n"
            f"**Trading:**\n"
            f"  📊 Total Trades: {total_trades}\n"
            f"  💰 Total Profit: ${total_profit:.2f}\n"
            f"  📈 Avg/Trade: ${total_profit/max(total_trades,1):.2f}\n\n"
            f"**Features:**\n"
            f"  🎯 Vanity Wallets: {stats.get('total_vanity_wallets', 0)}\n"
            f"  🛑 Risk Orders: {stats.get('active_risk_orders', 0)}\n"
            f"  🐋 Copy Targets: {stats.get('copy_trading_targets', 0)}\n\n"
            f"Generated: {datetime.now().strftime('%H:%M:%S')}"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]
        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return ADMIN_MENU
    
    async def admin_report_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Generate admin report"""
        await update.callback_query.answer()
        report = admin_panel.generate_admin_report()
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]
        await update.callback_query.edit_message_text(
            report,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return ADMIN_MENU

    async def admin_list_wallets_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List every user wallet address"""
        await update.callback_query.answer()
        users = admin_panel.get_all_users()
        if not users:
            text = "No users found."
        else:
            text = "💼 **All User Wallets**\n\n"
            for u in users[:20]:
                addr = u.get('wallet_address') or 'N/A'
                short = f"`{addr[:20]}...`" if addr != 'N/A' else 'N/A'
                badge = "👨‍💼" if u.get('is_admin') else "👤"
                text += f"{badge} `{u['telegram_id']}` — {short}\n"
            if len(users) > 20:
                text += f"\n_…and {len(users) - 20} more_"
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_wallets")]]
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
        )
        return ADMIN_WALLET_ACTION

    async def admin_view_balance_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show SOL balance for every user wallet"""
        await update.callback_query.answer()
        users = admin_panel.get_all_users()
        if not users:
            text = "No users found."
        else:
            text = "💰 **Wallet Balances**\n\n"
            for u in users[:15]:
                addr = u.get('wallet_address')
                if addr:
                    balance = admin_panel.get_wallet_balance(addr)
                    text += f"• `{u['telegram_id']}` — {balance:.4f} SOL\n"
            if len(users) > 15:
                text += f"\n_…and {len(users) - 15} more_"
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_wallets")]]
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
        )
        return ADMIN_WALLET_ACTION

    async def admin_view_profit_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show PnL summary for every user wallet"""
        await update.callback_query.answer()
        users = admin_panel.get_all_users()
        if not users:
            text = "No users found."
        else:
            text = "📈 **Wallet Profit Summary**\n\n"
            for u in users[:15]:
                addr = u.get('wallet_address')
                if addr:
                    pnl = admin_panel.get_wallet_profit(addr)
                    sign = "+" if pnl['net_profit'] >= 0 else ""
                    text += (
                        f"• `{u['telegram_id']}`\n"
                        f"  Net: {sign}{pnl['net_profit']:.4f} SOL | "
                        f"Trades: {pnl['total_trades']} | "
                        f"WR: {pnl['win_rate']:.0f}%\n"
                    )
            if len(users) > 15:
                text += f"\n_…and {len(users) - 15} more_"
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_wallets")]]
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
        )
        return ADMIN_WALLET_ACTION


# Smart notification handlers
from datetime import datetime

async def send_notification_to_user(bot_app, user_id: int, alert: Dict):
    """Send profit/loss alert to user with action buttons"""
    try:
        if alert['type'] == 'profit_milestone':
            keyboard = [
                [InlineKeyboardButton(
                    f"💰 Sell Now ({alert['roi']:.1f}% 📈)",
                    callback_data=f"sell_token_{alert['position_id']}"
                )],
                [InlineKeyboardButton(
                    "💎 Hold (Set Take-Profit)",
                    callback_data=f"tp_token_{alert['position_id']}"
                )],
                [InlineKeyboardButton(
                    "📊 View Position",
                    callback_data=f"view_pos_{alert['position_id']}"
                )]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            text = (
                f"🎉 **PROFIT ALERT!**\n\n"
                f"📈 ROI: **{alert['roi']:.1f}%**\n"
                f"💵 Profit: **${alert['profit']:.4f}**\n"
                f"Entry Price: ${alert['entry_price']:.8f}\n"
                f"Current Price: ${alert['current_price']:.8f}\n\n"
                f"🎯 Milestone Hit: {alert['threshold']}%\n\n"
                f"Do you want to take profits?"
            )
            
            await bot_app.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        
        elif alert['type'] == 'cutloss_suggestion':
            keyboard = [
                [InlineKeyboardButton(
                    "❌ Cut Loss (Sell Now)",
                    callback_data=f"cutloss_{alert['position_id']}"
                )],
                [InlineKeyboardButton(
                    "💪 Hold & Recover",
                    callback_data=f"hold_{alert['position_id']}"
                )]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            text = (
                f"⚠️ **LOSS WARNING**\n\n"
                f"📉 ROI: **{alert['roi']:.1f}%**\n"
                f"💔 Loss: **${alert['profit']:.4f}**\n"
                f"Current Price: ${alert['current_price']:.8f}\n\n"
                f"Your position is down over 50%.\n"
                f"Consider cutting losses?"
            )
            
            await bot_app.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        
        elif alert['type'] == 'aging_position':
            keyboard = [
                [InlineKeyboardButton(
                    f"💸 Sell & Take Profits ({alert['roi']:.1f}%)",
                    callback_data=f"sell_aging_{alert['position_id']}"
                )],
                [InlineKeyboardButton(
                    "🚀 Let It Ride",
                    callback_data=f"ride_{alert['position_id']}"
                )]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            text = (
                f"⏰ **AGING POSITION ALERT**\n\n"
                f"Position held for **{alert['hours_held']:.1f} hours**\n"
                f"📈 ROI: **{alert['roi']:.1f}%**\n"
                f"💰 Profit: **${alert['profit']:.4f}**\n\n"
                f"This token has been profitable for a while.\n"
                f"Good time to secure profits?"
            )
            
            await bot_app.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
    
    except Exception as e:
        logger.error(f"Error sending notification: {e}")


async def handle_sell_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle sell action from profit alert"""
    query = update.callback_query
    action_data = query.data
    
    await query.answer("Processing sell action...")
    
    if 'sell_token' in action_data:
        position_id = action_data[len("sell_token_"):]
        
        # Get position data
        position = notification_engine.active_positions.get(position_id)
        if position:
            # Create sell order message
            keyboard = [
                [InlineKeyboardButton("✅ Confirm Sell", callback_data=f"confirm_sell_{position_id}")],
                [InlineKeyboardButton("❌ Cancel", callback_data="back_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            text = (
                f"🔄 **SELL CONFIRMATION**\n\n"
                f"Token: {position['token_address'][:20]}...\n"
                f"Amount: {position['amount_bought']}\n"
                f"Current Price: ${position['current_price']:.8f}\n"
                f"Expected Profit: ${position['profit']:.4f}\n\n"
                f"Swap back to SOL?\n"
                f"🎯 ROI: {position['roi']:.1f}%"
            )
            
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def confirm_sell_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and execute sell"""
    query = update.callback_query
    position_id = query.data[len("confirm_sell_"):]
    
    position = notification_engine.active_positions.get(position_id)
    if position:
        # Execute sell (swap back to SOL)
        await query.edit_message_text("⏳ Executing sell order on DEX...", parse_mode='Markdown')
        
        try:
            # Swap token back to SOL
            from trading.copy_trader import copy_trader as _ct
            _keypair = _ct._get_user_keypair(position['user_id'])
            swap_result = await swapper.execute_swap(
                position['token_address'],
                "So11111111111111111111111111111111111111112",  # WSOL
                position['amount_bought'],
                position['dex'],
                keypair=_keypair
            )
            
            if swap_result and swap_result.get('status') in ('confirmed', 'quoted'):
                sol_received = swap_result.get('expectedOutput', 0)
                tx_sig = swap_result.get('signature', 'manual_sell')
                u_id = position['user_id']
                tok = position['token_address']

                notification_engine.close_position(position_id)

                # Close DB record and cancel background monitor
                pos_type = position.get('position_type', 'smart')
                if pos_type == 'copy':
                    db_pos_id = position.get('db_position_id')
                    if db_pos_id:
                        db.close_copy_position(db_pos_id, position.get('current_price', 0),
                                               sol_received, 'manual_sell')
                    from trading.copy_trader import copy_trader as _copy_trader
                    pm = _copy_trader._position_monitors.pop((u_id, tok), None)
                else:
                    db.update_pending_trade_closed(u_id, tok, sol_received, tx_sig)
                    from trading.smart_trader import smart_trader as _smart_trader
                    pm = _smart_trader._position_monitors.pop((u_id, tok), None)
                if pm and not pm.done():
                    pm.cancel()

                text = (
                    f"✅ **SELL COMPLETED!**\n\n"
                    f"📈 Final ROI: {position['roi']:.1f}%\n"
                    f"💰 SOL received: {sol_received:.4f}\n"
                    f"Entry: {position['entry_price']:.8f} SOL/token\n\n"
                    f"🎉 Profits secured!"
                )
                await query.edit_message_text(text, parse_mode='Markdown')

                db.add_trade(
                    user_id=u_id,
                    input_mint=tok,
                    output_mint="So11111111111111111111111111111111111111112",
                    input_amount=position['amount_bought'],
                    output_amount=sol_received,
                    dex=position['dex'],
                    price=position.get('current_price', 0),
                    slippage=0.5,
                    tx_hash=tx_sig,
                    is_copy=(pos_type == 'copy')
                )
            else:
                await query.edit_message_text("❌ Sell failed. Please try again.", parse_mode='Markdown')
        
        except Exception as e:
            logger.error(f"Error executing sell: {e}")
            await query.edit_message_text(f"❌ Error: {str(e)}", parse_mode='Markdown')


async def notification_checker(context):
    """Background job — runs every 60s via job_queue."""
    async def _send(user_id, alert):
        await send_notification_to_user(context.application, user_id, alert)
    await notification_engine.check_once(_send)


async def main():
    """Start the bot"""
    # Create the Application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    bot = TelegramBot()
    
    # Conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", bot.start)],
        states={
            START: [
                CallbackQueryHandler(bot.create_wallet_callback, pattern="^create_wallet$"),
                CallbackQueryHandler(bot.import_key_callback, pattern="^import_key$"),
                CallbackQueryHandler(bot.start),
            ],
            MENU: [
                # Wallet setup (new users land here after /start)
                CallbackQueryHandler(bot.create_wallet_callback, pattern="^create_wallet$"),
                CallbackQueryHandler(bot.import_key_callback, pattern="^import_key$"),
                # Main menu — Solana
                CallbackQueryHandler(bot.swap_callback, pattern="^swap$"),
                CallbackQueryHandler(bot.smart_trade_callback, pattern="^smart_trade$"),
                # Smart Trader v2 sub-menu (accessible from MENU via back-nav)
                CallbackQueryHandler(bot.st_manual_callback, pattern="^st_manual$"),
                CallbackQueryHandler(bot.st_auto_copy_toggle_callback,  pattern="^st_auto_copy_toggle$"),
                CallbackQueryHandler(bot.st_auto_smart_toggle_callback, pattern="^st_auto_smart_toggle$"),
                CallbackQueryHandler(bot.st_suggestions_callback, pattern="^st_suggestions$"),
                CallbackQueryHandler(bot.st_buy_suggested_callback, pattern="^st_buy_"),
                CallbackQueryHandler(bot.st_blacklist_callback, pattern="^st_blacklist$"),
                CallbackQueryHandler(bot.copy_trade_callback, pattern="^copy_trade$"),
                CallbackQueryHandler(bot.view_trades_callback, pattern="^view_trades$"),
                CallbackQueryHandler(bot.send_tokens_callback, pattern="^send_tokens$"),
                CallbackQueryHandler(bot.send_from_sol_callback, pattern="^send_from_sol$"),
                CallbackQueryHandler(bot.receive_callback, pattern="^receive$"),
                CallbackQueryHandler(bot.risk_mgmt_callback, pattern="^risk_mgmt$"),
                CallbackQueryHandler(bot.analytics_callback, pattern="^analytics$"),
                CallbackQueryHandler(bot.tools_callback, pattern="^tools$"),
                CallbackQueryHandler(bot.settings_callback, pattern="^settings$"),
                CallbackQueryHandler(bot.slippage_settings_callback, pattern="^slippage_settings$"),
                CallbackQueryHandler(bot.slippage_select_callback, pattern="^slip_"),
                CallbackQueryHandler(bot.view_private_key_callback, pattern="^view_private_key$"),
                CallbackQueryHandler(bot.st_settings_callback, pattern="^st_settings$"),
                CallbackQueryHandler(bot.st_settings_action_callback, pattern="^sts_"),
                CallbackQueryHandler(bot.admin_panel_callback, pattern="^admin_panel$"),
                # Copy trade sub-menu (SOL)
                CallbackQueryHandler(bot.add_watch_wallet_callback, pattern="^add_watch_wallet$"),
                CallbackQueryHandler(bot.view_watched_callback, pattern="^view_watched$"),
                CallbackQueryHandler(bot.suggested_whales_callback, pattern="^suggested_whales$"),
                CallbackQueryHandler(bot.add_sol_whale_callback, pattern="^add_sol_whale_"),
                # Analytics sub-menu
                CallbackQueryHandler(bot.active_positions_callback, pattern="^active_positions$"),
                CallbackQueryHandler(bot.my_holdings_callback, pattern="^my_holdings$"),
                CallbackQueryHandler(bot.daily_report_callback, pattern="^daily_report$"),
                CallbackQueryHandler(bot.copy_stats_callback, pattern="^copy_stats$"),
                # Risk management sub-menu
                CallbackQueryHandler(bot.set_stoploss_callback, pattern="^set_stoploss$"),
                CallbackQueryHandler(bot.set_takeprofit_callback, pattern="^set_takeprofit$"),
                CallbackQueryHandler(bot.trailing_stop_callback, pattern="^trailing_stop$"),
                CallbackQueryHandler(bot.view_orders_callback, pattern="^view_orders$"),
                # Tools sub-menu
                CallbackQueryHandler(bot.create_vanity_callback, pattern="^create_vanity$"),
                CallbackQueryHandler(bot.manage_wallets_callback, pattern="^manage_wallets$"),
                CallbackQueryHandler(bot.priority_fees_callback, pattern="^priority_fees$"),
                CallbackQueryHandler(bot.handle_priority_fee_select, pattern="^pf_"),
                # Back to menu
                CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
            ],
            WALLET_TRADING_CHOICE: [
                CallbackQueryHandler(bot.create_sol_wallet_callback, pattern="^create_sol_wallet$"),
                CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
            ],
            IMPORT_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_private_key)],
            # ── Solana swap ────────────────────────────────────────────────────
            SWAP_SELECT: [
                CallbackQueryHandler(bot.swap_amount_prompt,
                                     pattern="^swap_(sol_to_token|token_to_sol|token_to_token)$"),
            ],
            SWAP_TOKEN_INPUT: [
                CallbackQueryHandler(bot.sol_token_pick_callback, pattern="^soltok_(?!out_)"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_sol_custom_token),
                CommandHandler("start", bot.start),
            ],
            SWAP_OUTPUT_TOKEN: [
                CallbackQueryHandler(bot.sol_output_token_pick_callback, pattern="^soltok_out_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_sol_custom_output_token),
                CommandHandler("start", bot.start),
            ],
            SWAP_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_swap_amount),
                CommandHandler("start", bot.start),
            ],
            CONFIRM_SWAP: [
                CallbackQueryHandler(bot.confirm_swap, pattern="^confirm_sol_swap_"),
                CallbackQueryHandler(bot.confirm_custom_token_callback, pattern="^confirm_custom_(input|output)$"),
                CallbackQueryHandler(bot.large_trade_confirm_callback, pattern="^large_trade_confirm$"),
                CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
            ],
            SEND_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot._route_send_text),
                CallbackQueryHandler(bot.confirm_send_callback, pattern="^confirm_send$"),
                CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
                CommandHandler("start", bot.start),
            ],
            ADD_WALLET: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_watch_wallet)],
            # ── Holdings view ────────────────────────────────────────────────
            HOLDINGS_VIEW: [
                CallbackQueryHandler(bot.holdings_sell_callback, pattern="^hold_sell_"),
                CallbackQueryHandler(bot.holdings_send_callback, pattern="^hold_send_"),
                CallbackQueryHandler(bot.my_holdings_callback, pattern="^my_holdings$"),
                CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
                CommandHandler("start", bot.start),
            ],
            HOLDINGS_SELL_CONFIRM: [
                CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
                CommandHandler("start", bot.start),
            ],
            # ── Smart Trader v2 states ────────────────────────────────────────
            SMART_TRADE: [
                CallbackQueryHandler(bot.st_manual_callback, pattern="^st_manual$"),
                CallbackQueryHandler(bot.st_auto_copy_toggle_callback,  pattern="^st_auto_copy_toggle$"),
                CallbackQueryHandler(bot.st_auto_smart_toggle_callback, pattern="^st_auto_smart_toggle$"),
                CallbackQueryHandler(bot.st_suggestions_callback, pattern="^st_suggestions$"),
                CallbackQueryHandler(bot.st_buy_suggested_callback, pattern="^st_buy_"),
                CallbackQueryHandler(bot.st_blacklist_callback, pattern="^st_blacklist$"),
                CallbackQueryHandler(bot.active_positions_callback, pattern="^active_positions$"),
                CallbackQueryHandler(bot.smart_trade_callback, pattern="^smart_trade$"),
                CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
                CommandHandler("start", bot.start),
            ],
            TRADE_PERCENT_SELECT: [
                CallbackQueryHandler(bot.handle_trade_percent, pattern="^trade_"),
                CallbackQueryHandler(bot.smart_trade_callback, pattern="^smart_trade$"),
                CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
                CommandHandler("start", bot.start),
            ],
            AUTO_TRADE_PERCENT: [
                CallbackQueryHandler(bot.handle_auto_trade_percent, pattern="^at_"),
                CallbackQueryHandler(bot.smart_trade_callback, pattern="^smart_trade$"),
                CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
            ],
            AUTO_SMART_PERCENT: [
                CallbackQueryHandler(bot.handle_auto_smart_percent,   pattern="^as_"),
                CallbackQueryHandler(bot.handle_auto_smart_positions, pattern="^asp_"),
                CallbackQueryHandler(bot.smart_trade_callback,        pattern="^smart_trade$"),
                CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
            ],
            BLACKLIST_TOKEN_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_blacklist_input),
            ],
            SMART_TOKEN_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_smart_token)],
            TAKE_PROFIT_PERCENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_takeprofit_amount)],
            STOPLOS_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_stoploss_amount)],
            ST_SETTINGS_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_st_settings_input),
                CallbackQueryHandler(bot.st_settings_callback, pattern="^st_settings$"),
                CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
            ],
            VANITY_PREFIX: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_vanity_prefix)],
            VANITY_POSITION: [
                CallbackQueryHandler(bot.handle_vanity_position, pattern="^vanity_pos_"),
                CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
            ],
            VANITY_CASE: [
                CallbackQueryHandler(bot.handle_vanity_case, pattern="^case_"),
                CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
            ],
            VANITY_DIFFICULTY: [
                CallbackQueryHandler(bot.handle_vanity_generation, pattern="^diff_"),
                CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
            ],
            ADMIN_MENU: [
                CallbackQueryHandler(bot.admin_users_callback, pattern="^admin_users$"),
                CallbackQueryHandler(bot.admin_wallets_callback, pattern="^admin_wallets$"),
                CallbackQueryHandler(bot.admin_stats_callback, pattern="^admin_stats$"),
                CallbackQueryHandler(bot.admin_decrypt_callback, pattern="^admin_decrypt$"),
                CallbackQueryHandler(bot.admin_report_callback, pattern="^admin_report$"),
                CallbackQueryHandler(bot.admin_panel_callback, pattern="^admin_panel$"),
                CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
            ],
            ADMIN_MASTER_PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_admin_decrypt),
            ],
            ADMIN_WALLET_ACTION: [
                CallbackQueryHandler(bot.admin_list_wallets_callback,  pattern="^admin_list_wallets$"),
                CallbackQueryHandler(bot.admin_view_balance_callback,  pattern="^admin_view_balance$"),
                CallbackQueryHandler(bot.admin_view_profit_callback,   pattern="^admin_view_profit$"),
                CallbackQueryHandler(bot.admin_wallets_callback,       pattern="^admin_wallets$"),
                CallbackQueryHandler(bot.admin_panel_callback,         pattern="^admin_panel$"),
                CallbackQueryHandler(bot.back_to_menu,                 pattern="^back_menu$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
            # Navigation that can happen from any state
            CallbackQueryHandler(bot.copy_trade_callback, pattern="^copy_trade$"),
            CallbackQueryHandler(bot.active_positions_callback, pattern="^active_positions$"),
            CallbackQueryHandler(bot.my_holdings_callback, pattern="^my_holdings$"),
            CallbackQueryHandler(bot.analytics_callback, pattern="^analytics$"),
            CallbackQueryHandler(bot.risk_mgmt_callback, pattern="^risk_mgmt$"),
            CallbackQueryHandler(bot.tools_callback, pattern="^tools$"),
            CallbackQueryHandler(bot.settings_callback, pattern="^settings$"),
            CallbackQueryHandler(bot.slippage_settings_callback, pattern="^slippage_settings$"),
            CallbackQueryHandler(bot.slippage_select_callback, pattern="^slip_"),
            CallbackQueryHandler(bot.view_private_key_callback, pattern="^view_private_key$"),
            CallbackQueryHandler(bot.st_settings_callback, pattern="^st_settings$"),
            CallbackQueryHandler(bot.st_settings_action_callback, pattern="^sts_"),
            CallbackQueryHandler(bot.priority_fees_callback, pattern="^priority_fees$"),
            CallbackQueryHandler(bot.handle_priority_fee_select, pattern="^pf_"),
            CallbackQueryHandler(bot.admin_panel_callback,        pattern="^admin_panel$"),
            CallbackQueryHandler(bot.admin_wallets_callback,      pattern="^admin_wallets$"),
            CallbackQueryHandler(bot.admin_users_callback,        pattern="^admin_users$"),
            CallbackQueryHandler(bot.admin_stats_callback,        pattern="^admin_stats$"),
            CallbackQueryHandler(bot.admin_report_callback,       pattern="^admin_report$"),
            CallbackQueryHandler(bot.admin_list_wallets_callback, pattern="^admin_list_wallets$"),
            CallbackQueryHandler(bot.admin_view_balance_callback, pattern="^admin_view_balance$"),
            CallbackQueryHandler(bot.admin_view_profit_callback,  pattern="^admin_view_profit$"),
            CallbackQueryHandler(bot.import_key_callback, pattern="^import_key$"),
            CallbackQueryHandler(bot.create_wallet_callback, pattern="^create_wallet$"),
            # Send flow
            CallbackQueryHandler(bot.send_from_sol_callback, pattern="^send_from_sol$"),
            CallbackQueryHandler(bot.confirm_send_callback, pattern="^confirm_send$"),
            # Whales
            CallbackQueryHandler(bot.suggested_whales_callback, pattern="^suggested_whales$"),
            CallbackQueryHandler(bot.add_sol_whale_callback, pattern="^add_sol_whale_"),
            # Notification action buttons
            CallbackQueryHandler(bot.notification_hold_callback, pattern="^hold_"),
            CallbackQueryHandler(bot.notification_ride_callback, pattern="^ride_"),
            CallbackQueryHandler(bot.notification_tp_callback, pattern="^tp_token_"),
            CallbackQueryHandler(bot.notification_view_pos_callback, pattern="^view_pos_"),
            CallbackQueryHandler(handle_sell_action, pattern="^sell_token_"),
            CallbackQueryHandler(handle_sell_action, pattern="^cutloss_"),
            CallbackQueryHandler(handle_sell_action, pattern="^sell_aging_"),
            CallbackQueryHandler(confirm_sell_action, pattern="^confirm_sell_"),
            # Allow /start command from any state
            CommandHandler("start", bot.start),
            CommandHandler("cancel", bot.cancel)
        ]
    )
    
    application.add_handler(conv_handler)
    
    # Add background notification checker
    application.job_queue.run_repeating(
        notification_checker,
        interval=60,  # Check every 60 seconds
        first=0  # Start immediately
    )
    
    # Start the bot using low-level async API so it works inside an
    # already-running event loop (asyncio.run in main.py).
    async with application:
        await application.start()
        await application.updater.start_polling()

        # Wire up plain-text notifications
        async def _notify_callback(user_id: int, message: str):
            try:
                await application.bot.send_message(
                    chat_id=user_id, text=message, parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"notify_user failed for {user_id}: {e}")
        notification_engine.set_send_callback(_notify_callback)

        # Wire up rich trade-opened notifications (with Sell / View buttons)
        async def _trade_opened_callback(user_id: int, position_id: str, text: str):
            try:
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("💰 Sell Now", callback_data=f"sell_token_{position_id}"),
                    InlineKeyboardButton("📊 View", callback_data=f"view_pos_{position_id}"),
                ]])
                await application.bot.send_message(
                    chat_id=user_id, text=text,
                    reply_markup=keyboard, parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"_trade_opened_callback failed for {user_id}: {e}")
        notification_engine.set_trade_opened_callback(_trade_opened_callback)

        # Recover any auto-traders that were running before the last restart
        await smart_trader.recover_auto_traders()
        try:
            await asyncio.Event().wait()  # Block until cancelled
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            await application.updater.stop()
            await application.stop()


if __name__ == '__main__':
    asyncio.run(main())
