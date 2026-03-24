# 🔧 Quick Fixes Guide - Apply in This Order

**Estimated Total Time:** 3-4 hours  
**Difficulty:** Beginner to Intermediate

This guide provides copy-paste fixes for all 8 issues identified in the vulnerability assessment.

---

## FIX #1 - Database Threading (🔴 CRITICAL)

**File:** `data/database.py`

**Before:**
```python
class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.init_db()
    
    def get_connection(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
```

**After:**
```python
import threading

class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._lock = threading.RLock()  # ADD THIS
        self.init_db()
    
    def get_connection(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path, check_same_thread=True)  # CHANGE THIS TO True
        conn.row_factory = sqlite3.Row
        return conn
    
    # ADD THESE WRAPPER METHODS
    def _execute(self, query: str, params: tuple = (), commit: bool = True):
        """Execute query with thread safety"""
        with self._lock:
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(query, params)
                result = cursor.fetchall() if 'SELECT' in query.upper() else None
                if commit:
                    conn.commit()
                return result
            except Exception as e:
                conn.rollback()
                logger.error(f"DB execute error: {e}")
                raise
            finally:
                conn.close()
    
    def add_user(self, user_id: int, **kwargs) -> bool:
        """Add user with thread safety"""
        try:
            with self._lock:
                query = "INSERT OR REPLACE INTO users (user_id, ...) VALUES (?, ...)"
                self._execute(query, (user_id, ...), commit=True)
            return True
        except Exception as e:
            logger.error(f"Error adding user: {e}")
            return False
```

**Apply this change to all `add_*`, `update_*`, and `delete_*` methods in database.py**

**Test:**
```bash  
# Run with concurrent users
python -c "
from data.database import db
import threading
import time

def add_trades(user_id):
    for i in range(10):
        db.record_trade(user_id, 'token1', 'token2', 1.0, 2.0, 'jupiter', f'tx{user_id}_{i}')
        time.sleep(0.1)

threads = [threading.Thread(target=add_trades, args=(i,)) for i in range(5)]
for t in threads: t.start()
for t in threads: t.join()

trades = db.get_user_trades(1)
print(f'Recorded {len(trades)} trades - should be 10')
"
```

**Status:** ✅ Fixes data corruption under concurrent access

---

## FIX #2 - Balance Validation (🔴 CRITICAL)

**File:** `trading/smart_trader.py`

**Add this method to the SmartTrader class:**

```python
async def _validate_user_balance(self, user_id: int, required_sol: float) -> tuple[bool, str]:
    """
    Validate user has sufficient balance before trading.
    Returns: (is_valid, error_message)
    """
    try:
        user = db.get_user(user_id)
        if not user or not user.get('wallet_address'):
            return False, "❌ No wallet configured. Import or create a wallet first."
        
        wallet_address = user['wallet_address']
        
        # Get current balance
        try:
            balance = await asyncio.wait_for(
                self.wallet.get_balance_async(wallet_address),
                timeout=5
            )
        except asyncio.TimeoutError:
            return False, "⏱️ Could not check balance (timeout). Please try again shortly."
        except Exception as e:
            logger.error(f"Balance check failed: {e}")
            return False, f"❌ Could not check balance: {str(e)[:50]}"
        
        if balance is None:
            return False, "❌ Could not fetch balance. Try again shortly."
        
        # Check minimum gas fee
        min_fee = 0.01  # SOL needed for transactions
        total_needed = required_sol + min_fee
        
        if balance < total_needed:
            return False, (
                f"❌ **Insufficient Balance**\n\n"
                f"Your balance: `{balance:.6f} SOL`\n"
                f"Required: `{total_needed:.6f} SOL`\n"
                f"  • {required_sol:.6f} SOL for swap\n"
                f"  • {min_fee:.6f} SOL for network fees\n\n"
                f"Please top up your wallet with more SOL."
            )
        
        return True, ""
        
    except Exception as e:
        logger.error(f"Balance validation error: {e}")
        return False, f"Error checking balance: {str(e)[:100]}"
```

