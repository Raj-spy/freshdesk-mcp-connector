"""All settings come from environment variables or .env. No secrets in code."""
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    freshdesk_mode: Literal["mock", "live"] = "mock"
    freshdesk_domain: str = "yourcompany.freshdesk.com"
    freshdesk_api_key: SecretStr = SecretStr("mock-key")  # SecretStr never prints its value
    mock_base_url: str = "http://localhost:9000"

    request_timeout_seconds: float = 10.0
    max_retries: int = 3                  # retries after the first attempt
    max_retry_after_seconds: int = 30     # longest Retry-After we are willing to wait
    rate_limit_per_minute: int = 100      # set below your Freshdesk plan's limit
    cache_ttl_seconds: int = 30           # 0 disables the cache

    @property
    def base_url(self) -> str:
        if self.freshdesk_mode == "mock":
            return self.mock_base_url
        return f"https://{self.freshdesk_domain}"

    @model_validator(mode="after")
    def live_needs_real_credentials(self):
        key = self.freshdesk_api_key.get_secret_value()
        if self.freshdesk_mode == "live" and key in {"", "mock-key", "changeme"}:
            raise ValueError("FRESHDESK_API_KEY must be set to a real key in live mode")
        return self
    # who the connector acts as (Agent Studio would set these per tenant)
    connector_tenant_id: str = "demo-tenant"
    connector_credential_id: str = "demo-cred"
    connector_permissions: str = "tickets:read"
    audit_log_path: str = ""
