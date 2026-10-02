import json

import httpx
import pytest

from src.connector.audit import AuditLogger
from src.connector.schemas import GetTicketInput, ListTicketsInput, SearchTicketsInput
from src.connector.security import TICKETS_READ, Principal
from src.connector.ticket_service import TicketService
from src.connector.tool_runner import ToolRunner

GET_URL = "http://mock/api/v2/tickets/1"
P = Principal("tenantA", "cred1", frozenset({TICKETS_READ}))
EXPECTED_KEYS = {"ts", "request_id", "tenant_id", "credential_id", "tool", "status", "latency_ms"}


def raw(desc="Hello"):
    return {"id": 1, "subject": "Subject 1", "status": 2, "priority": 3, "requester_id": 101,
            "created_at": "2026-10-01T10:00:00Z", "updated_at": "2026-10-02T09:00:00Z",
            "description_text": desc}


@pytest.fixture
def lines():
    return []


@pytest.fixture
async def service(fd_client):
    return TicketService(fd_client, cache_ttl_seconds=0)


def runner_for(lines, principal=P):
    return ToolRunner(AuditLogger(sink=lines.append), principal)


async def test_success_is_clean_json_and_audited(respx_mock, service, lines):
    respx_mock.get(GET_URL).mock(return_value=httpx.Response(200, json=raw()))
    out = await runner_for(lines).run("get_ticket", GetTicketInput, {"ticket_id": 1}, service.get_ticket)
    assert out["id"] == 1 and out["status"] == "open"
    assert "description" not in out                      # None fields are dropped
    rec = json.loads(lines[0])
    assert set(rec) == EXPECTED_KEYS
    assert rec["tool"] == "get_ticket" and rec["status"] == "ok"
    assert rec["tenant_id"] == "tenantA" and rec["latency_ms"] >= 0
    assert "Subject 1" not in lines[0]                   # no ticket content in the log


async def test_invalid_input_never_reaches_freshdesk(service, lines):
    # no respx route: a real call would fail the test
    out = await runner_for(lines).run("list_tickets", ListTicketsInput, {"per_page": 5000}, service.list_tickets)
    assert out["error_code"] == "invalid_input"
    assert out["retryable"] is False
    assert "per_page" in out["message"]
    rec = json.loads(lines[0])
    assert rec["status"] == "invalid_input"
    assert out["request_id"] == rec["request_id"]        # agent and log share the same id


async def test_search_without_filters_is_rejected(service, lines):
    out = await runner_for(lines).run("search_tickets", SearchTicketsInput, {}, service.search_tickets)
    assert out["error_code"] == "invalid_input"
    assert "filter" in out["message"].lower()


async def test_missing_permission_is_forbidden_and_audited(service, lines):
    nobody = Principal("tenantA", "cred1")
    out = await runner_for(lines, nobody).run("get_ticket", GetTicketInput, {"ticket_id": 1}, service.get_ticket)
    assert out["error_code"] == "forbidden"
    assert json.loads(lines[0])["status"] == "forbidden"


async def test_upstream_401_is_mapped_and_audited(respx_mock, service, lines):
    respx_mock.get(GET_URL).mock(return_value=httpx.Response(401, json={}))
    out = await runner_for(lines).run("get_ticket", GetTicketInput, {"ticket_id": 1}, service.get_ticket)
    assert out["error_code"] == "unauthenticated"
    assert json.loads(lines[0])["status"] == "unauthenticated"


async def test_unexpected_crash_is_hidden_from_agent(lines):
    async def boom(principal, inp, request_id):
        raise RuntimeError("secret-internal-detail")

    out = await runner_for(lines).run("get_ticket", GetTicketInput, {"ticket_id": 1}, boom)
    assert out["error_code"] == "internal_error"
    assert "secret-internal-detail" not in json.dumps(out)
    assert json.loads(lines[0])["status"] == "internal_error"


async def test_audit_has_no_content_or_keys_even_when_description_requested(respx_mock, service, lines):
    respx_mock.get(GET_URL).mock(return_value=httpx.Response(200, json=raw(desc="SECRET body")))
    out = await runner_for(lines).run(
        "get_ticket", GetTicketInput, {"ticket_id": 1, "include_description": True}, service.get_ticket)
    assert out["description"] == "SECRET body"          # the agent gets it when asked
    assert "SECRET" not in lines[0]                      # the log never does
    assert "test-key" not in lines[0]


def test_audit_file_gets_one_json_line_per_call(tmp_path):
    path = tmp_path / "audit.log"
    audit = AuditLogger(path=str(path))
    for _ in range(2):
        audit.record(request_id="r1", tenant_id="t", credential_id="c",
                     tool="get_ticket", status="ok", latency_ms=1.23)
    rows = path.read_text().strip().splitlines()
    assert len(rows) == 2
    assert set(json.loads(rows[0])) == EXPECTED_KEYS