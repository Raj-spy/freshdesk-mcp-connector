# Freshdesk MCP Connector (read-only)

A private connector that lets an Agent Studio agent **read Freshdesk tickets** over MCP:
`list_tickets`, `get_ticket`, `search_tickets`.

It is built to be correct, reliable and safe first: API-key auth, input validation,
permission checks, rate-limit and failure handling (retry, backoff, timeouts), PII masking,
caching and an audit log. No write operations exist.

> **Status:** verified end to end against the included mock Freshdesk server (55 automated
> tests plus a scripted demo of 429 / 500 / timeout / bad key). The live-Freshdesk mode is
> implemented but has **not** been verified against a real account. See Limitations.

## Quick start (about 2 minutes)

Requires Python 3.12. No Freshdesk account needed.

```bash
git clone <this-repo> && cd freshdesk-mcp-connector
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env

bash scripts/run_demo.sh      # starts a mock Freshdesk, runs the demo, stops it
pytest -q                     # 55 tests
```

`docs/demo-output.txt` contains the output of the demo, so you can read it without running.

The demo uses a real MCP client over stdio against the real connector, then breaks things
on purpose: two 429s then success, endless 429, endless 500, a hanging server, a wrong API
key, a caller without permission, invalid input, and a repeated request served from cache.

## Using it with an MCP client

Run the server over stdio:

```bash
PYTHONPATH=. python -m src.connector.mcp_server
```

Typical client configuration:

```json
{
  "mcpServers": {
    "freshdesk": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "src.connector.mcp_server"],
      "cwd": "/path/to/freshdesk-mcp-connector",
      "env": {
        "PYTHONPATH": ".",
        "FRESHDESK_MODE": "live",
        "FRESHDESK_DOMAIN": "yourcompany.freshdesk.com",
        "FRESHDESK_API_KEY": "<from your secret store>",
        "CONNECTOR_TENANT_ID": "merchant-123",
        "CONNECTOR_CREDENTIAL_ID": "cred-1",
        "CONNECTOR_PERMISSIONS": "tickets:read"
      }
    }
  }
}
```

Tool definitions (names, descriptions, JSON schemas) are in `docs/mcp-tools.json`.
I verified the server with the reference MCP Python client over stdio only; I have not tried
it inside Agent Studio itself.

## Authentication

API-key flow. The key comes from the `FRESHDESK_API_KEY` environment variable and is sent as
HTTP Basic auth (key as username, `X` as password), which is Freshdesk's documented scheme.
The key is held as a secret value (never printed), is never returned to the agent, and never
appears in logs or error messages. In live mode the connector refuses to start with a
placeholder key. There is no OAuth flow; Freshdesk uses API keys.

To use a real account: set `FRESHDESK_MODE=live`, `FRESHDESK_DOMAIN`, `FRESHDESK_API_KEY`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| FRESHDESK_MODE | mock | `mock` or `live` |
| FRESHDESK_DOMAIN | yourcompany.freshdesk.com | Live account domain |
| FRESHDESK_API_KEY | mock-key | API key (live mode rejects placeholders) |
| MOCK_BASE_URL | http://localhost:9000 | Mock server address |
| REQUEST_TIMEOUT_SECONDS | 10 | Per-request timeout |
| MAX_RETRIES | 3 | Retries after the first attempt |
| MAX_RETRY_AFTER_SECONDS | 30 | Longest `Retry-After` we will wait; longer fails fast |
| RATE_LIMIT_PER_MINUTE | 100 | Client-side limit; set below your Freshdesk plan's limit |
| CACHE_TTL_SECONDS | 30 | In-memory cache lifetime; 0 disables |
| CONNECTOR_TENANT_ID / CONNECTOR_CREDENTIAL_ID | demo-tenant / demo-cred | Identity used for cache scoping and the audit log |
| CONNECTOR_PERMISSIONS | tickets:read | Comma-separated permissions granted to this connector |
| AUDIT_LOG_PATH | (empty) | Optional file for audit lines (they always go to stderr) |

## How it works

```
Agent Studio -> MCP -> ToolRunner -> TicketService -> FreshdeskClient -> Freshdesk
                       (validate,    (permission,     (auth, timeout,
                        errors,       routing, cache,  retry, rate limit)
                        audit)        normalization)
```

Details in `docs/architecture.md`. What the agent can and cannot do is in
`docs/agent-capabilities.md`. Per-tool inputs and outputs are in `docs/tool-spec.md`.

## Assumptions

- One connector process serves one tenant and one credential, configured by environment.
  Agent Studio would run one process per merchant connection.
- Permissions are static for the life of the process.
- Freshdesk behaviours listed under Limitations are taken from its public documentation as I
  understand it and are encoded in one place (`ticket_service.py` and `schemas.py`) so they
  are easy to correct.

## Limitations (and the long-term fix for each)

- **Not verified against live Freshdesk.** Status/priority codes (2-5 and 1-4), the search
  page size (30), search page cap (10), the `email:` search field, the `Retry-After` header on
  429 and the per-plan rate limits are implemented from documentation and checked only
  against the mock. Fix: run the same test suite against a trial account and adjust.
- **Requester-email search may need a different call on real Freshdesk.** If `email:` is not a
  supported search field, the fix is a contact lookup (or the list endpoint's `email`
  filter) before listing tickets.
- **List by status goes through search**, because Freshdesk's list endpoint cannot filter by
  status. Consequences: `per_page` is ignored (30 fixed), max page 10, and `status` cannot be
  combined with `updated_since`.
- **Cache and rate limiter are per process and in memory.** Several replicas would not share
  them. Fix: a shared store (for example Redis) once there is more than one replica.
- **One tenant per process.** Fix: a multi-tenant gateway that resolves credentials per
  request from a secret manager.
- **Permissions are static configuration.** Fix: derive them from Agent Studio's own
  authorization at call time.
- **Audit log is a file/stderr stream.** Fix: ship it to a log store with retention.
- **PII masking is pattern-based** and covers the description only; `subject` is unmasked and
  a long digit sequence (for example a 10-digit order id) may be masked too. Fix: a
  proper PII classifier, or exclude free text entirely.
- **Errors are returned as normal tool results** (with `error_code`), not with MCP's
  `isError` flag. Chosen so the agent always receives `retryable` and `retry_after_seconds`.
- **MCP SDK is pinned to `mcp<2`** (v1 `FastMCP`). Fix: migrate to `MCPServer` in v2.
- **Not containerized.** Run with Python as shown above.
- **Read-only by design.** Writes would need their own review, idempotency and approval design.

## Repository layout

```
src/connector/   config, schemas, errors, freshdesk_client, ticket_service, cache,
                 security, audit, tool_runner, mcp_server
mock_freshdesk/  fake Freshdesk API with failure injection (fake data only)
scripts/         run_demo.sh, demo.py, mcp_client_check.py, export_tool_spec.py
tests/           55 tests (client reliability, service, security, tool runner)
docs/            architecture, tool spec, agent capabilities, demo output
```

All data in this repository is fictional. No credentials are included.