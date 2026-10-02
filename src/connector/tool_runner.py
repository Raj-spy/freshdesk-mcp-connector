"""Runs one tool call: validate input, call the service, map errors, write the audit line."""
import time
import uuid
from typing import Any, Awaitable, Callable, Type

from pydantic import BaseModel, ValidationError

from .audit import AuditLogger
from .errors import FreshdeskError
from .schemas import ErrorCode, ErrorResponse
from .security import Principal


def _summarize(exc: ValidationError) -> str:
    # field name + reason only; we never echo the submitted values back
    parts = []
    for e in exc.errors():
        where = ".".join(str(p) for p in e["loc"]) or "input"
        parts.append(f"{where}: {e['msg']}")
    return "; ".join(parts)


def _dump(model: BaseModel) -> dict:
    return model.model_dump(mode="json", exclude_none=True)


class ToolRunner:
    def __init__(self, audit: AuditLogger, principal: Principal):
        self._audit = audit
        self._principal = principal

    async def run(self, tool: str, input_cls: Type[BaseModel], args: dict[str, Any],
                  handler: Callable[..., Awaitable[BaseModel]]) -> dict:
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        status = "ok"
        try:
            inp = input_cls(**args)
            result = await handler(self._principal, inp, request_id)
            return _dump(result)
        except ValidationError as exc:
            status = ErrorCode.INVALID_INPUT.value
            return _dump(ErrorResponse(
                error_code=ErrorCode.INVALID_INPUT, message=_summarize(exc),
                retryable=False, request_id=request_id))
        except FreshdeskError as exc:
            status = exc.code.value
            return _dump(exc.to_response(request_id))
        except Exception:
            # Never leak internals to the agent; details belong in server logs.
            status = ErrorCode.INTERNAL_ERROR.value
            return _dump(ErrorResponse(
                error_code=ErrorCode.INTERNAL_ERROR, message="Unexpected connector error",
                retryable=False, request_id=request_id))
        finally:
            self._audit.record(
                request_id=request_id, tenant_id=self._principal.tenant_id,
                credential_id=self._principal.credential_id, tool=tool,
                status=status, latency_ms=(time.monotonic() - started) * 1000)