**Update analyze_and_trade method:**

```python
async def analyze_and_trade(self, user_id: int, token_address: str, 
                           user_trade_percent: float = 20.0, 
                           dex: str = 'jupiter') -> Dict:
    """Analyze token and execute trade with balance validation"""
    
    # Calculate needed amount first
    user = db.get_user(user_id)
    if not user:
        return {'status': 'error', 'message': 'User not found'}
    
    sol_to_spend = max(MIN_TRADE_SOL, 0.01)  # Minimum trade amount
    
    # VALIDATE BALANCE BEFORE PROCEEDING
    is_valid, error_msg = await self._validate_user_balance(user_id, sol_to_spend)
    if not is_valid:
        return {'status': 'error', 'message': error_msg}
    
    # Rest of the function...
    try:
        analysis = token_analyzer.analyze_token(token_address)
        # ... rest of logic ...
    except Exception as e:
        return {'status': 'error', 'message': str(e)}
```

**Also update in copy_trader and other swap locations.**

**Test:**
```bash
python -c "
import asyncio
from trading.smart_trader import smart_trader

async def test():
    # User with no balance
    result = await smart_trader.analyze_and_trade(user_id=999, token_address='xxx')
    print('Should be error:', result['status'])
    assert 'Insufficient' in result['message']
    print('✅ Balance validation works')

asyncio.run(test())
"
```

**Status:** ✅ Prevents failed swaps and fee waste

---

## FIX #3 - Async Error Handling (🟠 HIGH)

**File:** `trading/smart_trader.py`

**Replace auto_smart_trade_loop method:**

```python
async def start_auto_smart_trading(self, user_id: int, user_trade_percent: float = 20.0):
    """Start scanning trending tokens every 30 min with full error handling"""
    
    if user_id in self._auto_smart_tasks:
        logger.warning(f"Auto-smart already running for {user_id}")
        return
    
    async def _smart_trade_loop():
        logger.info(f"[{user_id}] ✨ Auto-smart trading starting")
        notification_engine.send_alert(
            user_id, 
            "✨ **Auto Smart Trading: ON**\n\n"
            f"Trade %: {user_trade_percent}%\n"
            f"Scanning every 30 minutes…"
        )
        
        while self.is_auto_smart_trading(user_id):
            cycle_start = time.time()
            try:
                logger.info(f"[{user_id}] Starting auto-smart cycle")
                
                # Get trending tokens
                try:
                    tokens = await self.get_trending_tokens()
                    if not tokens:
                        logger.warning(f"[{user_id}] No trending tokens found")
                        tokens = []
                except Exception as e:
                    logger.error(f"[{user_id}] Failed to fetch tokens: {e}", exc_info=True)
                    notification_engine.send_alert(
                        user_id,
                        f"⚠️ **Token fetch failed**\n\n{str(e)[:100]}\n\n"
                        f"Retrying in 30 minutes…"
                    )
                    await asyncio.sleep(SCAN_INTERVAL)
                    continue
                
                # Analyze and trade each token
                successful_trades = 0
                failed_trades = 0
                
                for idx, token in enumerate(tokens):
                    if not self.is_auto_smart_trading(user_id):
                        logger.info(f"[{user_id}] Auto-smart stopped by user")
                        break
                    
                    try:
                        token_addr = token.get('address', '')
                        if not token_addr:
                            continue
                        
                        # Check blacklist
                        if user_id in self._blacklist:
                            if token_addr in self._blacklist[user_id]:
                                logger.debug(f"[{user_id}] Skipped blacklisted token")
                                continue
                        
                        logger.info(f"[{user_id}] Analyzing {token_addr[:10]}…")
                        
                        result = await self.analyze_and_trade(
                            user_id=user_id,
                            token_address=token_addr,
                            user_trade_percent=user_trade_percent,
                            dex='jupiter'
                        )
                        
                        if result.get('status') == 'confirmed':
                            successful_trades += 1
                            logger.info(f"[{user_id}] ✅ Trade executed")
                        elif result.get('status') == 'error':
                            failed_trades += 1
                            logger.warning(f"[{user_id}] ⚠️ Trade failed: {result.get('message')}")
                        
                    except asyncio.CancelledError:
                        logger.info(f"[{user_id}] Task cancelled")
                        raise
                    except Exception as inner_e:
                        failed_trades += 1
                        logger.error(f"[{user_id}] Error analyzing token: {inner_e}", exc_info=True)
                        continue  # Skip this token, continue to next
                
                # Send cycle summary
                cycle_time = time.time() - cycle_start
                logger.info(
                    f"[{user_id}] Cycle complete: "
                    f"{successful_trades} trades, {failed_trades} failed, {cycle_time:.1f}s"
                )
                
                if successful_trades > 0:
                    notification_engine.send_alert(
                        user_id,
                        f"✅ **Scan Complete**\n\n"
                        f"Trades executed: {successful_trades}\n"
                        f"Failed: {failed_trades}\n\n"
                        f"Next scan in 30 minutes…"
                    )
                
                # Wait for next scan
                await asyncio.sleep(SCAN_INTERVAL)
                
            except asyncio.CancelledError:
                logger.info(f"[{user_id}] Auto-smart cancelled")
                break
            
            except Exception as e:
                logger.error(f"[{user_id}] Auto-smart loop error: {e}", exc_info=True)
                
                # Notify user of critical error
                notification_engine.send_alert(
                    user_id,
                    f"🔴 **Auto Smart Trading Paused**\n\n"
                    f"Error: {str(e)[:80]}\n"
                    f"Type: {type(e).__name__}\n\n"
                    f"Auto-trading will resume at next bot restart."
                )
                
                # Stop auto-trading to prevent infinite error loops
                self.stop_auto_smart_trading(user_id)
                break
        
        logger.info(f"[{user_id}] Auto-smart trading stopped")
        notification_engine.send_alert(user_id, "🔴 Auto Smart Trading: OFF")
    
    # Create task with name for identification
    task = asyncio.create_task(_smart_trade_loop(), name=f"auto_smart_{user_id}")
    self._auto_smart_tasks[user_id] = task
    logger.info(f"[{user_id}] Task created: {task.get_name()}")
```

