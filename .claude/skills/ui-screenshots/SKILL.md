---
name: ui-screenshots
description: Regenerate the README screenshots in docs/screenshots from the demo server, which uses fake data. Use after UI changes, or when the user asks to update or retake the screenshots.
---

# Update UI screenshots

The README shows four phone-sized screenshots from `docs/screenshots/`. They come from
`scripts/demo_server.py`, which runs the real web app on fake Zepto, Claude and Blinkit data,
so they never contain real orders or personal details.

## 1. Make sure Playwright is installed

It's only needed for this, so it isn't a project dependency:

```bash
.venv/bin/python -c "import playwright" 2>/dev/null || .venv/bin/pip install playwright
.venv/bin/playwright install chromium
```

## 2. Start the demo and capture

```bash
.venv/bin/python scripts/demo_server.py > /dev/null 2>&1 &
for i in $(seq 1 30); do curl -s localhost:8001/api/status > /dev/null && break; sleep 0.5; done
.venv/bin/python .claude/skills/ui-screenshots/capture.py
pkill -f scripts/demo_server.py
```

The script writes `chat-and-cart.png`, `price-comparison.png`, `review-order.png` and
`dark-mode.png` at 390 x 844 (2x). It exits non-zero if the page logged errors. Use `--out`
to write somewhere else, e.g. a temp folder to compare before replacing.

## 3. Look at every image

Open each PNG with the Read tool and check:

- nothing is cut off or overlapping, and the cart, price check and review sheet are readable;
- light and dark mode both look right;
- only demo data appears.

If the UI changed shape (new buttons, different flow), update the selectors and steps in
`capture.py` rather than accepting a broken screenshot. If the demo data needs to change,
edit `scripts/demo_server.py`.

## 4. Report

Tell the user which screenshots changed. If the README's alt text or captions no longer match
what's shown, suggest updates.
