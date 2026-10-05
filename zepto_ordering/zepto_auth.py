"""OAuth client for the hosted Zepto MCP server.

Zepto only whitelists http://localhost redirect URIs, so the browser login runs
once on the server (see scripts/zepto_login.py). After that, the stored refresh
token keeps the access token alive without any user interaction.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import secrets
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import httpx

from zepto_ordering.config import DATA_DIR

AUTH_SERVER = "https://auth.zepto.co.in"
MCP_RESOURCE = "https://mcp.zepto.co.in"
MCP_URL = f"{MCP_RESOURCE}/mcp"
SCOPES = [
    "tools:read",
    "tools:write",
    "dev.ucp.shopping.cart:manage",
    "dev.ucp.shopping.catalog.search:read",
    "dev.ucp.shopping.catalog.lookup:read",
    "dev.ucp.common.location.search:read",
    "dev.ucp.common.location.lookup:read",
    "dev.ucp.shopping.order:manage",
    "dev.ucp.shopping.checkout:manage",
]
CALLBACK_PORT = 8765
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"
CLIENT_NAME = "zepto-ordering"

# A Claude turn uses one access token for all of its tool calls, which can take a
# few minutes for a long list, so refresh well before expiry.
REFRESH_MARGIN_SECONDS = 15 * 60
DEFAULT_TOKEN_LIFETIME_SECONDS = 3600

TOKEN_FILE = DATA_DIR / "zepto_tokens.json"
CLIENT_FILE = DATA_DIR / "zepto_client.json"
TOKEN_LOCK_FILE = DATA_DIR / "zepto_tokens.lock"

_refresh_lock = threading.Lock()


class ZeptoAuthError(Exception):
    """Raised when Zepto login or token refresh fails."""


@dataclass
class ZeptoTokens:
    client_id: str
    access_token: str
    refresh_token: str | None
    expires_at: float

    def is_expiring(self) -> bool:
        """Return True if the access token expires within the refresh margin."""
        return time.time() >= self.expires_at - REFRESH_MARGIN_SECONDS


@dataclass
class PendingLogin:
    client_id: str
    code_verifier: str
    state: str
    authorize_url: str


def load_tokens() -> ZeptoTokens | None:
    """Load stored tokens, or return None if no login has been saved."""
    if not TOKEN_FILE.exists():
        return None
    try:
        return ZeptoTokens(**json.loads(TOKEN_FILE.read_text()))
    except (ValueError, TypeError) as error:
        raise ZeptoAuthError(
            f"{TOKEN_FILE} is unreadable. Run scripts/zepto_login.py again."
        ) from error


def _write_private_json(path: Path, data: dict) -> None:
    """Write JSON atomically, readable only by the current user."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(data, file, indent=2)
    os.replace(temp_path, path)


def save_tokens(tokens: ZeptoTokens) -> None:
    """Persist tokens to the token file."""
    _write_private_json(TOKEN_FILE, asdict(tokens))


def get_or_register_client(http: httpx.Client) -> str:
    """Return the saved client id, registering a new client only if none matches.

    The registration lives in its own file so that deleting the token file
    (to log out) doesn't leave an orphaned client behind on every new login.
    """
    if CLIENT_FILE.exists():
        saved = json.loads(CLIENT_FILE.read_text())
        if saved.get("redirect_uri") == REDIRECT_URI:
            return saved["client_id"]
    client_id = register_client(http)
    _write_private_json(CLIENT_FILE, {"client_id": client_id, "redirect_uri": REDIRECT_URI})
    return client_id


