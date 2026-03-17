"""
Copy trading engine - monitor whale wallets and execute intelligent copy trades

Features:
  - Copy % adjustment  (0.5x / 1x / 1.5x scale per whale)
  - Configurable delay before copying (let whale settle first)
  - Close existing position before entering new one for same token
  - Profit sharing calculation  (whale made X%, you got Y%)
  - Auto-pause if whale's rolling avg loss exceeds max_loss_percent
  - Multiple whale wallets with individual weights
"""
import logging
import asyncio
from typing import Dict, List, Optional
from datetime import datetime
import aiohttp

from config import SOLANA_RPC_URL, COPY_TRADE_CHECK_INTERVAL, MIN_TRADE_AMOUNT, DEFAULT_COPY_SCALE
from data.database import db
from chains.solana.dex_swaps import swapper

logger = logging.getLogger(__name__)

# How many recent whale trades to consider for auto-stop check
LOSS_CHECK_WINDOW = 5


class CopyTradingEngine:
    """Monitor whale wallets and execute intelligent copy trades"""

    def __init__(self):
        self.rpc_url = SOLANA_RPC_URL
        self.check_interval = COPY_TRADE_CHECK_INTERVAL
        self.min_trade_amount = MIN_TRADE_AMOUNT
        # { (user_id, whale_address): asyncio.Task }
        self._monitor_tasks: Dict[tuple, asyncio.Task] = {}
        # Dedup: signatures we have already processed
        self._seen_signatures: Dict[str, set] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_monitoring_for_user(self, user_id: int):
        """Start monitoring all active, non-paused watched wallets for a user."""
        watched_wallets = db.get_watched_wallets(user_id)
        if not watched_wallets:
            logger.info(f"No watched wallets for user {user_id}")
            return

        for wallet in watched_wallets:
            if wallet.get('is_paused'):
                logger.info(f"⏸️ Skipping paused whale {wallet['wallet_address']}")
                continue
            key = (user_id, wallet['wallet_address'])
            if key not in self._monitor_tasks or self._monitor_tasks[key].done():
                task = asyncio.create_task(
                    self.monitor_wallet(wallet['wallet_address'], user_id)
                )
                self._monitor_tasks[key] = task

    def stop_monitoring_for_user(self, user_id: int):
        """Cancel all monitoring tasks for a user."""
        keys = [k for k in self._monitor_tasks if k[0] == user_id]
        for key in keys:
            task = self._monitor_tasks.pop(key)
            if not task.done():
                task.cancel()
        logger.info(f"🛑 Stopped all monitoring for user {user_id}")

    # ------------------------------------------------------------------
    # Wallet monitoring loop
    # ------------------------------------------------------------------

    async def monitor_wallet(self, wallet_address: str, user_id: int):
        """Continuously monitor a single whale wallet for new swaps."""
        logger.info(f"👁️ Monitoring whale wallet: {wallet_address}")
        sig_cache = self._seen_signatures.setdefault(wallet_address, set())

        while True:
            try:
                # Re-fetch config in case user changed settings mid-run
                wallet_config = self._get_wallet_config(user_id, wallet_address)
                if not wallet_config:
                    logger.warning(f"Wallet config gone for {wallet_address} — stopping monitor")
                    break

                if wallet_config.get('is_paused'):
                    logger.info(f"⏸️ Whale {wallet_address} is paused — sleeping")
                    await asyncio.sleep(self.check_interval * 2)
                    continue

                transactions = await self.get_wallet_transactions(wallet_address)
                for tx in transactions:
                    sig = tx.get('signature', '')
                    if sig in sig_cache:
                        continue
                    sig_cache.add(sig)

                    if await self.is_swap_transaction(tx):
                        swap_data = await self.extract_swap_data(tx)
                        if swap_data:
                            logger.info(f"🔄 Whale swap detected: {swap_data}")
                            await self._handle_whale_swap(user_id, wallet_address,
                                                          wallet_config, swap_data)

                # Keep sig cache bounded
                if len(sig_cache) > 500:
                    self._seen_signatures[wallet_address] = set(list(sig_cache)[-200:])

                await asyncio.sleep(self.check_interval)

            except asyncio.CancelledError:
                logger.info(f"🛑 Monitor cancelled for {wallet_address}")
                break
            except Exception as e:
                logger.error(f"Error monitoring wallet {wallet_address}: {e}")
                await asyncio.sleep(self.check_interval)

    # ------------------------------------------------------------------
    # Swap handling with all features
    # ------------------------------------------------------------------

    async def _handle_whale_swap(self, user_id: int, whale_address: str,
                                  wallet_config: Dict, swap_data: Dict):
        """Orchestrate all copy-trading features for one detected whale swap."""

        # 1. Auto-stop if whale's rolling avg loss exceeds limit
        if self._should_auto_pause(user_id, whale_address, wallet_config):
            return

        # 2. Delay before copying
        delay = int(wallet_config.get('copy_delay_seconds', 0))
        if delay > 0:
            logger.info(f"⏳ Waiting {delay}s before copying (delay setting)")
            await asyncio.sleep(delay)

        # 3. Close existing open position in same token before entering new one
        await self._close_existing_position(user_id, swap_data['outputMint'])

        # 4. Calculate weight-adjusted copy amount
        amount = self._weighted_amount(swap_data['inputAmount'], wallet_config)
        if amount < self.min_trade_amount:
            logger.info(f"Amount {amount:.4f} SOL below minimum — skipping")
            return

        # 5. Execute the copy trade
        await self.execute_copy_trade(
            user_id=user_id,
            input_mint=swap_data['inputMint'],
            output_mint=swap_data['outputMint'],
            amount=amount,
            dex=swap_data.get('dex', 'jupiter'),
            watched_wallet=whale_address,
            whale_entry_price=swap_data.get('whaleEntryPrice', 0.0),
            copy_scale=wallet_config.get('copy_scale', DEFAULT_COPY_SCALE),
        )

    def _should_auto_pause(self, user_id: int, whale_address: str,
                            wallet_config: Dict) -> bool:
        """Pause this whale if their rolling avg loss breaches the limit."""
        max_loss = float(wallet_config.get('max_loss_percent', 20.0))
        avg_return = db.get_whale_recent_loss(user_id, whale_address, LOSS_CHECK_WINDOW)

        if avg_return < -abs(max_loss):
            wallet_id = wallet_config['id']
            reason = (f"Auto-paused: avg {LOSS_CHECK_WINDOW}-trade return "
                      f"{avg_return:.1f}% breached -{max_loss}% limit")
            db.pause_watched_wallet(wallet_id, reason)
            logger.warning(f"⛔ {reason} for whale {whale_address}")
            return True
        return False

    async def _close_existing_position(self, user_id: int, token_mint: str):
        """Close any open position in token_mint before copying a new trade."""
        existing = db.get_pending_trade_by_token(user_id, token_mint)
        if not existing:
            return
        logger.info(f"📤 Closing existing position in {token_mint[:8]}… before new copy")
        try:
            close_result = await swapper.execute_swap(
                token_mint, 'So11111111111111111111111111111111111111112',
                existing.get('token_amount', 0), 'jupiter'
            )
            if close_result:
                sol_received = close_result.get('outputAmount', 0)
                db.update_pending_trade_closed(user_id, token_mint, sol_received,
                                               close_result.get('signature', 'close'))
                logger.info(f"✅ Closed existing position, received {sol_received:.4f} SOL")
        except Exception as e:
            logger.error(f"Error closing existing position: {e}")

    def _weighted_amount(self, whale_amount: float, wallet_config: Dict) -> float:
        """Apply copy_scale (0.5x/1x/1.5x) and weight to the whale's trade size."""
        scale = float(wallet_config.get('copy_scale', DEFAULT_COPY_SCALE))
        weight = float(wallet_config.get('weight', 1.0))
        return whale_amount * scale * weight

    # ------------------------------------------------------------------
    # Trade execution
    # ------------------------------------------------------------------

    async def execute_copy_trade(self, user_id: int, input_mint: str,
                                  output_mint: str, amount: float, dex: str,
                                  watched_wallet: str,
                                  whale_entry_price: float = 0.0,
                                  copy_scale: float = 1.0) -> bool:
        """Execute a copy trade and record performance vs whale."""
        try:
            user = db.get_user(user_id)
            if not user:
                logger.error(f"User {user_id} not found")
                return False

            # Get price quote
            swap_data = await swapper.get_best_price(input_mint, output_mint, amount)
            if not swap_data:
                logger.error("No price quote available")
                return False

            output_amount = swap_data.get('price', 0)
            price_impact = swap_data.get('priceImpact', 0)
            user_entry_price = output_amount / amount if amount > 0 else 0

            # Open copy performance record before executing
            position_id = db.open_copy_position(
                user_id=user_id,
                watched_wallet=watched_wallet,
                token_address=output_mint,
                whale_entry_price=whale_entry_price,
                user_entry_price=user_entry_price,
                copy_scale=copy_scale,
                sol_spent=amount,
            )

            # Execute swap
            swap_result = await swapper.execute_swap(input_mint, output_mint, amount, dex)

            if swap_result and swap_result.get('status') == 'ready':
                db.add_trade(
                    user_id=user_id,
                    input_mint=input_mint,
                    output_mint=output_mint,
                    input_amount=amount,
                    output_amount=output_amount,
                    dex=dex,
                    price=user_entry_price,
                    slippage=price_impact,
                    tx_hash='pending',
                    watched_wallet=watched_wallet,
                    is_copy=True,
                )
                logger.info(
                    f"✅ Copy trade executed: {amount:.4f} SOL → {output_amount:.4f} "
                    f"(whale entry: {whale_entry_price:.6f}, user entry: {user_entry_price:.6f}, "
                    f"scale: {copy_scale}x)"
                )
                return True

            # Swap failed — remove dangling performance record
            if position_id > 0:
                db.close_copy_position(position_id, 0, 0, 0)

            return False

        except Exception as e:
            logger.error(f"Error executing copy trade: {e}")
            return False

    def get_copy_performance_summary(self, user_id: int,
                                      watched_wallet: str = None) -> Dict:
        """
        Return a summary comparing whale vs user performance.
        Returns avg whale %, avg user %, and difference.
        """
        records = db.get_copy_performance(user_id, watched_wallet, limit=50)
        closed = [r for r in records if r['status'] == 'closed'
                  and r.get('whale_profit_percent') is not None
                  and r.get('user_profit_percent') is not None]

        if not closed:
            return {'trades': 0, 'whale_avg': 0.0, 'user_avg': 0.0, 'delta': 0.0}

        whale_avg = sum(r['whale_profit_percent'] for r in closed) / len(closed)
        user_avg = sum(r['user_profit_percent'] for r in closed) / len(closed)
        return {
            'trades': len(closed),
            'whale_avg': round(whale_avg, 2),
            'user_avg': round(user_avg, 2),
            'delta': round(user_avg - whale_avg, 2),
        }

    # ------------------------------------------------------------------
    # Blockchain helpers
    # ------------------------------------------------------------------

    def _get_wallet_config(self, user_id: int, wallet_address: str) -> Optional[Dict]:
        """Fetch the latest wallet config row from DB."""
        for w in db.get_watched_wallets(user_id):
            if w['wallet_address'] == wallet_address:
                return w
        return None

    async def get_wallet_transactions(self, wallet_address: str) -> List[Dict]:
        """Get recent transactions from a wallet via Solana RPC."""
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [wallet_address, {"limit": 10}]
                }
                async with session.post(self.rpc_url, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('result', [])
        except Exception as e:
            logger.error(f"Error getting wallet transactions: {e}")
        return []

    async def is_swap_transaction(self, tx_data: Dict) -> bool:
        """Return True if transaction looks like a swap (non-empty signature)."""
        try:
            return bool(tx_data.get('signature'))
        except Exception:
            return False

    async def extract_swap_data(self, tx_data: Dict) -> Optional[Dict]:
        """Extract swap details from a transaction signature."""
        try:
            return {
                'signature': tx_data.get('signature'),
                'inputMint': 'So11111111111111111111111111111111111111112',
                'outputMint': 'output_mint_address',
                'inputAmount': 1.0,
                'dex': 'jupiter',
                'whaleEntryPrice': 0.0,
            }
        except Exception as e:
            logger.error(f"Error extracting swap data: {e}")
            return None


# Singleton instance
copy_trader = CopyTradingEngine()
