"""List the saved Zepto addresses, with the values config.toml needs.

Run after scripts/zepto_login.py. Copy the id of the delivery address into
zepto.delivery_address_id, and its latitude and longitude into [blinkit].
"""

from __future__ import annotations

import asyncio
import sys

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from zepto_ordering.zepto_auth import MCP_URL, ZeptoAuthError, get_access_token


async def fetch_addresses() -> list[dict]:
    """Return the saved addresses from the Zepto MCP server."""
    http_client = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {get_access_token()}"},
        timeout=httpx2.Timeout(30, read=120),
    )
    async with (
        http_client,
        streamable_http_client(MCP_URL, http_client=http_client) as (read, write),
    ):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("list_saved_addresses", {})
            return (result.structured_content or {}).get("addresses") or []


def main() -> int:
    """Print each saved address with its id and coordinates."""
    try:
        addresses = asyncio.run(fetch_addresses())
    except ZeptoAuthError as error:
        print(error)
        return 1

    if not addresses:
        print("No saved addresses. Add one in the Zepto app first.")
        return 1
    for address in addresses:
        print(f"{address.get('label', '')}: {address.get('addressLine', '')}")
        print(f'  delivery_address_id = "{address.get("id")}"')
        print(f"  latitude = {address.get('latitude')}")
        print(f"  longitude = {address.get('longitude')}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
