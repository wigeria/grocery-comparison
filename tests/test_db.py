from __future__ import annotations

from pathlib import Path

import pytest

from zepto_ordering.config import MIGRATIONS_DIR
from zepto_ordering.db import Database, MigrationError


def test_migrate_applies_each_migration_once(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite3")

    first = db.migrate(MIGRATIONS_DIR)
    second = db.migrate(MIGRATIONS_DIR)

    assert first == sorted(file.name for file in MIGRATIONS_DIR.glob("*.sql"))
    assert second == []


def test_failed_migration_is_rolled_back(tmp_path: Path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_good.sql").write_text("CREATE TABLE good (id INTEGER);")
    (migrations / "0002_bad.sql").write_text("CREATE TABLE half (id INTEGER); NOT SQL;")
    db = Database(tmp_path / "test.sqlite3")

    with pytest.raises(MigrationError, match="0002_bad.sql"):
        db.migrate(migrations)

    (migrations / "0002_bad.sql").write_text("CREATE TABLE half (id INTEGER);")
    assert db.migrate(migrations) == ["0002_bad.sql"]


def test_badly_named_migration_is_rejected(tmp_path: Path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "add-table.sql").write_text("SELECT 1;")

    with pytest.raises(MigrationError, match="add-table.sql"):
        Database(tmp_path / "test.sqlite3").migrate(migrations)


def test_update_order_encodes_json_and_rejects_unknown_fields(db: Database):
    order_id = db.create_order()

    db.update_order(order_id, review_json={"token": "abc"})

    assert db.get_order(order_id)["review_json"] == {"token": "abc"}
    with pytest.raises(ValueError):
        db.update_order(order_id, created_at="2020-01-01")


def test_active_order_ignores_closed_orders(db: Database):
    closed = db.create_order()
    db.update_order(closed, status="placed")
    assert db.get_active_order() is None

    open_order = db.create_order()
    assert db.get_active_order()["id"] == open_order
