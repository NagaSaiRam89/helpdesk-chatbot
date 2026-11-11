"""
app/services/inflight_service.py
Single-flight (request deduplication) for concurrent identical Gemini requests.

If 20 students send the same question simultaneously, only one Gemini call is made.
All waiting coroutines share the result via asyncio.Future objects.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class InflightService:
    """
    Process-local in-flight deduplication using asyncio Futures.
    Key = cache_key (which encodes question + context + fingerprint).
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._inflight: Dict[str, asyncio.Future] = {}
        self._waiters: Dict[str, int] = {}

    async def get_or_start(
        self, key: str
    ) -> tuple[bool, Optional[asyncio.Future]]:
        """
        Returns (is_leader, future_to_await).
        Leader: is_leader=True, future_to_await=None — caller should do the work.
        Follower: is_leader=False, future_to_await=Future — caller should await this.
        """
        async with self._lock:
            if key in self._inflight:
                future = self._inflight[key]
                self._waiters[key] = self._waiters.get(key, 0) + 1
                logger.debug("In-flight follower for key=%s", key[:12])
                return False, future
            # This caller is the leader
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            self._inflight[key] = future
            self._waiters[key] = 0
            logger.debug("In-flight leader for key=%s", key[:12])
            return True, None

    async def resolve(self, key: str, result: Any) -> None:
        """Leader calls this to broadcast result to all followers."""
        async with self._lock:
            future = self._inflight.pop(key, None)
            self._waiters.pop(key, None)
        if future and not future.done():
            future.set_result(result)

    async def reject(self, key: str, exc: Exception) -> None:
        """Leader calls this to broadcast an exception to all followers."""
        async with self._lock:
            future = self._inflight.pop(key, None)
            waiters = self._waiters.pop(key, 0)
        if future and not future.done():
            future.set_exception(exc)
            if waiters == 0:
                # Nobody will ever await this future (the leader failed before
                # any follower joined) — consume the exception ourselves so
                # asyncio does not log "Future exception was never retrieved".
                future.exception()


inflight_service = InflightService()

