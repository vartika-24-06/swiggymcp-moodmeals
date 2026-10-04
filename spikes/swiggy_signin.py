"""Spike A (tasks.md T0.2): can the Python `mcp` SDK sign in to Swiggy on localhost?

Signs in to the Swiggy Food MCP server with OAuth 2.1 + PKCE over streamable
HTTP, lists the server's tools, makes ONE read-only tool call (`get_addresses`)
and prints ONLY the number of saved addresses, plus a progress line per step.

Privacy rules this script follows:
- It never prints, logs or saves address text, phone numbers or names.
- Tokens and the client registration live in memory only and are gone on exit.
- It calls no write tool and no payment tool.

Run it on your own machine (see spikes/README.md), not in a Codespace: the
OAuth redirect goes to localhost on the machine where the browser runs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx2
from mcp import Client
from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import ValidationError

SERVER_URL = "https://mcp.swiggy.com/food"
READ_ONLY_TOOL = "get_addresses"
DEFAULT_PORT = 8765
CALLBACK_PATH = "/callback"
SIGN_IN_TIMEOUT_S = 300
CALL_TIMEOUT_S = 90
PAGE_SIZE = 10  # the tool's documented page size

_DONE_PAGE = (
    b"<!doctype html><meta charset='utf-8'><title>MoodMeals spike</title>"
    b"<p>Sign-in step finished. You can close this tab and go back to the terminal.</p>"
)


class MemoryStorage:
    """Token storage that never touches disk (design.md DD7)."""

    def __init__(self) -> None:
        self._tokens: OAuthToken | None = None
        self._client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self._tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self._client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._client_info = client_info


class CallbackServer:
    """One-shot localhost server that receives the OAuth redirect."""

    def __init__(self, port: int) -> None:
        self.params: dict[str, str] = {}
        self.received = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 (http.server naming)
                parsed = urlparse(self.path)
                if parsed.path != CALLBACK_PATH:
                    self.send_error(404)
                    return
                query = parse_qs(parsed.query)
                outer.params = {key: values[0] for key, values in query.items()}
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_DONE_PAGE)
                outer.received.set()

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                # The default logger prints the request line, which holds the
                # authorization code. Print nothing.
                return

        self._httpd = HTTPServer(("127.0.0.1", port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def step(message: str) -> None:
    """Progress line, flushed at once so a stall shows the last finished step."""
    print(f"[step] {message}", flush=True)


def mask_digits(text: str) -> str:
    """Mask long digit runs so no output can ever carry a phone number."""
    return re.sub(r"\d{6,}", "<digits>", text)


def scrub(text: str) -> str:
    """Masked and cut short, for error messages."""
    return mask_digits(text)[:500]


def safe_message(exc: BaseException) -> str:
    """Exception message that cannot quote the server's response."""
    if isinstance(exc, ValidationError):
        # The default message echoes the input values, which could be address
        # text. Keep only where it failed and why.
        errors = exc.errors(include_url=False, include_input=False, include_context=False)
        return scrub("; ".join(f"{error['loc']}: {error['type']}" for error in errors))
    return scrub(str(exc))


def print_failure(exc: BaseException, indent: str = "", seen: set[int] | None = None) -> None:
    """Type, message and traceback frames for an exception, its causes and group members."""
    seen = set() if seen is None else seen
    if id(exc) in seen:
        return
    seen.add(id(exc))
    print(f"{indent}FAILED: {type(exc).__name__}: {safe_message(exc)}")
    # Frames hold file names, line numbers and source lines only, never values.
    for line in mask_digits("".join(traceback.format_tb(exc.__traceback__))).splitlines():
        print(f"{indent}{line}")
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            print_failure(inner, indent + "  | ", seen)
    cause = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    if cause is not None and id(cause) not in seen:
        print(f"{indent}caused by:")
        print_failure(cause, indent, seen)


def find_total(payload: Any) -> int | None:
    """Find `pagination.total` anywhere in the payload, without reading other fields."""
    if isinstance(payload, dict):
        pagination = payload.get("pagination")
        if isinstance(pagination, dict) and isinstance(pagination.get("total"), int):
            return pagination["total"]
        for value in payload.values():
            found = find_total(value)
            if found is not None:
                return found
    return None


