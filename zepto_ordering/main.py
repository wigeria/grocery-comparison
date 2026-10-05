"""Entry point: run migrations, wire the services, and start the web server.

Run with: .venv/bin/python -m zepto_ordering.main
"""

from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI

from zepto_ordering.api import create_app
from zepto_ordering.blinkit_client import BlinkitClient
from zepto_ordering.cart_agent import run_cart_turn
from zepto_ordering.comparison import BlinkitComparer
from zepto_ordering.config import DB_FILE, MIGRATIONS_DIR, UPLOADS_DIR, Config, load_config
from zepto_ordering.db import Database
from zepto_ordering.orders import OrderService
from zepto_ordering.zepto_client import ZeptoClient

logger = logging.getLogger(__name__)


def build_app(config: Config) -> FastAPI:
    """Migrate the database and build the app."""
    db = Database(DB_FILE)
    applied = db.migrate(MIGRATIONS_DIR)
    if applied:
        logger.info("Applied migrations: %s", ", ".join(applied))

    blinkit = BlinkitClient(config.blinkit_latitude, config.blinkit_longitude)
    comparer = BlinkitComparer(blinkit, config.claude_model)
    service = OrderService(
        db,
        address_id=config.delivery_address_id,
        uploads_dir=UPLOADS_DIR,
        zepto_connect=lambda: ZeptoClient.connect(config.delivery_address_id),
        run_turn=run_cart_turn,
        compare_carts=comparer.compare,
        claude_model=config.claude_model,
    )
    service.recover_interrupted_order()
    return create_app(service, UPLOADS_DIR, on_shutdown=[blinkit.close])


def main() -> None:
    """Start the server on the configured host and port."""
    logging.basicConfig(level=logging.INFO)
    config = load_config()
    uvicorn.run(build_app(config), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
