"""The tool gate is what stops Claude from ordering, so test it directly."""

from __future__ import annotations

import asyncio

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, ToolPermissionContext

from zepto_ordering.cart_agent import BLOCKED_TOOLS, CART_TOOLS, make_tool_checker, tool_name
from zepto_ordering.zepto_client import DEVICE_ID

ADDRESS_ID = "home-address-id"


def check(name: str, tool_input: dict | None = None):
    checker = make_tool_checker(ADDRESS_ID)
    return asyncio.run(checker(name, tool_input or {}, ToolPermissionContext()))


@pytest.mark.parametrize("name", sorted(BLOCKED_TOOLS))
def test_ordering_and_account_tools_are_denied(name: str):
    assert isinstance(check(tool_name(name)), PermissionResultDeny)


@pytest.mark.parametrize("name", ["Bash", "Read", "WebFetch", "mcp__other__view_cart"])
def test_tools_outside_the_zepto_cart_set_are_denied(name: str):
    assert isinstance(check(name), PermissionResultDeny)


def test_cart_and_order_tool_sets_do_not_overlap():
    assert not CART_TOOLS & BLOCKED_TOOLS
    assert "create_order" in BLOCKED_TOOLS


def test_catalog_tools_are_allowed_unchanged():
    result = check(tool_name("search_products"), {"query": "milk"})

    assert isinstance(result, PermissionResultAllow)
    assert result.updated_input is None


def test_only_the_configured_address_can_be_selected():
    allowed = check(tool_name("select_saved_address"), {"addressId": ADDRESS_ID})
    denied = check(tool_name("select_saved_address"), {"addressId": "office"})

    assert isinstance(allowed, PermissionResultAllow)
    assert isinstance(denied, PermissionResultDeny)
    assert ADDRESS_ID in denied.message


def test_update_cart_always_uses_the_app_device_id():
    cart_items = [{"productVariantId": "pv", "storeProductId": "sp", "quantity": 1}]

    result = check(
        tool_name("update_cart"), {"deviceId": "something-else", "cartItems": cart_items}
    )

    assert isinstance(result, PermissionResultAllow)
    assert result.updated_input == {"deviceId": DEVICE_ID, "cartItems": cart_items}
