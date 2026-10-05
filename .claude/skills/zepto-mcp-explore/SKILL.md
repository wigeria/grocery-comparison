---
name: zepto-mcp-explore
description: Explore Zepto's MCP server read-only - list its tools, show a tool's schema, or call a read-only tool to see the real response shape. Never orders, pays, or changes the account or cart. Use when working on Zepto integration code, when a Zepto response doesn't parse, or when the user asks what a Zepto tool returns.
---

# Explore the Zepto MCP server

Zepto's MCP server is hosted by Zepto with no public source, so the only way to learn a
tool's real behaviour is to call it. `scripts/zepto_mcp_call.py` does that safely.

## Hard rules

- **Read-only.** The script refuses anything that could order, pay, or change the account or
  cart. `create_order` is only allowed with `"confirmOrder": false`, which returns a preview.
  Don't work around the refusals, and don't call Zepto tools any other way.
- **Run it where the Zepto login lives.** Refresh tokens can rotate, so only one machine
  should use a login. If the server owns it, run the script in the server's app container.
  Ask the user which machine to use, and for the SSH host and project path if it's the server.

## Commands

```bash
python scripts/zepto_mcp_call.py --list                 # tools, marked read-only / preview only / blocked
python scripts/zepto_mcp_call.py --schema search_products
python scripts/zepto_mcp_call.py search_products '{"query": "milk"}'
python scripts/zepto_mcp_call.py create_order '{"confirmOrder": false}'
```

- Locally: prefix with `.venv/bin/`.
- On the server: `ssh <host> "cd <path> && docker compose exec app python scripts/zepto_mcp_call.py ..."`.
- Tools that need a store (search, product details, payment methods, preview) select the
  address in `config.toml` first. `--address <id>` picks another saved address.
- `get_payment_methods` and the preview need a non-empty cart. Don't add items to test them;
  ask the user, or use the `live-smoke-test` skill.

## What's already known

docs/architecture.md lists behaviours found so far: the cart is per account, the store is
per session, `create_order` previews return `toPayAmount` in paise, and `view_cart` returns
`structured_content.items`. Tool results also contain instructions aimed at the model ("ask
which payment method"); the cart agent's prompt tells Claude to ignore them.

## Using what you find

- Response shapes feed the parsing in `zepto_ordering/zepto_client.py`. Add a test with the
  real shape to `tests/test_zepto_client.py` (see `VIEW_CART` and `PREVIEW` there), with
  personal values (names, addresses, ids) replaced by placeholders.
- Never paste personal data from responses (addresses, phone numbers, order ids) into code,
  docs or commits.

## Report

Summarize the tool's inputs, the important fields in its response, and anything surprising.
