"""
Configuration and constants for the DEX Copy Trading Bot
All tuneable values live here — override any of them via .env
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN    = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID   = os.getenv('TELEGRAM_CHANNEL_ID')

# Fail fast if required env vars are missing
_REQUIRED_ENV = ['TELEGRAM_BOT_TOKEN', 'ENCRYPTION_MASTER_PASSWORD']
_missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
if _missing:
    raise EnvironmentError(
        f"Missing required environment variable(s): {', '.join(_missing)}. "
        f"Check your .env file."
    )

# ── Solana RPC ────────────────────────────────────────────────────────────────
SOLANA_RPC_URL = os.getenv('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')
SOLANA_WSS_URL = os.getenv('SOLANA_WSS_URL', 'wss://api.mainnet-beta.solana.com')

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = os.getenv('DB_PATH', 'trade_bot.db')  # Legacy SQLite path
DATABASE_URL = os.getenv('DATABASE_URL')  # Neon PostgreSQL connection string

# ── Token addresses ───────────────────────────────────────────────────────────
SOL_MINT  = os.getenv('SOL_MINT',  '11111111111111111111111111111111')
WSOL_MINT = os.getenv('WSOL_MINT', 'So11111111111111111111111111111111111111112')

# ── API Keys ──────────────────────────────────────────────────────────────────
BIRDEYE_API_KEY = os.getenv('BIRDEYE_API_KEY', '')

# ── DEX / Discovery API endpoints ────────────────────────────────────────────
JUPITER_API          = os.getenv('JUPITER_API',          'https://quote-api.jup.ag/v6')
BIRDEYE_API_URL      = os.getenv('BIRDEYE_API_URL',      'https://public-api.birdeye.so')
SOLSCAN_API_URL      = os.getenv('SOLSCAN_API_URL',      'https://api.solscan.io')
DEXSCREENER_API_URL  = os.getenv('DEXSCREENER_API_URL',  'https://api.dexscreener.com')

DEXSCREENER_BOOSTED_URL = os.getenv(
    'DEXSCREENER_BOOSTED_URL',
    'https://api.dexscreener.com/token-boosts/top/v1',
)
DEXSCREENER_SEARCH_URL  = os.getenv(
    'DEXSCREENER_SEARCH_URL',
    'https://api.dexscreener.com/latest/dex/search',
)
DEXSCREENER_NEW_URL     = os.getenv(
    'DEXSCREENER_NEW_URL',
    'https://api.dexscreener.com/token-profiles/latest/v1',
)
BIRDEYE_TRENDING_URL    = os.getenv(
    'BIRDEYE_TRENDING_URL',
    'https://public-api.birdeye.so/defi/token_trending',
)
BIRDEYE_NEW_TOKENS_URL  = os.getenv(
    'BIRDEYE_NEW_TOKENS_URL',
    'https://public-api.birdeye.so/defi/v3/token/new-listing',
)
JITO_API_URL            = os.getenv(
    'JITO_API_URL',
    'https://api.jito.wtf/api/v1',
)

# ── Request timeouts (seconds) ────────────────────────────────────────────────
RPC_TIMEOUT             = int(os.getenv('RPC_TIMEOUT',              '10'))
JUPITER_QUOTE_TIMEOUT   = int(os.getenv('JUPITER_QUOTE_TIMEOUT',    '10'))
JUPITER_SWAP_TIMEOUT    = int(os.getenv('JUPITER_SWAP_TIMEOUT',     '15'))
TX_SUBMIT_TIMEOUT       = int(os.getenv('TX_SUBMIT_TIMEOUT',        '30'))
PRIORITY_FEE_TIMEOUT    = int(os.getenv('PRIORITY_FEE_TIMEOUT',     '5'))

# ── Copy-trading — polling / monitor intervals ────────────────────────────────
COPY_TRADE_CHECK_INTERVAL = int(os.getenv('COPY_TRADE_CHECK_INTERVAL', '10'))
WALLET_MONITOR_INTERVAL   = int(os.getenv('WALLET_MONITOR_INTERVAL',   '5'))

# ── Swap settings ─────────────────────────────────────────────────────────────
SLIPPAGE_TOLERANCE   = float(os.getenv('SLIPPAGE_TOLERANCE',   '2.0'))   # %
MAX_SLIPPAGE         = float(os.getenv('MAX_SLIPPAGE',         '5.0'))   # %
MIN_TRADE_AMOUNT     = float(os.getenv('MIN_TRADE_AMOUNT',     '0.01'))  # SOL
DEFAULT_COPY_SCALE   = float(os.getenv('DEFAULT_COPY_SCALE',   '1.0'))

# Priority fee floor when live data is unavailable (microlamports)
DEFAULT_PRIORITY_FEE_FLOOR = int(os.getenv('DEFAULT_PRIORITY_FEE_FLOOR', '5000'))

# ── Copy-trader — whale qualification ────────────────────────────────────────
WHALE_MIN_TRADES     = int(os.getenv('WHALE_MIN_TRADES',     '5'))
WHALE_MIN_WIN_RATE   = float(os.getenv('WHALE_MIN_WIN_RATE', '0.40'))
WHALE_MIN_AVG_PROFIT = float(os.getenv('WHALE_MIN_AVG_PROFIT', '-10.0'))

# ── Copy-trader — signal / position defaults ──────────────────────────────────
COPY_SIGNAL_WINDOW_SECONDS  = int(os.getenv('COPY_SIGNAL_WINDOW_SECONDS',   '300'))
COPY_LOSS_CHECK_WINDOW      = int(os.getenv('COPY_LOSS_CHECK_WINDOW',        '5'))
COPY_DEFAULT_PROFIT_TARGET  = float(os.getenv('COPY_DEFAULT_PROFIT_TARGET',  '0.30'))
COPY_DEFAULT_TRAILING_STOP  = float(os.getenv('COPY_DEFAULT_TRAILING_STOP',  '0.15'))
COPY_DEFAULT_MAX_LOSS       = float(os.getenv('COPY_DEFAULT_MAX_LOSS',       '0.20'))
COPY_DEFAULT_MAX_HOLD_HOURS = float(os.getenv('COPY_DEFAULT_MAX_HOLD_HOURS', '24.0'))
COPY_MAX_PRICE_IMPACT_PCT   = float(os.getenv('COPY_MAX_PRICE_IMPACT_PCT',   '5.0'))

# ── Smart Trader — position management ───────────────────────────────────────
SMART_MIN_TRADE_SOL       = float(os.getenv('SMART_MIN_TRADE_SOL',       '0.05'))
SMART_MAX_OPEN_POSITIONS  = int(os.getenv('SMART_MAX_OPEN_POSITIONS',     '8'))
SMART_MAX_PCT_PER_TOKEN   = float(os.getenv('SMART_MAX_PCT_PER_TOKEN',    '20.0'))
POSITION_CHECK_INTERVAL   = int(os.getenv('POSITION_CHECK_INTERVAL',      '30'))
SMART_MAX_HOLD_HOURS      = float(os.getenv('SMART_MAX_HOLD_HOURS',       '24.0'))
SMART_HARD_STOP_LOSS      = float(os.getenv('SMART_HARD_STOP_LOSS',       '-0.20'))

# Graduated take-profit ladder (threshold → fraction to sell)
# Override individual levels via env vars
SMART_TP1_THRESHOLD = float(os.getenv('SMART_TP1_THRESHOLD', '0.30'))
SMART_TP1_FRACTION  = float(os.getenv('SMART_TP1_FRACTION',  '0.25'))
SMART_TP2_THRESHOLD = float(os.getenv('SMART_TP2_THRESHOLD', '0.60'))
SMART_TP2_FRACTION  = float(os.getenv('SMART_TP2_FRACTION',  '0.50'))
SMART_TP3_THRESHOLD = float(os.getenv('SMART_TP3_THRESHOLD', '1.00'))
SMART_TP3_FRACTION  = float(os.getenv('SMART_TP3_FRACTION',  '1.00'))

# Assembled ladder — used directly by smart_trader.py
SMART_TP_LADDER = [
    (SMART_TP1_THRESHOLD, SMART_TP1_FRACTION),
    (SMART_TP2_THRESHOLD, SMART_TP2_FRACTION),
    (SMART_TP3_THRESHOLD, SMART_TP3_FRACTION),
]

# ── Smart Trader — token discovery filters ────────────────────────────────────
SMART_MIN_VOLUME_USD       = float(os.getenv('SMART_MIN_VOLUME_USD',       '50000'))
SMART_MIN_LIQUIDITY_USD    = float(os.getenv('SMART_MIN_LIQUIDITY_USD',    '20000'))
SMART_AUTO_TRADE_MIN_SCORE = int(os.getenv('SMART_AUTO_TRADE_MIN_SCORE',   '65'))

# ── Smart Trader — auto-copy whale ranking loop ───────────────────────────────
SMART_WHALE_RANK_INTERVAL   = int(os.getenv('SMART_WHALE_RANK_INTERVAL',    str(6 * 3600)))
SMART_MIN_ACTIVE_SCORE      = float(os.getenv('SMART_MIN_ACTIVE_SCORE',     '2.0'))
SMART_MAX_ACTIVE_WHALES     = int(os.getenv('SMART_MAX_ACTIVE_WHALES',      '10'))
SMART_MIN_TRADES_TO_RANK    = int(os.getenv('SMART_MIN_TRADES_TO_RANK',     '3'))
SMART_WHALE_LOOKBACK_DAYS   = int(os.getenv('SMART_WHALE_LOOKBACK_DAYS',    '30'))

# ── Smart Trader — auto-smart scan loop ──────────────────────────────────────
SMART_SCAN_INTERVAL         = int(os.getenv('SMART_SCAN_INTERVAL',          str(30 * 60)))
SMART_TRAILING_STOP_PCT     = float(os.getenv('SMART_TRAILING_STOP_PCT',    '0.15'))
SMART_REBUY_COOLDOWN        = int(os.getenv('SMART_REBUY_COOLDOWN',         '300'))
SMART_REBUY_MIN_MOMENTUM    = int(os.getenv('SMART_REBUY_MIN_MOMENTUM',     '60'))
SMART_REBUY_MAX_RISK        = float(os.getenv('SMART_REBUY_MAX_RISK',       '65'))
SMART_DEFAULT_MAX_POSITIONS = int(os.getenv('SMART_DEFAULT_MAX_POSITIONS',  '4'))
SMART_DEFAULT_TRADE_PCT     = float(os.getenv('SMART_DEFAULT_TRADE_PCT',    '10.0'))

# ── Token Analyzer — liquidity tiers (USD) ───────────────────────────────────
TOKEN_MIN_LIQUIDITY_SAFE   = float(os.getenv('TOKEN_MIN_LIQUIDITY_SAFE',   '50000'))
TOKEN_MIN_LIQUIDITY_MEDIUM = float(os.getenv('TOKEN_MIN_LIQUIDITY_MEDIUM', '5000'))
TOKEN_MIN_LIQUIDITY_RISKY  = float(os.getenv('TOKEN_MIN_LIQUIDITY_RISKY',  '1000'))

# ── Token Analyzer — holder / tax thresholds ─────────────────────────────────
TOKEN_HOLDER_CONCENTRATION_DANGER  = float(os.getenv('TOKEN_HOLDER_CONCENTRATION_DANGER',  '50'))
TOKEN_HOLDER_CONCENTRATION_WARNING = float(os.getenv('TOKEN_HOLDER_CONCENTRATION_WARNING', '30'))
TOKEN_HONEYPOT_TAX_THRESHOLD       = float(os.getenv('TOKEN_HONEYPOT_TAX_THRESHOLD',       '30'))
TOKEN_HIGH_TAX_THRESHOLD           = float(os.getenv('TOKEN_HIGH_TAX_THRESHOLD',           '5'))

# ── Token Analyzer — risk → recommendation thresholds ────────────────────────
# risk_score < TIER → recommendation
TOKEN_RISK_SAFE       = float(os.getenv('TOKEN_RISK_SAFE',       '20'))
TOKEN_RISK_NORMAL     = float(os.getenv('TOKEN_RISK_NORMAL',     '35'))
TOKEN_RISK_CAUTION    = float(os.getenv('TOKEN_RISK_CAUTION',    '50'))
TOKEN_RISK_HIGH       = float(os.getenv('TOKEN_RISK_HIGH',       '65'))
TOKEN_RISK_VERY_HIGH  = float(os.getenv('TOKEN_RISK_VERY_HIGH',  '80'))

# ── Notifications ─────────────────────────────────────────────────────────────
NOTIFICATION_CHECK_INTERVAL  = int(os.getenv('NOTIFICATION_CHECK_INTERVAL',   '60'))
NOTIFICATION_CUTLOSS_THRESHOLD = float(os.getenv('NOTIFICATION_CUTLOSS_THRESHOLD', '-50'))
NOTIFICATION_AGING_HOURS     = float(os.getenv('NOTIFICATION_AGING_HOURS',    '24'))
NOTIFICATION_AGING_MIN_ROI   = float(os.getenv('NOTIFICATION_AGING_MIN_ROI',  '20'))
NOTIFICATION_PROFIT_MILESTONES = [
    float(x) for x in
    os.getenv('NOTIFICATION_PROFIT_MILESTONES', '10,25,50,100,250,500').split(',')
]

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')

# ── Supported chains / DEXs ───────────────────────────────────────────────────
SUPPORTED_CHAINS = ["solana"]
SUPPORTED_DEXS   = ["jupiter"]
