"""Check that the unofficial Blinkit API used for price comparison still works.

Read-only: gets an anonymous device key, checks delivery to the configured
location, runs one search and prices one item. Nothing is logged in or ordered.

    python scripts/blinkit_check.py            # searches for "milk"
    python scripts/blinkit_check.py "eggs"

Exits non-zero at the first failing step, with a hint about where to look.
"""

from __future__ import annotations

import asyncio
import sys
import time

from zepto_ordering.blinkit_client import BlinkitClient, BlinkitError, BlinkitLine
from zepto_ordering.config import ConfigError, load_config

HINTS = {
    "device key": (
        "Blinkit rejected the bootstrap request. HTTP 403 usually means its bot protection "
        "changed (try a newer curl_cffi browser profile); HTTP 400 means REQ_KEY changed. "
        "Compare blinkit_client.py with a fresh capture of blinkit.com's requests."
    ),
    "search": (
        "Search failed or found nothing. Check STATIC_HEADERS (web_app_version, app_version) "
        "against what blinkit.com currently sends, and whether /v1/layout/search moved. "
        "extract_products looks for atc_action.add_to_cart.cart_item in the response."
    ),
    "pricing": (
        "Pricing failed. Check the /v5/carts request body and that cart_data.bill_details "
        "still has payable_amount."
    ),
}


async def check(query: str) -> int:
    """Run each step in order and report it."""
    config = load_config()
    client = BlinkitClient(config.blinkit_latitude, config.blinkit_longitude)
    step = "device key"
    try:
        start = time.monotonic()
        await client._ensure_ready()
        print(f"ok   device key and delivery to the configured location ({_ms(start)})")

        step = "search"
        start = time.monotonic()
        products = await client.search(query)
        in_stock = [product for product in products if product.inventory]
        if not in_stock:
            raise BlinkitError(f"No in-stock results for {query!r} ({len(products)} total).")
        print(
            f"ok   search {query!r}: {len(products)} results, {len(in_stock)} in stock "
            f"({_ms(start)})"
        )
        top = in_stock[0]
        print(f"       e.g. {top.name} | {top.unit} | Rs {(top.price_paise or 0) / 100:g}")

        step = "pricing"
        start = time.monotonic()
        bill = await client.price_cart([BlinkitLine(top, 1)])
        print(
            f"ok   priced 1 x {top.name}: Rs {bill.to_pay_paise / 100:g} to pay "
            f"(delivery Rs {(bill.delivery_fee_paise or 0) / 100:g}) ({_ms(start)})"
        )
        return 0
    except BlinkitError as error:
        print(f"FAIL {step}: {error}")
        print(f"hint: {HINTS[step]}")
        return 1
    finally:
        await client.close()


def _ms(start: float) -> str:
    """Elapsed time since start, for display."""
    return f"{(time.monotonic() - start) * 1000:.0f} ms"


def main() -> int:
    """Run the check for the query given on the command line."""
    query = sys.argv[1] if len(sys.argv) > 1 else "milk"
    try:
        return asyncio.run(check(query))
    except ConfigError as error:
        print(error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
