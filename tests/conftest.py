import pytest

from src.connector.config import Settings
from src.connector.freshdesk_client import FreshdeskClient


async def _no_sleep(seconds: float) -> None:
    return None


@pytest.fixture
async def fd_client():
    settings = Settings(
        _env_file=None, freshdesk_mode="mock", freshdesk_api_key="test-key",
        mock_base_url="http://mock", max_retries=2, rate_limit_per_minute=1000,
    )
    c = FreshdeskClient(settings, sleep=_no_sleep)
    yield c
    await c.aclose()
