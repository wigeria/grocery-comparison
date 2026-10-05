---
name: live-smoke-test
description: Safe end-to-end test of the deployed app against the real Zepto account - start an order, add a test item through Claude, review, compare with Blinkit, then cancel. Never places an order. Use when the user asks to test the live app, check the deployment end to end, or verify Claude and Zepto work on the server.
disable-model-invocation: true
---

# Live smoke test

Runs one full order flow against the real Zepto account through the deployed app's HTTP API,
and cancels it at the end. It never places an order.

## Hard rules

- **Never call `/approve`.** Never call Zepto's `create_order` with `confirmOrder: true`, and
  never call any other ordering or payment tool.
- **Test through the deployed site, not local tokens.** Only one machine should use the Zepto
  login (refresh tokens can rotate). Don't run local scripts against a copy of
  `data/zepto_tokens.json` if the server owns the login.
- **Always cancel**, even if a step fails, so the cart is left empty.

## 1. Ask first

Before running anything, ask the user (use AskUserQuestion):

- **Site URL**: the HTTPS address of the deployed app, e.g. `https://name.duckdns.org`.
- **Test item**: what to add, e.g. "one diet coke can". Suggest a single cheap item.

Also tell them this empties their real Zepto cart (starting an order clears it), and get a yes
before continuing.

## 2. Pre-check

`GET $SITE/api/status` must return `zepto_logged_in: true` and `active_order_id: null`.
If there is an active order, stop and ask: starting a new order would cancel it.

## 3. Run the flow

Use this script, filling in `SITE` and `ITEM`. It only uses the standard library.

```python
import json, time, urllib.request, urllib.error

SITE = "https://..."  # from the user
ITEM = "one diet coke can"  # from the user


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{SITE}/api{path}", data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


order_id = None
try:
    status, order = call("POST", "/orders")
    assert status == 201, order
    order_id = order["id"]

    start = time.time()
    status, view = call("POST", f"/orders/{order_id}/messages", {"text": ITEM})
    print(f"chat: HTTP {status} in {time.time() - start:.0f}s")
    print("  reply:", view.get("detail") or view["messages"][-1]["text"])
    print(
        "  cart:",
        [(i["name"][:40], i["quantity"]) for i in (view.get("cart") or {}).get("items", [])],
    )

    status, review = call("POST", f"/orders/{order_id}/review")
    print(
        f"review: HTTP {status}",
        review.get("detail")
        or f"to pay Rs {review['to_pay_paise'] / 100:g}, delivery Rs {(review['delivery_fee_paise'] or 0) / 100:g}",
    )

    start = time.time()
    status, comparison = call("POST", f"/orders/{order_id}/compare")
    blinkit_total = (comparison.get("blinkit") or {}).get("to_pay_paise")
    print(
        f"compare: HTTP {status} in {time.time() - start:.0f}s",
        comparison.get("detail")
        or f"Zepto Rs {comparison['zepto']['to_pay_paise'] / 100:g} vs Blinkit "
        f"{'n/a' if blinkit_total is None else f'Rs {blinkit_total / 100:g}'}, "
        f"cheaper: {comparison['cheaper']}",
    )
finally:
    if order_id is not None:
        status, view = call("POST", f"/orders/{order_id}/cancel")
        print(f"cancel: HTTP {status}", view.get("status") or view.get("detail"))
    print("status after:", call("GET", "/status")[1])
```

## 4. Check the result

- Chat returned 200 and the cart has the test item.
- Review returned a total that includes the delivery fee.
- Compare returned 200 (a Blinkit failure here is reported, not fatal; suggest the
  `blinkit-health-check` skill).
- Cancel returned `cancelled`, and status shows no active order. Cancelling empties the cart.

If cancel failed, tell the user right away that the test item may still be in their Zepto cart.

## Report

Give the user each step's result and timing, and anything that looked wrong.
