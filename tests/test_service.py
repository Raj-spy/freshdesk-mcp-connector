import asyncio

import httpx
import pytest

from src.connector.errors import FreshdeskError
from src.connector.schemas import (
    ErrorCode, GetTicketInput, ListTicketsInput, SearchTicketsInput, TicketStatus,
)
from src.connector.security import TICKETS_READ, Principal
from src.connector.ticket_service import MAX_DESCRIPTION_CHARS, TicketService

GET_URL = "http://mock/api/v2/tickets/1"
LIST_URL = "http://mock/api/v2/tickets"
SEARCH_URL = "http://mock/api/v2/search/tickets"

P = Principal("tenantA", "cred1", frozenset({TICKETS_READ}))


def raw(id=1, status=2, priority=3, desc="Hello"):
    return {
        "id": id, "subject": f"Subject {id}", "status": status, "priority": priority,
        "requester_id": 101, "created_at": "2026-10-01T10:00:00Z",
        "updated_at": "2026-10-02T09:00:00Z", "description_text": desc,
    }


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
async def service(fd_client, clock):
    return TicketService(fd_client, cache_ttl_seconds=30, clock=clock)


# ---------- normalization ----------
async def test_codes_become_readable_names(respx_mock, service):
    respx_mock.get(GET_URL).mock(return_value=httpx.Response(200, json=raw(status=3, priority=4)))
    t = await service.get_ticket(P, GetTicketInput(ticket_id=1))
    assert t.status == TicketStatus.PENDING
    assert t.priority.value == "urgent"


async def test_description_not_returned_by_default(respx_mock, service):
    respx_mock.get(GET_URL).mock(return_value=httpx.Response(200, json=raw(desc="SECRET body")))
    t = await service.get_ticket(P, GetTicketInput(ticket_id=1))
    assert t.description is None
    assert "SECRET" not in t.model_dump_json()


async def test_description_is_redacted_and_truncated_when_requested(respx_mock, service):
    desc = "Call +91 98765 43210 or mail raj@example.com. " + "x" * 1000
    respx_mock.get(GET_URL).mock(return_value=httpx.Response(200, json=raw(desc=desc)))
    t = await service.get_ticket(P, GetTicketInput(ticket_id=1, include_description=True))
    assert "raj@example.com" not in t.description
    assert "98765" not in t.description
    assert len(t.description) <= MAX_DESCRIPTION_CHARS + 1


async def test_unknown_status_code_becomes_other(respx_mock, service):
    respx_mock.get(GET_URL).mock(return_value=httpx.Response(200, json=raw(status=7)))
    t = await service.get_ticket(P, GetTicketInput(ticket_id=1))
    assert t.status == TicketStatus.OTHER


async def test_malformed_upstream_ticket_is_reported(respx_mock, service):
    respx_mock.get(GET_URL).mock(return_value=httpx.Response(200, json={"id": 1}))
    with pytest.raises(FreshdeskError) as exc:
        await service.get_ticket(P, GetTicketInput(ticket_id=1))
    assert exc.value.code == ErrorCode.UPSTREAM_ERROR


# ---------- permissions ----------
async def test_no_permission_means_no_downstream_call(service):
    # No respx route is defined: any real call would fail the test.
    nobody = Principal("tenantA", "cred1")
    with pytest.raises(FreshdeskError) as exc:
        await service.get_ticket(nobody, GetTicketInput(ticket_id=1))
    assert exc.value.code == ErrorCode.FORBIDDEN


async def test_not_found_passes_through(respx_mock, service):
    respx_mock.get(GET_URL).mock(return_value=httpx.Response(404, json={}))
    with pytest.raises(FreshdeskError) as exc:
        await service.get_ticket(P, GetTicketInput(ticket_id=1))
    assert exc.value.code == ErrorCode.NOT_FOUND


# ---------- list ----------
async def test_list_without_status_uses_list_endpoint(respx_mock, service):
    route = respx_mock.get(LIST_URL).mock(return_value=httpx.Response(
        200, json=[raw(1), raw(2)], headers={"Link": '<http://x?page=2>; rel="next"'}))
    result = await service.list_tickets(P, ListTicketsInput(page=1, per_page=2))
    assert len(result.tickets) == 2
    assert result.has_more is True
    assert route.calls.last.request.url.params["per_page"] == "2"


async def test_list_with_status_is_routed_to_search(respx_mock, service):
    # Only the search route exists: using the list endpoint would fail the test.
    route = respx_mock.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": [raw(1)], "total": 1}))
    result = await service.list_tickets(P, ListTicketsInput(status="open"))
    assert result.total == 1
    assert route.calls.last.request.url.params["query"] == '"status:2"'


