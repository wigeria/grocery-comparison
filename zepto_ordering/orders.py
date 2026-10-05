"""Order flow: start fresh, build the cart through chat or photos, review, approve.

Rules enforced here:
- Only one order is active at a time, and starting one empties the Zepto cart.
- Every step that touches the cart runs under one lock, so steps never interleave.
- An order is placed only with the review token from the latest review, and only
  if the live cart and total still match it. Otherwise the new review is returned.
- Any cart change clears the stored review and Blinkit price comparison.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

from zepto_ordering.cart_agent import AgentTurn, ImageInput
from zepto_ordering.db import Database
from zepto_ordering.zepto_client import Cart, OrderPreview, ZeptoClient, ZeptoError

logger = logging.getLogger(__name__)

PHOTO_PROMPT = "Add everything on this grocery list to the cart."
INTERRUPTED_ERROR = "Interrupted while placing. Check the Zepto app for this order."
# Fields that describe the cart as it was, so they go stale when the cart changes.
CART_SNAPSHOT_RESET = {"review_token": None, "review_json": None, "comparison_json": None}

ZeptoConnector = Callable[[], AbstractAsyncContextManager[ZeptoClient]]
TurnRunner = Callable[..., Awaitable[AgentTurn]]
CartComparer = Callable[[Cart, OrderPreview], Awaitable[dict[str, Any]]]


class OrderError(Exception):
    """Base class for order flow errors shown to the user."""


class InvalidRequestError(OrderError):
    """The request itself is invalid, e.g. an empty message."""


class OrderNotFoundError(OrderError):
    """No order with that id."""


class OrderConflictError(OrderError):
    """The order isn't in a state that allows this action."""


class ReviewChangedError(OrderError):
    """The cart or total changed since the user's review. Carries the new review."""

    def __init__(self, review: dict[str, Any]) -> None:
        super().__init__("The cart or total changed since your review. Please review it again.")
        self.review = review


