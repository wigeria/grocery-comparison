"""ZeptoClient.connect error handling, using real anyio task groups like the MCP library."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import anyio
import pytest
from mcp.shared.exceptions import MCPError

from zepto_ordering import zepto_client
from zepto_ordering.orders import OrderConflictError
from zepto_ordering.zepto_client import ZeptoClient, ZeptoError, unwrap_exception_group


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Replace the MCP session with one wrapped in nested task groups, like the real one."""
    attempts: list[int] = []
    monkeypatch.setattr(zepto_client, "CONNECT_RETRY_DELAY_SECONDS", 0)

    def install(fail_setup_times: int = 0) -> None:
        @asynccontextmanager
        async def fake_open_session(cls, address_id):
            attempts.append(1)
            async with anyio.create_task_group(), anyio.create_task_group():
                if len(attempts) <= fail_setup_times:
                    raise MCPError(code=-32603, message="Server returned an error response")
                yield cls(session=None, address_id=address_id)

        monkeypatch.setattr(ZeptoClient, "_open_session", classmethod(fake_open_session))

    install.attempts = attempts
    return install


async def _raise_inside_session(error: Exception) -> None:
    async with ZeptoClient.connect("home"):
        raise error


def test_unwrap_exception_group_returns_single_cause():
    error = ValueError("inner")
    nested = ExceptionGroup("outer", [ExceptionGroup("middle", [error])])

    assert unwrap_exception_group(nested) is error


def test_caller_errors_come_out_unwrapped(opened):
    opened()

    with pytest.raises(OrderConflictError, match="cart is empty"):
        asyncio.run(_raise_inside_session(OrderConflictError("The cart is empty.")))


def test_setup_failure_is_retried_once(opened):
    opened(fail_setup_times=1)

    async def use_session() -> str:
        async with ZeptoClient.connect("home") as client:
            return client.address_id

    assert asyncio.run(use_session()) == "home"
    assert len(opened.attempts) == 2


def test_repeated_setup_failure_becomes_zepto_error(opened):
    opened(fail_setup_times=5)

    async def use_session() -> None:
        async with ZeptoClient.connect("home"):
            pass

    with pytest.raises(ZeptoError, match="Could not talk to Zepto"):
        asyncio.run(use_session())
    assert len(opened.attempts) == 2


class CannedSession:
    """Returns fixed structured results per tool, shaped like the MCP SDK's CallToolResult."""

    def __init__(self, results: dict[str, object]) -> None:
        self.results = results
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        return SimpleNamespace(
            content=[SimpleNamespace(text=f"{name} text")],
            is_error=False,
            structured_content=self.results.get(name),
        )


# Shapes captured from the live Zepto MCP server.
VIEW_CART = {
    "items": [
        {
            "productVariantId": "pv-coke",
            "storeProductId": "sp-coke",
            "quantity": 2,
            "name": "Diet Coke  Can| Cola Sparkling Soft Drink ",
            "price": 5000,
        }
    ],
    "isEmpty": False,
    "totalItems": 1,
}
PREVIEW = {
    "isPreview": True,
    "toPayAmount": 13000,
    "deliveryFee": 3000,
    "deliverable": True,
    "paymentMethod": "COD",
    "deliveryAddress": {"id": "home", "label": "home"},
}


def run_with(results: dict[str, object], method: str):
    session = CannedSession(results)
    client = ZeptoClient(session=session, address_id="home")
    return asyncio.run(getattr(client, method)()), session


def test_view_cart_parses_items():
    cart, _ = run_with({"view_cart": VIEW_CART}, "view_cart")

    item = cart.items[0]
    assert (item.product_variant_id, item.quantity, item.price_paise) == ("pv-coke", 2, 5000)
    assert item.name == "Diet Coke  Can| Cola Sparkling Soft Drink"


@pytest.mark.parametrize(
    "structured",
    [
        None,
        {"isEmpty": True},
        {"items": [{"name": "no ids"}]},
        {"items": [{"productVariantId": "a", "storeProductId": "b", "quantity": "two"}]},
    ],
)
def test_malformed_cart_becomes_zepto_error(structured):
    with pytest.raises(ZeptoError, match="Unexpected view_cart"):
        run_with({"view_cart": structured}, "view_cart")


def test_preview_parses_total_address_and_payment():
    preview, session = run_with({"create_order": PREVIEW}, "preview_order")

    assert (preview.to_pay_paise, preview.delivery_fee_paise) == (13000, 3000)
    assert (preview.address_id, preview.payment_method, preview.deliverable) == (
        "home",
        "COD",
        True,
    )
    assert session.calls == [("create_order", {"confirmOrder": False, "userAddressId": "home"})]


@pytest.mark.parametrize(
    "structured", [None, {"isPreview": False}, {**PREVIEW, "toPayAmount": None}]
)
def test_malformed_preview_becomes_zepto_error(structured):
    with pytest.raises(ZeptoError, match="Unexpected order preview"):
        run_with({"create_order": structured}, "preview_order")


def test_place_order_rejects_a_preview_response():
    with pytest.raises(ZeptoError, match="preview instead of placing"):
        run_with({"create_order": PREVIEW}, "place_cod_order")


def test_place_order_confirms_with_the_configured_address():
    response, session = run_with({"create_order": {"orderId": "z-1"}}, "place_cod_order")

    assert response["structured"] == {"orderId": "z-1"}
    assert session.calls[-1] == ("create_order", {"confirmOrder": True, "userAddressId": "home"})


def test_failure_after_setup_is_not_retried(opened):
    opened()

    with pytest.raises(ZeptoError, match="Could not talk to Zepto"):
        asyncio.run(_raise_inside_session(MCPError(code=-1, message="dropped")))
    assert len(opened.attempts) == 1
