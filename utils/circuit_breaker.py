"""
Circuit Breaker Pattern for Resilience
Prevents cascading failures by automatically stopping requests to failing services
States: CLOSED (normal) -> OPEN (stop requests) -> HALF_OPEN (test recovery) -> CLOSED
"""
import logging
import time
from typing import Callable, Optional, Any, Dict
from datetime import datetime
from config import (
    CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    CIRCUIT_BREAKER_RECOVERY_TIMEOUT,
    CIRCUIT_BREAKER_HALF_OPEN_TIMEOUT,
    ENABLE_ERROR_RECOVERY_LOGGING,
    ALERT_ON_CIRCUIT_BREAK,
)

logger = logging.getLogger(__name__)


class CircuitBreakerState:
    """Enum for circuit breaker states"""
    CLOSED = "CLOSED"       # Normal operation
    OPEN = "OPEN"           # Stop requests, wait for recovery timeout
    HALF_OPEN = "HALF_OPEN" # Testing if service recovered


class CircuitBreaker:
    """
    Circuit breaker for managing service failures.
    Prevents hammering failing services by automatically cutting requests.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        recovery_timeout: int = CIRCUIT_BREAKER_RECOVERY_TIMEOUT,
        half_open_timeout: int = CIRCUIT_BREAKER_HALF_OPEN_TIMEOUT,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout  # How long to wait in OPEN state
        self.half_open_timeout = half_open_timeout  # How long to test in HALF_OPEN

        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None
        self.state_change_time: Optional[float] = None

    def _set_state(self, new_state: str) -> None:
        """Change state and log it"""
        if new_state != self.state:
            old_state = self.state
            self.state = new_state
            self.state_change_time = time.time()
            logger.info(
                f"🔄 Circuit Breaker '{self.name}': {old_state} → {new_state} "
                f"[failures={self.failure_count}, successes={self.success_count}]"
            )
            if new_state == CircuitBreakerState.OPEN and ALERT_ON_CIRCUIT_BREAK:
                logger.warning(
                    f"🚨 ALERT: Circuit breaker OPEN for '{self.name}' "
                    f"after {self.failure_count} consecutive failures. "
                    f"Recovery timeout: {self.recovery_timeout}s"
                )

    async def call(
        self, func: Callable, *args, **kwargs
    ) -> Optional[Any]:
        """
        Execute function with circuit breaker protection.
        Raises CircuitBreakerOpen if breaker is OPEN.
        """
        if self.state == CircuitBreakerState.OPEN:
            if self._should_attempt_reset():
                self._set_state(CircuitBreakerState.HALF_OPEN)
                logger.info(f"⚡ Testing recovery for '{self.name}'...")
            else:
                elapsed = time.time() - self.last_failure_time
                raise CircuitBreakerOpen(
                    f"Circuit breaker OPEN for '{self.name}'. "
                    f"Retry in {self.recovery_timeout - elapsed:.1f}s"
                )

        try:
            result = await func(*args, **kwargs)
            self.on_success()
            return result
        except Exception as e:
            self.on_failure()
            raise

    def on_success(self) -> None:
        """Record successful call"""
        self.success_count += 1
        if self.state == CircuitBreakerState.HALF_OPEN:
            logger.info(f"✅ Recovery successful for '{self.name}'")
            self.failure_count = 0
            self.success_count = 0
            self._set_state(CircuitBreakerState.CLOSED)
        elif self.state == CircuitBreakerState.CLOSED:
            # Reset failure count on success to prevent false positives
            self.failure_count = 0

    def on_failure(self) -> None:
        """Record failed call"""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if ENABLE_ERROR_RECOVERY_LOGGING:
            logger.warning(
                f"❌ Failure #{self.failure_count}/{self.failure_threshold} "
                f"for '{self.name}'"
            )

        if self.failure_count >= self.failure_threshold:
            if self.state != CircuitBreakerState.OPEN:
                self._set_state(CircuitBreakerState.OPEN)

        if self.state == CircuitBreakerState.HALF_OPEN:
            logger.error(f"Recovery failed for '{self.name}', re-opening circuit")
            self.failure_count = 0
            self._set_state(CircuitBreakerState.OPEN)

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt recovery"""
        if not self.last_failure_time:
            return False
        elapsed = time.time() - self.last_failure_time
        return elapsed >= self.recovery_timeout

    def get_status(self) -> Dict[str, Any]:
        """Get current circuit breaker status"""
        return {
            "name": self.name,
            "state": self.state,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_time": (
                datetime.fromtimestamp(self.last_failure_time).isoformat()
                if self.last_failure_time
                else None
            ),
            "state_change_time": (
                datetime.fromtimestamp(self.state_change_time).isoformat()
                if self.state_change_time
                else None
            ),
        }

    def reset(self) -> None:
        """Force reset circuit breaker to CLOSED state"""
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
        logger.info(f"🔄 Circuit breaker '{self.name}' manually reset")


class CircuitBreakerOpen(Exception):
    """Exception raised when circuit breaker is OPEN"""

    pass


class CircuitBreakerManager:
    """Manage multiple circuit breakers for different services"""

    def __init__(self):
        self.breakers: Dict[str, CircuitBreaker] = {}

    def get_or_create(
        self,
        name: str,
        failure_threshold: int = CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        recovery_timeout: int = CIRCUIT_BREAKER_RECOVERY_TIMEOUT,
    ) -> CircuitBreaker:
        """Get existing breaker or create new one"""
        if name not in self.breakers:
            self.breakers[name] = CircuitBreaker(name, failure_threshold, recovery_timeout)
        return self.breakers[name]

    def get_status_all(self) -> Dict[str, Dict]:
        """Get status of all circuit breakers"""
        return {name: breaker.get_status() for name, breaker in self.breakers.items()}

    def reset_all(self) -> None:
        """Reset all circuit breakers"""
        for breaker in self.breakers.values():
            breaker.reset()
        logger.info("🔄 All circuit breakers reset")


# Global circuit breaker manager
circuit_breaker_manager = CircuitBreakerManager()

# Pre-create breakers for critical services
rpc_breaker = circuit_breaker_manager.get_or_create("solana-rpc", failure_threshold=5)
token_analyzer_breaker = circuit_breaker_manager.get_or_create("token-analyzer", failure_threshold=4)
dex_screener_breaker = circuit_breaker_manager.get_or_create("dex-screener", failure_threshold=4)
birdeye_breaker = circuit_breaker_manager.get_or_create("birdeye", failure_threshold=4)
jupiter_breaker = circuit_breaker_manager.get_or_create("jupiter", failure_threshold=3)