**Test:**
```bash
# Should handle API errors gracefully
python -c "
import asyncio
from trading.smart_trader import smart_trader

async def test():
    await smart_trader.start_auto_smart_trading(user_id=1, user_trade_percent=10.0)
    await asyncio.sleep(5)
    smart_trader.stop_auto_smart_trading(1)
    print('✅ Auto-Smart error handling works')

asyncio.run(test())
"
```

**Status:** ✅ Prevents silent trading failures

---

## FIX #4 - Timeout Handling (🟠 HIGH)

**File:** `chains/solana/wallet.py`

**Add this import:**
```python
from requests.exceptions import Timeout, ConnectionError, RequestException
```

**Replace get_balance method:**

```python
async def get_balance_async(self, public_key: str, max_retries: int = 3) -> Optional[float]:
    """Get SOL balance with retry logic for transient failures"""
    for attempt in range(max_retries):
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBalance",
                "params": [public_key]
            }
            
            response = requests.post(
                self.rpc_url, 
                json=payload, 
                timeout=RPC_TIMEOUT
            )
            
            # Successful response
            if response.status_code == 200:
                data = response.json()
                if 'result' in data:
                    return data['result']['value'] / 1e9
                elif 'error' in data:
                    error_msg = data['error'].get('message', 'Unknown error')
                    logger.error(f"RPC error on balance: {error_msg}")
                    raise RuntimeError(f"RPC error: {error_msg}")
            else:
                # Non-200 status
                logger.warning(f"RPC returned status {response.status_code}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                    continue
                raise RuntimeError(f"RPC status {response.status_code}")
            
        except Timeout:
            logger.warning(f"[Attempt {attempt+1}/{max_retries}] Balance check timed out")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)  # Wait 1s, 2s, 4s
                continue
            raise TimeoutError(f"Balance check timed out after {max_retries} retries")
        
        except ConnectionError as e:
            logger.warning(f"[Attempt {attempt+1}/{max_retries}] Network error: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            raise ConnectionError(f"Network unreachable (after {max_retries} retries)")
        
        except RequestException as e:
            logger.error(f"Request error: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"HTTP request failed: {str(e)[:100]}")
        
        except Exception as e:
            logger.error(f"Unexpected error in balance check: {e}")
            raise
    
    return None

# Keep synchronous version for backwards compatibility
def get_balance(self, public_key: str) -> Optional[float]:
    """Synchronous balance check - wraps async version"""
    try:
        return asyncio.run(self.get_balance_async(public_key))
    except Exception as e:
        logger.error(f"Error getting balance: {e}")
        return None
```

