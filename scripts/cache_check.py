"""Phase 4 live check: cache, routing, permissions, PII, proven with the mock's hit counter."""
import asyncio

import httpx

from src.connector.config import Settings
from src.connector.errors import FreshdeskError
from src.connector.freshdesk_client import FreshdeskClient
from src.connector.schemas import GetTicketInput, ListTicketsInput, SearchTicketsInput
from src.connector.security import TICKETS_READ, Principal
from src.connector.ticket_service import TicketService

MOCK = "http://localhost:9000"


def hits() -> int:
    return httpx.get(f"{MOCK}/_mock/stats").json()["hits"]


async def main():
    httpx.post(f"{MOCK}/_mock/reset")
    client = FreshdeskClient(Settings())
    svc = TicketService(client, cache_ttl_seconds=30)
    p = Principal("demo-tenant", "demo-cred", frozenset({TICKETS_READ}))

    t = await svc.get_ticket(p, GetTicketInput(ticket_id=1))
    print("1. first call        ->", t.subject, "| mock hits:", hits())

    await svc.get_ticket(p, GetTicketInput(ticket_id=1))
    print("2. same call again   -> mock hits:", hits(), "(should not increase)")

    await asyncio.gather(*[svc.search_tickets(p, SearchTicketsInput(status="open")) for _ in range(5)])
    print("3. 5 parallel search -> mock hits:", hits(), "(+1 only)")

    r = await svc.list_tickets(p, ListTicketsInput(status="open"))
    print("4. list by status    ->", len(r.tickets), "tickets, total:", r.total, "| mock hits:", hits())

    print("5. description       ->", t.description, "(None = not leaked)")

    try:
        await svc.get_ticket(Principal("demo-tenant", "demo-cred"), GetTicketInput(ticket_id=2))
    except FreshdeskError as e:
        print("6. no permission     ->", e.code.value, "| mock hits:", hits(), "(should not increase)")

    await client.aclose()


asyncio.run(main())