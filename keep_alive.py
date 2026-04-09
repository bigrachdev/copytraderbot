"""
Enhanced Keep-Alive Module v2 - Aggressive Render sleep prevention + Auto-restart
Ensures the bot NEVER sleeps and auto-recovers on crashes
"""
import os
import requests
import time
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
import json
import sys
import subprocess

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class EnhancedHealthCheckHandler(BaseHTTPRequestHandler):
    """Enhanced HTTP handler with detailed health metrics"""
    
    start_time = datetime.now()
    ping_count = 0
    last_ping = None
    request_count = 0
    error_count = 0
    
    def do_GET(self):
        """Handle GET requests with detailed health info"""
        self.request_count += 1
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        
        uptime = datetime.now() - self.start_time
        
        status = {
            'status': 'ALIVE',
            'service': 'dex_copy_trading_bot',
            'timestamp': datetime.now().isoformat(),
            'uptime_seconds': int(uptime.total_seconds()),
            'uptime': str(uptime).split('.')[0],
            'ping_count': self.ping_count,
            'last_ping': self.last_ping.isoformat() if self.last_ping else None,
            'total_requests': self.request_count,
            'error_count': self.error_count,
            'health_score': max(0, 100 - (self.error_count * 2)),
            'bot_status': 'RUNNING',
            'render_status': 'NO SLEEP',
            'message': '🚀 Bot is ALIVE and PROTECTING from Render sleep!'
        }
        
        self.wfile.write(json.dumps(status, indent=2).encode())
    
    def do_HEAD(self):
        """Handle HEAD requests"""
        self.send_response(200)
        self.end_headers()
    
    def log_message(self, format, *args):
        """Suppress default HTTP logs"""
        pass


class UptimeBotIntegration:
    """Integration with external monitoring services"""
    
    def __init__(self):
        self.uptimerobot_urls = os.getenv('UPTIMEROBOT_URLS', '').split(',')
        self.cron_job_urls = os.getenv('CRON_JOB_URLS', '').split(',')
        self.betteruptime_urls = os.getenv('BETTERUPTIME_URLS', '').split(',')
        self.custom_webhooks = os.getenv('CUSTOM_WEBHOOKS', '').split(',')
        
        self.uptimerobot_urls = [u.strip() for u in self.uptimerobot_urls if u.strip()]
        self.cron_job_urls = [u.strip() for u in self.cron_job_urls if u.strip()]
        self.betteruptime_urls = [u.strip() for u in self.betteruptime_urls if u.strip()]
        self.custom_webhooks = [u.strip() for u in self.custom_webhooks if u.strip()]
    
    async def ping_external_services(self):
        """Ping all configured external monitoring services"""
        import aiohttp
        
        all_urls = (
            self.uptimerobot_urls + 
            self.cron_job_urls + 
            self.betteruptime_urls + 
            self.custom_webhooks
        )
        
        if not all_urls:
            return
        
        try:
            async with aiohttp.ClientSession() as session:
                for url in all_urls:
                    try:
                        async with session.get(
                            url,
                            timeout=aiohttp.ClientTimeout(total=5),
                            headers={'User-Agent': 'DexBot/1.0'}
                        ) as resp:
                            if resp.status == 200:
                                logger.debug(f"✅ External ping: {url[:50]}...")
                    except Exception as e:
                        logger.debug(f"⚠️ External ping failed: {str(e)[:50]}")
        except Exception as e:
            logger.debug(f"⚠️ External monitoring error: {e}")