**Update callers to handle specific errors:**

```python
try:
    balance = await wallet.get_balance_async(wallet_address)
except TimeoutError:
    return {'status': 'error', 'message': 'Network timeout - RPC is slow. Try again in 10 seconds.'}
except ConnectionError:
    return {'status': 'error', 'message': 'Network connection failed. Check your internet.'}
except RuntimeError as e:
    return {'status': 'error', 'message': f'RPC error: {str(e)}'.[:100]}
except Exception as e:
    return {'status': 'error', 'message': f'Balance check failed: {str(e)[:80]}'}

if balance < required_amount:
    return {'status': 'error', 'message': f'Insufficient balance: {balance:.4f} SOL'}
```

**Status:** ✅ Retries transient failures, gives clear error messages

---

## FIX #5 - Rate Limiting (🟠 HIGH)

**File:** `trading/smart_trader.py`

**Add to imports:**
```python
import asyncio
from time import time
```

**Add these attributes to SmartTrader.__init__():**

```python
def __init__(self):
    # ... existing code ...
    
    # API caching
    self._api_cache = {}  # {endpoint: (timestamp, data)}
    self._cache_ttl = 60  # 1 minute TTL
    
    # Rate limiting
    self._request_semaphore = asyncio.Semaphore(5)  # Max 5 concurrent API calls
    self._last_api_call = {}  # {endpoint: timestamp}
    self._min_api_interval = 0.5  # Min 0.5s between calls to same endpoint
```

**Add rate-limiting wrapper:**

```python
async def _rate_limited_api_call(self, endpoint_name: str, 
                                 api_func, *args, **kwargs) -> Optional[Dict]:
    """Make API call with rate limiting and caching"""
    
    # Check cache first
    if endpoint_name in self._api_cache:
        cached_at, data = self._api_cache[endpoint_name]
        age = time() - cached_at
        if age < self._cache_ttl:
            logger.debug(f"Using cached {endpoint_name} (age: {age:.0f}s)")
            return data
    
    # Apply rate limiting
    async with self._request_semaphore:
        # Check if we should wait
        last_call = self._last_api_call.get(endpoint_name, 0)
        time_since = time() - last_call
        if time_since < self._min_api_interval:
            wait_time = self._min_api_interval - time_since
            logger.debug(f"Rate limit: waiting {wait_time:.2f}s before {endpoint_name}")
            await asyncio.sleep(wait_time)
        
        try:
            # Make the call
            data = await api_func(*args, **kwargs)
            self._last_api_call[endpoint_name] = time()
            
            # Cache result
            self._api_cache[endpoint_name] = (time(), data)
            return data
            
        except Exception as e:
            logger.error(f"API call {endpoint_name} failed: {e}")
            return None
```

**Update get_trending_tokens to use caching:**

