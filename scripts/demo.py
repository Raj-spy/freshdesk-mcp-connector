"""End-to-end demo. An MCP client (standing in for Agent Studio) uses the Freshdesk
connector, then we break things on purpose and show how the connector handles it."""
import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MOCK = os.getenv("MOCK_BASE_URL", "http://localhost:9000")
AUDIT = "demo_audit.log"


def mock(path: str, **params):
    return httpx.post(f"{MOCK}{path}", params=params, timeout=5)


def hits() -> int:
    return httpx.get(f"{MOCK}/_mock/stats", timeout=5).json()["hits"]


@asynccontextmanager
async def connector(**env):
    """Start the connector as a real MCP server (stdio) with optional env overrides."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.connector.mcp_server"],
        env={**os.environ, "PYTHONPATH": ".", "AUDIT_LOG_PATH": AUDIT, **env},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def call(session, tool: str, args: dict) -> str:
    res = await session.call_tool(tool, args)
    return res.content[0].text


def summarize(text: str) -> str:
    try:
        d = json.loads(text)
    except ValueError:
        return text[:200]
    if "error_code" in d:
        wait = f", retry_after={d['retry_after_seconds']}s" if d.get("retry_after_seconds") else ""
        return f"ERROR {d['error_code']} (retryable={d['retryable']}{wait}): {d['message']}"
    if "tickets" in d:
        ids = [t["id"] for t in d["tickets"]]
        return f"{len(ids)} ticket(s) {ids} | total={d.get('total')} has_more={d['has_more']}"
    return f"ticket {d['id']}: {d['subject']} [{d['status']}, {d['priority']}]"


async def scenario(title, note, tool, args, setup=None, env=None):
    print(f"\n=== {title}")
    mock("/_mock/reset")
    if setup:
        setup()
    async with connector(**(env or {})) as session:
        started = time.monotonic()
        text = await call(session, tool, args)
        took = time.monotonic() - started
    print(f"    agent asks : {tool} {json.dumps(args)}")
    print(f"    agent gets : {summarize(text)}")
    print(f"    took {took:.1f}s | Freshdesk (mock) was hit {hits()} time(s)")
    print(f"    proves     : {note}")


async def main():
    if os.path.exists(AUDIT):
        os.remove(AUDIT)

    await scenario(
        "1. Normal request", "happy path, clean normalized output",
        "search_tickets", {"requester_email": "asha@example.com", "status": "open"})

    await scenario(
        "2. Freshdesk rate-limits us twice (429, 429, then OK)",
        "connector waits Retry-After and recovers; the agent never sees the failure",
        "get_ticket", {"ticket_id": 1},
        setup=lambda: mock("/_mock/fail", mode="429", count=2))

    await scenario(
        "3. Freshdesk keeps rate-limiting (429 forever)",
        "bounded retries, then a clear retryable error with retry_after_seconds",
        "get_ticket", {"ticket_id": 1},
        setup=lambda: mock("/_mock/fail", mode="429", count=-1),
        env={"MAX_RETRIES": "2"})

    await scenario(
        "4. Freshdesk is down (500 forever)",
        "bounded retries with backoff, then upstream_error",
        "get_ticket", {"ticket_id": 1},
        setup=lambda: mock("/_mock/fail", mode="500", count=-1),
        env={"MAX_RETRIES": "2"})

    await scenario(
        "5. Freshdesk hangs (timeout)",
        "request timeout enforced, retried once, then timeout error (agent is never left hanging)",
        "get_ticket", {"ticket_id": 1},
        setup=lambda: mock("/_mock/fail", mode="slow", count=-1),
        env={"REQUEST_TIMEOUT_SECONDS": "1", "MAX_RETRIES": "1"})

    await scenario(
        "6. Wrong API key",
        "401 is mapped to unauthenticated and NOT retried",
        "get_ticket", {"ticket_id": 1},
        env={"FRESHDESK_API_KEY": "wrong-key"})

    await scenario(
        "7. Caller without the tickets:read permission",
        "blocked before any Freshdesk call (hits stay at 0)",
        "get_ticket", {"ticket_id": 1},
        env={"CONNECTOR_PERMISSIONS": ""})

    await scenario(
        "8. Invalid input from the agent",
        "validated and rejected before any Freshdesk call (hits stay at 0)",
        "list_tickets", {"per_page": 5000})

    print("\n=== 9. Same request twice in one session")
    mock("/_mock/reset")
    async with connector() as session:
        await call(session, "get_ticket", {"ticket_id": 1})
        first = hits()
        await call(session, "get_ticket", {"ticket_id": 1})
        second = hits()
    print(f"    mock hits after 1st call: {first}, after identical 2nd call: {second}")
    print("    proves     : the second answer came from cache, not from Freshdesk")

    print("\n=== Audit log (who / which tool / outcome / latency, never content or keys)")
    with open(AUDIT) as f:
        for line in f:
            d = json.loads(line)
            print(f"    {d['request_id'][:8]}  {d['tool']:<15} {d['status']:<15} {d['latency_ms']:>8}ms")


asyncio.run(main())
