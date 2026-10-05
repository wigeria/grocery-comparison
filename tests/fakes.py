"""In-memory stand-ins for Zepto and Claude, so flow tests never touch a real account."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from zepto_ordering.cart_agent import AgentTurn
from zepto_ordering.zepto_client import Cart, CartItem, OrderPreview

ADDRESS_ID = "home-address-id"
DELIVERY_FEE_PAISE = 3000


def make_item(name: str, quantity: int = 1, price_paise: int = 5000) -> CartItem:
    """Build a cart item with ids derived from its name."""
    return CartItem(
        product_variant_id=f"pv-{name}",
        store_product_id=f"sp-{name}",
        name=name,
        quantity=quantity,
        price_paise=price_paise,
    )


@dataclass
class FakeZepto:
    items: list[CartItem] = field(default_factory=list)
    placed_orders: list[list[CartItem]] = field(default_factory=list)
    # Exception raised by place_cod_order instead of placing.
    place_error: BaseException | None = None
    # Exception raised when the session closes, after the caller's code finished.
    close_error: Exception | None = None
    deliverable: bool = True

    @asynccontextmanager
    async def connect(self):
        """Mimic ZeptoClient.connect."""
        yield self
        if self.close_error is not None:
            raise self.close_error

    async def view_cart(self) -> Cart:
        return Cart(items=list(self.items))

    async def clear_cart(self) -> None:
        self.items.clear()

    async def preview_order(self) -> OrderPreview:
        to_pay = Cart(items=self.items).item_total_paise + DELIVERY_FEE_PAISE
        return OrderPreview(
            to_pay_paise=to_pay,
            delivery_fee_paise=DELIVERY_FEE_PAISE,
            deliverable=self.deliverable,
            payment_method="COD",
            address_id=ADDRESS_ID,
            raw={},
        )

    async def place_cod_order(self) -> dict[str, Any]:
        if self.place_error is not None:
            raise self.place_error
        self.placed_orders.append(list(self.items))
        return {"structured": {"orderId": "zepto-1"}, "text": "Order placed"}


@dataclass
class FakeComparer:
    """Records what it was asked to compare and returns a fixed result."""

    calls: list[tuple[Cart, OrderPreview]] = field(default_factory=list)

    async def compare(self, cart: Cart, preview: OrderPreview) -> dict[str, Any]:
        self.calls.append((cart, preview))
        return {"cheaper": "blinkit", "zepto": {"to_pay_paise": preview.to_pay_paise}}


@dataclass
class FakeAgent:
    """Adds one item per turn, named after the message text."""

    zepto: FakeZepto
    calls: list[dict[str, Any]] = field(default_factory=list)
    # Exception raised after adding the item, like a turn that fails partway.
    error: Exception | None = None

    async def run_turn(self, text: str, **kwargs: Any) -> AgentTurn:
        self.calls.append({"text": text, **kwargs})
        self.zepto.items.append(make_item(text))
        if self.error is not None:
            raise self.error
        return AgentTurn(reply=f"Added {text}.", not_found=[], session_id="session-1")
