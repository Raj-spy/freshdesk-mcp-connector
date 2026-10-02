"""Tiny in-memory TTL cache that also merges identical in-flight requests."""
import asyncio
import time
from typing import Any, Awaitable, Callable, Hashable


class ResponseCache:
    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic,
                 max_entries: int = 256):
        self._ttl = ttl_seconds
        self._clock = clock
        self._max = max_entries
        self._data: dict[Hashable, tuple[float, Any]] = {}
        self._inflight: dict[Hashable, asyncio.Future] = {}

    async def get_or_fetch(self, key: Hashable, fetch: Callable[[], Awaitable[Any]]) -> Any:
        if self._ttl <= 0:                       # cache disabled
            return await fetch()

        hit = self._data.get(key)
        if hit is not None and hit[0] > self._clock():
            return hit[1]

        task = self._inflight.get(key)
        if task is None:                         # first caller does the real work
            task = asyncio.ensure_future(self._fill(key, fetch))
            self._inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
        return await task                        # later identical callers just wait

    async def _fill(self, key: Hashable, fetch: Callable[[], Awaitable[Any]]) -> Any:
        value = await fetch()                    # errors propagate and are NOT cached
        self._data[key] = (self._clock() + self._ttl, value)
        while len(self._data) > self._max:
            self._data.pop(next(iter(self._data)))
        return value