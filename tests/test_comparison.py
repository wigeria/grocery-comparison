from __future__ import annotations

import asyncio

import pytest

from tests.fakes import FakeComparer, FakeZepto, make_item
from zepto_ordering.blinkit_client import (
    BlinkitBill,
    BlinkitClient,
    BlinkitError,
    BlinkitProduct,
    extract_products,
)
from zepto_ordering.comparison import Match, _merge_lines, _validate_matches, build_comparison
from zepto_ordering.orders import OrderConflictError, OrderService
from zepto_ordering.zepto_client import Cart, OrderPreview


def make_product(product_id: int, price_rupees: int = 50, inventory: int = 10) -> BlinkitProduct:
    return BlinkitProduct.from_cart_item(
        {
            "product_id": product_id,
            "merchant_id": 1,
            "display_name": f"Product {product_id}",
            "unit": "330 ml",
            "price": price_rupees,
            "mrp": price_rupees,
            "inventory": inventory,
        }
    )


def make_preview(to_pay_paise: int) -> OrderPreview:
    return OrderPreview(
        to_pay_paise=to_pay_paise,
        delivery_fee_paise=3000,
        deliverable=True,
        payment_method="COD",
        address_id="home",
        raw={},
    )


def make_bill(to_pay_paise: int, unit_prices: dict[int, int], unavailable: int = 0) -> BlinkitBill:
    return BlinkitBill(
        to_pay_paise=to_pay_paise,
        item_total_paise=sum(unit_prices.values()),
        delivery_fee_paise=0,
        other_charges_paise=200,
        unavailable_items=unavailable,
        unit_prices_paise=unit_prices,
    )


def test_extract_products_finds_cart_items_in_layout_order():
    layout = {
        "snippets": [
            {
                "data": {
                    "atc_action": {
                        "add_to_cart": {
                            "cart_item": {"product_id": 2, "display_name": "Second", "price": 40}
                        }
                    }
                }
            },
            {
                "data": {
                    "nested": [
                        {
                            "atc_action": {
                                "add_to_cart": {
                                    "cart_item": {
                                        "product_id": 1,
                                        "display_name": "First",
                                        "price": 50.5,
                                    }
                                }
                            }
                        }
                    ]
                }
            },
            {
                "data": {
                    "atc_action": {
                        "add_to_cart": {
                            "cart_item": {
                                "product_id": 2,
                                "display_name": "Second again",
                                "price": 40,
                            }
                        }
                    }
                }
            },
        ]
    }

    products = extract_products(layout)

    assert [(p.product_id, p.name, p.price_paise) for p in products] == [
        (2, "Second", 4000),
        (1, "First", 5050),
    ]


def test_rejected_device_key_is_replaced_once(monkeypatch: pytest.MonkeyPatch):
    client = BlinkitClient(0.0, 0.0)
    keys_fetched: list[int] = []
    search_attempts: list[str] = []

    async def fake_ensure_ready() -> None:
        if client._auth_key is None:
            keys_fetched.append(1)
            client._auth_key = f"key-{len(keys_fetched)}"

    async def fake_request(method, path, **kwargs):
        search_attempts.append(client._auth_key)
        if client._auth_key == "key-1":
            raise BlinkitError("expired", status_code=401)
        return {
            "atc_action": {"add_to_cart": {"cart_item": {"product_id": 1, "display_name": "Milk"}}}
        }

    monkeypatch.setattr(client, "_ensure_ready", fake_ensure_ready)
    monkeypatch.setattr(client, "_request", fake_request)

    products = asyncio.run(client.search("milk"))

    assert [p.name for p in products] == ["Milk"]
    assert search_attempts == ["key-1", "key-2"]
    asyncio.run(client.close())


