# Architecture

## Layers

```
Agent Studio
    |  MCP over stdio
    v
mcp_server.py      Tool names, descriptions, argument shapes. No logic.
    v
tool_runner.py     Validates input, runs the handler, maps every outcome to a safe
                   response, writes exactly one audit line per call.
    v
ticket_service.py  Permission check -> cache -> Freshdesk call -> normalization.
                   Routes list-by-status to search. Masks and truncates descriptions.
    v
freshdesk_client.py  Auth, timeout, error mapping, retry with backoff, rate limiter.
    v
Freshdesk (or the mock)
```

Cross-cutting: `security.py` (permissions, masking), `audit.py`, `cache.py`, `config.py`,
`schemas.py` (the contract), `errors.py`.

## Life of one request

1. The agent calls a tool. `mcp_server` hands the raw arguments to `ToolRunner`.
2. `ToolRunner` validates them against the schema. Bad input returns `invalid_input` and
   never reaches Freshdesk.
3. `TicketService` checks the `tickets:read` permission. No permission returns `forbidden`
   before any cache or network access.
4. The cache is consulted (key: tenant, credential, tool, input). A hit returns immediately.
   If an identical request is already in flight, this one waits for it instead of making a
   second call.
5. `FreshdeskClient` takes a slot from the rate limiter, sends the request with a timeout,
   and retries when it is safe (see below).
6. The raw JSON is normalized into the ticket contract. Unexpected shapes become
   `upstream_error` instead of a crash.
7. `ToolRunner` returns the result or an error object and writes the audit line
   (`request_id`, tenant, credential, tool, status, latency).

## Failure handling

| Situation | Behaviour |
|---|---|
| 429 with `Retry-After` <= 30s | Wait that long, retry, up to `MAX_RETRIES` |
| 429 with `Retry-After` > 30s | Fail fast with `rate_limited` and `retry_after_seconds` |
| 429 without the header | Exponential backoff with jitter |
| 5xx, timeout, connection error | Exponential backoff with jitter, up to `MAX_RETRIES`, then `upstream_error` or `timeout` |
| 400, 401, 403, 404 | Never retried; mapped to `invalid_input`, `unauthenticated`, `forbidden`, `not_found` |
| Unexpected response shape | `upstream_error` (not retried) |
| Any unexpected exception | `internal_error` with a generic message; details stay out of the response |

Retrying is safe because every tool is read-only. Every retry attempt passes through the rate
limiter, so retries cannot push the connector over its own limit. Errors are never cached.

## Security model

- **Read-only by construction:** there is no write tool and the client only issues GET.
- **Identity:** each process has one tenant id, credential id and permission set from
  configuration. The agent cannot change them through arguments.
- **Permission before data:** checked before the cache, so cached data is never served to a
  caller without permission. Cache keys include tenant and credential.
- **Input validation:** typed schemas with bounds; search values containing quote characters
  are rejected so a value cannot inject extra search clauses.
- **Secrets:** the API key is a secret value, is not returned or logged, and error messages
  never include the upstream response body.
- **Data minimisation:** a small fixed set of fields; description only on request, masked
  and truncated.
- **Audit:** one JSON line per call to stderr (stdout is reserved for the MCP protocol) and
  optionally a file. Contains no ticket content and no keys.

## Design decisions

- **Thin MCP layer:** the same service can be reused behind an HTTP API or another transport.
- **No Kafka, Redis, Kubernetes, database or LLM:** none is needed for a correct, reliable
  read connector, and each would add failure modes. The in-memory cache and file audit log
  are the deliberate simple choices; shared equivalents are listed as the long-term fix.
- **Mock Freshdesk with failure injection:** real Freshdesk cannot be told to return a 429 or
  to hang, so reliability behaviour is tested and demonstrated deterministically against the
  mock.
- **Errors as data:** tool results carry `error_code`, `retryable` and `retry_after_seconds`
  so the agent can decide what to do.