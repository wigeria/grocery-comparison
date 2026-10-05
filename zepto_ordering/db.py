"""SQLite storage for orders and chat messages.

Migrations are plain SQL files in db/migrations, named NNNN_description.sql.
They run in filename order, each in its own transaction, and applied versions
are recorded in the schema_migrations table.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

MIGRATION_NAME = re.compile(r"^\d{4}_[a-z0-9_]+\.sql$")
ACTIVE_STATUSES = ("open", "placing")
UPDATABLE_ORDER_FIELDS = {
    "status",
    "claude_session_id",
    "review_token",
    "review_json",
    "comparison_json",
    "zepto_response_json",
    "error",
}
JSON_ORDER_FIELDS = ("review_json", "comparison_json", "zepto_response_json")


class MigrationError(Exception):
    """Raised when a migration file is invalid or fails to apply."""


class Database:
    """Thin repository over a SQLite file. Opens a short-lived connection per call."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection that commits on success and rolls back on error."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection

    def migrate(self, migrations_dir: Path) -> list[str]:
        """Apply pending migrations and return the names of those applied."""
        files = sorted(migrations_dir.glob("*.sql"))
        invalid = [file.name for file in files if not MIGRATION_NAME.match(file.name)]
        if invalid:
            raise MigrationError(f"Badly named migration files: {', '.join(invalid)}")

        applied_now = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version TEXT PRIMARY KEY,"
                " applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')))"
            )
            connection.commit()
            done = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}

            for file in files:
                if file.name in done:
                    continue
                # executescript commits implicitly, so the transaction is part of the script.
                script = (
                    f"BEGIN;\n{file.read_text()}\n"
                    f"INSERT INTO schema_migrations (version) VALUES ('{file.name}');\nCOMMIT;"
                )
                try:
                    connection.executescript(script)
                except sqlite3.Error as error:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise MigrationError(f"Migration {file.name} failed: {error}") from error
                applied_now.append(file.name)
        return applied_now

    def create_order(self) -> int:
        """Insert a new open order and return its id."""
        with self._connect() as connection:
            cursor = connection.execute("INSERT INTO orders (status) VALUES ('open')")
            return cursor.lastrowid

    def get_order(self, order_id: int) -> dict[str, Any] | None:
        """Return one order, with JSON columns decoded."""
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return _decode_order(row) if row else None

    def get_active_order(self) -> dict[str, Any] | None:
        """Return the most recent open or placing order, if any."""
        placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT * FROM orders WHERE status IN ({placeholders}) ORDER BY id DESC LIMIT 1",
                ACTIVE_STATUSES,
            ).fetchone()
        return _decode_order(row) if row else None

    def list_orders(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the newest orders first."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_decode_order(row) for row in rows]

    def update_order(self, order_id: int, **fields: Any) -> None:
        """Update order columns. Dict values for *_json columns are encoded."""
        unknown = set(fields) - UPDATABLE_ORDER_FIELDS
        if unknown:
            raise ValueError(f"Cannot update order fields: {', '.join(sorted(unknown))}")
        values = {
            key: json.dumps(value) if key.endswith("_json") and value is not None else value
            for key, value in fields.items()
        }
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE orders SET {assignments},"
                " updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
                (*values.values(), order_id),
            )

    def add_message(
        self, order_id: int, role: str, text: str, image_file: str | None = None
    ) -> None:
        """Append a chat message to an order."""
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO messages (order_id, role, text, image_file) VALUES (?, ?, ?, ?)",
                (order_id, role, text, image_file),
            )

    def list_messages(self, order_id: int) -> list[dict[str, Any]]:
        """Return an order's chat messages, oldest first."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE order_id = ? ORDER BY id", (order_id,)
            ).fetchall()
        return [dict(row) for row in rows]


def _decode_order(row: sqlite3.Row) -> dict[str, Any]:
    """Convert an orders row to a dict, parsing its JSON columns."""
    order = dict(row)
    for key in JSON_ORDER_FIELDS:
        if order[key] is not None:
            order[key] = json.loads(order[key])
    return order
