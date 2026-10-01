# Tool Specification

All tools are **read-only**. There are no create, update, delete or reply tools.
Every tool returns either the success shape below or an `ErrorResponse`.

## list_tickets
List tickets, newest first.

| Param | Type | Required | Notes |
|---|---|---|---|
| status | open / pending / resolved / closed | no | |
| updated_since | ISO datetime | no | |
| page | int 1-300 | no | default 1 |
| per_page | int 1-100 | no | default 30 |
| include_description | bool | no | default false, truncated when true |

Returns: `TicketList`

## get_ticket
Fetch one ticket by id.

| Param | Type | Required |
|---|---|---|
| ticket_id | int > 0 | yes |
| include_description | bool | no |

Returns: `Ticket`

## search_tickets
Search tickets by filters. At least one filter is required.

| Param | Type | Required | Notes |
|---|---|---|---|
| requester_email | string (email) | no | |
| status | enum | no | |
| priority | low / medium / high / urgent | no | |
| tag | string, max 64 | no | |
| page | int 1-10 | no | Freshdesk search page cap |
| include_description | bool | no | |

Returns: `TicketList`

## Ticket (normalized)
`id, subject, status, priority, requester_id, created_at, updated_at, description (optional)`

## ErrorResponse
`error_code, message, retryable, retry_after_seconds (optional), request_id (optional)`

| error_code | When | retryable |
|---|---|---|
| invalid_input | Bad parameters | no |
| unauthenticated | Freshdesk 401 | no |
| forbidden | 403 or missing permission | no |
| not_found | 404 | no |
| rate_limited | 429 after retries | yes |
| upstream_error | 5xx after retries | yes |
| timeout | Request exceeded timeout | yes |