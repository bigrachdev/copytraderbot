import os
import requests
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class HealthCheckHandler(BaseHTTPRequestHandler):
    """HTTP handler for health check endpoint"""
    
    start_time = datetime.now()
    ping_count = 0
    last_ping = None
    
    def do_GET(self):
        """Handle GET requests"""
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        
        uptime = datetime.now() - self.start_time
        
        status = {
            'status': 'alive',
            'service': 'whale_monitor_bot',
            'timestamp': datetime.now().isoformat(),
            'uptime_seconds': int(uptime.total_seconds()),
            'uptime': str(uptime).split('.')[0],
            'ping_count': self.ping_count,
            'last_ping': self.last_ping.isoformat() if self.last_ping else None,
            'message': '🤖 AI Whale Monitor is running!'
        }
        
        import json
        self.wfile.write(json.dumps(status, indent=2).encode())
    
    def do_HEAD(self):
        """Handle HEAD requests (for some monitoring services)"""
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
    
    def log_message(self, format, *args):
        """Suppress default HTTP logs (too verbose)"""
        pass


class AggressiveKeepAlive:
    """Aggressive keep-alive service to prevent Render sleep - NO SLEEP ALLOWED"""

    def __init__(self, port=10000):
        self.port = port
        self.render_url = os.getenv('RENDER_EXTERNAL_URL', '')
        self.ping_interval = 180  # 3 minutes (Render sleeps after 15 min) - AGGRESSIVE
        self.http_server = None
        self.running = True
        
    def start_http_server(self):
        """Start HTTP server for health checks"""
        try:
            self.http_server = HTTPServer(('0.0.0.0', self.port), HealthCheckHandler)
            logger.info(f"🟢 Health check server started on port {self.port}")
            logger.info(f"📡 Endpoint: http://0.0.0.0:{self.port}/")
            self.http_server.serve_forever()
        except Exception as e:
            logger.error(f"❌ Failed to start HTTP server: {e}")
    
    def self_ping(self):
        """Aggressively ping self to prevent sleep - NO SLEEP ALLOWED"""
        if not self.render_url:
            logger.warning("⚠️ RENDER_EXTERNAL_URL not set - self-ping disabled")
            logger.warning("⚠️ Set this in Render dashboard after first deployment!")
            return

        logger.info(f"🔥 AGGRESSIVE Self-ping service started")
        logger.info(f"🎯 Target: {self.render_url}")
        logger.info(f"⚡ Interval: {self.ping_interval} seconds (NO SLEEP!)")

        consecutive_failures = 0
        max_failures = 10

        while self.running:
            try:
                start_time = time.time()

                # Try multiple endpoints aggressively
                endpoints = ['/', '/health', '/api', '']
                success = False

                for endpoint in endpoints:
                    try:
                        url = f"{self.render_url.rstrip('/')}{endpoint}"
                        response = requests.get(
                            url,
                            timeout=10,
                            headers={
                                'User-Agent': 'KeepAlive-Service/1.0 (Aggressive)',
                                'Cache-Control': 'no-cache',
                                'Pragma': 'no-cache'
                            }
                        )

                        if response.status_code == 200:
                            elapsed = time.time() - start_time
                            HealthCheckHandler.ping_count += 1
                            HealthCheckHandler.last_ping = datetime.now()

                            logger.info(f"✅ Ping #{HealthCheckHandler.ping_count} successful "
                                      f"({response.status_code}) - {elapsed:.2f}s")
                            success = True
                            consecutive_failures = 0
                            break
                    except requests.exceptions.RequestException:
                        continue

                if not success:
                    consecutive_failures += 1
                    logger.warning(f"⚠️ Ping failed (attempt {consecutive_failures}/{max_failures})")

                    if consecutive_failures >= max_failures:
                        logger.error(f"❌ {max_failures} consecutive failures - restarting service!")
                        # Reset counter but keep trying aggressively
                        consecutive_failures = 0
                        # Quick retry after failure
                        time.sleep(30)
                        continue

            except Exception as e:
                logger.error(f"❌ Ping error: {e}")
                consecutive_failures += 1

            # Wait before next ping - NO SLEEP ALLOWED
            time.sleep(self.ping_interval)
    
    def external_ping_recommendations(self):
        """Show recommendations for external monitoring"""
        logger.info("\n" + "="*60)
        logger.info("📊 EXTERNAL MONITORING RECOMMENDATIONS")
        logger.info("="*60)
        logger.info("\nFor maximum uptime, also use external services:")
        logger.info("\n1️⃣ UptimeRobot (https://uptimerobot.com)")
        logger.info(f"   - Monitor Type: HTTP(s)")
        logger.info(f"   - URL: {self.render_url if self.render_url else 'YOUR_RENDER_URL'}")
        logger.info(f"   - Interval: 5 minutes")
        logger.info("\n2️⃣ Cron-Job.org (https://cron-job.org)")
        logger.info(f"   - URL: {self.render_url if self.render_url else 'YOUR_RENDER_URL'}")
        logger.info(f"   - Schedule: */5 * * * * (every 5 minutes)")
        logger.info("\n3️⃣ Better Uptime (https://betteruptime.com)")
        logger.info(f"   - URL: {self.render_url if self.render_url else 'YOUR_RENDER_URL'}")
        logger.info(f"   - Check frequency: 3 minutes")
        logger.info("\n" + "="*60 + "\n")
    
    def heartbeat_logger(self):
        """Log periodic heartbeat to show service is alive - NO SLEEP ALLOWED"""
        while self.running:
            time.sleep(180)  # Every 3 minutes - MORE FREQUENT
            uptime = datetime.now() - HealthCheckHandler.start_time
            logger.info(f"💚 HEARTBEAT - Uptime: {str(uptime).split('.')[0]} | "
                       f"Pings: {HealthCheckHandler.ping_count} | NO SLEEP!")
    
    def start(self):
        """Start all keep-alive services - AGGRESSIVE MODE - NO SLEEP ALLOWED"""
        logger.info("\n" + "🔥"*30)
        logger.info("🔥 AGGRESSIVE KEEP-ALIVE SERVICE - NO SLEEP ALLOWED")
        logger.info("🔥"*30)
        logger.info(f"\n⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
        logger.info(f"🌐 Port: {self.port}")
        logger.info(f"⚡ Ping Interval: {self.ping_interval} seconds (AGGRESSIVE)")
        logger.info(f"🔗 URL: {self.render_url if self.render_url else 'Not set (will update after deployment)'}")
        logger.info(f"💪 Status: ACTIVE - PREVENTING SLEEP\n")
        
        # Show external monitoring recommendations
        self.external_ping_recommendations()
        
        # Start HTTP server in thread
        http_thread = threading.Thread(target=self.start_http_server, daemon=True)
        http_thread.start()
        logger.info("✅ HTTP server thread started")
        
        # Wait a moment for HTTP server to initialize
        time.sleep(2)
        
        # Start self-ping in thread
        ping_thread = threading.Thread(target=self.self_ping, daemon=True)
        ping_thread.start()
        logger.info("✅ Self-ping thread started")
        
        # Start heartbeat logger in thread
        heartbeat_thread = threading.Thread(target=self.heartbeat_logger, daemon=True)
        heartbeat_thread.start()
        logger.info("✅ Heartbeat logger started")
        
        logger.info("\n" + "="*60)
        logger.info("🎯 All keep-alive services running!")
        logger.info("="*60 + "\n")
        
        # Keep main thread alive
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\n🛑 Shutting down keep-alive service...")
            self.running = False


def main():
    """Main entry point"""
    port = int(os.getenv('PORT', 10000))
    keep_alive = AggressiveKeepAlive(port=port)
    keep_alive.start()


if __name__ == "__main__":
    main()