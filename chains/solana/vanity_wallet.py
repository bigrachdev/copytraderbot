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
    
    def check_prefix(self, address: str, prefix: str) -> bool:
        """Check if address matches prefix"""
        return address.startswith(prefix)
    
    def generate_single_vanity(self, target_prefix: str, iterations: int = 1000000) -> Optional[Tuple[str, str]]:
        """Generate single vanity wallet in worker thread"""
        for _ in range(iterations):
            if self.found:
                return None
            
            public_key, secret_key = self.generate_keypair()
            
            if self.check_prefix(public_key, target_prefix):
                self.found = True
                logger.info(f"✨ Found vanity wallet: {public_key}")
                return public_key, secret_key
        
        return None
    
    async def generate_vanity_wallet(self, prefix: str, 
                                    difficulty: int = 3) -> Tuple[str, str, int]:
        """
        Generate vanity wallet with specific prefix
        
        difficulty: number of matching leading characters (3-6 recommended)
        - 3: ~100k attempts (~1-2 seconds)
        - 4: ~2.5M attempts (~30-60 seconds)
        - 5: ~58M attempts (~10-15 minutes)
        - 6: ~1.3B attempts (~many hours)
        """
        if not prefix or len(prefix) > 6:
            raise ValueError("Prefix must be 1-6 characters")
        
        # Validate prefix (Solana addresses are base58)
        valid_chars = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        if not all(c in valid_chars for c in prefix):
            raise ValueError(f"Invalid prefix. Use only base58 characters: {valid_chars}")
        
        logger.info(f"🎲 Generating vanity wallet with prefix '{prefix}'...")
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
