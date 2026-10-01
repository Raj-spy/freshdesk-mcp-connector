"""Freshdesk HTTP client: auth, timeout, error mapping, retry, rate limiting."""
import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from .config import Settings
from .errors import FreshdeskError
from .schemas import ErrorCode


@dataclass
class FreshdeskResponse:
    data: Any
    has_more: bool   # from the Link header (list endpoint)
    attempts: int    # how many downstream calls it took


class RateLimiter:
    """Sliding window: at most `max_calls` per `period` seconds."""

    def __init__(self, max_calls: int, period: float = 60.0):
        self.max_calls = max_calls
        self.period = period
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self.period:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                wait = self.period - (now - self._calls[0])
            await asyncio.sleep(wait)


# Errors that will never succeed on retry: (code, safe message)
_FATAL = {
    400: (ErrorCode.INVALID_INPUT, "Freshdesk rejected the request"),
    401: (ErrorCode.UNAUTHENTICATED, "Freshdesk API key is invalid or missing"),
    403: (ErrorCode.FORBIDDEN, "Not allowed to access this Freshdesk resource"),
    404: (ErrorCode.NOT_FOUND, "Record not found"),
}

BACKOFF_BASE = 0.5
BACKOFF_CAP = 8.0


class FreshdeskClient:
    def __init__(self, settings: Settings, *, sleep=asyncio.sleep, limiter: Optional[RateLimiter] = None):
        self._s = settings
        self._sleep = sleep  # injectable so tests never really wait
        self._limiter = limiter or RateLimiter(settings.rate_limit_per_minute)
        self._http = httpx.AsyncClient(
            base_url=settings.base_url,
            auth=(settings.freshdesk_api_key.get_secret_value(), "X"),  # Freshdesk: key as username
            timeout=settings.request_timeout_seconds,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---------- core ----------
    async def request(self, method: str, path: str, params: Optional[dict] = None,
                      request_id: Optional[str] = None) -> FreshdeskResponse:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {"X-Request-Id": request_id} if request_id else None
        max_attempts = self._s.max_retries + 1

        for attempt in range(1, max_attempts + 1):
            await self._limiter.acquire()
            try:
                resp = await self._http.request(method, path, params=params, headers=headers)
            except httpx.TimeoutException:
                error = FreshdeskError(ErrorCode.TIMEOUT, "Freshdesk did not respond in time", retryable=True)
                delay = self._backoff(attempt)
            except httpx.TransportError:
                error = FreshdeskError(ErrorCode.UPSTREAM_ERROR, "Could not reach Freshdesk", retryable=True)
                delay = self._backoff(attempt)
            else:
                if resp.is_success:
                    return FreshdeskResponse(
                        data=resp.json(),
                        has_more='rel="next"' in resp.headers.get("link", ""),
                        attempts=attempt,
                    )
                error, delay = self._map_error(resp, attempt)

            if not error.retryable or attempt == max_attempts:
                raise error
            await self._sleep(delay)

        raise AssertionError("unreachable")

    # ---------- helpers ----------
    def _backoff(self, attempt: int) -> float:
        return min(BACKOFF_CAP, BACKOFF_BASE * 2 ** (attempt - 1)) + random.uniform(0, BACKOFF_BASE)

    @staticmethod
    def _parse_retry_after(resp: httpx.Response) -> Optional[int]:
        try:
            return max(0, int(float(resp.headers["retry-after"])))
        except (KeyError, ValueError):
            return None

    def _map_error(self, resp: httpx.Response, attempt: int) -> tuple[FreshdeskError, float]:
        status = resp.status_code
        if status == 429:
            retry_after = self._parse_retry_after(resp)
            if retry_after is not None and retry_after > self._s.max_retry_after_seconds:
                # Too long to wait: fail fast so the agent is never left hanging.
                raise FreshdeskError(
                    ErrorCode.RATE_LIMITED, f"Rate limited; Freshdesk asks to wait {retry_after}s",
                    retryable=True, retry_after_seconds=retry_after, status_code=429)
            delay = retry_after if retry_after is not None else self._backoff(attempt)
            return FreshdeskError(
                ErrorCode.RATE_LIMITED, "Freshdesk rate limit exceeded",
                retryable=True, retry_after_seconds=retry_after, status_code=429), delay
        if status >= 500:
            return FreshdeskError(
                ErrorCode.UPSTREAM_ERROR, f"Freshdesk server error ({status})",
                retryable=True, status_code=status), self._backoff(attempt)
        code, message = _FATAL.get(status, (ErrorCode.INVALID_INPUT, f"Unexpected response ({status})"))
        return FreshdeskError(code, message, retryable=False, status_code=status), 0

    # ---------- thin endpoint wrappers (normalization happens in Phase 4) ----------
    async def get_ticket(self, ticket_id: int, request_id: Optional[str] = None) -> FreshdeskResponse:
        return await self.request("GET", f"/api/v2/tickets/{ticket_id}", request_id=request_id)

    async def list_tickets(self, *, page: int = 1, per_page: int = 30, updated_since: Optional[str] = None,
                           email: Optional[str] = None, request_id: Optional[str] = None) -> FreshdeskResponse:
        params = {"page": page, "per_page": per_page, "updated_since": updated_since, "email": email}
        return await self.request("GET", "/api/v2/tickets", params, request_id)

    async def search_tickets(self, query: str, page: int = 1, request_id: Optional[str] = None) -> FreshdeskResponse:
        return await self.request("GET", "/api/v2/search/tickets", {"query": query, "page": page}, request_id)