class OrderService:
    def __init__(
        self,
        db: Database,
        *,
        address_id: str,
        uploads_dir: Path,
        zepto_connect: ZeptoConnector,
        run_turn: TurnRunner,
        compare_carts: CartComparer,
        claude_model: str | None = None,
    ) -> None:
        self._db = db
        self._address_id = address_id
        self._uploads_dir = uploads_dir
        self._zepto_connect = zepto_connect
        self._run_turn = run_turn
        self._compare_carts = compare_carts
        self._claude_model = claude_model
        self._lock = asyncio.Lock()

    async def start_order(self) -> dict[str, Any]:
        """Close any active order, empty the cart, and open a new order."""
        async with self._lock:
            active = self._db.get_active_order()
            if active and active["status"] == "open":
                self._db.update_order(active["id"], status="cancelled")
            elif active and active["status"] == "placing":
                # Holding the lock means no placement is running, so it was interrupted.
                self._db.update_order(active["id"], status="failed", error=INTERRUPTED_ERROR)
            async with self._zepto_connect() as zepto:
                await zepto.clear_cart()
            order_id = self._db.create_order()
        return await self.get_order(order_id)

    async def get_order(self, order_id: int) -> dict[str, Any]:
        """Return an order with its messages, plus the live cart while it's open."""
        order = self._require_order(order_id)
        cart = None
        if order["status"] == "open":
            async with self._zepto_connect() as zepto:
                cart = await zepto.view_cart()
        return _order_view(order, self._db.list_messages(order_id), cart)

    def get_active_order_id(self) -> int | None:
        """Return the id of the open or placing order, if there is one."""
        active = self._db.get_active_order()
        return active["id"] if active else None

    def list_orders(self) -> list[dict[str, Any]]:
        """Return order summaries, newest first."""
        return [_order_summary(order) for order in self._db.list_orders()]

    async def send_message(
        self, order_id: int, text: str, photo_jpeg: bytes | None = None
    ) -> dict[str, Any]:
        """Run one chat turn with Claude, optionally with a photo of a list."""
        text = text.strip()
        if not text and photo_jpeg is None:
            raise InvalidRequestError("Type a message or add a photo.")

        async with self._lock:
            order = self._require_order(order_id, "open")
            image_file = self._save_photo(order_id, photo_jpeg) if photo_jpeg else None
            self._db.add_message(order_id, "user", text, image_file)

            try:
                turn = await self._run_turn(
                    text or PHOTO_PROMPT,
                    image=ImageInput(photo_jpeg, "image/jpeg") if photo_jpeg else None,
                    session_id=order["claude_session_id"],
                    address_id=self._address_id,
                    model=self._claude_model,
                )
            except Exception as error:
                # Claude may have changed the cart before failing, so the old review
                # and comparison no longer describe it.
                self._db.update_order(order_id, **CART_SNAPSHOT_RESET)
                self._db.add_message(order_id, "assistant", f"Something went wrong: {error}")
                raise

            self._db.update_order(
                order_id, claude_session_id=turn.session_id, **CART_SNAPSHOT_RESET
            )
            self._db.add_message(order_id, "assistant", _reply_text(turn))
        return await self.get_order(order_id)

    async def clear_cart(self, order_id: int) -> dict[str, Any]:
        """Empty the cart but keep the order and its chat open."""
        async with self._lock:
            self._require_order(order_id, "open")
            async with self._zepto_connect() as zepto:
                await zepto.clear_cart()
            self._db.update_order(order_id, **CART_SNAPSHOT_RESET)
        return await self.get_order(order_id)

    async def cancel_order(self, order_id: int) -> dict[str, Any]:
        """Empty the cart and close the order without placing it."""
        async with self._lock:
            self._require_order(order_id, "open")
            async with self._zepto_connect() as zepto:
                await zepto.clear_cart()
            self._db.update_order(order_id, status="cancelled")
        return await self.get_order(order_id)

    async def review_order(self, order_id: int) -> dict[str, Any]:
        """Fetch the live cart and Zepto's COD preview, and store them as the review."""
        async with self._lock:
            self._require_order(order_id, "open")
            async with self._zepto_connect() as zepto:
                review = await self._build_review(zepto)
            self._db.update_order(order_id, review_token=review["token"], review_json=review)
        return review

    async def compare_order(self, order_id: int) -> dict[str, Any]:
        """Price the current Zepto cart on Blinkit and store the comparison. Orders nothing."""
        async with self._lock:
            self._require_order(order_id, "open")
            async with self._zepto_connect() as zepto:
                cart = await zepto.view_cart()
                if cart.is_empty:
                    raise OrderConflictError("The cart is empty.")
                preview = await zepto.preview_order()
            comparison = await self._compare_carts(cart, preview)
            self._db.update_order(order_id, comparison_json=comparison)
        return comparison

    async def approve_order(self, order_id: int, review_token: str) -> dict[str, Any]:
        """Place the order as COD if the cart still matches the reviewed one."""
        async with self._lock:
            order = self._require_order(order_id, "open")
            if not order["review_token"] or order["review_token"] != review_token:
                raise OrderConflictError("Review the cart before approving it.")

            placed = False
            try:
                async with self._zepto_connect() as zepto:
                    review = await self._build_review(zepto)
                    if review["token"] != review_token:
                        self._db.update_order(
                            order_id, review_token=review["token"], review_json=review
                        )
                        raise ReviewChangedError(review)

                    self._db.update_order(order_id, status="placing")
                    try:
                        response = await zepto.place_cod_order()
                    except BaseException as error:
                        # Includes cancellation (e.g. a server restart). The outcome is
                        # unknown, so the order is never retried automatically.
                        self._db.update_order(
                            order_id, status="failed", error=_placement_error(error)
                        )
                        raise
                    # Recorded before the session closes, so a failure while closing the
                    # connection can't hide an order Zepto has already accepted.
                    self._db.update_order(order_id, status="placed", zepto_response_json=response)
                    placed = True
            except Exception:
                if not placed:
                    raise
                logger.warning(
                    "Zepto session failed to close after placing order %s.", order_id, exc_info=True
                )
        return await self.get_order(order_id)

    def recover_interrupted_order(self) -> None:
        """Mark an order left in "placing" by a previous run as failed.

        Called at startup, when no placement can be in progress. Only one order
        can be active at a time, so there is at most one.
        """
        active = self._db.get_active_order()
        if active and active["status"] == "placing":
            self._db.update_order(active["id"], status="failed", error=INTERRUPTED_ERROR)

    async def _build_review(self, zepto: ZeptoClient) -> dict[str, Any]:
        """Combine the live cart and COD preview, after checking they're safe to approve."""
        cart = await zepto.view_cart()
        if cart.is_empty:
            raise OrderConflictError("The cart is empty.")
        preview = await zepto.preview_order()
        if preview.address_id != self._address_id:
            raise ZeptoError("Zepto's preview is for a different delivery address.")
        if preview.payment_method != "COD":
            raise ZeptoError(f"Zepto's preview uses {preview.payment_method}, not COD.")
        if not preview.deliverable:
            raise OrderConflictError("Zepto says this cart can't be delivered right now.")

        review = {
            "cart": _cart_view(cart),
            "delivery_fee_paise": preview.delivery_fee_paise,
            "to_pay_paise": preview.to_pay_paise,
        }
        review["token"] = _review_token(cart, preview.to_pay_paise)
        return review

    def _require_order(self, order_id: int, status: str | None = None) -> dict[str, Any]:
        """Load an order, optionally checking its status."""
        order = self._db.get_order(order_id)
        if order is None:
            raise OrderNotFoundError(f"Order {order_id} not found.")
        if status and order["status"] != status:
            raise OrderConflictError(f"Order {order_id} is {order['status']}, not {status}.")
        return order

    def _save_photo(self, order_id: int, photo_jpeg: bytes) -> str:
        """Store an uploaded photo and return its file name."""
        self._uploads_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{order_id}-{uuid.uuid4().hex}.jpg"
        (self._uploads_dir / file_name).write_bytes(photo_jpeg)
        return file_name


