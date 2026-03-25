#!/usr/bin/env python
"""
Standalone Web UI Server
Run this separately from the main bot for independent web service
"""
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from bot.web_ui import app

if __name__ == '__main__':
    port = int(os.getenv('WEB_UI_PORT', 3000))
    print(f"🌐 Starting Web UI on port {port}...")
    print(f"📍 Access at http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
