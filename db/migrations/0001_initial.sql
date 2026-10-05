CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL
        CHECK (status IN ('open', 'placing', 'placed', 'cancelled', 'failed')),
    -- Claude Code session that builds this order's cart, resumed on every chat turn.
    claude_session_id TEXT,
    -- Hash of the cart and total the user last reviewed. Approve must send it back.
    review_token TEXT,
    review_json TEXT,
    zepto_response_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX orders_status ON orders (status);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders (id),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text TEXT NOT NULL DEFAULT '',
    image_file TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX messages_order_id ON messages (order_id);
