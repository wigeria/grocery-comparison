"""One-time Zepto login. Run this on the server over SSH.

Zepto redirects back to http://localhost:8765/callback after the OTP step.
There are two ways to complete the login:

1. Forward the port when you SSH in, so the redirect reaches this script:
       ssh -L 8765:localhost:8765 user@server
   Then open the printed URL in your local browser.

2. Open the printed URL on any device. After the OTP, the browser lands on a
   page that fails to load. Copy that page's full URL and paste it here.

Inside Docker, the listener must bind to all interfaces so the published port
reaches it. ZEPTO_LOGIN_BIND_HOST sets that (see README).
"""

from __future__ import annotations

import os
import queue
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

from zepto_ordering.zepto_auth import (
    CALLBACK_PORT,
    TOKEN_FILE,
    ZeptoAuthError,
    exchange_code,
    extract_code,
    save_tokens,
    start_login,
)


def start_callback_server(callbacks: queue.Queue[str]) -> HTTPServer | None:
    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if not self.path.startswith("/callback"):
                self.send_error(404)
                return
            callbacks.put(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Zepto login received. You can close this tab.")

        def log_message(self, format: str, *args: object) -> None:
            pass

    try:
        bind_host = os.environ.get("ZEPTO_LOGIN_BIND_HOST", "127.0.0.1")
        server = HTTPServer((bind_host, CALLBACK_PORT), CallbackHandler)
    except OSError as error:
        print(f"Could not listen on port {CALLBACK_PORT} ({error}). Use the paste option.")
        return None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def read_pasted_url(callbacks: queue.Queue[str]) -> None:
    for line in sys.stdin:
        if line.strip():
            callbacks.put(line.strip())
            return


def main() -> int:
    callbacks: queue.Queue[str] = queue.Queue()

    with httpx.Client(timeout=30) as http:
        try:
            pending = start_login(http)
        except ZeptoAuthError as error:
            print(error)
            return 1

        server = start_callback_server(callbacks)
        print("\nOpen this URL in a browser and log in with your phone number and OTP:\n")
        print(pending.authorize_url)
        print(
            f"\nWaiting for the redirect on port {CALLBACK_PORT}."
            "\nIf it doesn't arrive, paste the full URL you were redirected to and press Enter:"
        )
        threading.Thread(target=read_pasted_url, args=(callbacks,), daemon=True).start()

        try:
            callback = callbacks.get()
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 1
        finally:
            if server:
                server.shutdown()

        try:
            code = extract_code(callback, pending)
            tokens = exchange_code(http, pending, code)
        except ZeptoAuthError as error:
            print(f"\nLogin failed: {error}")
            return 1

    save_tokens(tokens)
    refresh_note = "with" if tokens.refresh_token else "WITHOUT"
    print(f"\nLogged in {refresh_note} a refresh token. Saved to {TOKEN_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
