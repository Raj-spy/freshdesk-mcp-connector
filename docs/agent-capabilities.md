# What the agent can and cannot do

This connector gives an Agent Studio agent **read-only** access to Freshdesk tickets
through three MCP tools: `list_tickets`, `get_ticket`, `search_tickets`.

## The agent CAN

- List tickets, newest first, optionally filtered by status or `updated_since`.
- Fetch one ticket by id.
- Search tickets by requester email, status, priority and tag (filters combine with AND).
- Ask for the customer's message text (`include_description=true`). It comes back with
  email addresses and phone numbers masked and cut to 500 characters.
- Rely on the connector to handle transient failures: rate limits (429), server errors (5xx),
  timeouts and dropped connections are retried with backoff before the agent sees an error.
- Get a structured error for everything else: `error_code`, `message`, `retryable`, and
  `retry_after_seconds` when known. Every response carries a `request_id` that also appears
  in the audit log.

## The agent CANNOT

- Create, update, delete, assign, reply to or close tickets. No write tools exist.
- Read conversations, notes, attachments, contacts, companies, agents or custom fields.
  Only the normalized ticket fields below are returned.
- See the customer's message unless it asks for it. By default `description` is absent.
- See unmasked contact details in descriptions. Note: `subject` is returned as-is, and
  masking is pattern-based, so it is a safety net and not a guarantee.
- Use `status=other` as a filter. `other` appears in output only (custom Freshdesk statuses).
- Combine `status` with `updated_since` in `list_tickets`.
- Page deeper than page 10 when filtering by status or searching (Freshdesk search limit, see
  limitations in the README).
- Choose its own tenant, credential or permissions. These are fixed by the connector's
  configuration, not by tool arguments.
- Cause unbounded waiting. Timeouts and retries are capped; a `Retry-After` longer than
  30 seconds fails immediately with `rate_limited` instead of hanging.

## Fields returned for a ticket

`id, subject, status, priority, requester_id, created_at, updated_at` and, only on request,
`description`. Fields with no value are omitted.

- status: `open | pending | resolved | closed | other`
- priority: `low | medium | high | urgent`

## How the agent should react to errors

| error_code | Meaning | Agent should |
|---|---|---|
| invalid_input | Bad arguments (message names the field) | Fix the arguments; do not retry unchanged |
| unauthenticated | API key rejected | Stop and report; a human must fix the credential |
| forbidden | Missing permission | Stop and report; do not retry |
| not_found | Ticket does not exist | Do not retry; tell the user |
| rate_limited | Freshdesk limit hit, retries exhausted | Wait `retry_after_seconds` (if present), then retry later |
| upstream_error | Freshdesk 5xx or unreachable, retries exhausted | Retry later; tell the user if it persists |
| timeout | Freshdesk too slow, retries exhausted | Retry later |
| internal_error | Unexpected connector fault | Report with the `request_id` |

The `retryable` flag in each error says whether trying again can ever help.

## Data handling

- Ticket content is never written to the audit log. The log holds who, which tool, when,
  `request_id`, status and latency only.
- API keys are never returned to the agent, never logged, and never included in errors.
- Identical requests within 30 seconds are answered from an in-memory cache scoped to the
  tenant and credential, so data cannot cross tenants.