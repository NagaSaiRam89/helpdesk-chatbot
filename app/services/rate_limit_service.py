"""
app/services/rate_limit_service.py
Basic sliding-window rate limiting per session and per client IP.

Design notes:
- In-memory sliding window (deque of monotonic timestamps per key).
  Fast, allocation-light, and adequate for a single-process deployment.
- Two independent keys per chat request:
    * "session:<id>"  — bounds a single runaway browser/conversation.
    * "ip:<addr>"     — bounds session-rotation abuse; limit is higher so
      students behind a shared campus NAT are not unfairly throttled.
- Checks are synchronous (no awaits inside), so no lock is needed on the
  FastAPI event loop. For multi-worker deployments each worker enforces its
  own window — a documented trade-off (see README, Known Limitations).
"""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Deque, Dict

logger = logging.getLogger(__name__)

_MAX_TRACKED_KEYS = 50_000  # safety cap for the in-memory window store


class RateLimitService:

    def __init__(self) -> None:
        self._windows: Dict[str, Deque[float]] = {}

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        """
        Sliding-window check. Returns True (and records the hit) when the
        caller is under `limit` requests inside the trailing window.
        """
        if limit <= 0:  # 0/negative disables limiting
            return True

        now = time.monotonic()
        q = self._windows.get(key)
        if q is None:
            q = deque()
            self._windows[key] = q

        cutoff = now - window_seconds
        while q and q[0] <= cutoff:
            q.popleft()

        if len(q) >= limit:
            return False

        q.append(now)

        if len(self._windows) > _MAX_TRACKED_KEYS:
            self._sweep(window_seconds)
        return True

    def retry_after_seconds(self, window_seconds: int) -> int:
        """Friendly Retry-After hint for 429 responses."""
        return max(1, int(window_seconds))

    def _sweep(self, window_seconds: int) -> None:
        """Drop empty/expired windows to keep memory bounded."""
        now = time.monotonic()
        cutoff = now - window_seconds
        for key in list(self._windows.keys()):
            q = self._windows[key]
            while q and q[0] <= cutoff:
                q.popleft()
            if not q:
                del self._windows[key]

    def reset(self) -> None:
        """Clear all windows (used by tests)."""
        self._windows.clear()


# Singleton instance
rate_limit_service = RateLimitService()
