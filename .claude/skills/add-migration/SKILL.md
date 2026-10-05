---
name: add-migration
description: Add a SQLite schema change the way this project does it - a numbered SQL file in db/migrations, the matching updates in db.py, and tests. Use when a change needs a new table, column or index, or the user asks for a migration.
---

# Add a database migration

Migrations are plain SQL files in `db/migrations/`. `Database.migrate` (in
`zepto_ordering/db.py`) runs them in filename order on startup, each in its own transaction,
and records applied ones in `schema_migrations`.

## 1. Create the file

- Name: the next 4-digit number plus a short snake_case description, e.g.
  `0003_order_notes.sql`. The name must match `^\d{4}_[a-z0-9_]+\.sql$` or startup fails.
- Never edit or renumber a migration that has been committed or deployed. Fix mistakes with a
  new migration.
- Add a one-line SQL comment explaining what the change is for.
- Don't write `BEGIN` or `COMMIT`; the runner wraps the file in a transaction.
- SQLite limits: `ALTER TABLE` can add columns but not change or drop constraints. For that,
  create a new table, copy rows, drop the old one and rename.
- New columns on existing tables need a default or must be nullable, since rows exist on the
  server.

## 2. Update db.py

For a new column on `orders`:

- Add it to `UPDATABLE_ORDER_FIELDS` if code should write it through `update_order`.
- If it stores JSON, name it `*_json` and add it to `JSON_ORDER_FIELDS`, so it is encoded on
  write and decoded on read.

For a new table, add focused repository methods (`add_...`, `list_...`) in the same style as
`add_message` / `list_messages`, using `self._connect()` and `?` placeholders. Never build
SQL from values; column names may only come from an allowlist.

Then use the new field where it belongs (usually `orders.py`) and expose it in
`_order_summary` / `_order_view` if the frontend needs it.

## 3. Test

- `tests/test_db.py::test_migrate_applies_each_migration_once` picks up new files on its own.
- Add a test for the new repository behaviour (e.g. a JSON column round-trip).
- Run `.venv/bin/pytest -q` and `.venv/bin/ruff check . && .venv/bin/ruff format --check .`.

## 4. Check against a copy of real data (optional)

To try the migration on existing data without touching it:

```bash
cp db/zepto_ordering.sqlite3 /tmp/migration-test.sqlite3
.venv/bin/python -c "
from pathlib import Path
from zepto_ordering.db import Database
print(Database(Path('/tmp/migration-test.sqlite3')).migrate(Path('db/migrations')))"
```

## Report

Tell the user the new file name, what it changes, and that it runs automatically on the next
server start (`docker compose up -d --build`).
