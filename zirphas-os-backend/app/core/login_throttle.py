"""Brute-force protection for the staff login endpoint.

Failed attempts are counted per (client IP, normalized e-mail) in a sliding
window. Once the limit is reached, further attempts for that pair are refused
with HTTP 429 until the oldest failure leaves the window, even if the password
is correct. A successful login clears the pair's history.

State is kept in process memory, so each worker process keeps its own counters
and a restart resets them. A deployment with several workers or instances should
move this state to a shared store such as Redis.
"""

from collections import deque
from math import ceil
from threading import Lock
from time import monotonic

from app.core.config import get_settings


class LoginThrottle:
    def __init__(self, max_failures: int, window_seconds: int, clock=monotonic):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._clock = clock
        self._failures: dict[tuple[str, str], deque[float]] = {}
        self._lock = Lock()

    def _prune(self, key: tuple[str, str], now: float) -> deque[float] | None:
        attempts = self._failures.get(key)
        if attempts is None:
            return None
        while attempts and now - attempts[0] >= self.window_seconds:
            attempts.popleft()
        if not attempts:
            del self._failures[key]
            return None
        return attempts

    def retry_after(self, client_ip: str, email: str) -> int | None:
        """Seconds until the pair may try again, or None if it is not blocked."""
        key = (client_ip, email)
        with self._lock:
            now = self._clock()
            attempts = self._prune(key, now)
            if attempts is None or len(attempts) < self.max_failures:
                return None
            return max(1, ceil(self.window_seconds - (now - attempts[0])))

    def record_failure(self, client_ip: str, email: str) -> None:
        key = (client_ip, email)
        with self._lock:
            now = self._clock()
            attempts = self._prune(key, now)
            if attempts is None:
                attempts = self._failures[key] = deque()
            attempts.append(now)

    def reset(self, client_ip: str, email: str) -> None:
        with self._lock:
            self._failures.pop((client_ip, email), None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()


_settings = get_settings()
login_throttle = LoginThrottle(_settings.login_max_failures, _settings.login_window_seconds)