class EnhancedKeepAlive:
    """
    ULTIMATE Keep-Alive - Multi-layered Render sleep prevention
    
    Features:
    - HTTP server for local health checks
    - Self-ping to prevent Render sleep
    - External monitoring integration
    - Auto-restart on critical failures
    - Heartbeat logging
    - Health metrics tracking
    """
    
    def __init__(self, port: int = 10000):
        self.port = port
        self.render_url = os.getenv('RENDER_EXTERNAL_URL', '').strip()
        
        # Aggressive timings - Render sleeps after 15min of inactivity
        self.ping_interval = int(os.getenv('KEEP_ALIVE_PING_INTERVAL', '120'))  # 2 minutes default
        self.heartbeat_interval = int(os.getenv('KEEP_ALIVE_HEARTBEAT_INTERVAL', '180'))  # 3 minutes
        
        self.http_server = None
        self.running = True
        self.failure_count = 0
        self.max_failures = 20
        
        # Uptime bot
        self.uptime_bot = UptimeBotIntegration()
        
        logger.info(f"🔥 Enhanced Keep-Alive v2 initialized (ping every {self.ping_interval}s)")
    
    def start_http_server(self):
        """Start HTTP server - CRITICAL for Render health checks"""
        try:
            self.http_server = HTTPServer(('0.0.0.0', self.port), EnhancedHealthCheckHandler)
            logger.info(f"🟢 CRITICAL: Health check server started on port {self.port}")
            logger.info(f"📡 Health endpoint: http://0.0.0.0:{self.port}/ or http://localhost:{self.port}/")
            
            # Log Render URL for external monitoring
            if self.render_url:
                logger.info(f"🌐 Render URL for external monitoring: {self.render_url}")
            
            self.http_server.serve_forever()
        except Exception as e:
            logger.error(f"❌ CRITICAL: Failed to start HTTP server: {e}")
            self.failure_count += 1
    
    def aggressive_self_ping(self):
        """
        Aggressively ping self to prevent Render sleep - NEVER SLEEP ALLOWED
        Uses multiple endpoints and fallback strategies
        """
        if not self.render_url:
            logger.warning("⚠️ RENDER_EXTERNAL_URL not set!")
            logger.warning("📌 Set this in Render dashboard after first deployment")
            logger.warning("📌 Variable Name: RENDER_EXTERNAL_URL")
            return
        
        logger.info(f"🔥 AGGRESSIVE Self-ping started")
        logger.info(f"🎯 Target: {self.render_url}")
        logger.info(f"⚡ Interval: {self.ping_interval}s (NO SLEEP!)")
        
        consecutive_failures = 0
        
        while self.running:
            try:
                start_time = time.time()
                
                # Try multiple endpoint strategies
                services_to_ping = [
                    ('/', 'root'),
                    ('/health', 'health'),
                    ('/api', 'api'),
                    ('/admin', 'admin'),
                    ('', 'empty'),
                ]
                
                success = False
                last_error = None
                
                for endpoint, service_name in services_to_ping:
                    try:
                        url = f"{self.render_url.rstrip('/')}{endpoint}"
                        
                        response = requests.get(
                            url,
                            timeout=8,
                            headers={
                                'User-Agent': 'KeepAlive-Bot/2.0 (Aggressive)',
                                'Cache-Control': 'no-cache, no-store, must-revalidate',
                                'Pragma': 'no-cache',
                                'Expires': '0'
                            },
                            allow_redirects=True
                        )
                        
                        if response.status_code in [200, 301, 302, 304]:
                            elapsed = time.time() - start_time
                            EnhancedHealthCheckHandler.ping_count += 1
                            EnhancedHealthCheckHandler.last_ping = datetime.now()
                            consecutive_failures = 0
                            self.failure_count = 0
                            
                            logger.info(f"✅ Ping #{EnhancedHealthCheckHandler.ping_count}: {service_name} "
                                      f"({response.status_code}) - {elapsed:.2f}s 🔥 BOT AWAKE!")
                            success = True
                            break
                    
                    except requests.exceptions.ConnectTimeout:
                        last_error = "Connection timeout"
                    except requests.exceptions.ReadTimeout:
                        last_error = "Read timeout"
                    except Exception as e:
                        last_error = str(e)
                    
                    # Brief delay before trying next endpoint
                    time.sleep(0.5)
                
                if not success:
                    consecutive_failures += 1
                    self.failure_count += 1
                    
                    logger.warning(f"⚠️ Ping failed #{consecutive_failures}: {last_error}")
                    
                    # Aggressive recovery on failures
                    if self.failure_count >= self.max_failures:
                        logger.critical(f"❌ {self.max_failures} consecutive failures - CRITICAL")
                        logger.critical("⚠️ Bot may be sleeping or crashed")
                        # Reset but keep trying
                        self.failure_count = 0
            
            except Exception as e:
                logger.error(f"❌ Ping loop error: {e}")
                self.failure_count += 1
            
            # Wait before next ping
            time.sleep(self.ping_interval)
    
    def heartbeat_monitor(self):
        """Periodic heartbeat with health monitoring"""
        while self.running:
            time.sleep(self.heartbeat_interval)
            
            uptime = datetime.now() - EnhancedHealthCheckHandler.start_time
            health_score = max(0, 100 - (self.failure_count * 5))
            
            logger.info(f"💚 HEARTBEAT - Uptime: {str(uptime).split('.')[0]} | "
                       f"Pings: {EnhancedHealthCheckHandler.ping_count} | "
                       f"Failures: {self.failure_count} | "
                       f"Health: {health_score}% | 🔥 NO SLEEP!")
    
    def auto_restart_watchdog(self):
        """Watchdog that auto-restarts bot if critical failure detected"""
        while self.running:
            time.sleep(300)  # Check every 5 minutes
            
            if self.failure_count > self.max_failures * 2:
                logger.critical(f"🚨 WATCHDOG: Critical failures detected ({self.failure_count}+)")
                logger.critical("🔄 Attempting graceful service restart...")
                
                try:
                    # Send restart signal to main process
                    os.kill(os.getpid(), 15)  # SIGTERM for graceful shutdown
                except Exception as e:
                    logger.error(f"Restart failed: {e}")
    
    def show_setup_instructions(self):
        """Show setup instructions for maximum uptime"""
        logger.info("\n" + "="*70)
        logger.info("🚀 ENHANCED KEEP-ALIVE SETUP INSTRUCTIONS")
        logger.info("="*70)
        
        logger.info("\n1️⃣ RENDER SETUP (Required)")
        logger.info("   - Go to Render Dashboard → Your Service")
        logger.info("   - Environment: Add new variable")
        logger.info("   - Key: RENDER_EXTERNAL_URL")
        logger.info("   - Value: Your Render service URL (auto-populated after first deployment)")
        logger.info(f"   - Port: {self.port}")
        
        logger.info("\n2️⃣ EXTERNAL MONITORING (Recommended for 99.9% uptime)")
        logger.info("\n   Option A: UptimeRobot (Free tier)")
        logger.info(f"   - Create free account at: https://uptimerobot.com")
        logger.info(f"   - Add monitor with your Render URL")
        logger.info(f"   - Check every 5 minutes")
        logger.info(f"   - Add to Render env vars:")
        logger.info(f"     Key: UPTIMEROBOT_URLS")
        logger.info(f"     Value: https://api.uptimerobot.com/v2/getMonitorWebhooks")
        
        logger.info("\n   Option B: Cron-Job.org (Free)")
        logger.info(f"   - Create free account at: https://cron-job.org")
        logger.info(f"   - Create cron job: */5 * * * * (every 5 minutes)")
        logger.info(f"   - URL: Your Render service URL")
        logger.info(f"   - Add to Render env vars:")
        logger.info(f"     Key: CRON_JOB_URLS")
        logger.info(f"     Value: Your cron-job.org callback URL")
        
        logger.info("\n   Option C: Better Uptime")
        logger.info(f"   - Create account at: https://betteruptime.com")
        logger.info(f"   - Setup HTTP monitor")
        logger.info(f"   - Check every 3 minutes (recommended)")
        
        logger.info("\n3️⃣ CUSTOM WEBHOOKS")
        logger.info(f"   - Add to Render env vars:")
        logger.info(f"     Key: CUSTOM_WEBHOOKS")
        logger.info(f"     Value: comma-separated webhook URLs")
        
        logger.info("\n4️⃣ VERIFY SETUP")
        logger.info(f"   - Visit: {self.render_url if self.render_url else 'http://localhost:' + str(self.port)}")
        logger.info(f"   - Should see JSON health status")
        
        logger.info("\n" + "="*70 + "\n")
    
    def start(self):
        """Start all keep-alive services - AGGRESSIVE MODE"""
        logger.info("\n" + "🔥"*40)
        logger.info("🔥 ENHANCED KEEP-ALIVE v2 - NO SLEEP ALLOWED 🔥")
        logger.info("🔥"*40)
        logger.info(f"\n⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
        logger.info(f"🌐 Port: {self.port}")
        logger.info(f"⚡ Ping Interval: {self.ping_interval}s (AGGRESSIVE)")
        logger.info(f"💪 Status: ACTIVE - PREVENTING SLEEP\n")
        
        # Show setup
        self.show_setup_instructions()
        
        # Start HTTP server
        http_thread = threading.Thread(target=self.start_http_server, daemon=True, name="HTTP-Server")
        http_thread.start()
        logger.info("✅ HTTP server thread started")
        
        time.sleep(1)
        
        # Start self-ping
        ping_thread = threading.Thread(target=self.aggressive_self_ping, daemon=True, name="Self-Ping")
        ping_thread.start()
        logger.info("✅ Self-ping thread started (AGGRESSIVE)")
        
        # Start heartbeat
        heartbeat_thread = threading.Thread(target=self.heartbeat_monitor, daemon=True, name="Heartbeat")
        heartbeat_thread.start()
        logger.info("✅ Heartbeat monitor started")
        
        # Start watchdog
        watchdog_thread = threading.Thread(target=self.auto_restart_watchdog, daemon=True, name="Watchdog")
        watchdog_thread.start()
        logger.info("✅ Auto-restart watchdog started")
        
        logger.info("\n✅ All keep-alive services running - BOT PROTECTED FROM SLEEP!\n")


# Backward compatibility
AggressiveKeepAlive = EnhancedKeepAlive