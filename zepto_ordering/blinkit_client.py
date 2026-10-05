"""Read-only client for Blinkit's public website data, used for price comparison.

Experimental and unofficial. Blinkit has no public API, so this makes the same
requests blinkit.com makes in a browser, based on the research published in
github.com/yniks/blinkit-mcp (RESEARCH.md). It is not affiliated with or endorsed
by Blinkit, may stop working whenever the website changes, and is meant for
personal, low-volume use in line with Blinkit's terms.

What it does and doesn't do:
- Only reads public catalog data and prices a list of items. It never logs in,
  never touches a Blinkit account or cart, and never places an order.
  /v5/carts is stateless: it prices whatever items are sent and stores nothing.
- Uses curl_cffi with a Chrome browser profile, because blinkit.com only accepts
  requests that look like they come from a browser.
- Gets an anonymous device key the same way the website does.

Blinkit returns prices in rupees. This module converts everything to paise to
match the rest of the app.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

BASE_URL = "https://blinkit.com"
# Public constant that blinkit.com sends when requesting an anonymous device key.
REQ_KEY = "c0e6868e-1180-400c-be51-f473479f1f0a"
STATIC_HEADERS = {
    "app_client": "consumer_web",
    "platform": "desktop_web",
    "web_app_version": "1008010016",
    "rn_bundle_version": "1009003012",
    "app_version": "52434332",
    "x-age-consent-granted": "false",
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "referer": f"{BASE_URL}/",
    "origin": BASE_URL,
}
REQUEST_TIMEOUT_SECONDS = 30
SEARCH_RESULT_LIMIT = 12


class BlinkitError(Exception):
    """Raised when Blinkit is unreachable, blocks the request, or returns something unexpected."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def rupees_to_paise(amount: Any) -> int | None:
    """Convert a rupee amount from Blinkit to paise, or None if it's missing."""
    if amount is None:
        return None
    return round(float(amount) * 100)


@dataclass(frozen=True)
class BlinkitProduct:
    product_id: int
    name: str
    unit: str
    price_paise: int | None
    mrp_paise: int | None
    inventory: int | None
    # The exact line item /v5/carts expects, as Blinkit's search returned it.
    cart_item: dict[str, Any]

    @classmethod
    def from_cart_item(cls, cart_item: dict[str, Any]) -> BlinkitProduct:
        """Build a product from the cart_item attached to a search result."""
        return cls(
            product_id=int(cart_item["product_id"]),
            name=(cart_item.get("display_name") or cart_item.get("product_name") or "").strip(),
            unit=(cart_item.get("unit") or "").strip(),
            price_paise=rupees_to_paise(cart_item.get("price")),
            mrp_paise=rupees_to_paise(cart_item.get("mrp")),
            inventory=cart_item.get("inventory"),
            cart_item=cart_item,
        )


@dataclass(frozen=True)
class BlinkitLine:
    product: BlinkitProduct
    quantity: int


@dataclass(frozen=True)
class BlinkitBill:
    to_pay_paise: int
    item_total_paise: int | None
    delivery_fee_paise: int | None
    other_charges_paise: int | None
    unavailable_items: int
    # Unit prices as Blinkit priced them, by product id.
    unit_prices_paise: dict[int, int]


def extract_products(search_response: Any) -> list[BlinkitProduct]:
    """Collect products from Blinkit's server-driven search layout, in result order.

    The layout is a deep tree of UI widgets. Anything that can be added to the cart
    carries atc_action.add_to_cart.cart_item, so that's what this looks for.
    """
    products: dict[int, BlinkitProduct] = {}
    pending: list[Any] = [search_response]
    while pending:
        node = pending.pop()
        if isinstance(node, list):
            pending.extend(reversed(node))
        elif isinstance(node, dict):
            cart_item = ((node.get("atc_action") or {}).get("add_to_cart") or {}).get("cart_item")
            if isinstance(cart_item, dict) and cart_item.get("product_id") is not None:
                product = BlinkitProduct.from_cart_item(cart_item)
                if product.name:
                    products.setdefault(product.product_id, product)
            pending.extend(reversed(list(node.values())))
    return list(products.values())


