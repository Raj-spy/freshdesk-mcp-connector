"""Live check: real FreshdeskClient against the running mock server."""
import asyncio

import httpx

from src.connector.config import Settings
from src.connector.errors import FreshdeskError
from src.connector.freshdesk_client import FreshdeskClient

MOCK = "http://localhost:9000"


async def main():
    httpx.post(f"{MOCK}/_mock/reset")
    c = FreshdeskClient(Settings())

    r = await c.get_ticket(1)
    print("1. normal   ->", r.data["subject"], "| attempts:", r.attempts)

    httpx.post(f"{MOCK}/_mock/fail", params={"mode": "429", "count": 2})
    r = await c.get_ticket(1)
    print("2. two 429s -> success after", r.attempts, "attempts")

    try:
        await c.get_ticket(999)
    except FreshdeskError as e:
        print("3. missing  ->", e.code.value, "| retryable:", e.retryable)
    await c.aclose()

    bad = FreshdeskClient(Settings(freshdesk_api_key="wrong"))
    try:
        await bad.get_ticket(1)
    except FreshdeskError as e:
        print("4. bad key  ->", e.code.value, "| retryable:", e.retryable)
    await bad.aclose()


asyncio.run(main())