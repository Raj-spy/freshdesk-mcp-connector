"""Fake Freshdesk API for local dev, tests and demos. Fake data only."""
import asyncio
import os
import random
import re
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

API_KEY = os.getenv("MOCK_API_KEY", "mock-key")
SLOW_SECONDS = float(os.getenv("MOCK_SLOW_SECONDS", "15"))
LIST_PAGE_DEFAULT, LIST_PAGE_MAX = 30, 100
SEARCH_PAGE_SIZE, SEARCH_MAX_PAGE = 30, 10

app = FastAPI(title="Mock Freshdesk")
security = HTTPBasic(auto_error=False)

# Failure injection + request counter (used by demos and tests)
STATE = {"fail_mode": None, "fail_remaining": 0, "hits": 0}

# ---------- Fake data (seeded, so it is identical on every run) ----------
REQUESTERS = {
    101: "asha@example.com", 102: "ravi@example.com",
    103: "meera@example.com", 104: "kabir@example.com",
}
SUBJECTS = [
    "Refund not received", "Cannot log in", "Invoice mismatch",
    "Order stuck in processing", "Wrong item delivered", "Need GST invoice",
    "Payment failed but amount deducted", "Change delivery address",
    "Product warranty question", "Account locked",
]
TAGS = ["billing", "login", "shipping", "refund", "warranty"]


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed() -> list[dict]:
    rnd = random.Random(42)
    base = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    tickets = []
    for i in range(1, 21):
        created = base - timedelta(hours=6 * (21 - i))
        updated = created + timedelta(hours=rnd.randint(1, 5))
        rid = rnd.choice(list(REQUESTERS))
        tickets.append({
            "id": i,
            "subject": SUBJECTS[(i - 1) % len(SUBJECTS)],
            "status": rnd.choice([2, 3, 4, 5]),
            "priority": rnd.choice([1, 2, 3, 4]),
            "requester_id": rid,
            "tags": [rnd.choice(TAGS)],
            "created_at": _fmt(created),
            "updated_at": _fmt(updated),
            "description_text": f"Fake customer message for ticket {i}. " * 20,
            "_email": REQUESTERS[rid],  # internal only, never returned
        })
    tickets.sort(key=lambda t: t["created_at"], reverse=True)  # newest first
    return tickets


TICKETS = _seed()


def _public(t: dict) -> dict:
    return {k: v for k, v in t.items() if not k.startswith("_")}


def _page(items: list, page: int, per_page: int):
    start = (page - 1) * per_page
    return items[start:start + per_page], start + per_page < len(items)


# ---------- Auth + failure injection (runs before every /api/v2 call) ----------
async def gate(request: Request, creds: HTTPBasicCredentials | None = Depends(security)):
    STATE["hits"] += 1
    if creds is None or creds.username != API_KEY:
        raise HTTPException(401, "Authentication failed")

    mode = request.headers.get("x-mock-fail")  # per-request override
    if not mode and STATE["fail_mode"] and STATE["fail_remaining"] != 0:
        mode = STATE["fail_mode"]
        if STATE["fail_remaining"] > 0:
            STATE["fail_remaining"] -= 1

    if mode == "429":
        raise HTTPException(429, "Rate limit exceeded", headers={"Retry-After": "2"})
    if mode == "500":
        raise HTTPException(500, "Internal server error")
    if mode == "slow":
        await asyncio.sleep(SLOW_SECONDS)


@app.exception_handler(HTTPException)
async def error_shape(_: Request, exc: HTTPException):
    # Freshdesk-style error body
    return JSONResponse({"description": exc.detail}, status_code=exc.status_code,
                        headers=getattr(exc, "headers", None))


# ---------- Freshdesk-like endpoints ----------
@app.get("/api/v2/tickets", dependencies=[Depends(gate)])
async def list_tickets(
    response: Response, page: int = 1, per_page: int = LIST_PAGE_DEFAULT,
    updated_since: str | None = None, email: str | None = None,
    requester_id: int | None = None,
):
    if page < 1 or not 1 <= per_page <= LIST_PAGE_MAX:
        raise HTTPException(400, "Validation failed: invalid page or per_page")
    items = TICKETS
    if updated_since:
        items = [t for t in items if t["updated_at"] >= updated_since]
    if email:
        items = [t for t in items if t["_email"] == email.lower()]
    if requester_id:
        items = [t for t in items if t["requester_id"] == requester_id]
    chunk, more = _page(items, page, per_page)
    if more:
        response.headers["Link"] = (
            f'<http://mock/api/v2/tickets?page={page + 1}&per_page={per_page}>; rel="next"')
    return [_public(t) for t in chunk]


@app.get("/api/v2/tickets/{ticket_id}", dependencies=[Depends(gate)])
async def get_ticket(ticket_id: int):
    for t in TICKETS:
        if t["id"] == ticket_id:
            return _public(t)
    raise HTTPException(404, "Record not found")


CLAUSE = re.compile(r"(\w+):(?:'([^']*)'|(\d+))")


@app.get("/api/v2/search/tickets", dependencies=[Depends(gate)])
async def search_tickets(query: str = "", page: int = 1):
    q = query.strip().strip('"')
    clauses = CLAUSE.findall(q)
    if not clauses:
        raise HTTPException(400, "Validation failed: invalid query")
    if not 1 <= page <= SEARCH_MAX_PAGE:
        raise HTTPException(400, f"Validation failed: page must be 1-{SEARCH_MAX_PAGE}")

    items = TICKETS
    for field, quoted, number in clauses:
        value = quoted or number
        if field == "status":
            items = [t for t in items if str(t["status"]) == value]
        elif field == "priority":
            items = [t for t in items if str(t["priority"]) == value]
        elif field == "tag":
            items = [t for t in items if value in t["tags"]]
        elif field == "email":
            items = [t for t in items if t["_email"] == value.lower()]
        else:
            raise HTTPException(400, f"Unsupported search field: {field}")

    chunk, _ = _page(items, page, SEARCH_PAGE_SIZE)
    return {"results": [_public(t) for t in chunk], "total": len(items)}


# ---------- Mock control endpoints (not part of real Freshdesk) ----------
@app.post("/_mock/fail")
async def set_failure(mode: str = "none", count: int = 1):
    """mode: 429 | 500 | slow | none. count = next N requests (-1 = forever)."""
    STATE["fail_mode"] = None if mode == "none" else mode
    STATE["fail_remaining"] = count
    return STATE


@app.get("/_mock/stats")
async def stats():
    return STATE


@app.post("/_mock/reset")
async def reset():
    STATE.update(fail_mode=None, fail_remaining=0, hits=0)
    return STATE