def _placement_error(error: BaseException) -> str:
    """Message stored on an order whose placement failed with an unknown outcome."""
    reason = str(error) or type(error).__name__
    return f"{reason}. The order may have gone through. Check the Zepto app before ordering again."


def _review_token(cart: Cart, to_pay_paise: int) -> str:
    """Hash of the exact items, quantities, prices and total the user reviewed."""
    items = sorted(
        (item.product_variant_id, item.quantity, item.price_paise) for item in cart.items
    )
    payload = json.dumps({"items": items, "to_pay_paise": to_pay_paise})
    return hashlib.sha256(payload.encode()).hexdigest()


def _reply_text(turn: AgentTurn) -> str:
    """Claude's reply, with items it couldn't find listed at the end."""
    if not turn.not_found:
        return turn.reply
    return f"{turn.reply}\n\nCouldn't find: {', '.join(turn.not_found)}"


def _cart_view(cart: Cart) -> dict[str, Any]:
    """Cart as returned by the API."""
    return {
        "items": [
            {
                "name": item.name,
                "quantity": item.quantity,
                "price_paise": item.price_paise,
                "line_total_paise": (
                    item.price_paise * item.quantity if item.price_paise is not None else None
                ),
            }
            for item in cart.items
        ],
        "item_total_paise": cart.item_total_paise,
    }


def _order_summary(order: dict[str, Any]) -> dict[str, Any]:
    """Order fields shared by the list and detail views."""
    return {
        "id": order["id"],
        "status": order["status"],
        "created_at": order["created_at"],
        "updated_at": order["updated_at"],
        "error": order["error"],
        "review": order["review_json"],
        "comparison": order["comparison_json"],
    }


def _order_view(
    order: dict[str, Any], messages: list[dict[str, Any]], cart: Cart | None
) -> dict[str, Any]:
    """Full order as returned by the API."""
    return {
        **_order_summary(order),
        "messages": [
            {
                "role": message["role"],
                "text": message["text"],
                "image_file": message["image_file"],
                "created_at": message["created_at"],
            }
            for message in messages
        ],
        "cart": _cart_view(cart) if cart is not None else None,
        "zepto_response": order["zepto_response_json"],
    }