def find_address_list_length(payload: Any) -> int | None:
    """Fallback: length of a list stored under a key named like 'addresses'."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if "address" in key.lower() and isinstance(value, list):
                return len(value)
        for value in payload.values():
            found = find_address_list_length(value)
            if found is not None:
                return found
    return None


def shape(payload: Any, depth: int = 0) -> Any:
    """Key names and value types only. Never values."""
    if isinstance(payload, dict):
        if depth >= 3:
            return "dict"
        return {str(key): shape(value, depth + 1) for key, value in payload.items()}
    if isinstance(payload, list):
        return f"list[{len(payload)}]"
    return type(payload).__name__


def payloads_from(result: Any) -> list[Any]:
    """Structured result first, then any text block that parses as JSON."""
    payloads: list[Any] = []
    if result.structured_content is not None:
        payloads.append(result.structured_content)
    for block in result.content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            try:
                payloads.append(json.loads(text))
            except ValueError:
                continue
    return payloads


async def run(port: int, open_browser: bool) -> int:
    redirect_uri = f"http://localhost:{port}{CALLBACK_PATH}"
    callback_server = CallbackServer(port)
    callback_server.start()

    async def redirect_handler(authorization_url: str) -> None:
        print("Opening the Swiggy sign-in page in your browser...")
        if not (open_browser and webbrowser.open(authorization_url)):
            # The URL holds no personal data, but there is no need to share it.
            print("Could not open a browser. Open this URL yourself:")
            print(authorization_url)

    async def callback_handler() -> AuthorizationCodeResult:
        got_it = await asyncio.to_thread(callback_server.received.wait, SIGN_IN_TIMEOUT_S)
        if not got_it:
            raise TimeoutError(f"No sign-in redirect arrived within {SIGN_IN_TIMEOUT_S} seconds")
        params = callback_server.params
        if "code" not in params:
            reason = params.get("error", "no code in redirect")
            detail = params.get("error_description", "")
            raise RuntimeError(f"Authorization failed: {reason} {detail}".strip())
        return AuthorizationCodeResult(
            code=params["code"], state=params.get("state"), iss=params.get("iss")
        )

    oauth = OAuthClientProvider(
        server_url=SERVER_URL,
        client_metadata=OAuthClientMetadata(
            client_name="MoodMeals sign-in spike",
            redirect_uris=[redirect_uri],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",  # public client, PKCE only
        ),
        storage=MemoryStorage(),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )

    try:
        # Long read timeout: the server may hold a response stream open.
        timeout = httpx2.Timeout(30.0, read=300.0)
        async with httpx2.AsyncClient(auth=oauth, timeout=timeout) as http_client:
            transport = streamable_http_client(SERVER_URL, http_client=http_client)
            step("opening MCP session (sign-in, then initialize)")
            async with Client(transport, read_timeout_seconds=60) as client:
                step("signed in, session initialised")

                step("list_tools started")
                tools = (await client.list_tools()).tools
                names = ", ".join(tool.name for tool in tools)
                step(f"list_tools finished: {len(tools)} tools: {names}")

                step(f"call_tool {READ_ONLY_TOOL} started")
                try:
                    result = await asyncio.wait_for(
                        client.call_tool(
                            READ_ONLY_TOOL,
                            {"page": 1, "pageSize": PAGE_SIZE},
                            # Longer than the limit below, so that limit is the one that fires.
                            read_timeout_seconds=CALL_TIMEOUT_S + 30,
                        ),
                        timeout=CALL_TIMEOUT_S,
                    )
                except TimeoutError:
                    print(f"FAILED: TimeoutError at {READ_ONLY_TOOL} after {CALL_TIMEOUT_S}s")
                    sys.stdout.flush()
                    # Hard exit: closing a stalled session could hang as well.
                    # Tokens are in memory only, so there is nothing to clean up.
                    os._exit(1)
                step(f"call_tool {READ_ONLY_TOOL} returned")
    finally:
        callback_server.stop()

    if result.is_error:
        texts = [getattr(block, "text", "") or "" for block in result.content]
        print(f"FAILED: {READ_ONLY_TOOL} returned a tool error: {scrub(' '.join(texts))}")
        return 1

    payloads = payloads_from(result)
    for finder, source in (
        (find_total, "pagination.total"),
        (find_address_list_length, "length of first page"),
    ):
        for payload in payloads:
            count = finder(payload)
            if count is not None:
                print(f"SUCCESS: saved addresses = {count} (from {source})")
                return 0

    # The call worked but the response was not shaped as expected. Show the
    # structure only (key names and types), never any values.
    print("PARTIAL: sign-in and the call worked, but no address count was found.")
    print(f"structured result present: {result.structured_content is not None}")
    print(f"content block types: {[type(block).__name__ for block in result.content]}")
    print(f"shape (keys and types only): {json.dumps([shape(p) for p in payloads])}")
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="localhost redirect port")
    parser.add_argument("--no-browser", action="store_true", help="print the URL instead")
    args = parser.parse_args()

    try:
        return asyncio.run(run(args.port, open_browser=not args.no_browser))
    except KeyboardInterrupt:
        print("Cancelled.")
        return 130
    except BaseException as exc:  # noqa: BLE001 (report every failure the same way)
        print_failure(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
