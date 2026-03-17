#!/usr/bin/env python3
"""Test all module imports"""
import sys

modules_to_check = [
    'config',
    'data.database',
    'chains.solana.wallet',
    'wallet.encryption',
    'chains.solana.dex_swaps',
    'trading.copy_trader',
    'trading.risk_manager',
    'data.analytics',
    'chains.solana.vanity_wallet',
    'utils.notifications',
    'bot.telegram_bot',
    'main'
]

print("🔍 Checking all module imports...\n")
failed = []

for module in modules_to_check:
    try:
        __import__(module)
        print(f"✅ {module:20} - OK")
    except Exception as e:
        print(f"❌ {module:20} - ERROR: {str(e)[:60]}")
        failed.append((module, str(e)))

print("\n" + "="*60)
if not failed:
    print("🎉 ALL MODULES IMPORTED SUCCESSFULLY!")
else:
    print(f"⚠️  {len(failed)} module(s) failed to import:")
    for mod, err in failed:
        print(f"  - {mod}: {err[:60]}")
    sys.exit(1)