def test_other_blinkit_errors_are_not_retried(monkeypatch: pytest.MonkeyPatch):
    client = BlinkitClient(0.0, 0.0)
    attempts: list[int] = []

    async def ready() -> None:
        client._auth_key = "key"

    async def fail(method, path, **kwargs):
        attempts.append(1)
        raise BlinkitError("server error", status_code=500)

    monkeypatch.setattr(client, "_ensure_ready", ready)
    monkeypatch.setattr(client, "_request", fail)

    with pytest.raises(BlinkitError, match="server error"):
        asyncio.run(client.search("milk"))
    assert len(attempts) == 1
    asyncio.run(client.close())


def test_validate_matches_drops_unsearched_products_and_fills_gaps():
    seen = {10: make_product(10)}
    raw = [
        {"item": 1, "blinkit_product_id": 10, "quantity": 2, "exact": True, "note": ""},
        {"item": 1, "blinkit_product_id": 10, "quantity": 9, "exact": True, "note": "dup"},
        {"item": 2, "blinkit_product_id": 99, "quantity": 1, "exact": True, "note": ""},
        {"item": 7, "blinkit_product_id": 10, "quantity": 1, "exact": True, "note": ""},
    ]

    matches = _validate_matches(raw, item_count=3, seen=seen)

    assert (matches[0].product.product_id, matches[0].quantity) == (10, 2)
    assert matches[1].product is None and not matches[1].exact
    assert "search results" in matches[1].note
    assert matches[2].product is None and matches[2].note == "Not matched."


def test_merge_lines_adds_quantities_for_shared_products():
    product = make_product(10)
    matches = [
        Match(0, product, 1, True, ""),
        Match(1, product, 2, False, ""),
        Match(2, None, 1, False, ""),
    ]

    lines = _merge_lines(matches)

    assert [(line.product.product_id, line.quantity) for line in lines] == [(10, 3)]


def test_build_comparison_picks_cheaper_total_including_fees():
    cart = Cart(items=[make_item("coke", quantity=2, price_paise=5000)])
    matches = [Match(0, make_product(10), 2, True, "")]

    result = build_comparison(cart, make_preview(13000), matches, make_bill(10200, {10: 5000}))

    assert result["cheaper"] == "blinkit"
    assert result["difference_paise"] == 2800
    assert result["complete"] is True
    assert result["items"][0]["blinkit"]["line_total_paise"] == 10000


def test_build_comparison_is_incomplete_when_items_are_missing():
    cart = Cart(items=[make_item("coke"), make_item("rare cheese")])
    matches = [Match(0, make_product(10), 1, True, ""), Match(1, None, 1, False, "Not matched.")]

    result = build_comparison(cart, make_preview(13000), matches, make_bill(7700, {10: 5000}))

    assert result["complete"] is False
    assert result["unmatched_items"] == 1
    assert result["items"][1]["blinkit"] is None


def test_build_comparison_without_any_blinkit_match():
    cart = Cart(items=[make_item("rare cheese")])
    matches = [Match(0, None, 1, False, "Not matched.")]

    result = build_comparison(cart, make_preview(8000), matches, None)

    assert result["cheaper"] is None
    assert result["blinkit"]["to_pay_paise"] is None
    assert result["complete"] is False


def test_compare_order_stores_result_and_cart_change_clears_it(
    service: OrderService, zepto: FakeZepto, comparer: FakeComparer
):
    order = asyncio.run(service.start_order())
    zepto.items.append(make_item("coke", quantity=2, price_paise=5000))

    comparison = asyncio.run(service.compare_order(order["id"]))

    assert comparison["cheaper"] == "blinkit"
    assert comparer.calls[0][1].to_pay_paise == 13000
    assert asyncio.run(service.get_order(order["id"]))["comparison"] == comparison

    view = asyncio.run(service.send_message(order["id"], "milk"))
    assert view["comparison"] is None


def test_compare_order_on_empty_cart_fails(service: OrderService, comparer: FakeComparer):
    order = asyncio.run(service.start_order())

    with pytest.raises(OrderConflictError, match="empty"):
        asyncio.run(service.compare_order(order["id"]))
    assert comparer.calls == []