def register_client(http: httpx.Client) -> str:
    """Register a public OAuth client (dynamic client registration) and return its id."""
    response = http.post(
        f"{AUTH_SERVER}/register",
        json={
            "client_name": CLIENT_NAME,
            "redirect_uris": [REDIRECT_URI],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    if response.status_code >= 400:
        raise ZeptoAuthError(
            f"Client registration failed ({response.status_code}): {response.text[:300]}"
        )
    return response.json()["client_id"]


def start_login(http: httpx.Client) -> PendingLogin:
    """Build the PKCE authorize URL for a new login, reusing the saved client."""
    client_id = get_or_register_client(http)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    state = secrets.token_urlsafe(24)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "resource": MCP_RESOURCE,
    }
    return PendingLogin(
        client_id=client_id,
        code_verifier=code_verifier,
        state=state,
        authorize_url=f"{AUTH_SERVER}/authorize?{urlencode(params)}",
    )


def extract_code(callback: str, pending: PendingLogin) -> str:
    """Pull the authorization code out of a callback URL, path, or raw query string."""
    query = callback.split("?", 1)[1] if "?" in callback else callback
    params = {key: values[0] for key, values in parse_qs(query).items()}

    if "error" in params:
        detail = params.get("error_description", "")
        raise ZeptoAuthError(f"Zepto returned an error: {params['error']} {detail}".strip())
    if params.get("state") != pending.state:
        raise ZeptoAuthError("State mismatch. This URL is not from the current login attempt.")
    if "iss" in params and params["iss"] != AUTH_SERVER:
        raise ZeptoAuthError(f"Unexpected issuer in callback: {params['iss']}")
    if "code" not in params:
        raise ZeptoAuthError("No authorization code found in the callback URL.")
    return params["code"]


def _request_tokens(http: httpx.Client, client_id: str, form: dict[str, str]) -> dict:
    """POST to the token endpoint and return the parsed JSON response."""
    try:
        response = http.post(
            f"{AUTH_SERVER}/token",
            data={"client_id": client_id, "resource": MCP_RESOURCE, **form},
        )
    except httpx.HTTPError as error:
        raise ZeptoAuthError(f"Could not reach Zepto's login server: {error!r}") from error
    if response.status_code >= 400:
        raise ZeptoAuthError(
            f"Token request failed ({response.status_code}): {response.text[:300]}"
        )
    try:
        return response.json()
    except ValueError as error:
        raise ZeptoAuthError("Zepto's login server returned an invalid response.") from error


def _expiry_from(payload: dict) -> float:
    """Convert a token response's expires_in into an absolute timestamp."""
    return time.time() + payload.get("expires_in", DEFAULT_TOKEN_LIFETIME_SECONDS)


def exchange_code(http: httpx.Client, pending: PendingLogin, code: str) -> ZeptoTokens:
    """Exchange an authorization code for access and refresh tokens."""
    payload = _request_tokens(
        http,
        pending.client_id,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": pending.code_verifier,
        },
    )
    return ZeptoTokens(
        client_id=pending.client_id,
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token"),
        expires_at=_expiry_from(payload),
    )


def refresh_tokens(http: httpx.Client, tokens: ZeptoTokens) -> ZeptoTokens:
    """Get a new access token using the stored refresh token."""
    if not tokens.refresh_token:
        raise ZeptoAuthError("No refresh token stored. Run scripts/zepto_login.py again.")
    payload = _request_tokens(
        http,
        tokens.client_id,
        {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token},
    )
    return ZeptoTokens(
        client_id=tokens.client_id,
        access_token=payload["access_token"],
        # Servers that don't rotate refresh tokens omit it from the response.
        refresh_token=payload.get("refresh_token", tokens.refresh_token),
        expires_at=_expiry_from(payload),
    )


@contextmanager
def _token_lock() -> Iterator[None]:
    """Serialize token refreshes across threads and processes.

    Matters if refresh tokens rotate: two concurrent refreshes (for example the
    server and a script run with docker exec) would leave one of them holding an
    already-used refresh token.
    """
    with _refresh_lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(TOKEN_LOCK_FILE, "a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)


def get_access_token() -> str:
    """Return a valid access token, refreshing and persisting it when needed."""
    with _token_lock():
        tokens = load_tokens()
        if tokens is None:
            raise ZeptoAuthError("Not logged in to Zepto. Run scripts/zepto_login.py first.")
        if tokens.is_expiring():
            with httpx.Client(timeout=30) as http:
                tokens = refresh_tokens(http, tokens)
            save_tokens(tokens)
        return tokens.access_token
