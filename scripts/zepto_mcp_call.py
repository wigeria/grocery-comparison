"""Call a read-only tool on Zepto's MCP server and print the raw response.

For exploring tool schemas and real response shapes. Anything that could change
an order, a payment or the account is refused, and create_order is only allowed
as a preview (confirmOrder false). update_cart is refused too, since the cart is
shared with the Zepto app; view_cart is fine.

    python scripts/zepto_mcp_call.py --list
    python scripts/zepto_mcp_call.py --schema search_products
    python scripts/zepto_mcp_call.py search_products '{"query": "milk"}'

Tools that need a store (search, payment methods, preview) select the address in
config.toml first. Pass --address <id> to use another saved address.

Run this where the Zepto login lives. If the server owns it, run it in the app
container: docker compose exec app python scripts/zepto_mcp_call.py ...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from zepto_ordering.config import ConfigError, load_config
from zepto_ordering.zepto_auth import MCP_URL, ZeptoAuthError, get_access_token

READ_ONLY_TOOLS = {
    "get_user_details",
    "list_saved_addresses",
    "select_saved_address",
    "get_location_serviceability",
    "get_past_order_items",
    "search_products",
    "search_multiple_products",
    "get_product_details",
    "view_cart",
    "get_payment_methods",
    "list_order_history",
    "get_order_detail",
}
PREVIEW_ONLY_TOOLS = {"create_order"}
NEEDS_STORE = {
    "search_products",
    "search_multiple_products",
    "get_product_details",
    "get_payment_methods",
    "create_order",
}


class RefusedError(Exception):
    """Raised for tool calls this script won't make."""


def check_allowed(tool: str, arguments: dict[str, Any]) -> None:
    """Refuse anything that could place an order or change the account or cart."""
    if tool in PREVIEW_ONLY_TOOLS:
        if arguments.get("confirmOrder") is not False:
            raise RefusedError(f'{tool} is only allowed with "confirmOrder": false.')
        return
    if tool not in READ_ONLY_TOOLS:
        raise RefusedError(f"{tool} is not a read-only tool, so this script won't call it.")


async def run(args: argparse.Namespace) -> int:
    """Connect, optionally select the address, and run the requested command."""
    token = await asyncio.to_thread(get_access_token)
    http_client = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx2.Timeout(30, read=120),
    )
    async with (
        http_client,
        streamable_http_client(MCP_URL, http_client=http_client) as (read, write),
    ):
        async with ClientSession(read, write) as session:
            await session.initialize()

            if args.list or args.schema:
                tools = (await session.list_tools()).tools
                for tool in tools:
                    if args.schema and tool.name != args.schema:
                        continue
                    if args.schema:
                        print(tool.description)
                        print(json.dumps(tool.inputSchema, indent=2))
                    else:
                        marker = "read-only" if tool.name in READ_ONLY_TOOLS else "blocked"
                        if tool.name in PREVIEW_ONLY_TOOLS:
                            marker = "preview only"
                        print(f"{tool.name:32} {marker}")
                return 0

            arguments = json.loads(args.arguments) if args.arguments else {}
            check_allowed(args.tool, arguments)

            if args.tool in NEEDS_STORE:
                address_id = args.address or load_config().delivery_address_id
                selected = await session.call_tool(
                    "select_saved_address", {"addressId": address_id}
                )
                if selected.is_error:
                    print("Could not select the address.", file=sys.stderr)
                    return 1

            result = await session.call_tool(args.tool, arguments)
            print(f"is_error: {result.is_error}\n")
            print("--- structured_content")
            print(json.dumps(result.structured_content, indent=2, ensure_ascii=False))
            print("\n--- text")
            print("\n".join(getattr(block, "text", "") for block in result.content))
            return 1 if result.is_error else 0


def main() -> int:
    """Parse arguments and run."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("tool", nargs="?", help="tool name to call")
    parser.add_argument("arguments", nargs="?", help="tool arguments as a JSON object")
    parser.add_argument(
        "--list", action="store_true", help="list tools and whether they're allowed"
    )
    parser.add_argument("--schema", metavar="TOOL", help="show one tool's description and schema")
    parser.add_argument("--address", help="saved address id to select first")
    args = parser.parse_args()
    if not (args.list or args.schema or args.tool):
        parser.error("give a tool name, --list or --schema")

    try:
        return asyncio.run(run(args))
    except (RefusedError, ZeptoAuthError, ConfigError, json.JSONDecodeError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
