"""Dump the MCP tool definitions (names, descriptions, JSON schemas) for the docs."""
import asyncio
import json

from src.connector.mcp_server import build_server


async def main():
    server = build_server(service=None, runner=None)   # handlers are never called here
    tools = await server.list_tools()
    spec = [{"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
            for t in tools]
    with open("docs/mcp-tools.json", "w") as f:
        json.dump(spec, f, indent=2)
    print("wrote docs/mcp-tools.json with", len(spec), "tools")


asyncio.run(main())