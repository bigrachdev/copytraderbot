"""
Vanity wallet generator for Solana - create custom wallets with specific prefixes
"""
import logging
import asyncio
import base58
from solders.keypair import Keypair
from typing import Tuple, Optional
import concurrent.futures

logger = logging.getLogger(__name__)


class VanityWalletGenerator:
    """Generate vanity Solana wallets with custom prefixes"""
    
    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        self.found = False
    
    def generate_keypair(self) -> Tuple[str, str]:
        """Generate single keypair"""
        keypair = Keypair()
        public_key = str(keypair.pubkey())
        secret_key = base58.b58encode(bytes(keypair)).decode()
        return public_key, secret_key
    
    def check_prefix(
        self,
        address: str,
        prefix: str,
        match_position: str = "start",
        case_sensitive: bool = True,
    ) -> bool:
        """Check if `address` matches `prefix` at `match_position`.

        If `case_sensitive` is False, comparison is done case-insensitively.
        """
        if not case_sensitive:
            address = address.lower()
            prefix = prefix.lower()

        if match_position == "start":
            return address.startswith(prefix)
        if match_position == "end":
            return address.endswith(prefix)

        raise ValueError("match_position must be 'start' or 'end'")
    
    def generate_single_vanity(
        self,
        target_prefix: str,
        match_position: str = "start",
        case_sensitive: bool = True,
        iterations: int = 1000000,
    ) -> Optional[Tuple[str, str]]:
        """Generate single vanity wallet in worker thread"""
        for _ in range(iterations):
            if self.found:
                return None
            
            public_key, secret_key = self.generate_keypair()
            
            if self.check_prefix(
                public_key,
                target_prefix,
                match_position=match_position,
                case_sensitive=case_sensitive,
            ):
                self.found = True
                logger.info(f"✨ Found vanity wallet: {public_key}")
                return public_key, secret_key
        
        return None
    
    async def generate_vanity_wallet(
        self,
        prefix: str,
        difficulty: int = 3,
        match_position: str = "start",
        case_sensitive: bool = True,
    ) -> Tuple[str, str, int]:
        """
        Generate vanity wallet with specific prefix
        
        difficulty: number of matching characters (3-6 recommended)
        When `match_position="start"`, it matches leading characters.
        When `match_position="end"`, it matches trailing characters.
        - 3: ~100k attempts (~1-2 seconds)
        - 4: ~2.5M attempts (~30-60 seconds)
        - 5: ~58M attempts (~10-15 minutes)
        - 6: ~1.3B attempts (~many hours)
        """
        if not prefix or len(prefix) > 6:
            raise ValueError("Prefix must be 1-6 characters")
        if match_position not in ("start", "end"):
            raise ValueError("match_position must be 'start' or 'end'")
        if case_sensitive not in (True, False):
            raise ValueError("case_sensitive must be boolean")
        
        # Validate prefix (Solana addresses are base58)
        valid_chars = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        if not all(c in valid_chars for c in prefix):
            raise ValueError(f"Invalid prefix. Use only base58 characters: {valid_chars}")
        
        logger.info(
            f"🎲 Generating vanity wallet with vanity '{prefix}' at {match_position}..."
        )
        logger.info(f"   Case sensitive: {case_sensitive}")
        logger.info(f"   Difficulty: {difficulty} (estimated ~{10**(difficulty-2):.0e} attempts)")
        
        self.found = False
        loop = asyncio.get_running_loop()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            tasks = []
            for _ in range(self.max_workers):
                task = loop.run_in_executor(
                    executor,
                    self.generate_single_vanity,
                    prefix,
                    match_position,
                    case_sensitive,
                    10000000  # iterations per worker
                )
                tasks.append(task)
            
            # Wait for first result
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            
            # Cancel remaining tasks
            for task in pending:
                task.cancel()
            
            # Get result
            for task in done:
                result = await task
                if result:
                    return result[0], result[1], difficulty
        
        raise TimeoutError("Could not generate vanity wallet within timeout")
    
    async def generate_batch_vanity(self, prefixes: list, 
                                   difficulty: int = 3) -> dict:
        """Generate multiple vanity wallets"""
        results = {}
        
        for prefix in prefixes:
            try:
                public_key, secret_key, diff = await self.generate_vanity_wallet(prefix, difficulty)
                results[prefix] = {
                    'address': public_key,
                    'private_key': secret_key,
                    'difficulty': diff
                }
                logger.info(f"✨ Generated wallet for prefix '{prefix}'")
            except Exception as e:
                logger.error(f"❌ Failed to generate wallet for prefix '{prefix}': {e}")
                results[prefix] = {'error': str(e)}
        
        return results


# Common vanity patterns for Solana
COMMON_PATTERNS = {
    'elite': ['ELITE', 'FLUX', 'ALPHA', 'BETA', 'MOON'],
    'fun': ['DOGE', 'PUMP', 'MOON', 'PEPE', 'CHAD'],
    'professional': ['SOL', 'TRD', 'DFI', 'DEX', 'NFT'],
}


vanity_generator = VanityWalletGenerator()
