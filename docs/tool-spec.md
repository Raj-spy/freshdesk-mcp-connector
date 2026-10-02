# Tool specification

All tools are **read-only**. Machine-readable definitions are in `mcp-tools.json`.
Every call returns either the success shape below or an error object. Fields without a value
are omitted from responses.

## list_tickets
List tickets, newest first.

| Param | Type | Required | Notes |
|---|---|---|---|
| status | open / pending / resolved / closed | no | Routed through search (see below) |
| updated_since | ISO datetime | no | Cannot be combined with `status` |
| page | int 1-300 | no | Max 10 when `status` is set |
| per_page | int 1-100 | no | Default 30; ignored when `status` is set (fixed 30) |
| include_description | bool | no | Default false; masked and cut to 500 chars when true |

Returns `{tickets, page, has_more, total?}`. `total` is present only when filtering by status.

## get_ticket
Fetch one ticket by id.

| Param | Type | Required |
|---|---|---|
| ticket_id | int > 0 | yes |
| include_description | bool | no |

Returns one ticket.

## search_tickets
Search by filters combined with AND. At least one filter is required.

| Param | Type | Required | Notes |
|---|---|---|---|
| requester_email | email string | no | |
| status | open / pending / resolved / closed | no | |
| priority | low / medium / high / urgent | no | |
| tag | string, 1-64 chars | no | No quote characters |
| page | int 1-10 | no | 30 results per page |
| include_description | bool | no | |

Returns `{tickets, page, has_more, total}`.

## Ticket
`id, subject, status, priority, requester_id, created_at, updated_at`, and `description` only
when requested.

- status: `open | pending | resolved | closed | other` (`other` = custom Freshdesk status;
  output only, not valid as a filter)
- priority: `low | medium | high | urgent`

## Error object
`{error_code, message, retryable, retry_after_seconds?, request_id}`

| error_code | When | retryable |
|---|---|---|
| invalid_input | Bad arguments or unsupported combination | no |
| unauthenticated | Freshdesk rejected the API key (401) | no |
| forbidden | Missing `tickets:read` (or Freshdesk 403) | no |
| not_found | Ticket does not exist (404) | no |
| rate_limited | 429 after retries, or `Retry-After` > 30s | yes |
| upstream_error | 5xx, connection failure or unexpected response, after retries | yes |
| timeout | Freshdesk too slow after retries | yes |
| internal_error | Unexpected connector fault (generic message) | no |

`request_id` matches the line in the audit log.

## Behaviour notes
- Identical requests within the cache TTL (default 30s) return the cached result.
- Search and list-by-status limits (page size 30, max page 10) follow Freshdesk's documented
  search limits and are unverified against a live account.