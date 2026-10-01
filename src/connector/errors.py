"""One exception type for everything that can go wrong talking to Freshdesk."""
from typing import Optional

from .schemas import ErrorCode, ErrorResponse


class FreshdeskError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        retry_after_seconds: Optional[int] = None,
        status_code: Optional[int] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.status_code = status_code

    def to_response(self, request_id: Optional[str] = None) -> ErrorResponse:
        return ErrorResponse(
            error_code=self.code,
            message=self.message,
            retryable=self.retryable,
            retry_after_seconds=self.retry_after_seconds,
            request_id=request_id,
        )