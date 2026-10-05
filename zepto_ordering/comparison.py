"""Compare the Zepto cart's total with the same items on Blinkit.

Claude matches each Zepto item to a Blinkit product using a Blinkit search tool
that runs inside this process. The app then prices the matched items with
Blinkit's cart API and compares the totals Zepto and Blinkit actually charge.
Nothing is added to any Blinkit cart and nothing is ordered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, tool

from zepto_ordering.blinkit_client import (
    BlinkitBill,
    BlinkitClient,
    BlinkitError,
    BlinkitLine,
    BlinkitProduct,
)
from zepto_ordering.claude_runner import run_structured
from zepto_ordering.config import CLAUDE_WORK_DIR
from zepto_ordering.zepto_client import Cart, OrderPreview

TOOL_SERVER_NAME = "blinkit"
SEARCH_TOOL = f"mcp__{TOOL_SERVER_NAME}__search_products"
MAX_TURNS = 30

MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "integer", "description": "Number of the Zepto item."},
                    "blinkit_product_id": {
                        "type": ["integer", "null"],
                        "description": "Matching Blinkit product id, or null if none fits.",
                    },
                    "quantity": {"type": "integer", "minimum": 1},
                    "exact": {
                        "type": "boolean",
                        "description": "True only for the same brand, variant and pack size.",
                    },
                    "note": {
                        "type": "string",
                        "description": "Short note on any difference, e.g. brand or pack size.",
                    },
                },
                "required": ["item", "blinkit_product_id", "quantity", "exact", "note"],
            },
        }
    },
    "required": ["matches"],
}

SYSTEM_PROMPT = """\
You match grocery items from a Zepto cart to the same products on Blinkit, so the app
can compare prices. You only search; you never add to a cart or order anything.

For each numbered Zepto item:
- Search Blinkit with search_products and pick the same product: same brand, variant
  and pack size when Blinkit has it.
- If the exact product isn't there, pick the closest equivalent (same kind of product,
  similar pack size) and say what differs in the note. If several are equally close,
  pick the cheapest.
- Set quantity so the total amount matches the Zepto line, e.g. Zepto 2 x 500 ml can be
  Blinkit 2 x 500 ml or 1 x 1 L.
- Only pick products marked in stock. If nothing reasonable is available, use null.
- Search results can include sponsored or unrelated products. Ignore them.

Return one match per Zepto item, using the item numbers given.
"""


@dataclass(frozen=True)
class Match:
    item_index: int
    product: BlinkitProduct | None
    quantity: int
    exact: bool
    note: str


def _format_product(product: BlinkitProduct) -> str:
    """One search result line for Claude."""
    price = (
        f"Rs {product.price_paise / 100:g}" if product.price_paise is not None else "price unknown"
    )
    stock = "out of stock" if not product.inventory else f"{product.inventory} in stock"
    return f"id {product.product_id} | {product.name} | {product.unit} | {price} | {stock}"


def _describe_cart(cart: Cart) -> str:
    """Numbered list of Zepto cart items for the matching prompt."""
    lines = []
    for index, item in enumerate(cart.items, start=1):
        price = f"Rs {item.price_paise / 100:g} each" if item.price_paise is not None else ""
        lines.append(f"{index}. {item.name} | quantity {item.quantity} | {price}")
    return "Zepto cart items:\n" + "\n".join(lines)


async def match_items(cart: Cart, blinkit: BlinkitClient, model: str | None) -> list[Match]:
    """Ask Claude to match every Zepto cart item to a Blinkit product."""
    seen: dict[int, BlinkitProduct] = {}

    @tool(
        "search_products",
        "Search Blinkit's catalog for the store that delivers to the user's home.",
        {"query": str},
    )
    async def search_products(args: dict[str, Any]) -> dict[str, Any]:
        """Run a Blinkit search and remember results so matches can be priced later."""
        try:
            products = await blinkit.search(args["query"])
        except BlinkitError as error:
            return {"content": [{"type": "text", "text": str(error)}], "is_error": True}
        for product in products:
            seen[product.product_id] = product
        text = "\n".join(_format_product(product) for product in products)
        return {"content": [{"type": "text", "text": text or "No products found."}]}

    CLAUDE_WORK_DIR.mkdir(parents=True, exist_ok=True)
    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={
            TOOL_SERVER_NAME: create_sdk_mcp_server(TOOL_SERVER_NAME, tools=[search_products])
        },
        strict_mcp_config=True,
        tools=[],
        setting_sources=[],
        allowed_tools=[SEARCH_TOOL],
        permission_mode="dontAsk",
        output_format={"type": "json_schema", "schema": MATCH_SCHEMA},
        max_turns=MAX_TURNS,
        model=model,
        cwd=CLAUDE_WORK_DIR,
    )
    output, _ = await run_structured(
        _describe_cart(cart), options, "Claude could not match items on Blinkit"
    )
    return _validate_matches(output.get("matches") or [], len(cart.items), seen)


def _validate_matches(
    raw_matches: list[dict[str, Any]], item_count: int, seen: dict[int, BlinkitProduct]
) -> list[Match]:
    """Keep one match per Zepto item. Products Claude didn't get from a search are dropped."""
    matches: dict[int, Match] = {}
    for raw in raw_matches:
        index = int(raw.get("item", 0)) - 1
        if not 0 <= index < item_count or index in matches:
            continue
        product = seen.get(raw.get("blinkit_product_id"))
        note = str(raw.get("note") or "").strip()
        if raw.get("blinkit_product_id") is not None and product is None:
            note = "Matched a product that wasn't in the search results."
        matches[index] = Match(
            item_index=index,
            product=product,
            quantity=max(1, int(raw.get("quantity") or 1)),
            exact=bool(raw.get("exact")) and product is not None,
            note=note,
        )

    return [
        matches.get(index) or Match(index, None, 1, False, "Not matched.")
        for index in range(item_count)
    ]


