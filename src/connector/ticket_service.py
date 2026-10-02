"""Business logic: permissions, validation, routing, normalization, caching."""
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from pydantic import BaseModel, ValidationError

from .cache import ResponseCache
from .errors import FreshdeskError
from .freshdesk_client import FreshdeskClient
from .schemas import (
    CODE_TO_PRIORITY, CODE_TO_STATUS, PRIORITY_TO_CODE, STATUS_TO_CODE, ErrorCode,
    GetTicketInput, ListTicketsInput, SearchTicketsInput, Ticket, TicketList, TicketStatus,
)
from .security import TICKETS_READ, Principal, redact_pii, require, truncate

MAX_DESCRIPTION_CHARS = 500
SEARCH_PAGE_SIZE = 30     # verify against Freshdesk docs
SEARCH_MAX_PAGE = 10      # verify against Freshdesk docs


def _invalid(message: str) -> FreshdeskError:
    return FreshdeskError(ErrorCode.INVALID_INPUT, message, retryable=False)


def _bad_upstream() -> FreshdeskError:
    return FreshdeskError(ErrorCode.UPSTREAM_ERROR,
                          "Unexpected response format from Freshdesk", retryable=False)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_ticket(raw: Any, include_description: bool) -> Ticket:
    """Raw Freshdesk JSON -> our clean Ticket. Unknown extras are dropped."""
    try:
        description = None
        if include_description:
            description = truncate(redact_pii(raw.get("description_text") or ""),
                                   MAX_DESCRIPTION_CHARS)
        return Ticket(
            id=raw["id"],
            subject=raw["subject"],
            status=CODE_TO_STATUS.get(raw["status"], TicketStatus.OTHER),
            priority=CODE_TO_PRIORITY[raw["priority"]],
            requester_id=raw.get("requester_id"),
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
            description=description,
        )
    except (KeyError, TypeError, AttributeError, ValidationError) as exc:
        raise _bad_upstream() from exc


class TicketService:
    def __init__(self, client: FreshdeskClient, cache_ttl_seconds: float = 30,
                 clock: Callable[[], float] = time.monotonic):
        self._client = client
        self._cache = ResponseCache(cache_ttl_seconds, clock)

    # ---------- public tools ----------
    async def get_ticket(self, principal: Principal, inp: GetTicketInput,
                         request_id: Optional[str] = None) -> Ticket:
        require(principal, TICKETS_READ)

        async def fetch() -> Ticket:
            resp = await self._client.get_ticket(inp.ticket_id, request_id)
            return normalize_ticket(resp.data, inp.include_description)

        return await self._cached(principal, "get_ticket", inp, fetch)

    async def list_tickets(self, principal: Principal, inp: ListTicketsInput,
                           request_id: Optional[str] = None) -> TicketList:
        require(principal, TICKETS_READ)
        if inp.status is not None:
            # Freshdesk's list endpoint cannot filter by status, so we use search.
            self._status_code(inp.status)
            if inp.updated_since is not None:
                raise _invalid("status and updated_since cannot be combined")
            if inp.page > SEARCH_MAX_PAGE:
                raise _invalid(f"page must be <= {SEARCH_MAX_PAGE} when filtering by status")

        async def fetch() -> TicketList:
            if inp.status is not None:
                query = f"status:{STATUS_TO_CODE[inp.status]}"
                return await self._run_search(query, inp.page, inp.include_description, request_id)
            resp = await self._client.list_tickets(
                page=inp.page, per_page=inp.per_page,
                updated_since=_iso(inp.updated_since) if inp.updated_since else None,
                request_id=request_id)
            if not isinstance(resp.data, list):
                raise _bad_upstream()
            return TicketList(
                tickets=[normalize_ticket(t, inp.include_description) for t in resp.data],
                page=inp.page, has_more=resp.has_more)

        return await self._cached(principal, "list_tickets", inp, fetch)

    async def search_tickets(self, principal: Principal, inp: SearchTicketsInput,
                             request_id: Optional[str] = None) -> TicketList:
        require(principal, TICKETS_READ)
        parts = []
        if inp.status is not None:
            parts.append(f"status:{self._status_code(inp.status)}")
        if inp.priority is not None:
            parts.append(f"priority:{PRIORITY_TO_CODE[inp.priority]}")
        if inp.tag is not None:
            self._no_quotes(inp.tag, "tag")
            parts.append(f"tag:'{inp.tag}'")
        if inp.requester_email is not None:
            self._no_quotes(inp.requester_email, "requester_email")
            parts.append(f"email:'{inp.requester_email.lower()}'")
        query = " AND ".join(parts)

        async def fetch() -> TicketList:
            return await self._run_search(query, inp.page, inp.include_description, request_id)

        return await self._cached(principal, "search_tickets", inp, fetch)

    # ---------- helpers ----------
    async def _run_search(self, query: str, page: int, include_description: bool,
                          request_id: Optional[str]) -> TicketList:
        resp = await self._client.search_tickets(f'"{query}"', page, request_id)
        try:
            results, total = resp.data["results"], int(resp.data["total"])
        except (KeyError, TypeError, ValueError):
            raise _bad_upstream() from None
        return TicketList(
            tickets=[normalize_ticket(t, include_description) for t in results],
            page=page,
            has_more=page < SEARCH_MAX_PAGE and page * SEARCH_PAGE_SIZE < total,
            total=total)

    @staticmethod
    def _status_code(status: TicketStatus) -> int:
        if status == TicketStatus.OTHER:
            raise _invalid("'other' is an output-only status and cannot be used as a filter")
        return STATUS_TO_CODE[status]

    @staticmethod
    def _no_quotes(value: str, name: str) -> None:
        if "'" in value or '"' in value:
            raise _invalid(f"{name} must not contain quote characters")

    async def _cached(self, principal: Principal, tool: str, inp: BaseModel, fetch):
        key = (principal.tenant_id, principal.credential_id, tool, inp.model_dump_json())
        return await self._cache.get_or_fetch(key, fetch)