"""
Configuration and constants for the DEX Copy Trading Bot
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')

# Solana
SOLANA_RPC_URL = os.getenv('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')
SOLANA_WSS_URL = os.getenv('SOLANA_WSS_URL', 'wss://api.mainnet-beta.solana.com')

# Base (Coinbase L2)
BASE_RPC_URL = os.getenv('BASE_RPC_URL', 'https://mainnet.base.org')
BASE_CHAIN_ID = 8453

# Database
DB_PATH = os.getenv('DB_PATH', 'trade_bot.db')

# Copy Trading Settings
COPY_TRADE_CHECK_INTERVAL = 10  # seconds
WALLET_MONITOR_INTERVAL = 5  # seconds
SLIPPAGE_TOLERANCE = 2.0  # %
MIN_TRADE_AMOUNT = 0.01  # SOL

# DEX Endpoints
JUPITER_API = "https://quote-api.jup.ag/v6"
RAYDIUM_API = "https://api.raydium.io/v2"
ORCA_URL = "https://api.mainnet.orca.so"

# Supported DEXs
SUPPORTED_DEXS = ["jupiter", "raydium", "orca"]

# Token settings
SOL_MINT = "11111111111111111111111111111111"
WSOL_MINT = "So11111111111111111111111111111111111111112"

# Logging
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')

# Default values for copy trading
DEFAULT_COPY_SCALE = 1.0  # 1:1 ratio
MAX_SLIPPAGE = 5.0