class BlinkitClient:
    """Blinkit session for one delivery location. Safe to share across requests."""

    def __init__(self, latitude: float, longitude: float) -> None:
        self.latitude = latitude
        self.longitude = longitude
        self._device_id = str(uuid.uuid4())
        self._session_uuid = str(uuid.uuid4())
        self._auth_key: str | None = None
        self._session = AsyncSession(impersonate="chrome", timeout=REQUEST_TIMEOUT_SECONDS)
        self._setup_lock = asyncio.Lock()

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        await self._session.close()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Send a request with Blinkit's standard headers and return the parsed JSON."""
        all_headers = {
            **STATIC_HEADERS,
            "device_id": self._device_id,
            "session_uuid": self._session_uuid,
            "lat": str(self.latitude),
            "lon": str(self.longitude),
            "cookie": f"gr_1_deviceId={self._device_id}",
            **(headers or {}),
        }
        if self._auth_key:
            all_headers["auth_key"] = self._auth_key
        try:
            response = await self._session.request(
                method, f"{BASE_URL}{path}", params=params, json=json, headers=all_headers
            )
        except RequestException as error:
            raise BlinkitError(f"Could not reach Blinkit: {error}") from error

        if response.status_code == 403:
            raise BlinkitError("Blinkit blocked the request (HTTP 403).", status_code=403)
        if response.status_code >= 400:
            raise BlinkitError(
                f"Blinkit returned HTTP {response.status_code} for {path}.",
                status_code=response.status_code,
            )
        try:
            return response.json()
        except ValueError as error:
            raise BlinkitError(f"Blinkit returned a non-JSON response for {path}.") from error

    async def _ensure_ready(self) -> None:
        """Get a device auth_key and confirm Blinkit delivers to this location."""
        if self._auth_key:
            return
        async with self._setup_lock:
            if self._auth_key:
                return
            bootstrap = await self._request(
                "GET", "/v2/accounts/auth_key/", headers={"req_key": REQ_KEY}
            )
            auth_key = (bootstrap or {}).get("auth_key")
            if not auth_key:
                raise BlinkitError("Blinkit did not return a device auth key.")

            self._auth_key = auth_key
            visibility = await self._request(
                "GET",
                "/visibility",
                params={"latitude": self.latitude, "longitude": self.longitude},
            )
            if not (visibility or {}).get("serviceable"):
                self._auth_key = None
                raise BlinkitError("Blinkit does not deliver to the configured location.")

    async def _request_with_key(self, method: str, path: str, **kwargs: Any) -> Any:
        """Request an endpoint that needs the device auth key.

        If Blinkit rejects the key (it can expire while the server runs), get a new
        one and try once more.
        """
        await self._ensure_ready()
        try:
            return await self._request(method, path, **kwargs)
        except BlinkitError as error:
            if error.status_code not in (401, 403):
                raise
            self._auth_key = None
            await self._ensure_ready()
            return await self._request(method, path, **kwargs)

    async def search(self, query: str) -> list[BlinkitProduct]:
        """Search the catalog of the store serving this location."""
        response = await self._request_with_key(
            "POST",
            "/v1/layout/search",
            params={"q": query, "search_type": "type_to_search"},
            json={"applied_filters": None, "sort": "", "previous_search_query": query},
        )
        try:
            return extract_products(response)[:SEARCH_RESULT_LIMIT]
        except (KeyError, TypeError, ValueError) as error:
            raise BlinkitError(
                "Blinkit returned search results in an unexpected format."
            ) from error

    async def price_cart(self, lines: list[BlinkitLine]) -> BlinkitBill:
        """Price a set of items, including delivery and other charges. Stores nothing."""
        items = [{**line.product.cart_item, "quantity": line.quantity} for line in lines]
        response = await self._request_with_key(
            "POST", "/v5/carts", json={"items": items, "promo_codes": [""]}
        )
        try:
            return _parse_bill(response)
        except (KeyError, TypeError, ValueError) as error:
            raise BlinkitError("Blinkit returned a cart in an unexpected format.") from error


def _parse_bill(response: Any) -> BlinkitBill:
    """Read the totals and unit prices from a /v5/carts response."""
    cart = (response or {}).get("cart_data") or {}
    bill = cart.get("bill_details") or {}
    if bill.get("payable_amount") is None:
        raise BlinkitError("Blinkit's cart response had no payable amount.")

    unit_prices = {
        int(item["product_id"]): rupees_to_paise(item["price"])
        for item in cart.get("items") or []
        if item.get("product_id") is not None and item.get("price") is not None
    }
    return BlinkitBill(
        to_pay_paise=rupees_to_paise(bill["payable_amount"]),
        item_total_paise=rupees_to_paise(bill.get("total_cost")),
        delivery_fee_paise=rupees_to_paise(bill.get("delivery_charge")),
        other_charges_paise=rupees_to_paise(bill.get("additional_charge")),
        unavailable_items=int(bill.get("unavailable_items") or 0),
        unit_prices_paise=unit_prices,
    )
