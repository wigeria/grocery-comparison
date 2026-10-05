"""List the tools exposed by the Zepto MCP server, using the stored login.

Prints a summary and saves the full tool schemas to data/zepto_tools.json.
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from zepto_ordering.config import DATA_DIR
from zepto_ordering.zepto_auth import MCP_URL, ZeptoAuthError, get_access_token


async def fetch_tools() -> list[dict]:
    """Connect to the Zepto MCP server and return every tool's schema."""
    http_client = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {get_access_token()}"},
        timeout=httpx2.Timeout(30, read=300),
    )
    async with (
        http_client,
        streamable_http_client(MCP_URL, http_client=http_client) as (
            read,
            write,
        ),
    ):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return [tool.model_dump(exclude_none=True) for tool in result.tools]


def main() -> int:
    """Print a summary of the Zepto tools and save their full schemas."""
    try:
        tools = asyncio.run(fetch_tools())
    except ZeptoAuthError as error:
        print(error)
        return 1

    output_file = DATA_DIR / "zepto_tools.json"
    output_file.write_text(json.dumps(tools, indent=2))

    for tool in tools:
        description = (tool.get("description") or "").strip().splitlines()
        print(f"- {tool['name']}: {description[0] if description else ''}")
    print(f"\n{len(tools)} tools. Full schemas saved to {output_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