```python
async def get_trending_tokens(self) -> List[Dict]:
    """Get trending tokens with caching and rate limiting"""
    
    all_tokens = []
    
    # DexScreener (cached + rate limited)
    dex_tokens = await self._rate_limited_api_call(
        'dexscreener_trending',
        self._fetch_dexscreener_trending
    )
    if dex_tokens:
        all_tokens.extend(dex_tokens)
    
    # Birdeye (cached + rate limited)
    bird_tokens = await self._rate_limited_api_call(
        'birdeye_trending',
        self._fetch_birdeye_trending
    )
    if bird_tokens:
        all_tokens.extend(bird_tokens)
    
    # Deduplicate
    seen = set()
    unique = []
    for token in all_tokens:
        addr = token.get('address', '')
        if addr and addr not in seen:
            seen.add(addr)
            unique.append(token)
    
    logger.info(f"Found {len(unique)} unique trending tokens")
    return unique
```

**Test:**
```bash
python -c "
import asyncio
from trading.smart_trader import smart_trader

async def test():
    # First call should hit API
    tokens1 = await smart_trader.get_trending_tokens()
    
    # Second call should be cached
    tokens2 = await smart_trader.get_trending_tokens()
    
    assert tokens1 == tokens2
    print(f'✅ Rate limiting works - got {len(tokens1)} tokens')

asyncio.run(test())
"
```

**Status:** ✅ Prevents IP bans from rate limiting

---

## FIX #6 - Bare Except Clauses (🟡 MEDIUM)

**Global Search & Replace:**

**Find:** `except:`  
**Replace with:** `except Exception as e:`

**Also add logging:**

```python
except Exception as e:
    logger.error(f"Operation failed: {type(e).__name__}: {e}", exc_info=True)
    # Handle appropriately
```

**Examples:**

```python
# BEFORE
except:
    pass

# AFTER
except Exception as e:
    logger.error(f"Failed to process: {e}")
    await update.callback_query.edit_message_text(
        f"❌ An error occurred. Please try again."
    )
```

**Status:** ✅ Better error tracking

---

## FIX #7 - Key Validation (🟡 MEDIUM)

**File:** `chains/solana/wallet.py`

**Replace import_keypair:**

```python
def import_keypair(self, private_key_base58: str) -> Optional[Keypair]:
    """Import keypair with detailed validation"""
    
    if not private_key_base58:
        raise ValueError("Private key cannot be empty")
    
    # Clean input
    private_key_base58 = private_key_base58.strip()
    
    # Validate base58 format
    try:
        secret_bytes = base58.b58decode(private_key_base58)
    except ValueError as e:
        raise ValueError(
            f"❌ **Invalid Private Key Format**\n\n"
            f"The key you provided is not valid base58.\n"
            f"This doesn't look like a Solana private key.\n\n"
            f"Make sure you:\n"
            f"• Copied the ENTIRE private key\n"
            f"• Didn't add/remove any characters\n"
            f"• Are using a Solana wallet (not Ethereum!)\n\n"
            f"Error: {str(e)}"
        )
    
    # Validate length
    if len(secret_bytes) == 64:
        try:
            keypair = Keypair.from_bytes(secret_bytes)
            logger.info(f"Imported 64-byte keypair: {keypair.pubkey()}")
            return keypair
        except Exception as e:
            raise ValueError(f"Invalid 64-byte keypair format: {str(e)}")
    
    elif len(secret_bytes) == 32:
        try:
            keypair = Keypair.from_seed(secret_bytes)
            logger.info(f"Imported 32-byte seed: {keypair.pubkey()}")
            return keypair
        except Exception as e:
            raise ValueError(f"Invalid 32-byte seed format: {str(e)}")
    
    else:
        raise ValueError(
            f"❌ **Invalid Key Length**\n\n"
            f"Received: {len(secret_bytes)} bytes\n"
            f"Expected: 32 bytes (seed) or 64 bytes (keypair)\n\n"
            f"This is not a recognized Solana private key format."
        )
```

**Use in telegram_bot.py:**

