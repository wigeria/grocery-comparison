from __future__ import annotations

from pathlib import Path

import pytest

from tests.fakes import ADDRESS_ID, FakeAgent, FakeComparer, FakeZepto
from zepto_ordering.config import MIGRATIONS_DIR
from zepto_ordering.db import Database
from zepto_ordering.orders import OrderService


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.sqlite3")
    database.migrate(MIGRATIONS_DIR)
    return database


@pytest.fixture
def zepto() -> FakeZepto:
    return FakeZepto()


@pytest.fixture
def agent(zepto: FakeZepto) -> FakeAgent:
    return FakeAgent(zepto)


@pytest.fixture
def comparer() -> FakeComparer:
    return FakeComparer()


@pytest.fixture
def service(
    db: Database, zepto: FakeZepto, agent: FakeAgent, comparer: FakeComparer, tmp_path: Path
) -> OrderService:
    return OrderService(
        db,
        address_id=ADDRESS_ID,
        uploads_dir=tmp_path / "uploads",
        zepto_connect=zepto.connect,
        run_turn=agent.run_turn,
        compare_carts=comparer.compare,
    )
