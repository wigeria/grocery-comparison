from __future__ import annotations

import asyncio

import pytest

from tests.fakes import FakeAgent, FakeZepto, make_item
from zepto_ordering.db import Database
from zepto_ordering.orders import (
    InvalidRequestError,
    OrderConflictError,
    OrderService,
    ReviewChangedError,
)
from zepto_ordering.zepto_client import ZeptoError


def test_start_order_empties_cart_and_cancels_previous(service: OrderService, zepto: FakeZepto):
    first = asyncio.run(service.start_order())
    zepto.items.append(make_item("leftover"))

    second = asyncio.run(service.start_order())

    assert zepto.items == []
    assert second["status"] == "open"
    assert asyncio.run(service.get_order(first["id"]))["status"] == "cancelled"


def test_start_order_marks_interrupted_placement_failed(service: OrderService, db: Database):
    order = asyncio.run(service.start_order())
    db.update_order(order["id"], status="placing")

    asyncio.run(service.start_order())

    stuck = db.get_order(order["id"])
    assert stuck["status"] == "failed"
    assert "Check the Zepto app" in stuck["error"]


def test_send_message_records_chat_and_resumes_session(service: OrderService, agent: FakeAgent):
    order = asyncio.run(service.start_order())

    asyncio.run(service.send_message(order["id"], "milk"))
    view = asyncio.run(service.send_message(order["id"], "bread"))

    assert [message["role"] for message in view["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert [item["name"] for item in view["cart"]["items"]] == ["milk", "bread"]
    assert agent.calls[0]["session_id"] is None
    assert agent.calls[1]["session_id"] == "session-1"


def test_send_message_rejects_empty_input(service: OrderService):
    order = asyncio.run(service.start_order())

    with pytest.raises(InvalidRequestError):
        asyncio.run(service.send_message(order["id"], "   "))


def test_photo_message_saves_image_and_uses_default_prompt(
    service: OrderService, agent: FakeAgent, tmp_path
):
    order = asyncio.run(service.start_order())

    view = asyncio.run(service.send_message(order["id"], "", b"jpeg-bytes"))

    image_file = view["messages"][0]["image_file"]
    assert (tmp_path / "uploads" / image_file).read_bytes() == b"jpeg-bytes"
    assert agent.calls[0]["image"].media_type == "image/jpeg"
    assert agent.calls[0]["text"].startswith("Add everything")


def test_review_includes_delivery_fee_in_total(service: OrderService, zepto: FakeZepto):
    order = asyncio.run(service.start_order())
    zepto.items.append(make_item("coke", quantity=2, price_paise=5000))

    review = asyncio.run(service.review_order(order["id"]))

    assert review["cart"]["item_total_paise"] == 10000
    assert review["to_pay_paise"] == 13000
    assert review["token"]


def test_review_of_empty_cart_fails(service: OrderService):
    order = asyncio.run(service.start_order())

    with pytest.raises(OrderConflictError, match="empty"):
        asyncio.run(service.review_order(order["id"]))


def test_review_of_undeliverable_cart_fails(service: OrderService, zepto: FakeZepto):
    order = asyncio.run(service.start_order())
    zepto.items.append(make_item("coke"))
    zepto.deliverable = False

    with pytest.raises(OrderConflictError, match="delivered"):
        asyncio.run(service.review_order(order["id"]))


def test_approve_places_reviewed_order(service: OrderService, zepto: FakeZepto):
    order = asyncio.run(service.start_order())
    zepto.items.append(make_item("coke"))
    review = asyncio.run(service.review_order(order["id"]))

    view = asyncio.run(service.approve_order(order["id"], review["token"]))

    assert view["status"] == "placed"
    assert view["zepto_response"]["structured"]["orderId"] == "zepto-1"
    assert [item.name for item in zepto.placed_orders[0]] == ["coke"]


def test_approve_without_review_is_refused(service: OrderService, zepto: FakeZepto):
    order = asyncio.run(service.start_order())
    zepto.items.append(make_item("coke"))

    with pytest.raises(OrderConflictError, match="Review"):
        asyncio.run(service.approve_order(order["id"], "made-up-token"))
    assert zepto.placed_orders == []


def test_approve_after_cart_changed_returns_new_review(service: OrderService, zepto: FakeZepto):
    order = asyncio.run(service.start_order())
    zepto.items.append(make_item("coke"))
    review = asyncio.run(service.review_order(order["id"]))
    zepto.items.append(make_item("chips"))  # e.g. someone used the Zepto app meanwhile

    with pytest.raises(ReviewChangedError) as raised:
        asyncio.run(service.approve_order(order["id"], review["token"]))

    assert zepto.placed_orders == []
    new_token = raised.value.review["token"]
    assert new_token != review["token"]
    view = asyncio.run(service.approve_order(order["id"], new_token))
    assert view["status"] == "placed"


def test_chat_after_review_invalidates_it(service: OrderService, zepto: FakeZepto):
    order = asyncio.run(service.start_order())
    zepto.items.append(make_item("coke"))
    review = asyncio.run(service.review_order(order["id"]))

    asyncio.run(service.send_message(order["id"], "milk"))

    with pytest.raises(OrderConflictError):
        asyncio.run(service.approve_order(order["id"], review["token"]))
    assert zepto.placed_orders == []


def test_failed_placement_is_recorded_and_not_retried(service: OrderService, zepto: FakeZepto):
    order = asyncio.run(service.start_order())
    zepto.items.append(make_item("coke"))
    review = asyncio.run(service.review_order(order["id"]))
    zepto.place_error = RuntimeError("connection dropped")

    with pytest.raises(RuntimeError):
        asyncio.run(service.approve_order(order["id"], review["token"]))

    view = asyncio.run(service.get_order(order["id"]))
    assert view["status"] == "failed"
    assert "connection dropped" in view["error"]
    assert "Check the Zepto app" in view["error"]
    with pytest.raises(OrderConflictError):
        asyncio.run(service.approve_order(order["id"], review["token"]))


def test_cancelled_placement_is_recorded_as_failed(service: OrderService, zepto: FakeZepto):
    order = asyncio.run(service.start_order())
    zepto.items.append(make_item("coke"))
    review = asyncio.run(service.review_order(order["id"]))
    zepto.place_error = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.approve_order(order["id"], review["token"]))

    stored = asyncio.run(service.get_order(order["id"]))
    assert stored["status"] == "failed"
    assert stored["error"].startswith("CancelledError.")


def test_session_close_failure_after_placing_still_reports_placed(
    service: OrderService, zepto: FakeZepto
):
    order = asyncio.run(service.start_order())
    zepto.items.append(make_item("coke"))
    review = asyncio.run(service.review_order(order["id"]))
    zepto.close_error = ZeptoError("Could not talk to Zepto: stream closed")

    view = asyncio.run(service.approve_order(order["id"], review["token"]))

    assert view["status"] == "placed"
    assert view["zepto_response"]["structured"]["orderId"] == "zepto-1"


def test_session_close_failure_before_placing_is_raised(service: OrderService, zepto: FakeZepto):
    order = asyncio.run(service.start_order())
    zepto.items.append(make_item("coke"))
    review = asyncio.run(service.review_order(order["id"]))
    zepto.items.append(make_item("chips"))
    zepto.close_error = ZeptoError("stream closed")

    with pytest.raises(ReviewChangedError):
        asyncio.run(service.approve_order(order["id"], review["token"]))
    assert zepto.placed_orders == []


def test_startup_marks_interrupted_placement_failed(service: OrderService, db: Database):
    order = asyncio.run(service.start_order())
    db.update_order(order["id"], status="placing")

    service.recover_interrupted_order()

    stored = db.get_order(order["id"])
    assert stored["status"] == "failed"
    assert "Check the Zepto app" in stored["error"]


def test_failed_chat_turn_replies_and_clears_stale_review(
    service: OrderService, zepto: FakeZepto, agent: FakeAgent, db: Database
):
    order = asyncio.run(service.start_order())
    zepto.items.append(make_item("coke"))
    asyncio.run(service.review_order(order["id"]))
    db.update_order(order["id"], comparison_json={"cheaper": "zepto"})
    agent.error = ZeptoError("Zepto is down")

    with pytest.raises(ZeptoError):
        asyncio.run(service.send_message(order["id"], "milk"))

    stored = db.get_order(order["id"])
    assert stored["review_token"] is None
    assert stored["comparison_json"] is None
    last_message = db.list_messages(order["id"])[-1]
    assert last_message["role"] == "assistant"
    assert "Zepto is down" in last_message["text"]


def test_clear_cart_keeps_order_open(service: OrderService, zepto: FakeZepto):
    order = asyncio.run(service.start_order())
    zepto.items.append(make_item("coke"))

    view = asyncio.run(service.clear_cart(order["id"]))

    assert view["status"] == "open"
    assert view["cart"]["items"] == []


def test_cancel_closes_order_and_blocks_chat(service: OrderService, zepto: FakeZepto):
    order = asyncio.run(service.start_order())
    zepto.items.append(make_item("coke"))

    view = asyncio.run(service.cancel_order(order["id"]))

    assert view["status"] == "cancelled"
    assert zepto.items == []
    with pytest.raises(OrderConflictError):
        asyncio.run(service.send_message(order["id"], "milk"))