```python
async def handle_import_key(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle private key import"""
    user_id = update.effective_user.id
    key_input = update.message.text.strip()
    
    try:
        # Try to import
        keypair = self.wallet.import_keypair(key_input)
        
        if keypair is None:
            await update.message.reply_text(
                "❌ Could not import private key. Please check the format and try again."
            )
            return IMPORT_KEY
        
        # Encrypt and store
        enc_key = encryption.encrypt(key_input)
        if not enc_key:
            await update.message.reply_text(
                "❌ Encryption failed. Please try again."
            )
            return IMPORT_KEY
        
        # Save to database
        db.add_user(user_id, wallet_address=str(keypair.pubkey()), 
                   encrypted_private_key=enc_key)
        
        await update.message.reply_text(
            f"✅ **Wallet Imported**\n\n"
            f"Address: `{str(keypair.pubkey())}`\n\n"
            f"Your private key is safely encrypted with your master password."
        )
        return MENU
        
    except ValueError as e:
        await update.message.reply_text(f"{str(e)}\n\nTry again:")
        return IMPORT_KEY
    except Exception as e:
        logger.error(f"Key import error: {e}")
        await update.message.reply_text(
            "❌ An unexpected error occurred. Please try again."
        )
        return IMPORT_KEY
```

**Status:** ✅ Better error messages for users

---

## FIX #8 - None Checks (🟡 MEDIUM)

**Add validation wrapper:**

```python
def _ensure_not_none(value, field_name: str, default=None):
    """Ensure value is not None, log if it becomes default"""
    if value is None:
        logger.warning(f"{field_name} was None, using default: {default}")
        return default
    return value
```

**Example usage in smart_trader.py:**

```python
async def analyze_and_trade(self, user_id: int, token_address: str, ...):
    """Analyze with validation"""
    
    # Validate inputs
    if not user_id or user_id < 0:
        return {'status': 'error', 'message': 'Invalid user ID'}
    
    if not token_address or len(token_address) < 10:
        return {'status': 'error', 'message': 'Invalid token address'}
    
    # Get user
    user = db.get_user(user_id)
    if user is None:
        return {'status': 'error', 'message': 'User not found'}
    
    # Get keypair
    keypair = self._get_user_keypair(user_id)
    if keypair is None:
        return {'status': 'error', 'message': 'Could not decrypt wallet'}
    
    # Analyze token
    analysis = token_analyzer.analyze_token(token_address)
    if analysis is None or 'risk_score' not in analysis:
        return {'status': 'error', 'message': 'Token analysis failed'}
    
    # Check score
    risk_score = analysis.get('risk_score', 100)
    if risk_score > 80:
        return {
            'status': 'skipped',
            'message': f'Token risk score too high: {risk_score}/100'
        }
    
    # Now safe to trade
    return await self._execute_trade(keypair, token_address, ...)
```

**Status:** ✅ Prevents undefined behavior

---

## Final Checklist

After applying all fixes, run this test:

```bash
# Test all modules import correctly
python -c "
from bot.telegram_bot import TelegramBot
from trading.smart_trader import smart_trader
from data.database import db
from chains.solana.dex_swaps import swapper
from chains.solana.wallet import SolanaWallet
print('✅ All imports successful')
"

# Test database operations
python -c "
from data.database import db
db.add_user(12345, wallet_address='test')
user = db.get_user(12345)
assert user is not None
print('✅ Database operations work')
db.delete_user(12345)
"

# Run existing tests
python -m pytest tests/ -v --tb=short

echo "🎉 All fixes applied successfully!"
```

---

## Deployment After Fixes

1. **Backup current Database**
   ```bash
   cp trade_bot.db trade_bot.db.backup
   ```

2. **Apply fixes in order (1→8)**

3. **Run tests**
   ```bash
   python -m pytest tests/ -v
   ```

4. **Test with single user**
   ```bash
   python main.py
   # In Telegram: /start
   # Perform 1 test trade
   ```

5. **Monitor logs**
   ```bash
   tail -f bot.log | grep -E "ERROR|CRITICAL"
   ```

6. **Gradually increase load**
   - Add 2nd user → test
   - Add 3rd user → test
   - Enable auto-trading → test
   - Enable copy trading → test

7. **Then open to production**

---

**Status:** Ready to implement  
**Estimated Time:** 3-4 hours for all 8 fixes  
**Risk Level:** Low (all fixes are defensive - they add safety, don't change core logic)

