"""MCP layer. Thin on purpose: describe the tools, hand everything to ToolRunner."""
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .audit import AuditLogger
from .config import Settings
from .freshdesk_client import FreshdeskClient
from .schemas import GetTicketInput, ListTicketsInput, SearchTicketsInput
from .security import Principal
from .ticket_service import TicketService
from .tool_runner import ToolRunner


def build_server(service: TicketService, runner: ToolRunner) -> FastMCP:
    mcp = FastMCP("freshdesk-connector")

    @mcp.tool()
    async def list_tickets(
        status: Optional[str] = None,
        updated_since: Optional[str] = None,
        page: int = 1,
        per_page: int = 30,
        include_description: bool = False,
    ) -> dict:
        """List Freshdesk tickets, newest first. READ-ONLY.

        status: open | pending | resolved | closed (optional). When set, results come from
        search: per_page is ignored (fixed 30), page max is 10, updated_since cannot be combined.
        updated_since: ISO datetime. page: 1-300. per_page: 1-100.
        include_description: default false. When true the customer message is returned with
        emails/phone numbers masked and cut to 500 characters.
        Returns tickets, page, has_more (and total when filtering by status), or an error
        object with error_code, message, retryable.
        """
        return await runner.run("list_tickets", ListTicketsInput, dict(
            status=status, updated_since=updated_since, page=page, per_page=per_page,
            include_description=include_description), service.list_tickets)

    @mcp.tool()
    async def get_ticket(ticket_id: int, include_description: bool = False) -> dict:
        """Get one Freshdesk ticket by numeric id. READ-ONLY.

        include_description: default false; when true the message is masked and truncated.
        Returns id, subject, status, priority, requester_id, created_at, updated_at,
        or an error object (not_found if the ticket does not exist).
        """
        return await runner.run("get_ticket", GetTicketInput, dict(
            ticket_id=ticket_id, include_description=include_description), service.get_ticket)

    @mcp.tool()
    async def search_tickets(
        requester_email: Optional[str] = None,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        tag: Optional[str] = None,
        page: int = 1,
        include_description: bool = False,
    ) -> dict:
        """Search Freshdesk tickets. READ-ONLY. At least one filter is required.

        requester_email: customer's email. status: open | pending | resolved | closed.
        priority: low | medium | high | urgent. tag: a single tag. page: 1-10 (30 per page).
        Filters are combined with AND. Returns tickets, page, has_more, total,
        or an error object with error_code, message, retryable.
        """
        return await runner.run("search_tickets", SearchTicketsInput, dict(
            requester_email=requester_email, status=status, priority=priority, tag=tag,
            page=page, include_description=include_description), service.search_tickets)

    return mcp


def main() -> None:
    s = Settings()
    principal = Principal(
        tenant_id=s.connector_tenant_id,
        credential_id=s.connector_credential_id,
        permissions=frozenset(p.strip() for p in s.connector_permissions.split(",") if p.strip()),
    )
    client = FreshdeskClient(s)
    service = TicketService(client, s.cache_ttl_seconds)
    runner = ToolRunner(AuditLogger(s.audit_log_path or None), principal)
    build_server(service, runner).run()      # stdio transport


if __name__ == "__main__":
    main()