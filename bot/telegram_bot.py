"""
Telegram bot with inline buttons for DEX copy trading
"""
import logging
import asyncio
from typing import Dict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler
)
from config import TELEGRAM_BOT_TOKEN
from data.database import db
from chains.solana.wallet import SolanaWallet, encrypt_private_key, decrypt_private_key
from chains.solana.dex_swaps import swapper
from trading.copy_trader import copy_trader
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

# Conversation states
(START, MENU, IMPORT_KEY, ADD_WALLET, SWAP_SELECT, SWAP_AMOUNT, CONFIRM_SWAP,
 VANITY_PREFIX, VANITY_DIFFICULTY, STOPLOS_AMOUNT, TAKE_PROFIT_PERCENT,
 ANALYTICS_TYPE, HARDWARE_WALLET_SELECT, SELL_AMOUNT,
 ADMIN_MENU, ADMIN_USERS, ADMIN_MASTER_PASSWORD, ADMIN_WALLET_ACTION,
 SMART_TRADE, TRADE_PERCENT_SELECT, SMART_TOKEN_INPUT,
 CREATE_WALLET, WALLET_TRADING_CHOICE, SEND_AMOUNT, RECEIVE_MENU) = range(25)


class TelegramBot:
    """Telegram bot interface"""
    
    def __init__(self):
        self.wallet_manager = SolanaWallet()
        self.import_keys = {}  # Temporary storage for import flow
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Start command"""
        user_id = update.effective_user.id
        
        # Check if user exists
        user = db.get_user(user_id)
        
        if not user:
            keyboard = [
                [InlineKeyboardButton("🔐 Import Private Key", callback_data="import_key")],
                [InlineKeyboardButton("📋 Create New Wallet", callback_data="create_wallet")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "👋 Welcome to **Ultimate DEX Copy Trader**!\n\n"
                "This bot lets you:\n"
                "✅ Swap any tokens on Solana DEXs (Jupiter, Raydium, Orca)\n"
                "✅ Copy trades from whale wallets\n"
                "✅ Manage multiple wallet strategies\n\n"
                "Let's get started!",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await self.show_main_menu(update, context)
        
        return MENU
    
    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show main menu"""
        user_id = update.effective_user.id
        user = db.get_user(user_id)
        
        balance = self.wallet_manager.get_balance(user['wallet_address']) or 0
        
        keyboard = [
            [InlineKeyboardButton("💱 Swap Tokens", callback_data="swap")],
            [InlineKeyboardButton("� Send SOL", callback_data="send_tokens"), InlineKeyboardButton("📥 Receive", callback_data="receive")],
            [InlineKeyboardButton("�🐋 Copy Trading", callback_data="copy_trade")],
            [InlineKeyboardButton("🤖 Smart Analyzer", callback_data="smart_trade")],
            [InlineKeyboardButton("⚠️ Risk Management", callback_data="risk_mgmt")],
            [InlineKeyboardButton("📊 Analytics", callback_data="analytics")],
            [InlineKeyboardButton("🔧 Wallets & Tools", callback_data="tools")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings")]
        ]
        
        # Add admin panel if user is admin
        if admin_panel.is_admin(user_id):
            keyboard.append([InlineKeyboardButton("🛡️ Admin Panel", callback_data="admin_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = (
            f"🤖 **Main Menu**\n\n"
            f"💰 Balance: {balance:.4f} SOL\n"
            f"📮 Wallet: `{user['wallet_address'][:20]}...`\n"
        )
        
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    
    # Import wallet flow
    async def import_key_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start import key flow"""
        await update.callback_query.edit_message_text(
            "🔐 Please send your Solana private key (base58 format)\n\n"
            "⚠️ **SECURITY WARNING**\n"
            "Never share your private key with anyone!\n"
            "This bot will encrypt and secure it.",
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
    
    # Swap flow
    async def swap_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start swap flow"""
        keyboard = [
            [InlineKeyboardButton("SOL → Token", callback_data="swap_sol_to_token")],
            [InlineKeyboardButton("Token → SOL", callback_data="swap_token_to_sol")],
            [InlineKeyboardButton("Token → Token", callback_data="swap_token_to_token")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            "💱 **Swap Tokens**\n\nSelect swap direction:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return SWAP_SELECT
    
    async def swap_amount_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Prompt for swap amount"""
        swap_type = update.callback_query.data
        context.user_data['swap_type'] = swap_type
        
        await update.callback_query.edit_message_text(
            f"Amount to swap? (in SOL or token units)"
        )
        return SWAP_AMOUNT
    
    async def handle_swap_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle swap amount"""
        try:
            amount = float(update.message.text)
            context.user_data['swap_amount'] = amount
            
            keyboard = [
                [InlineKeyboardButton("Jupiter (Best Rates)", callback_data="dex_jupiter")],
                [InlineKeyboardButton("Raydium", callback_data="dex_raydium")],
                [InlineKeyboardButton("Orca", callback_data="dex_orca")],
                [InlineKeyboardButton("Best Price", callback_data="dex_best")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"Select DEX for {amount} SOL swap:",
                reply_markup=reply_markup
            )
            return CONFIRM_SWAP
        
        except ValueError:
            await update.message.reply_text("❌ Invalid amount. Please enter a number.")
            return SWAP_AMOUNT
    
    async def confirm_swap(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Confirm and execute swap"""
        try:
            dex = update.callback_query.data.replace("dex_", "")
            amount = context.user_data.get('swap_amount', 0)
            
            await update.callback_query.edit_message_text(
                f"⏳ Processing swap on {dex.upper()}...",
                parse_mode='Markdown'
            )
            
            # Get best price
            # input_mint and output_mint would be set in actual implementation
            swap_data = await swapper.get_best_price("token_mint", "other_mint", amount)
            
            if swap_data:
                keyboard = [
                    [InlineKeyboardButton("✅ Confirm", callback_data="execute_swap")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_swap")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                text = (
                    f"💱 **Swap Preview**\n\n"
                    f"Input: {amount} SOL\n"
                    f"Output: ~{swap_data.get('price', 0)}\n"
                    f"DEX: {dex}\n"
                    f"Price Impact: {swap_data.get('priceImpact', 0)}%\n\n"
                    f"Continue?"
                )
                
                await update.callback_query.edit_message_text(
                    text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            
            return CONFIRM_SWAP
        
        except Exception as e:
            logger.error(f"Error confirming swap: {e}")
            await update.callback_query.edit_message_text("❌ Error processing swap")
    
    # Smart trader flow
    async def smart_trade_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start smart token analyzer and trader"""
        user_id = update.effective_user.id
        
        # Show trade % selection
        user = db.get_user(user_id)
        current_percent = user.get('trade_percent', 20.0) if user else 20.0
        
        keyboard = [
            [InlineKeyboardButton("5% - Conservative", callback_data="trade_5")],
            [InlineKeyboardButton("10% - Safe", callback_data="trade_10")],
            [InlineKeyboardButton("15% - Balanced", callback_data="trade_15")],
            [InlineKeyboardButton("20% - Standard", callback_data="trade_20")],
            [InlineKeyboardButton("25% - Moderate", callback_data="trade_25")],
            [InlineKeyboardButton("30% - Aggressive", callback_data="trade_30")],
            [InlineKeyboardButton("40% - Very Aggressive", callback_data="trade_40")],
            [InlineKeyboardButton("50% - Max", callback_data="trade_50")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            f"🤖 **Smart Token Analyzer**\n\n"
            f"Select trade amount % (currently: {current_percent}%)\n\n"
            f"The bot will:\n"
            f"1️⃣ Analyze token safety\n"
            f"2️⃣ Check contract, liquidity, holders\n"
            f"3️⃣ Detect honeypots & rug pulls\n"
            f"4️⃣ Auto-trade if safe\n"
            f"5️⃣ Auto-sell at 30% profit\n",
            reply_markup=reply_markup,
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
        await update.callback_query.edit_message_text(
            f"✅ Trade amount set to {percent}%\n\n"
            f"📝 Please send the token address (contract address):\n\n"
            f"Example:\n"
            f"`SolXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX`",
            parse_mode='Markdown'
        )
        return SMART_TOKEN_INPUT
    
    async def handle_smart_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle token address input and start smart analysis/trade"""
        user_id = update.effective_user.id
        token_address = update.message.text.strip()
        selected_percent = context.user_data.get('selected_trade_percent', 20.0)
        
        # Validate token address format
        if not token_address.startswith(('So', 'EPj')) or len(token_address) < 40:
            await update.message.reply_text(
                "❌ Invalid token address format!\n\n"
                "Please send a valid Solana token contract address.",
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
        
        try:
            # Run smart trade analysis in background
            result = await smart_trader.analyze_and_trade(
                user_id=user_id,
                token_address=token_address,
                user_trade_percent=selected_percent,
                dex="jupiter"
            )
            
            # Update message with results
            if result.get('status') == 'SUCCESS':
                risk_score = result.get('risk_assessment', {}).get('risk_score', 0)
                tx_sig = result.get('tx_signature', 'N/A')
                
                await msg.edit_text(
                    f"✅ **Trade Executed Successfully!**\n\n"
                    f"Token: `{token_address[:20]}`\n"
                    f"Amount: {result.get('trade_amount_sol', 0):.4f} SOL\n"
                    f"Risk Score: {risk_score:.1f}/100\n"
                    f"TX: `{tx_sig[:20]}...`\n\n"
                    f"📊 Position monitoring started...\n"
                    f"🎯 Auto-sell trigger: +30% profit\n",
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
        """Copy trading menu"""
        keyboard = [
            [InlineKeyboardButton("➕ Add Wallet to Watch", callback_data="add_watch_wallet")],
            [InlineKeyboardButton("👁️ View Watched Wallets", callback_data="view_watched")],
            [InlineKeyboardButton("🐋 Suggested Whales", callback_data="suggested_whales")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            "🐋 **Copy Trading**\n\nWhat would you like to do?",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def add_watch_wallet_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Add wallet to watch"""
        await update.callback_query.edit_message_text(
            "Enter the wallet address you want to monitor:\n\n"
            "(Must be valid Solana address)"
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
        keyboard = [
            [InlineKeyboardButton("🛑 Set Stop-Loss", callback_data="set_stoploss")],
            [InlineKeyboardButton("🎯 Set Take-Profit", callback_data="set_takeprofit")],
            [InlineKeyboardButton("📈 Trailing Stop", callback_data="trailing_stop")],
            [InlineKeyboardButton("👀 View Orders", callback_data="view_orders")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            "⚠️ **Risk Management**\n\nProtect your trades with orders:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def set_stoploss_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set stop-loss"""
        await update.callback_query.edit_message_text(
            "Enter stop-loss percentage (e.g., 5 for 5% below entry):"
        )
        return STOPLOS_AMOUNT
    
    async def handle_stoploss_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle stop-loss amount"""
        try:
            sl_percent = float(update.message.text)
            context.user_data['stoploss_percent'] = sl_percent
            
            # Create stop-loss order
            order = risk_manager.create_stop_loss_order(
                user_id=update.effective_user.id,
                token_address=context.user_data.get('current_token'),
                entry_price=context.user_data.get('entry_price', 0),
                loss_percent=sl_percent
            )
            
            text = (
                f"✅ Stop-loss order created\n\n"
                f"Stop Price: ${order['stop_price']:.8f}\n"
                f"Loss Limit: {sl_percent}%"
            )
            
            await update.message.reply_text(text, parse_mode='Markdown')
            await self.show_main_menu(update, context)
        except ValueError:
            await update.message.reply_text("❌ Invalid percentage")
            return STOPLOS_AMOUNT
    
    # Analytics
    async def analytics_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show analytics"""
        user_id = update.effective_user.id
        metrics = analytics.calculate_performance_metrics(user_id)
        
        text = (
            f"📈 **Performance Analytics**\n\n"
            f"Total Trades: {metrics['total_trades']}\n"
            f"Win Rate: {metrics['win_rate']:.1f}%\n"
            f"Winning Trades: {metrics['winning_trades']}\n"
            f"Losing Trades: {metrics['losing_trades']}\n"
            f"Profit Factor: {metrics['profit_factor']:.2f}\n"
            f"Max Drawdown: ${metrics['max_drawdown']:.2f}\n"
            f"Avg Profit/Trade: ${metrics['avg_profit_per_trade']:.2f}"
        )
        
        keyboard = [
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
        keyboard = [
            [InlineKeyboardButton("✨ Create Vanity Wallet", callback_data="create_vanity")],
            [InlineKeyboardButton("👁️ Manage Wallets", callback_data="manage_wallets")],
            [InlineKeyboardButton("🔑 Hardware Wallet", callback_data="hardware_wallet")],
            [InlineKeyboardButton("🛡️ MEV Protection", callback_data="mev_protection")],
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
        await update.callback_query.edit_message_text(
            "✨ **Vanity Wallet Generator**\n\n"
            "Enter prefix (1-6 characters, base58 only):\n"
            "Example: 'ELITE', 'MOON', 'SOL'\n\n"
            "⚠️ Warning: Longer prefixes take much longer to generate"
        )
        return VANITY_PREFIX
    
    async def handle_vanity_prefix(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle vanity prefix"""
        prefix = update.message.text.strip().upper()
        
        if len(prefix) > 6:
            await update.message.reply_text("❌ Prefix too long (max 6 characters)")
            return VANITY_PREFIX
        
        context.user_data['vanity_prefix'] = prefix
        
        keyboard = [
            [InlineKeyboardButton("⚡ Easy (3 chars, ~1min)", callback_data="diff_3")],
            [InlineKeyboardButton("🔥 Medium (4 chars, ~30min)", callback_data="diff_4")],
            [InlineKeyboardButton("💪 Hard (5 chars, ~2hrs)", callback_data="diff_5")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "Select difficulty level:",
            reply_markup=reply_markup
        )
        return VANITY_DIFFICULTY
    
    async def handle_vanity_generation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Generate vanity wallet"""
        difficulty = int(update.callback_query.data.split('_')[1])
        prefix = context.user_data['vanity_prefix']
        
        await update.callback_query.edit_message_text(
            f"🎲 Generating vanity wallet...\n"
            f"Prefix: {prefix}\n"
            f"Difficulty: {difficulty}\n\n"
            f"This may take a while..."
        )
        
        try:
            pub_key, secret_key, diff = await vanity_generator.generate_vanity_wallet(prefix, difficulty)
            
            # Store in database
            encrypted_key = encryption.encrypt(secret_key)
            db.add_vanity_wallet(update.effective_user.id, pub_key, prefix, diff, encrypted_key)
            
            await update.callback_query.edit_message_text(
                f"✨ **Vanity Wallet Created!**\n\n"
                f"Address: `{pub_key}`\n\n"
                f"⚠️ Save your private key securely!\n"
                f"The key has been encrypted and stored."
            )
        except Exception as e:
            logger.error(f"Vanity generation error: {e}")
            await update.callback_query.edit_message_text(
                f"❌ Error generating wallet: {str(e)}"
            )
        
        await self.show_main_menu(update, context)
        return MENU
    
    async def mev_protection_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """MEV protection options"""
        providers = mev_protection.get_mev_protection_providers()
        
        text = "🛡️ **MEV Protection Providers**\n\n"
        for prov in providers:
            text += (
                f"**{prov['name']}**\n"
                f"Type: {prov['type']}\n"
                f"Cost: {prov['cost']}\n\n"
            )
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
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
            f"  👥 Users: {stats['total_users']}\n"
            f"  👨‍💼 Admins: {stats['total_admins']}\n"
            f"  🔄 Trades: {stats['total_trades']}\n"
            f"  💰 Total Profit: ${stats['total_profit']:.2f}\n"
            f"  🎯 Risk Orders: {stats['active_risk_orders']}\n"
            f"  🐋 Copy Targets: {stats['copy_trading_targets']}"
        )
        
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        return ADMIN_MENU
    
    async def admin_users_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List all users"""
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
        stats = admin_panel.get_bot_stats()
        
        text = (
            f"📊 **Bot Statistics**\n\n"
            f"**Users:**\n"
            f"  👥 Total: {stats['total_users']}\n"
            f"  👨‍💼 Admins: {stats['total_admins']}\n\n"
            f"**Trading:**\n"
            f"  📊 Total Trades: {stats['total_trades']}\n"
            f"  💰 Total Profit: ${stats['total_profit']:.2f}\n"
            f"  📈 Avg/Trade: ${stats['total_profit']/max(stats['total_trades'],1):.2f}\n\n"
            f"**Features:**\n"
            f"  🎯 Vanity Wallets: {stats['total_vanity_wallets']}\n"
            f"  🛑 Risk Orders: {stats['active_risk_orders']}\n"
            f"  🐋 Copy Targets: {stats['copy_trading_targets']}\n\n"
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
        report = admin_panel.generate_admin_report()
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]
        await update.callback_query.edit_message_text(
            report,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return ADMIN_MENU


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
        position_id = action_data.split('_')[2]
        
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
    position_id = query.data.split('_')[2]
    
    position = notification_engine.active_positions.get(position_id)
    if position:
        # Execute sell (swap back to SOL)
        await query.edit_message_text("⏳ Executing sell order on DEX...", parse_mode='Markdown')
        
        try:
            # Swap token back to SOL
            swap_result = await swapper.execute_swap(
                position['token_address'],
                "So11111111111111111111111111111111111111112",  # WSOL
                position['amount_bought'],
                position['dex']
            )
            
            if swap_result:
                notification_engine.close_position(position_id)
                
                text = (
                    f"✅ **SELL COMPLETED!**\n\n"
                    f"📈 Final ROI: {position['roi']:.1f}%\n"
                    f"💰 Total Profit: ${position['profit']:.4f}\n"
                    f"Entry: ${position['entry_price']:.8f}\n"
                    f"Exit: ${position['current_price']:.8f}\n\n"
                    f"🎉 Great trade! Profits secured!"
                )
                
                await query.edit_message_text(text, parse_mode='Markdown')
                
                # Log trade
                db.add_trade(
                    user_id=position['user_id'],
                    input_mint=position['token_address'],
                    output_mint="So11111111111111111111111111111111111111112",
                    input_amount=position['amount_bought'],
                    output_amount=position['amount_bought'] * position['current_price'],
                    dex=position['dex'],
                    price=position['current_price'],
                    slippage=0.5,
                    tx_hash='notification_sell',
                    is_copy=False
                )
            else:
                await query.edit_message_text("❌ Sell failed. Please try again.", parse_mode='Markdown')
        
        except Exception as e:
            logger.error(f"Error executing sell: {e}")
            await query.edit_message_text(f"❌ Error: {str(e)}", parse_mode='Markdown')


async def notification_checker(context):
    """Background task to check for notifications"""
    await notification_engine.monitor_all_users()


async def main():
    """Start the bot"""
    # Create the Application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    bot = TelegramBot()
    
    # Conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", bot.start)],
        states={
            START: [CallbackQueryHandler(bot.start)],
            MENU: [
                CallbackQueryHandler(bot.import_key_callback, pattern="^import_key$"),
                CallbackQueryHandler(bot.swap_callback, pattern="^swap$"),
                CallbackQueryHandler(bot.smart_trade_callback, pattern="^smart_trade$"),
                CallbackQueryHandler(bot.copy_trade_callback, pattern="^copy_trade$"),
                CallbackQueryHandler(bot.view_trades_callback, pattern="^view_trades$"),
                CallbackQueryHandler(bot.admin_panel_callback, pattern="^admin_panel$"),
            ],
            IMPORT_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_private_key)],
            SWAP_SELECT: [CallbackQueryHandler(bot.swap_amount_prompt, pattern="^swap_")],
            SWAP_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_swap_amount)],
            CONFIRM_SWAP: [CallbackQueryHandler(bot.confirm_swap, pattern="^dex_")],
            ADD_WALLET: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_watch_wallet)],
            TRADE_PERCENT_SELECT: [CallbackQueryHandler(bot.handle_trade_percent, pattern="^trade_")],
            SMART_TOKEN_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_smart_token)],
            ADMIN_MENU: [
                CallbackQueryHandler(bot.admin_users_callback, pattern="^admin_users$"),
                CallbackQueryHandler(bot.admin_wallets_callback, pattern="^admin_wallets$"),
                CallbackQueryHandler(bot.admin_stats_callback, pattern="^admin_stats$"),
                CallbackQueryHandler(bot.admin_decrypt_callback, pattern="^admin_decrypt$"),
                CallbackQueryHandler(bot.admin_report_callback, pattern="^admin_report$"),
            ],
            ADMIN_MASTER_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_admin_decrypt)],
            ADMIN_WALLET_ACTION: [
                CallbackQueryHandler(bot.admin_wallets_callback, pattern="^admin_list_wallets$"),
                CallbackQueryHandler(bot.admin_wallets_callback, pattern="^admin_view_balance$"),
                CallbackQueryHandler(bot.admin_wallets_callback, pattern="^admin_view_profit$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(bot.back_to_menu, pattern="^back_menu$"),
            CallbackQueryHandler(handle_sell_action, pattern="^sell_token_"),
            CallbackQueryHandler(handle_sell_action, pattern="^cutloss_"),
            CallbackQueryHandler(handle_sell_action, pattern="^sell_aging_"),
            CallbackQueryHandler(confirm_sell_action, pattern="^confirm_sell_"),
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
    
    # Start the bot
    await application.run_polling()


if __name__ == '__main__':
    asyncio.run(main())
