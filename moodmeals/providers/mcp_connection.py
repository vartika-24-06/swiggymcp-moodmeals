"""The real MCP connection to Swiggy (design 7.3; spike A showed this sign-in works).

OAuth 2.1 with dynamic registration and PKCE as a public client; the redirect comes back
to a one-shot server on localhost. Tokens live in memory only (DD7) and are gone when the
process ends. The async SDK runs on its own thread so Streamlit and the sync loop can call
it with a plain function. Nothing here knows about meals; it moves one tool call at a time.

Only the owner's machine can complete this sign-in (the browser and the callback must be on
the same computer), so the public deployment never imports this module.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

SERVERS = {"food": "https://mcp.swiggy.com/food", "im": "https://mcp.swiggy.com/im"}
DEFAULT_PORT = 8765
CALLBACK_PATH = "/callback"
SIGN_IN_TIMEOUT_S = 300

_DONE_PAGE = (
    b"<!doctype html><meta charset='utf-8'><title>MoodMeals</title>"
    b"<p>Sign-in step finished. You can close this tab and go back to MoodMeals.</p>"
)


class ConnectionFailed(Exception):
    """Sign-in or session setup failed. The message never contains tokens or response data."""


class CallTimeout(Exception):
    pass


class MemoryStorage:
    """OAuth token storage that never touches disk."""

    def __init__(self) -> None:
        self._tokens: Any = None
        self._client_info: Any = None

    async def get_tokens(self) -> Any:
        return self._tokens

    async def set_tokens(self, tokens: Any) -> None:
        self._tokens = tokens

    async def get_client_info(self) -> Any:
        return self._client_info

    async def set_client_info(self, client_info: Any) -> None:
        self._client_info = client_info


class CallbackServer:
    """One-shot localhost server that receives the OAuth redirect."""

    def __init__(self, port: int) -> None:
        self.params: dict[str, str] = {}
        self.received = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != CALLBACK_PATH:
                    self.send_error(404)
                    return
                outer.params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_DONE_PAGE)
                outer.received.set()

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return  # the request line holds the authorization code

        try:
            self._httpd = HTTPServer(("127.0.0.1", port), Handler)
        except OSError as e:
            raise ConnectionFailed(f"Port {port} is busy. Close other sign-in windows.") from e
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def extract_payload(result: Any) -> dict[str, Any] | None:
    """The structured result if present, else the first text block that parses as JSON."""
    if getattr(result, "structured_content", None) is not None:
        sc = result.structured_content
        return sc if isinstance(sc, dict) else {"result": sc}
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            try:
                data = json.loads(text)
            except ValueError:
                continue
            return data if isinstance(data, dict) else {"result": data}
    return None


class McpConnection:
    """Sign in to the Food and Instamart servers and make one tool call at a time."""

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        on_auth_url: Callable[[str], None] | None = None,
        open_browser: bool = True,
    ) -> None:
        self._port = port
        self._on_auth_url = on_auth_url
        self._open_browser = open_browser
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._clients: dict[str, Any] = {}
        self._stacks: dict[str, contextlib.AsyncExitStack] = {}

    # -- public, synchronous --------------------------------------------------

    def connect(self, server: str) -> None:
        if server in self._clients:
            return
        fut = asyncio.run_coroutine_threadsafe(self._connect(server), self._loop)
        try:
            fut.result(timeout=SIGN_IN_TIMEOUT_S + 60)
        except ConnectionFailed:
            raise
        except Exception as e:
            raise ConnectionFailed(
                f"Could not sign in to Swiggy {server}: {type(e).__name__}"
            ) from None

    def call(self, server: str, tool: str, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        """Returns the payload dict. Raises CallTimeout, or ConnectionFailed for a tool error."""
        if server not in self._clients:
            raise ConnectionFailed(f"Not signed in to Swiggy {server}")
        fut = asyncio.run_coroutine_threadsafe(self._call(server, tool, args, timeout), self._loop)
        try:
            return fut.result(timeout=timeout + 5)
        except TimeoutError:
            fut.cancel()
            raise CallTimeout(tool) from None

    def list_tool_names(self, server: str) -> list[str]:
        fut = asyncio.run_coroutine_threadsafe(self._list(server), self._loop)
        return fut.result(timeout=60)

    def close(self) -> None:
        for server in list(self._stacks):
            fut = asyncio.run_coroutine_threadsafe(self._stacks.pop(server).aclose(), self._loop)
            with contextlib.suppress(Exception):
                fut.result(timeout=10)
        self._clients.clear()
        self._loop.call_soon_threadsafe(self._loop.stop)

    # -- async internals (on the loop thread) ---------------------------------

    async def _connect(self, server: str) -> None:
        import httpx2
        from mcp import Client
        from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared.auth import OAuthClientMetadata

        url = SERVERS[server]
        cb = CallbackServer(self._port)
        cb.start()

        async def redirect_handler(authorization_url: str) -> None:
            if self._on_auth_url:
                self._on_auth_url(authorization_url)
            if not (self._open_browser and webbrowser.open(authorization_url)):
                print("Open this URL to sign in to Swiggy:", authorization_url, flush=True)

        async def callback_handler() -> Any:
            got = await asyncio.to_thread(cb.received.wait, SIGN_IN_TIMEOUT_S)
            if not got:
                raise ConnectionFailed("No sign-in redirect arrived in time")
            if "code" not in cb.params:
                raise ConnectionFailed("Swiggy sign-in was not completed")
            return AuthorizationCodeResult(
                code=cb.params["code"], state=cb.params.get("state"), iss=cb.params.get("iss")
            )

        oauth = OAuthClientProvider(
            server_url=url,
            client_metadata=OAuthClientMetadata(
                client_name="MoodMeals",
                redirect_uris=[f"http://localhost:{self._port}{CALLBACK_PATH}"],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                token_endpoint_auth_method="none",
            ),
            storage=MemoryStorage(),
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )
        stack = contextlib.AsyncExitStack()
        try:
            http = await stack.enter_async_context(
                httpx2.AsyncClient(auth=oauth, timeout=httpx2.Timeout(30.0, read=300.0))
            )
            transport = streamable_http_client(url, http_client=http)
            client = await stack.enter_async_context(Client(transport, read_timeout_seconds=60))
        except BaseException:
            await stack.aclose()
            raise
        finally:
            cb.stop()
        self._clients[server], self._stacks[server] = client, stack

    async def _call(self, server: str, tool: str, args: dict[str, Any], timeout: float) -> Any:
        client = self._clients[server]
        result = await asyncio.wait_for(
            client.call_tool(tool, args, read_timeout_seconds=timeout + 30), timeout=timeout
        )
        if getattr(result, "is_error", False):
            raise ConnectionFailed(f"{tool} returned a tool error")
        payload = extract_payload(result)
        if payload is None:
            raise ConnectionFailed(f"{tool} returned no readable data")
        return payload

    async def _list(self, server: str) -> list[str]:
        return [t.name for t in (await self._clients[server].list_tools()).tools]
