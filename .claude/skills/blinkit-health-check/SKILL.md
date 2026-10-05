---
name: blinkit-health-check
description: Check whether the unofficial Blinkit API used for price comparison still works, and diagnose it when it doesn't. Read-only, no login, nothing ordered. Use when price comparison fails, Blinkit returns errors or 403s, or the user asks if Blinkit still works.
---

# Blinkit health check

The price comparison uses Blinkit's website requests (see `zepto_ordering/blinkit_client.py`
and docs/architecture.md). Blinkit can change them at any time, so this checks each step and
points to what broke.

## 1. Run the check

Locally:

```bash
.venv/bin/python scripts/blinkit_check.py            # searches "milk"
.venv/bin/python scripts/blinkit_check.py "eggs"     # another query
```

On the server, if the user wants to test from there (ask for the SSH host and project path):

```bash
ssh <host> "cd <path> && docker compose exec app python scripts/blinkit_check.py"
```

It gets an anonymous device key, checks delivery to the coordinates in `config.toml`, runs a
search and prices one in-stock item. It stops at the first failure and prints a hint.

## 2. If it fails

Read the hint, then narrow it down. Keep everything read-only and low-volume: a handful of
requests, no loops.

- **Device key, HTTP 403**: requests are being refused as non-browser traffic. Check whether a
  newer `curl_cffi` or a different browser profile (`impersonate=` in `BlinkitClient`) works.
  Check whether a plain `httpx` request also gets 403 (it always has) or something new.
- **Device key, HTTP 400**: `REQ_KEY` probably changed.
- **Delivery check fails**: the coordinates may be outside Blinkit's area, or `/visibility`
  changed shape.
- **Search returns nothing**: `STATIC_HEADERS` (`web_app_version`, `app_version`,
  `rn_bundle_version`) may be stale, or the layout no longer puts products under
  `atc_action.add_to_cart.cart_item`. Save one raw response to a temp file and look at its
  structure.
- **Pricing fails**: check the `/v5/carts` body and whether `cart_data.bill_details` still has
  `payable_amount`.

For current values, compare with what blinkit.com sends in a browser's network tab (ask the
user to capture a request if needed), and check
[yniks/blinkit-mcp](https://github.com/yniks/blinkit-mcp) for recent fixes.

## 3. Fix

Change only `blinkit_client.py` (and its tests in `tests/test_comparison.py`). Re-run the
check, then the test suite. Keep the module's docstring accurate about what the client does.

## Report

Tell the user which step failed, the likely cause, and what changed. Zepto ordering doesn't
depend on Blinkit, so it keeps working while Blinkit is broken.