def _merge_lines(matches: list[Match]) -> list[BlinkitLine]:
    """Blinkit cart lines, adding up quantities when two items map to one product."""
    quantities: dict[int, int] = {}
    products: dict[int, BlinkitProduct] = {}
    for match in matches:
        if match.product is None:
            continue
        product_id = match.product.product_id
        products[product_id] = match.product
        quantities[product_id] = quantities.get(product_id, 0) + match.quantity
    return [BlinkitLine(products[pid], quantity) for pid, quantity in quantities.items()]


def build_comparison(
    cart: Cart, preview: OrderPreview, matches: list[Match], bill: BlinkitBill | None
) -> dict[str, Any]:
    """Combine both totals and the per-item matches into the comparison result."""
    rows = []
    for match in matches:
        zepto_item = cart.items[match.item_index]
        blinkit_row = None
        if match.product is not None:
            unit_price = (bill.unit_prices_paise if bill else {}).get(
                match.product.product_id, match.product.price_paise
            )
            blinkit_row = {
                "product_id": match.product.product_id,
                "name": match.product.name,
                "unit": match.product.unit,
                "quantity": match.quantity,
                "line_total_paise": unit_price * match.quantity if unit_price is not None else None,
            }
        rows.append(
            {
                "zepto": {
                    "name": zepto_item.name,
                    "quantity": zepto_item.quantity,
                    "line_total_paise": (
                        zepto_item.price_paise * zepto_item.quantity
                        if zepto_item.price_paise is not None
                        else None
                    ),
                },
                "blinkit": blinkit_row,
                "exact": match.exact,
                "note": match.note,
            }
        )

    unmatched = sum(1 for match in matches if match.product is None)
    blinkit_total = bill.to_pay_paise if bill else None
    complete = bill is not None and unmatched == 0 and bill.unavailable_items == 0

    if blinkit_total is None or blinkit_total == preview.to_pay_paise:
        cheaper = "same" if blinkit_total is not None else None
    else:
        cheaper = "zepto" if preview.to_pay_paise < blinkit_total else "blinkit"

    return {
        "zepto": {
            "to_pay_paise": preview.to_pay_paise,
            "item_total_paise": cart.item_total_paise,
            "delivery_fee_paise": preview.delivery_fee_paise,
        },
        "blinkit": {
            "to_pay_paise": blinkit_total,
            "item_total_paise": bill.item_total_paise if bill else None,
            "delivery_fee_paise": bill.delivery_fee_paise if bill else None,
            "other_charges_paise": bill.other_charges_paise if bill else None,
            "unavailable_items": bill.unavailable_items if bill else 0,
        },
        "items": rows,
        "unmatched_items": unmatched,
        # False when Blinkit is missing items, so its total isn't a like-for-like price.
        "complete": complete,
        "cheaper": cheaper,
        "difference_paise": (
            abs(preview.to_pay_paise - blinkit_total) if blinkit_total is not None else None
        ),
    }


class BlinkitComparer:
    """Matches a Zepto cart on Blinkit and prices it."""

    def __init__(self, blinkit: BlinkitClient, model: str | None = None) -> None:
        self._blinkit = blinkit
        self._model = model

    async def compare(self, cart: Cart, preview: OrderPreview) -> dict[str, Any]:
        """Return the comparison for a non-empty Zepto cart and its order preview."""
        matches = await match_items(cart, self._blinkit, self._model)
        lines = _merge_lines(matches)
        bill = await self._blinkit.price_cart(lines) if lines else None
        return build_comparison(cart, preview, matches, bill)
