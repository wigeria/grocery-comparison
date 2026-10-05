"""Capture the README screenshots from the demo server (scripts/demo_server.py).

Needs Playwright, which is not a project dependency:
    .venv/bin/pip install playwright && .venv/bin/playwright install chromium

    .venv/bin/python .claude/skills/ui-screenshots/capture.py [--url URL] [--out DIR]

Writes chat-and-cart.png, price-comparison.png, review-order.png and
dark-mode.png at iPhone size (390 x 844, 2x). Prints any browser errors.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[3]
VIEWPORT = {"width": 390, "height": 844}
SAMPLE_LIST = "milk, 2 diet coke cans, brown bread, eggs, paneer"


def build_cart(page: Page, url: str) -> None:
    """Start an order and send the sample list."""
    page.goto(url)
    page.click("[data-action=start]")
    page.wait_for_selector("#message-input")
    page.fill("#message-input", SAMPLE_LIST)
    page.click("button[type=submit]")
    page.wait_for_selector(".bubble.assistant")


def compare_prices(page: Page) -> None:
    """Run the price check with the cart open, then close the cart."""
    page.click("[data-action=toggle-cart]")
    page.click("[data-action=compare]")
    page.wait_for_selector(".compare-totals", timeout=15000)
    page.click("[data-action=toggle-cart]")


def cancel_order(page: Page) -> None:
    """Leave the demo with no open order."""
    page.click("#cancel-button")
    page.click("#confirm-yes")
    page.wait_for_selector("[data-action=start]")


def capture(url: str, out: Path) -> list[str]:
    """Take all screenshots and return any page errors."""
    out.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()

        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2, color_scheme="light")
        page.on("pageerror", lambda error: errors.append(str(error)))
        build_cart(page, url)
        page.click("[data-action=toggle-cart]")
        page.screenshot(path=out / "chat-and-cart.png")
        page.click("[data-action=toggle-cart]")

        compare_prices(page)
        page.click(".card details summary")
        page.evaluate(
            "document.querySelector('.compare-totals').closest('.card')"
            ".scrollIntoView({block: 'start'}); window.scrollBy(0, -70)"
        )
        page.screenshot(path=out / "price-comparison.png")

        page.click("[data-action=toggle-cart]")
        page.click("[data-action=review]")
        page.wait_for_selector("[data-review=place]")
        page.screenshot(path=out / "review-order.png")
        page.click("[data-review=close]")
        cancel_order(page)
        page.close()

        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2, color_scheme="dark")
        page.on("pageerror", lambda error: errors.append(str(error)))
        build_cart(page, url)
        compare_prices(page)
        page.screenshot(path=out / "dark-mode.png")
        cancel_order(page)

        browser.close()
    return errors


def main() -> int:
    """Parse arguments, capture, and report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8001/")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "screenshots")
    args = parser.parse_args()

    errors = capture(args.url, args.out)
    for path in sorted(args.out.glob("*.png")):
        shown = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        print(f"{shown}  {path.stat().st_size // 1024} KB")
    if errors:
        print("Browser errors:", *errors, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
