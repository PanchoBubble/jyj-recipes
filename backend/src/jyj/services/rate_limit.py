import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Backoff:
    failures: int = 0
    blocked_until: float = 0.0
    last_failure: float = 0.0


class LoginRateLimiter:
    """In-process login throttle; valid because the backend runs a single worker.

    Two independent limits: a sliding window of attempts per client IP, and exponential
    backoff per username after repeated failures (applied to unknown usernames too, so the
    limiter does not reveal which accounts exist).
    """

    def __init__(
        self,
        *,
        ip_limit: int = 5,
        ip_window: float = 60.0,
        free_failures: int = 3,
        base_delay: float = 2.0,
        max_delay: float = 900.0,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ip_limit = ip_limit
        self.ip_window = ip_window
        self.free_failures = free_failures
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.max_keys = max_keys
        self.clock = clock
        self._ips: dict[str, deque[float]] = {}
        self._users: dict[str, _Backoff] = {}
        self._lock = threading.Lock()

    def acquire(self, ip: str, username: str) -> float:
        """Record an attempt. Returns 0 if allowed, else seconds until the next try."""
        now = self.clock()
        with self._lock:
            self._prune(now)
            backoff = self._users.get(username)
            if backoff and backoff.blocked_until > now:
                return backoff.blocked_until - now
            window = self._ips.setdefault(ip, deque())
            while window and window[0] <= now - self.ip_window:
                window.popleft()
            if len(window) >= self.ip_limit:
                return window[0] + self.ip_window - now
            window.append(now)
            return 0.0

    def record_failure(self, username: str) -> None:
        now = self.clock()
        with self._lock:
            backoff = self._users.setdefault(username, _Backoff())
            backoff.failures += 1
            backoff.last_failure = now
            over = backoff.failures - self.free_failures
            if over >= 0:
                delay = min(self.base_delay * 2**over, self.max_delay)
                backoff.blocked_until = now + delay

    def record_success(self, username: str) -> None:
        with self._lock:
            self._users.pop(username, None)

    def _prune(self, now: float) -> None:
        if len(self._ips) + len(self._users) < self.max_keys:
            return
        for ip in [k for k, w in self._ips.items() if not w or w[-1] <= now - self.ip_window]:
            del self._ips[ip]
        stale = now - self.max_delay
        for name in [k for k, b in self._users.items() if b.last_failure <= stale]:
            del self._users[name]
        excess = len(self._users) - self.max_keys // 2
        if excess > 0:
            oldest = sorted(self._users, key=lambda k: self._users[k].last_failure)[:excess]
            for name in oldest:
                del self._users[name]
