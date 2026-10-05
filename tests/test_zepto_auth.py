from __future__ import annotations

import json
import stat
import time
from pathlib import Path

import httpx
import pytest

from zepto_ordering import zepto_auth
from zepto_ordering.zepto_auth import (
    PendingLogin,
    ZeptoAuthError,
    ZeptoTokens,
    extract_code,
    get_access_token,
    save_tokens,
)

PENDING = PendingLogin(client_id="client", code_verifier="verifier", state="S1", authorize_url="")


@pytest.fixture
def token_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point token storage at a temporary folder."""
    monkeypatch.setattr(zepto_auth, "DATA_DIR", tmp_path)
    monkeypatch.setattr(zepto_auth, "TOKEN_FILE", tmp_path / "zepto_tokens.json")
    monkeypatch.setattr(zepto_auth, "TOKEN_LOCK_FILE", tmp_path / "zepto_tokens.lock")
    return tmp_path


def make_tokens(access: str, expires_in: float) -> ZeptoTokens:
    return ZeptoTokens(
        client_id="client",
        access_token=access,
        refresh_token="refresh-1",
        expires_at=time.time() + expires_in,
    )


def test_fresh_token_is_returned_without_refreshing(token_dir, monkeypatch):
    save_tokens(make_tokens("access-1", expires_in=3600))
    monkeypatch.setattr(zepto_auth, "refresh_tokens", lambda *args: pytest.fail("refreshed"))

    assert get_access_token() == "access-1"


def test_token_close_to_expiry_is_refreshed_and_saved(token_dir, monkeypatch):
    save_tokens(make_tokens("access-1", expires_in=600))
    refreshed = make_tokens("access-2", expires_in=3600)
    monkeypatch.setattr(zepto_auth, "refresh_tokens", lambda http, tokens: refreshed)

    assert get_access_token() == "access-2"

    stored = json.loads((token_dir / "zepto_tokens.json").read_text())
    assert stored["access_token"] == "access-2"
    assert stat.S_IMODE((token_dir / "zepto_tokens.json").stat().st_mode) == 0o600


def test_corrupt_token_file_is_a_login_error(token_dir):
    (token_dir / "zepto_tokens.json").write_text("{not json")

    with pytest.raises(ZeptoAuthError, match="unreadable"):
        get_access_token()


def test_missing_login_is_a_login_error(token_dir):
    with pytest.raises(ZeptoAuthError, match="Not logged in"):
        get_access_token()


def test_network_error_during_refresh_is_a_login_error():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with httpx.Client(transport=httpx.MockTransport(refuse)) as http:
        with pytest.raises(ZeptoAuthError, match="Could not reach"):
            zepto_auth.refresh_tokens(http, make_tokens("access-1", expires_in=0))


@pytest.mark.parametrize(
    "callback",
    [
        "/callback?code=abc&state=S1",
        "http://localhost:8765/callback?code=abc&state=S1&iss=https://auth.zepto.co.in",
        "?code=abc&state=S1",
        "code=abc&state=S1",
    ],
)
def test_extract_code_accepts_all_callback_formats(callback: str):
    assert extract_code(callback, PENDING) == "abc"


def test_extract_code_rejects_wrong_state():
    with pytest.raises(ZeptoAuthError, match="State mismatch"):
        extract_code("/callback?code=abc&state=other", PENDING)


def test_extract_code_reports_zepto_error():
    with pytest.raises(ZeptoAuthError, match="access_denied"):
        extract_code("/callback?error=access_denied&state=S1", PENDING)
