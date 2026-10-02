import pytest

from src.connector.errors import FreshdeskError
from src.connector.schemas import ErrorCode
from src.connector.security import (
    TICKETS_READ, Principal, redact_pii, require, scrub_secrets, truncate,
)


def test_require_passes_with_permission():
    require(Principal("t", "c", frozenset({TICKETS_READ})), TICKETS_READ)


def test_require_blocks_without_permission():
    with pytest.raises(FreshdeskError) as exc:
        require(Principal("t", "c"), TICKETS_READ)
    assert exc.value.code == ErrorCode.FORBIDDEN
    assert exc.value.retryable is False


def test_redact_email_and_phone():
    out = redact_pii("Mail raj@example.com or call +91 98765 43210 today")
    assert "raj@example.com" not in out
    assert "98765" not in out
    assert "[redacted-email]" in out and "[redacted-phone]" in out
    assert out.endswith("today")


def test_redact_leaves_normal_text_alone():
    text = "Refund not received for ticket 20"
    assert redact_pii(text) == text


def test_truncate():
    assert truncate("short", 10) == "short"
    out = truncate("x" * 100, 10)
    assert out.startswith("x" * 10) and len(out) == 11


def test_scrub_secrets():
    assert scrub_secrets("key=abc123 failed", ["abc123"]) == "key=[REDACTED] failed"
    assert scrub_secrets("nothing here", [""]) == "nothing here"