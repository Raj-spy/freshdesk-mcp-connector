"""Contract for the Freshdesk connector: what goes in, what comes out."""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


# ---------- Readable names for Freshdesk's integer codes ----------
# Verify these against the Freshdesk API docs before final submission.
class TicketStatus(str, Enum):
    OPEN = "open"          # 2
    PENDING = "pending"    # 3
    RESOLVED = "resolved"  # 4
    CLOSED = "closed"      # 5


class TicketPriority(str, Enum):
    LOW = "low"            # 1
    MEDIUM = "medium"      # 2
    HIGH = "high"          # 3
    URGENT = "urgent"      # 4


STATUS_TO_CODE = {
    TicketStatus.OPEN: 2, TicketStatus.PENDING: 3,
    TicketStatus.RESOLVED: 4, TicketStatus.CLOSED: 5,
}
PRIORITY_TO_CODE = {
    TicketPriority.LOW: 1, TicketPriority.MEDIUM: 2,
    TicketPriority.HIGH: 3, TicketPriority.URGENT: 4,
}
CODE_TO_STATUS = {v: k for k, v in STATUS_TO_CODE.items()}
CODE_TO_PRIORITY = {v: k for k, v in PRIORITY_TO_CODE.items()}


# ---------- Tool inputs ----------
class ListTicketsInput(BaseModel):
    status: Optional[TicketStatus] = None
    updated_since: Optional[datetime] = None
    page: int = Field(default=1, ge=1, le=300)
    per_page: int = Field(default=30, ge=1, le=100)
    include_description: bool = False


class GetTicketInput(BaseModel):
    ticket_id: int = Field(gt=0)
    include_description: bool = False


class SearchTicketsInput(BaseModel):
    requester_email: Optional[str] = Field(default=None, pattern=EMAIL_PATTERN)
    status: Optional[TicketStatus] = None
    priority: Optional[TicketPriority] = None
    tag: Optional[str] = Field(default=None, min_length=1, max_length=64)
    page: int = Field(default=1, ge=1, le=10)  # Freshdesk search page cap (verify)
    include_description: bool = False

    @model_validator(mode="after")
    def need_at_least_one_filter(self):
        if not any([self.requester_email, self.status, self.priority, self.tag]):
            raise ValueError("Provide at least one filter: requester_email, status, priority or tag")
        return self


# ---------- Tool outputs ----------
class Ticket(BaseModel):
    id: int
    subject: str
    status: TicketStatus
    priority: TicketPriority
    requester_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    description: Optional[str] = None  # only when include_description=True, truncated


class TicketList(BaseModel):
    tickets: list[Ticket]
    page: int
    has_more: bool
    total: Optional[int] = None  # search returns it, list may not


# ---------- Errors ----------
class ErrorCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    UNAUTHENTICATED = "unauthenticated"   # Freshdesk 401
    FORBIDDEN = "forbidden"               # 403 or missing permission
    NOT_FOUND = "not_found"               # 404
    RATE_LIMITED = "rate_limited"         # 429 after retries exhausted
    UPSTREAM_ERROR = "upstream_error"     # 5xx after retries exhausted
    TIMEOUT = "timeout"


class ErrorResponse(BaseModel):
    error_code: ErrorCode
    message: str
    retryable: bool
    retry_after_seconds: Optional[int] = None
    request_id: Optional[str] = None