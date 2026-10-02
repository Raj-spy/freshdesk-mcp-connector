"""Phase 5 check: talk to the connector over the real MCP protocol (stdio)."""
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    if os.path.exists("audit.log"):
        os.remove("audit.log")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.connector.mcp_server"],
        env={**os.environ, "PYTHONPATH": ".", "AUDIT_LOG_PATH": "audit.log"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("tools:", [t.name for t in tools.tools])

            calls = [
                ("get_ticket", {"ticket_id": 1}),
                ("search_tickets", {"status": "open"}),
                ("get_ticket", {"ticket_id": 999}),
                ("list_tickets", {"per_page": 5000}),
                ("search_tickets", {}),
            ]
            for name, args in calls:
                res = await session.call_tool(name, args)
                print(f"\n{name} {args}\n  ->", res.content[0].text[:160])

    print("\n--- audit.log ---")
    print(open("audit.log").read())


asyncio.run(main())