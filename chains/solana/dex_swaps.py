"""
DEX swap integration — Jupiter V6 (real on-chain execution)
"""
import logging
import aiohttp
import asyncio
import base64
from decimal import Decimal
from typing import Optional, Dict
from config import (
    JUPITER_API, SLIPPAGE_TOLERANCE, WSOL_MINT, SOL_MINT, SOLANA_RPC_URL,
    JUPITER_QUOTE_TIMEOUT, JUPITER_SWAP_TIMEOUT, TX_SUBMIT_TIMEOUT,
    PRIORITY_FEE_TIMEOUT, DEFAULT_PRIORITY_FEE_FLOOR,
)

logger = logging.getLogger(__name__)


class DEXSwapper:
    """Handle swaps via Jupiter V6 with real transaction signing and submission."""

    def __init__(self):
        self.jupiter_api = JUPITER_API

    # ------------------------------------------------------------------
    # Price / quote helpers
    # ------------------------------------------------------------------

    async def get_jupiter_price(self, input_mint: str, output_mint: str,
                                amount: float,
                                slippage_bps: Optional[int] = None) -> Optional[Dict]:
        """Get a price quote from Jupiter.

        amount: SOL (float) when input is WSOL/SOL, raw token units (int) otherwise.
        Returns a simplified price dict for display / PnL tracking.
        """
        try:
            raw_amount = self._to_raw(input_mint, amount)
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.jupiter_api}/quote",
                    params={
                        'inputMint':   input_mint,
                        'outputMint':  output_mint,
                        'amount':      raw_amount,
                        'slippageBps': slippage_bps if slippage_bps is not None else int(SLIPPAGE_TOLERANCE * 100),
                    },
                    timeout=aiohttp.ClientTimeout(total=JUPITER_QUOTE_TIMEOUT),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        out_raw = int(data.get('outAmount', 0))
                        # Normalise output to human units for SOL, raw for tokens
                        price = out_raw / 1e9 if output_mint == WSOL_MINT else out_raw
                        return {
                            'dex':         'jupiter',
                            'price':       price,
                            'priceImpact': float(data.get('priceImpactPct', 0)),
                            'quote':       data,   # full quote kept for swap step
                        }
                    logger.warning(f"Jupiter quote HTTP {resp.status}")
        except Exception as e:
            logger.error(f"Jupiter price error: {e}")
        return None

    async def get_best_price(self, input_mint: str, output_mint: str,
                             amount: float,
                             slippage_bps: Optional[int] = None) -> Dict:
        """Best price — Jupiter only."""
        result = await self.get_jupiter_price(input_mint, output_mint, amount, slippage_bps)
        return result or {}

    # ------------------------------------------------------------------
    # Priority fee
    # ------------------------------------------------------------------

    async def get_recent_priority_fee(self) -> int:
        """75th-percentile recent prioritisation fee in microlamports. Floor 1 000."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    SOLANA_RPC_URL,
                    json={"jsonrpc": "2.0", "id": 1,
                          "method": "getRecentPrioritizationFees", "params": []},
                    timeout=aiohttp.ClientTimeout(total=PRIORITY_FEE_TIMEOUT),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        fees = [f['prioritizationFee']
                                for f in data.get('result', [])
                                if f.get('prioritizationFee', 0) > 0]
                        if fees:
                            fees.sort()
                            idx = int(len(fees) * 0.75)
                            return max(fees[min(idx, len(fees) - 1)], DEFAULT_PRIORITY_FEE_FLOOR)
        except Exception as e:
            logger.warning(f"Priority fee fetch failed: {e}")
        return DEFAULT_PRIORITY_FEE_FLOOR

    #------------------------------------------------------------------
    # Swap execution (real on-chain)
    # 
    # ------------------------------------------------------------------

    async def execute_swap(self, input_mint: str, output_mint: str,
                           amount: float, dex: str = 'jupiter',
                           keypair=None, priority_override: int = None,
                           slippage_bps: Optional[int] = None,
                           use_private_tx: bool = False) -> Optional[Dict]:
        """Execute swap via Jupiter. keypair must be a solders Keypair to submit on-chain.
        priority_override: fixed microlamports fee; None = auto (75th percentile)."""
        if use_private_tx:
            logger.warning("Private/Jito submission requested but not implemented; using public RPC")
        return await self.execute_jupiter_swap(
            input_mint, output_mint, amount, keypair, priority_override, slippage_bps
        )

    async def execute_jupiter_swap(self, input_mint: str, output_mint: str,
                                   amount: float, keypair=None,
                                   priority_override: int = None,
                                   slippage_bps: Optional[int] = None) -> Optional[Dict]:
        """
        Full Jupiter V6 swap pipeline:
          1. GET /quote
          2. POST /swap  → serialised VersionedTransaction
          3. Sign with keypair
          4. sendTransaction → signature

        If keypair is None, returns the quote only (status='quoted') without submitting.
        """
        try:
            from solders.transaction import VersionedTransaction  # type: ignore

            raw_amount   = self._to_raw(input_mint, amount)
            priority_fee = priority_override if priority_override is not None else await self.get_recent_priority_fee()

            async with aiohttp.ClientSession() as session:

                # ── Step 1: quote ────────────────────────────────────────────
                async with session.get(
                    f"{self.jupiter_api}/quote",
                    params={
                        'inputMint':   input_mint,
                        'outputMint':  output_mint,
                        'amount':      raw_amount,
                        'slippageBps': slippage_bps if slippage_bps is not None else int(SLIPPAGE_TOLERANCE * 100),
                    },
                    timeout=aiohttp.ClientTimeout(total=JUPITER_QUOTE_TIMEOUT),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"Jupiter quote failed {resp.status}: {body[:200]}")
                        return None
                    quote = await resp.json()

                out_raw      = int(quote.get('outAmount', 0))
                price_impact = float(quote.get('priceImpactPct', 0))
                price        = out_raw / 1e9 if output_mint == WSOL_MINT else out_raw

                # Dry-run — no keypair supplied
                if keypair is None:
                    logger.debug("execute_jupiter_swap: no keypair — returning quote only")
                    return {
                        'dex':          'jupiter',
                        'status':       'quoted',
                        'inputAmount':  amount,
                        'expectedOutput': price,
                        'priceImpact':  price_impact,
                        'priorityFeeMicrolamports': priority_fee,
                    }

                # ── Step 2: build transaction ─────────────────────────────────
                async with session.post(
                    f"{self.jupiter_api}/swap",
                    json={
                        'quoteResponse':           quote,
                        'userPublicKey':           str(keypair.pubkey()),
                        'wrapAndUnwrapSol':        True,
                        'prioritizationFeeLamports': priority_fee,
                    },
                    timeout=aiohttp.ClientTimeout(total=JUPITER_SWAP_TIMEOUT),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"Jupiter swap build failed {resp.status}: {body[:200]}")
                        return None
                    swap_data = await resp.json()

                swap_tx_b64 = swap_data.get('swapTransaction')
                if not swap_tx_b64:
                    logger.error("Jupiter /swap returned no swapTransaction field")
                    return None

                # ── Step 3: deserialise → sign ────────────────────────────────
                # BUGFIX: Use tx.sign([keypair]) instead of VersionedTransaction(tx.message, [keypair])
                # The latter discards any existing partial signatures (e.g. Jupiter fee accounts).
                tx_bytes = base64.b64decode(swap_tx_b64)
                tx = VersionedTransaction.from_bytes(tx_bytes)
                tx.sign([keypair])
                signed_b64 = base64.b64encode(bytes(tx)).decode()

                # ── Step 4: submit ────────────────────────────────────────────
                async with session.post(
                    SOLANA_RPC_URL,
                    json={
                        "jsonrpc": "2.0", "id": 1,
                        "method": "sendTransaction",
                        "params": [
                            signed_b64,
                            {
                                "encoding":             "base64",
                                "skipPreflight":        False,
                                "preflightCommitment":  "processed",
                                "maxRetries":           3,
                            },
                        ],
                    },
                    timeout=aiohttp.ClientTimeout(total=TX_SUBMIT_TIMEOUT),
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"sendTransaction HTTP {resp.status}")
                        return None
                    result = await resp.json()

                if 'error' in result:
                    logger.error(f"Transaction rejected: {result['error']}")
                    return None

                signature = result.get('result', '')
                confirmed = await self._wait_for_confirmation(session, signature)
                if not confirmed:
                    logger.error(f"Swap not confirmed before timeout: {signature[:20]}...")
                    return {
                        'dex':          'jupiter',
                        'status':       'submitted',
                        'signature':    signature,
                        'inputAmount':  amount,
                        'expectedOutput': price,
                        'priceImpact':  price_impact,
                        'priorityFeeMicrolamports': priority_fee,
                    }
                fill = await self._fetch_swap_fill(session, signature)
                actual_input = fill.get('inputAmount') if fill else None
                actual_output = fill.get('outputAmount') if fill else None
                fill_price = fill.get('fillPrice') if fill else None

                logger.info(f"Swap confirmed: {signature[:20]}...  "
                            f"impact={price_impact:.2f}%  fee={priority_fee}uL")

                return {
                    'dex':          'jupiter',
                    'status':       'confirmed',
                    'signature':    signature,
                    'inputAmount':  amount,
                    'expectedOutput': price,
                    'actualInput':  actual_input if actual_input is not None else amount,
                    'actualOutput': actual_output if actual_output is not None else price,
                    'fillPrice':    fill_price if fill_price is not None else price,
                    'priceImpact':  price_impact,
                    'priorityFeeMicrolamports': priority_fee,
                }

        except ImportError:
            logger.error("solders not installed — run: pip install solders")
            return None
        except Exception as e:
            logger.error(f"execute_jupiter_swap error: {e}", exc_info=True)
            return None

    async def _wait_for_confirmation(self, session: aiohttp.ClientSession,
                                     signature: str,
                                     timeout_seconds: int = TX_SUBMIT_TIMEOUT) -> bool:
        """Poll RPC until the submitted transaction is confirmed or finalized."""
        if not signature:
            return False

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            try:
                async with session.post(
                    SOLANA_RPC_URL,
                    json={
                        "jsonrpc": "2.0", "id": 1,
                        "method": "getSignatureStatuses",
                        "params": [[signature], {"searchTransactionHistory": True}],
                    },
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        statuses = (data.get('result') or {}).get('value') or []
                        status = statuses[0] if statuses else None
                        if status:
                            if status.get('err'):
                                logger.error(f"Confirmed transaction failed: {status['err']}")
                                return False
                            confirmation = status.get('confirmationStatus')
                            if confirmation in ('confirmed', 'finalized'):
                                return True
            except Exception as e:
                logger.warning(f"Confirmation poll failed: {e}")

            await asyncio.sleep(1)

        return False

    async def _fetch_swap_fill(self, session: aiohttp.ClientSession, signature: str) -> Optional[Dict]:
        """Fetch the executed transaction and derive actual input/output amounts."""
        try:
            payload = {
                "jsonrpc": "2.0", "id": 1,
                "method": "getTransaction",
                "params": [
                    signature,
                    {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
                ]
            }
            async with session.post(SOLANA_RPC_URL, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

            tx = data.get('result') or {}
            meta = tx.get('meta') or {}
            if meta.get('err'):
                return None

            def _balance_map(balances):
                result = {}
                for bal in balances or []:
                    mint = bal.get('mint')
                    if not mint:
                        continue
                    idx = bal.get('accountIndex', mint)
                    token_amount = bal.get('uiTokenAmount', {}) or {}
                    amount = token_amount.get('uiAmount')
                    if amount is None:
                        raw = token_amount.get('amount')
                        decimals = int(token_amount.get('decimals', 0) or 0)
                        if raw is not None:
                            try:
                                amount = Decimal(str(raw)) / (Decimal(10) ** decimals)
                            except Exception:
                                amount = 0
                    result[(idx, mint)] = float(amount or 0)
                return result

            pre_map = _balance_map(meta.get('preTokenBalances'))
            post_map = _balance_map(meta.get('postTokenBalances'))
            input_mint = output_mint = None
            input_amount = output_amount = 0.0

            for key in set(pre_map) | set(post_map):
                pre = pre_map.get(key, 0.0)
                post = post_map.get(key, 0.0)
                delta = post - pre
                _, mint = key
                if delta < 0 and abs(delta) > input_amount:
                    input_mint = mint
                    input_amount = abs(delta)
                elif delta > 0 and delta > output_amount:
                    output_mint = mint
                    output_amount = delta

            if (not input_mint or not output_mint) and meta.get('preBalances') and meta.get('postBalances'):
                sol_delta = (meta['postBalances'][0] - meta['preBalances'][0]) / 1e9
                if sol_delta < -1e-6 and not input_mint:
                    input_mint = WSOL_MINT
                    input_amount = abs(sol_delta)
                elif sol_delta > 1e-6 and not output_mint:
                    output_mint = WSOL_MINT
                    output_amount = sol_delta

            if not input_mint or not output_mint:
                return None

            fill_price = (input_amount / output_amount) if output_amount > 0 else 0.0
            return {
                'inputMint': input_mint,
                'outputMint': output_mint,
                'inputAmount': input_amount,
                'outputAmount': output_amount,
                'fillPrice': fill_price,
            }
        except Exception as e:
            logger.debug(f"Fill fetch failed for {signature[:10]}: {e}")
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_raw(mint: str, amount: float) -> int:
        """Convert human amount to raw on-chain units.

        SOL/WSOL:  amount is in SOL  → multiply by 1e9 (lamports)
        Any token: amount is already in the token's raw units → cast to int
        """
        if mint in (WSOL_MINT, SOL_MINT):
            return int(amount * 1e9)
        return int(amount)


swapper = DEXSwapper()
