"""HTTP API for the ordering flow. All business rules live in orders.py."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from claude_agent_sdk import ClaudeSDKError
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from zepto_ordering.blinkit_client import BlinkitError
from zepto_ordering.claude_runner import AgentError
from zepto_ordering.images import InvalidImageError, normalize_photo
from zepto_ordering.orders import (
    InvalidRequestError,
    OrderConflictError,
    OrderNotFoundError,
    OrderService,
    ReviewChangedError,
)
from zepto_ordering.zepto_auth import ZeptoAuthError, load_tokens
from zepto_ordering.zepto_client import ZeptoError

MAX_PHOTO_BYTES = 20 * 1024 * 1024
STATIC_DIR = Path(__file__).resolve().parent / "static"

ERROR_STATUS_CODES = {
    InvalidRequestError: 400,
    InvalidImageError: 400,
    OrderNotFoundError: 404,
    OrderConflictError: 409,
    ZeptoAuthError: 502,
    ZeptoError: 502,
    BlinkitError: 502,
    AgentError: 502,
    ClaudeSDKError: 502,
}


class MessageRequest(BaseModel):
    text: str


class ApproveRequest(BaseModel):
    review_token: str


def create_app(
    service: OrderService,
    uploads_dir: Path,
    on_shutdown: list[Callable[[], Awaitable[None]]] | None = None,
) -> FastAPI:
    """Build the FastAPI app around an OrderService."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Run shutdown hooks, e.g. closing HTTP sessions, when the server stops."""
        yield
        for hook in on_shutdown or []:
            await hook()

    app = FastAPI(title="Zepto Ordering", lifespan=lifespan)

    for error_type, status_code in ERROR_STATUS_CODES.items():
        app.add_exception_handler(error_type, _error_handler(status_code))
    app.add_exception_handler(ReviewChangedError, _review_changed_handler)

    @app.get("/api/status")
    def get_status() -> dict[str, Any]:
        """Whether Zepto login is set up, and which order is active."""
        try:
            logged_in = load_tokens() is not None
        except ZeptoAuthError:
            logged_in = False
        return {"zepto_logged_in": logged_in, "active_order_id": service.get_active_order_id()}

    @app.get("/api/orders")
    def list_orders() -> list[dict[str, Any]]:
        """Order history, newest first."""
        return service.list_orders()

    @app.post("/api/orders", status_code=201)
    async def start_order() -> dict[str, Any]:
        """Start a new order with an empty cart. Closes any open order."""
        return await service.start_order()

    @app.get("/api/orders/{order_id}")
    async def get_order(order_id: int) -> dict[str, Any]:
        """One order with its chat, plus the live cart while it's open."""
        return await service.get_order(order_id)

    @app.post("/api/orders/{order_id}/messages")
    async def send_message(order_id: int, body: MessageRequest) -> dict[str, Any]:
        """Send a chat message. Claude updates the cart and replies."""
        return await service.send_message(order_id, body.text)

    @app.post("/api/orders/{order_id}/photo")
    async def send_photo(
        order_id: int,
        photo: Annotated[UploadFile, File()],
        text: Annotated[str, Form()] = "",
    ) -> dict[str, Any]:
        """Send a photo of a grocery list, with an optional note."""
        data = await photo.read(MAX_PHOTO_BYTES + 1)
        if len(data) > MAX_PHOTO_BYTES:
            raise InvalidRequestError("Photo is larger than 20 MB.")
        photo_jpeg = await asyncio.to_thread(normalize_photo, data)
        return await service.send_message(order_id, text, photo_jpeg)

    @app.post("/api/orders/{order_id}/clear")
    async def clear_cart(order_id: int) -> dict[str, Any]:
        """Empty the cart and keep chatting on the same order."""
        return await service.clear_cart(order_id)

    @app.post("/api/orders/{order_id}/review")
    async def review_order(order_id: int) -> dict[str, Any]:
        """Get the cart and Zepto's COD total, plus the token needed to approve."""
        return await service.review_order(order_id)

    @app.post("/api/orders/{order_id}/compare")
    async def compare_order(order_id: int) -> dict[str, Any]:
        """Compare the cart's Zepto total with the same items priced on Blinkit."""
        return await service.compare_order(order_id)

    @app.post("/api/orders/{order_id}/approve")
    async def approve_order(order_id: int, body: ApproveRequest) -> dict[str, Any]:
        """Place the reviewed order as COD. Returns 409 with a new review if it changed."""
        return await service.approve_order(order_id, body.review_token)

    @app.post("/api/orders/{order_id}/cancel")
    async def cancel_order(order_id: int) -> dict[str, Any]:
        """Empty the cart and close the order without placing it."""
        return await service.cancel_order(order_id)

    uploads_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/api/uploads", StaticFiles(directory=uploads_dir), name="uploads")
    # Mounted last so every /api route above takes priority.
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
    return app


def _error_handler(status_code: int):
    """Build a handler that returns the exception message with a fixed status code."""

    async def handle(request: Request, error: Exception) -> JSONResponse:
        """Return the error message as JSON."""
        return JSONResponse(status_code=status_code, content={"detail": str(error)})

    return handle


async def _review_changed_handler(request: Request, error: ReviewChangedError) -> JSONResponse:
    """Return 409 with the new review so the UI can show it for approval."""
    return JSONResponse(status_code=409, content={"detail": str(error), "review": error.review})
