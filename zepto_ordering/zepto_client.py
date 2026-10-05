"""Direct calls to the Zepto MCP server for the steps that don't need Claude.

The app reads, clears, previews and places orders itself, so these steps are
predictable and never depend on how a model interprets a tool result.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from zepto_ordering.zepto_auth import MCP_URL, get_access_token

logger = logging.getLogger(__name__)

# update_cart requires a device id. The cart itself belongs to the Zepto account.
DEVICE_ID = "zepto-ordering-app"
CONNECT_ATTEMPTS = 2
CONNECT_RETRY_DELAY_SECONDS = 2
CONNECTION_ERRORS = (MCPError, httpx2.HTTPError)


class ZeptoError(Exception):
    """Raised when a Zepto tool call fails or returns something unexpected."""


def unwrap_exception_group(error: BaseException) -> BaseException:
    """Return the single underlying error from nested one-error ExceptionGroups."""
    while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
        error = error.exceptions[0]
    return error


@dataclass(frozen=True)
class CartItem:
    product_variant_id: str
    store_product_id: str
    name: str
    quantity: int
    price_paise: int | None

    @classmethod
    def from_zepto(cls, item: dict[str, Any]) -> CartItem:
        """Build a cart item from one entry of view_cart's structured output."""
        return cls(
            product_variant_id=item["productVariantId"],
            store_product_id=item["storeProductId"],
            name=(item.get("name") or "").strip(),
            quantity=int(item["quantity"]),
            price_paise=item.get("price"),
        )


@dataclass(frozen=True)
class Cart:
    items: list[CartItem] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """True when the cart has no items."""
        return not self.items

    @property
    def item_total_paise(self) -> int:
        """Sum of item prices. Excludes fees, and skips items without a known price."""
        return sum((item.price_paise or 0) * item.quantity for item in self.items)


@dataclass(frozen=True)
class OrderPreview:
    to_pay_paise: int
    delivery_fee_paise: int | None
    deliverable: bool
    payment_method: str
    address_id: str
    raw: dict[str, Any]


class ZeptoClient:
    """One MCP session with the delivery address already selected."""

    def __init__(self, session: ClientSession, address_id: str) -> None:
        self._session = session
        self.address_id = address_id

    @classmethod
    @asynccontextmanager
    async def connect(cls, address_id: str) -> AsyncIterator[ZeptoClient]:
        """Open a session and select the delivery address, which sets the store.

        The MCP library runs sessions in task groups, which wrap every error raised
        inside the session (including the caller's own) in an ExceptionGroup. This
        unwraps them, so callers see their original errors and Zepto connection
        problems as ZeptoError. Setup is retried once, since Zepto sometimes
        rejects a new session briefly.
        """
        for attempt in range(1, CONNECT_ATTEMPTS + 1):
            started = False
            try:
                async with cls._open_session(address_id) as client:
                    started = True
                    yield client
                return
            except BaseException as error:
                cause = unwrap_exception_group(error)
                connection_problem = isinstance(cause, CONNECTION_ERRORS)
                if connection_problem and not started and attempt < CONNECT_ATTEMPTS:
                    logger.warning("Zepto session setup failed (%r), retrying.", cause)
                    await asyncio.sleep(CONNECT_RETRY_DELAY_SECONDS)
                    continue
                if connection_problem:
                    raise ZeptoError(f"Could not talk to Zepto: {cause!r}") from cause
                if cause is error:
                    raise
                raise cause from None

    @classmethod
    @asynccontextmanager
    async def _open_session(cls, address_id: str) -> AsyncIterator[ZeptoClient]:
        """Connect, initialize, and select the address. Errors come out wrapped in groups."""
        token = await asyncio.to_thread(get_access_token)
        http_client = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx2.Timeout(30, read=120),
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
                client = cls(session, address_id)
                await client._select_address()
                yield client

    async def _call(self, name: str, arguments: dict[str, Any]) -> tuple[Any, str]:
        """Call a tool and return (structured content, text). Raise on tool errors."""
        result = await self._session.call_tool(name, arguments)
        text = "\n".join(getattr(block, "text", "") for block in result.content).strip()
        if result.is_error:
            raise ZeptoError(f"{name} failed: {text or 'no details'}")
        return result.structured_content, text

    async def _select_address(self) -> None:
        """Select the configured address and confirm Zepto used it."""
        structured, text = await self._call("select_saved_address", {"addressId": self.address_id})
        selected_id = ((structured or {}).get("address") or {}).get("id")
        if selected_id != self.address_id:
            raise ZeptoError(f"Zepto selected a different address: {text[:200]}")

    async def view_cart(self) -> Cart:
        """Return the account's current cart."""
        structured, text = await self._call("view_cart", {})
        try:
            return Cart(items=[CartItem.from_zepto(item) for item in structured["items"]])
        except (KeyError, TypeError, ValueError) as error:
            raise ZeptoError(f"Unexpected view_cart response: {text[:200]}") from error

    async def clear_cart(self) -> None:
        """Remove every item from the cart and confirm it is empty."""
        cart = await self.view_cart()
        if cart.is_empty:
            return
        removals = [
            {
                "productVariantId": item.product_variant_id,
                "storeProductId": item.store_product_id,
                "quantity": 0,
            }
            for item in cart.items
        ]
        await self._call("update_cart", {"deviceId": DEVICE_ID, "cartItems": removals})
        if not (await self.view_cart()).is_empty:
            raise ZeptoError("Cart still has items after clearing it.")

    async def preview_order(self) -> OrderPreview:
        """Get Zepto's COD order preview without placing anything."""
        structured, text = await self._call(
            "create_order", {"confirmOrder": False, "userAddressId": self.address_id}
        )
        if not isinstance(structured, dict) or not structured.get("isPreview"):
            raise ZeptoError(f"Unexpected order preview response: {text[:200]}")
        try:
            return OrderPreview(
                to_pay_paise=int(structured["toPayAmount"]),
                delivery_fee_paise=structured.get("deliveryFee"),
                deliverable=bool(structured.get("deliverable")),
                payment_method=structured.get("paymentMethod") or "",
                address_id=(structured.get("deliveryAddress") or {}).get("id") or "",
                raw=structured,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ZeptoError(f"Unexpected order preview response: {text[:200]}") from error

    async def place_cod_order(self) -> dict[str, Any]:
        """Place the current cart as a COD order. Returns Zepto's response.

        Zepto asks clients to fetch payment methods before ordering, so that
        call is made first even though the app always uses COD.
        """
        await self._call("get_payment_methods", {})
        structured, text = await self._call(
            "create_order", {"confirmOrder": True, "userAddressId": self.address_id}
        )
        if isinstance(structured, dict) and structured.get("isPreview"):
            raise ZeptoError(f"Zepto returned a preview instead of placing the order: {text[:200]}")
        return {"structured": structured, "text": text}
