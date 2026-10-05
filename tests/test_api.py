from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tests.fakes import FakeZepto, make_item
from zepto_ordering import api, images
from zepto_ordering.api import create_app
from zepto_ordering.orders import OrderService
from zepto_ordering.zepto_auth import ZeptoAuthError
from zepto_ordering.zepto_client import ZeptoError


@pytest.fixture
def client(service: OrderService, tmp_path: Path) -> TestClient:
    return TestClient(create_app(service, tmp_path / "uploads"))


def make_png(width: int = 3000, height: int = 2000) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="PNG")
    return output.getvalue()


def test_full_flow_over_http(client: TestClient, zepto: FakeZepto):
    order = client.post("/api/orders").json()
    order_id = order["id"]

    chat = client.post(f"/api/orders/{order_id}/messages", json={"text": "coke"})
    review = client.post(f"/api/orders/{order_id}/review").json()
    placed = client.post(f"/api/orders/{order_id}/approve", json={"review_token": review["token"]})

    assert chat.status_code == 200
    assert placed.status_code == 200
    assert placed.json()["status"] == "placed"
    assert client.get("/api/orders").json()[0]["status"] == "placed"


def test_changed_cart_returns_409_with_new_review(client: TestClient, zepto: FakeZepto):
    order_id = client.post("/api/orders").json()["id"]
    zepto.items.append(make_item("coke"))
    review = client.post(f"/api/orders/{order_id}/review").json()
    zepto.items.append(make_item("chips"))

    response = client.post(
        f"/api/orders/{order_id}/approve", json={"review_token": review["token"]}
    )

    assert response.status_code == 409
    assert len(response.json()["review"]["cart"]["items"]) == 2


def test_photo_upload_is_resized_and_served(client: TestClient):
    order_id = client.post("/api/orders").json()["id"]

    response = client.post(
        f"/api/orders/{order_id}/photo",
        files={"photo": ("list.png", make_png(), "image/png")},
    )

    assert response.status_code == 200
    image_file = response.json()["messages"][0]["image_file"]
    served = client.get(f"/api/uploads/{image_file}")
    with Image.open(io.BytesIO(served.content)) as image:
        assert image.format == "JPEG"
        assert max(image.size) == 1568


def test_non_image_upload_is_rejected(client: TestClient):
    order_id = client.post("/api/orders").json()["id"]

    response = client.post(
        f"/api/orders/{order_id}/photo",
        files={"photo": ("list.txt", b"milk, eggs", "text/plain")},
    )

    assert response.status_code == 400


def test_unknown_order_returns_404(client: TestClient):
    assert client.get("/api/orders/999").status_code == 404


def test_zepto_failure_returns_502_with_message(client: TestClient, agent):
    order_id = client.post("/api/orders").json()["id"]
    agent.error = ZeptoError("Could not talk to Zepto: timed out")

    response = client.post(f"/api/orders/{order_id}/messages", json={"text": "milk"})

    assert response.status_code == 502
    assert "timed out" in response.json()["detail"]


def test_oversized_photo_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(images, "MAX_PHOTO_PIXELS", 1000)
    order_id = client.post("/api/orders").json()["id"]

    response = client.post(
        f"/api/orders/{order_id}/photo",
        files={"photo": ("list.png", make_png(100, 100), "image/png")},
    )

    assert response.status_code == 400
    assert "too large" in response.json()["detail"]


def test_status_reports_logged_out_when_token_file_is_corrupt(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def unreadable():
        raise ZeptoAuthError("unreadable")

    monkeypatch.setattr(api, "load_tokens", unreadable)

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["zepto_logged_in"] is False


def test_frontend_is_served_without_hiding_api_routes(client: TestClient):
    page = client.get("/")

    assert page.status_code == 200
    assert "app.js" in page.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/api/status").json()["active_order_id"] is None