async def test_list_status_with_updated_since_is_rejected(service):
    with pytest.raises(FreshdeskError) as exc:
        await service.list_tickets(
            P, ListTicketsInput(status="open", updated_since="2026-10-01T00:00:00Z"))
    assert exc.value.code == ErrorCode.INVALID_INPUT


async def test_other_cannot_be_used_as_a_filter(service):
    with pytest.raises(FreshdeskError) as exc:
        await service.list_tickets(P, ListTicketsInput(status="other"))
    assert exc.value.code == ErrorCode.INVALID_INPUT


# ---------- search ----------
async def test_search_builds_query_from_all_filters(respx_mock, service):
    route = respx_mock.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": [], "total": 0}))
    await service.search_tickets(P, SearchTicketsInput(
        status="open", priority="high", tag="billing", requester_email="Asha@Example.com"))
    assert route.calls.last.request.url.params["query"] == (
        "\"status:2 AND priority:3 AND tag:'billing' AND email:'asha@example.com'\"")


async def test_search_rejects_quote_injection(service):
    with pytest.raises(FreshdeskError) as exc:
        await service.search_tickets(P, SearchTicketsInput(tag="x' OR status:2 OR tag:'y"))
    assert exc.value.code == ErrorCode.INVALID_INPUT


@pytest.mark.parametrize("total,page,expected", [
    (45, 1, True), (30, 1, False), (45, 2, False), (500, 10, False),
])
async def test_search_has_more(respx_mock, service, total, page, expected):
    respx_mock.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": [raw(1)], "total": total}))
    result = await service.search_tickets(P, SearchTicketsInput(status="open", page=page))
    assert result.has_more is expected
    assert result.total == total


# ---------- cache ----------
async def test_same_request_twice_hits_freshdesk_once(respx_mock, service):
    route = respx_mock.get(GET_URL).mock(return_value=httpx.Response(200, json=raw()))
    a = await service.get_ticket(P, GetTicketInput(ticket_id=1))
    b = await service.get_ticket(P, GetTicketInput(ticket_id=1))
    assert a == b
    assert route.call_count == 1


async def test_different_include_description_is_a_different_cache_entry(respx_mock, service):
    route = respx_mock.get(GET_URL).mock(return_value=httpx.Response(200, json=raw()))
    await service.get_ticket(P, GetTicketInput(ticket_id=1))
    await service.get_ticket(P, GetTicketInput(ticket_id=1, include_description=True))
    assert route.call_count == 2


async def test_cache_is_not_shared_between_tenants(respx_mock, service):
    route = respx_mock.get(GET_URL).mock(return_value=httpx.Response(200, json=raw()))
    other = Principal("tenantB", "cred9", frozenset({TICKETS_READ}))
    await service.get_ticket(P, GetTicketInput(ticket_id=1))
    await service.get_ticket(other, GetTicketInput(ticket_id=1))
    assert route.call_count == 2


async def test_cache_expires_after_ttl(respx_mock, service, clock):
    route = respx_mock.get(GET_URL).mock(return_value=httpx.Response(200, json=raw()))
    await service.get_ticket(P, GetTicketInput(ticket_id=1))
    clock.now += 31
    await service.get_ticket(P, GetTicketInput(ticket_id=1))
    assert route.call_count == 2


async def test_parallel_identical_requests_share_one_call(respx_mock, service):
    route = respx_mock.get(GET_URL).mock(return_value=httpx.Response(200, json=raw()))
    results = await asyncio.gather(
        *[service.get_ticket(P, GetTicketInput(ticket_id=1)) for _ in range(5)])
    assert len({r.id for r in results}) == 1
    assert route.call_count == 1


async def test_errors_are_not_cached(respx_mock, service):
    route = respx_mock.get(GET_URL)
    route.side_effect = [httpx.Response(404, json={}), httpx.Response(200, json=raw())]
    with pytest.raises(FreshdeskError):
        await service.get_ticket(P, GetTicketInput(ticket_id=1))
    t = await service.get_ticket(P, GetTicketInput(ticket_id=1))
    assert t.id == 1
    assert route.call_count == 2


async def test_ttl_zero_disables_cache(respx_mock, fd_client):
    svc = TicketService(fd_client, cache_ttl_seconds=0)
    route = respx_mock.get(GET_URL).mock(return_value=httpx.Response(200, json=raw()))
    await svc.get_ticket(P, GetTicketInput(ticket_id=1))
    await svc.get_ticket(P, GetTicketInput(ticket_id=1))
    assert route.call_count == 2