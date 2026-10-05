"""Run the real web app against fake Zepto, Claude and Blinkit backends.

Useful for trying the UI or working on the frontend without a Zepto account.
Nothing here talks to a real service, and the database is a temporary file.

    .venv/bin/python scripts/demo_server.py      # then open http://localhost:8001
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.fakes import ADDRESS_ID, FakeZepto, make_item  # noqa: E402
from zepto_ordering.api import create_app  # noqa: E402
from zepto_ordering.blinkit_client import BlinkitBill, BlinkitProduct  # noqa: E402
from zepto_ordering.cart_agent import AgentTurn  # noqa: E402
from zepto_ordering.comparison import Match, build_comparison  # noqa: E402
from zepto_ordering.config import MIGRATIONS_DIR  # noqa: E402
from zepto_ordering.db import Database  # noqa: E402
from zepto_ordering.orders import OrderService  # noqa: E402
from zepto_ordering.zepto_client import Cart, OrderPreview  # noqa: E402

PORT = 8001
# Simulated time for Claude and Blinkit, so the progress indicator is visible.
FAKE_DELAY_SECONDS = 2

DEMO_ITEMS = [
    ("Amul Taaza Toned Fresh Milk | Pouch 500 ml", 2, 2900),
    ("Diet Coke Can | Cola Sparkling Soft Drink", 2, 5000),
    ("Harvest Gold Brown Bread 400 g", 1, 5500),
    ("Farm Fresh Eggs (Pack of 12)", 1, 9600),
]


def blinkit_product(product_id: int, name: str, unit: str, rupees: int) -> BlinkitProduct:
    """A Blinkit search result as the matcher would see it."""
    return BlinkitProduct.from_cart_item(
        {
            "product_id": product_id,
            "display_name": name,
            "unit": unit,
            "price": rupees,
            "inventory": 10,
        }
    )


def build_service(data_dir: Path) -> OrderService:
    """Wire OrderService to in-memory fakes with fixed demo responses."""
    zepto = FakeZepto()

    async def run_turn(text: str, **kwargs) -> AgentTurn:
        await asyncio.sleep(FAKE_DELAY_SECONDS)
        zepto.items[:] = [make_item(name, quantity, price) for name, quantity, price in DEMO_ITEMS]
        return AgentTurn(
            reply=(
                "I added 2 Amul Taaza 500 ml pouches, 2 Diet Coke cans, a Harvest Gold brown "
                "bread and a dozen eggs. I picked the milk you usually order."
            ),
            not_found=["paneer"],
            session_id="demo",
        )

    async def compare(cart: Cart, preview: OrderPreview) -> dict:
        await asyncio.sleep(FAKE_DELAY_SECONDS)
        matches = [
            Match(
                0,
                blinkit_product(1, "Amul Taaza Homogenised Toned Milk", "1 ltr", 77),
                1,
                False,
                "500 ml pouch not on Blinkit; 1 x 1 L is the same amount.",
            ),
            Match(1, blinkit_product(2, "Diet Coke Diets & Lights", "330 ml", 50), 2, True, ""),
            Match(
                2,
                blinkit_product(3, "English Oven Brown Bread", "400 g", 60),
                1,
                False,
                "Harvest Gold isn't on Blinkit; English Oven at the same weight.",
            ),
            Match(3, blinkit_product(4, "Farm Fresh Eggs", "12 pieces", 92), 1, True, ""),
        ]
        bill = BlinkitBill(
            to_pay_paise=33100,
            item_total_paise=32900,
            delivery_fee_paise=0,
            other_charges_paise=200,
            unavailable_items=0,
            unit_prices_paise={1: 7700, 2: 5000, 3: 6000, 4: 9200},
        )
        return build_comparison(cart, preview, matches, bill)

    db = Database(data_dir / "demo.sqlite3")
    db.migrate(MIGRATIONS_DIR)
    return OrderService(
        db,
        address_id=ADDRESS_ID,
        uploads_dir=data_dir / "uploads",
        zepto_connect=zepto.connect,
        run_turn=run_turn,
        compare_carts=compare,
    )


def main() -> None:
    """Serve the demo on PORT until interrupted."""
    with tempfile.TemporaryDirectory() as temp:
        data_dir = Path(temp)
        app = create_app(build_service(data_dir), data_dir / "uploads")
        print(f"Demo running on http://localhost:{PORT} (fake data, no real orders)")
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
