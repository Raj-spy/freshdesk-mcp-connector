"""Permissions and data-safety helpers. No network, no business logic."""
import re
from dataclasses import dataclass, field
from typing import Iterable

from .errors import FreshdeskError
from .schemas import ErrorCode

TICKETS_READ = "tickets:read"


@dataclass(frozen=True)
class Principal:
    """Who is calling: one tenant, one credential, a fixed set of permissions."""
    tenant_id: str
    credential_id: str
    permissions: frozenset[str] = field(default_factory=frozenset)

    def has(self, permission: str) -> bool:
        return permission in self.permissions


def require(principal: Principal, permission: str) -> None:
    if not principal.has(permission):
        raise FreshdeskError(
            ErrorCode.FORBIDDEN, f"Missing permission: {permission}", retryable=False
        )


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\d)\+?\d[\d\s().-]{7,}\d(?!\d)")


def redact_pii(text: str) -> str:
    text = _EMAIL.sub("[redacted-email]", text)
    return _PHONE.sub("[redacted-phone]", text)


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def scrub_secrets(text: str, secrets: Iterable[str]) -> str:
    """Replace known secret values (API keys) in any string headed for logs."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text