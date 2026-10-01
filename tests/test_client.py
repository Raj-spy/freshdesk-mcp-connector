import time

import httpx
import pytest

from src.connector.config import Settings
from src.connector.errors import FreshdeskError
from src.connector.freshdesk_client import FreshdeskClient, RateLimiter
from src.connector.schemas import ErrorCode

URL = "http://mock/api/v2/tickets/1"


class Sleeps:
    """Fake sleep: records requested waits instead of waiting."""
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, seconds: float):
        self.calls.append(seconds)


@pytest.fixture
def sleeps():
    return Sleeps()


@pytest.fixture
async def client(sleeps):
    settings = Settings(
        _env_file=None, freshdesk_mode="mock", freshdesk_api_key="test-key",
        mock_base_url="http://mock", max_retries=2, max_retry_after_seconds=30,
        rate_limit_per_minute=1000,
    )
    c = FreshdeskClient(settings, sleep=sleeps)
    yield c
    await c.aclose()


async def test_success_returns_data_and_sends_basic_auth(respx_mock, client):
    route = respx_mock.get(URL).mock(return_value=httpx.Response(200, json={"id": 1}))
    result = await client.get_ticket(1)
    assert result.data == {"id": 1}
    assert result.attempts == 1
    assert route.calls.last.request.headers["authorization"].startswith("Basic ")


@pytest.mark.parametrize("status,code", [
    (400, ErrorCode.INVALID_INPUT),
    (401, ErrorCode.UNAUTHENTICATED),
    (403, ErrorCode.FORBIDDEN),
    (404, ErrorCode.NOT_FOUND),
])
async def test_client_errors_map_and_are_never_retried(respx_mock, client, sleeps, status, code):
    route = respx_mock.get(URL).mock(return_value=httpx.Response(status, json={"description": "x"}))
    with pytest.raises(FreshdeskError) as exc:
        await client.get_ticket(1)
    assert exc.value.code == code
    assert exc.value.retryable is False
    assert route.call_count == 1      # no retry
    assert sleeps.calls == []


async def test_429_then_success_respects_retry_after(respx_mock, client, sleeps):
    route = respx_mock.get(URL)
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "2"}),
        httpx.Response(200, json={"id": 1}),
    ]
    result = await client.get_ticket(1)
    assert result.attempts == 2
    assert route.call_count == 2
    assert sleeps.calls == [2]


async def test_429_forever_gives_up_after_max_retries(respx_mock, client):
    route = respx_mock.get(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "1"}))
    with pytest.raises(FreshdeskError) as exc:
        await client.get_ticket(1)
    assert exc.value.code == ErrorCode.RATE_LIMITED
    assert exc.value.retryable is True
    assert route.call_count == 3       # 1 try + 2 retries


async def test_429_with_huge_retry_after_fails_fast(respx_mock, client, sleeps):
    route = respx_mock.get(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "120"}))
    with pytest.raises(FreshdeskError) as exc:
        await client.get_ticket(1)
    assert exc.value.code == ErrorCode.RATE_LIMITED
    assert exc.value.retry_after_seconds == 120
    assert route.call_count == 1       # did not hang waiting
    assert sleeps.calls == []


async def test_500_then_success(respx_mock, client, sleeps):
    route = respx_mock.get(URL)
    route.side_effect = [httpx.Response(500), httpx.Response(200, json={"id": 1})]
    result = await client.get_ticket(1)
    assert result.attempts == 2
    assert len(sleeps.calls) == 1


async def test_500_forever_becomes_upstream_error(respx_mock, client):
    route = respx_mock.get(URL).mock(return_value=httpx.Response(500))
    with pytest.raises(FreshdeskError) as exc:
        await client.get_ticket(1)
    assert exc.value.code == ErrorCode.UPSTREAM_ERROR
    assert route.call_count == 3


async def test_timeout_is_retried_then_reported(respx_mock, client):
    route = respx_mock.get(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(FreshdeskError) as exc:
        await client.get_ticket(1)
    assert exc.value.code == ErrorCode.TIMEOUT
    assert exc.value.retryable is True
    assert route.call_count == 3


async def test_connection_error_becomes_upstream_error(respx_mock, client):
    respx_mock.get(URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(FreshdeskError) as exc:
        await client.get_ticket(1)
    assert exc.value.code == ErrorCode.UPSTREAM_ERROR


async def test_pagination_link_header(respx_mock, client):
    respx_mock.get("http://mock/api/v2/tickets").mock(side_effect=[
        httpx.Response(200, json=[], headers={"Link": '<http://x?page=2>; rel="next"'}),
        httpx.Response(200, json=[]),
    ])
    assert (await client.list_tickets(page=1)).has_more is True
    assert (await client.list_tickets(page=2)).has_more is False


async def test_none_params_are_not_sent(respx_mock, client):
    route = respx_mock.get("http://mock/api/v2/tickets").mock(return_value=httpx.Response(200, json=[]))
    await client.list_tickets(page=1, per_page=5)
    url = str(route.calls.last.request.url)
    assert "email" not in url and "updated_since" not in url


async def test_error_never_leaks_upstream_body(respx_mock, client):
    respx_mock.get(URL).mock(return_value=httpx.Response(404, json={"description": "SECRET customer@x.com"}))
    with pytest.raises(FreshdeskError) as exc:
        await client.get_ticket(1)
    assert "SECRET" not in exc.value.message


async def test_rate_limiter_delays_calls_over_the_limit():
    limiter = RateLimiter(max_calls=2, period=0.3)
    start = time.monotonic()
    for _ in range(3):
        await limiter.acquire()
    assert time.monotonic() - start >= 0.25   # 3rd call had to wait for the window


def test_live_mode_rejects_placeholder_key():
    with pytest.raises(ValueError):
        Settings(_env_file=None, freshdesk_mode="live", freshdesk_api_key="changeme")