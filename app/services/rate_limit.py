"""In-memory rate limits with several buckets (plan section 5, "Rate limits").

The app runs as one process, so memory is enough; moving the counters to
Postgres is listed under "Later" in the plan.
"""
import math
import os
import threading
import time
from collections import defaultdict, deque

# bucket: (max events, window in seconds)
LIMITS = {
    "login_ip": (int(os.getenv("PDF_REPORT_LOGIN_MAX_FAILURES", "10")), int(os.getenv("PDF_REPORT_LOGIN_WINDOW_SECONDS", "300"))),
    "signup_ip": (5, 3600),
    "forgot_ip": (5, 3600),
    "forgot_email": (3, 3600),
    "resend_user": (3, 3600),
    "email_change_user": (5, 3600),
    "token_ip": (30, 3600),  # verify / reset / confirm-email attempts
}
# Failed logins on one account: no lockout, but each failure after the first few
# doubles the wait, up to this many seconds.
ACCOUNT_FREE_FAILURES = 4
ACCOUNT_MAX_DELAY_SECONDS = 900
ACCOUNT_FAILURE_WINDOW_SECONDS = 3600


class RateLimiter:
    def __init__(self):
        self._events: dict[tuple[str, str], deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, bucket: str, key: str, window: int, now: float) -> deque:
        events = self._events[(bucket, key)]
        while events and events[0] <= now - window:
            events.popleft()
        return events

    def retry_after(self, bucket: str, key: str) -> int:
        """Seconds to wait before the next attempt is allowed (0 = allowed)."""
        limit, window = LIMITS[bucket]
        now = time.time()
        with self._lock:
            events = self._recent(bucket, key, window, now)
            if len(events) < limit:
                return 0
            return max(1, int(events[0] + window - now) + 1)

    def hit(self, bucket: str, key: str) -> None:
        with self._lock:
            self._events[(bucket, key)].append(time.time())

    def check_and_hit(self, bucket: str, key: str) -> int:
        """Count an attempt unless the limit is reached; returns the wait when it is."""
        wait = self.retry_after(bucket, key)
        if not wait:
            self.hit(bucket, key)
        return wait

    def recent_count(self, bucket: str, key: str, window: int) -> int:
        with self._lock:
            return len(self._recent(bucket, key, window, time.time()))

    def account_delay(self, email: str) -> int:
        """Growing delay after repeated failed logins on one account."""
        now = time.time()
        with self._lock:
            failures = self._recent("login_account", email, ACCOUNT_FAILURE_WINDOW_SECONDS, now)
            extra = len(failures) - ACCOUNT_FREE_FAILURES
            if extra <= 0:
                return 0
            wait = failures[-1] + min(ACCOUNT_MAX_DELAY_SECONDS, 2 ** extra) - now
            return math.ceil(wait) if wait > 0 else 0

    def reset(self, bucket: str | None = None, key: str | None = None) -> None:
        with self._lock:
            if bucket is None:
                self._events.clear()
                return
            for stored in [k for k in self._events if k[0] == bucket and (key is None or k[1] == key)]:
                del self._events[stored]


limiter = RateLimiter()
