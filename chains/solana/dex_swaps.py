"""
DEX swap integration — Jupiter V6 (real on-chain execution)
"""
import logging
import aiohttp
import asyncio
import base64
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
                                amount: float) -> Optional[Dict]:
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
                        'slippageBps': int(SLIPPAGE_TOLERANCE * 100),
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
                             amount: float) -> Dict:
        """Best price — Jupiter only."""
        result = await self.get_jupiter_price(input_mint, output_mint, amount)
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
                           keypair=None, priority_override: int = None) -> Optional[Dict]:
        """Execute swap via Jupiter. keypair must be a solders Keypair to submit on-chain.
        priority_override: fixed microlamports fee; None = auto (75th percentile)."""
        return await self.execute_jupiter_swap(input_mint, output_mint, amount, keypair, priority_override)

    async def execute_jupiter_swap(self, input_mint: str, output_mint: str,
                                   amount: float, keypair=None,
                                   priority_override: int = None) -> Optional[Dict]:
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
                        'slippageBps': int(SLIPPAGE_TOLERANCE * 100),
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
                tx_bytes  = base64.b64decode(swap_tx_b64)
                tx        = VersionedTransaction.from_bytes(tx_bytes)
                signed_tx = VersionedTransaction(tx.message, [keypair])
                signed_b64 = base64.b64encode(bytes(signed_tx)).decode()

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
                logger.info(f"✅ Swap submitted: {signature[:20]}…  "
                            f"impact={price_impact:.2f}%  fee={priority_fee}μL")

                return {
                    'dex':          'jupiter',
                    'status':       'confirmed',
                    'signature':    signature,
                    'inputAmount':  amount,
                    'expectedOutput': price,
                    'priceImpact':  price_impact,
                    'priorityFeeMicrolamports': priority_fee,
                }

        except ImportError:
            logger.error("solders not installed — run: pip install solders")
            return None
        except Exception as e:
            logger.error(f"execute_jupiter_swap error: {e}", exc_info=True)
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
