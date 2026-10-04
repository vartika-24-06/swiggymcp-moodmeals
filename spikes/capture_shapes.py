"""Spike C (tasks.md T0.4, design DQ2): record the SHAPE of real Swiggy responses.

Signs in the same way as Spike A, makes a few read-only calls, and writes two things:

1. `spikes/captures/<server>/<tool>.json`  the raw responses. LOCAL ONLY, git-ignored,
   never printed, never sent anywhere. They are for the offline compaction check.
2. `spikes/results/shapes_<server>.json`   a SHAPE REPORT that is safe to commit:
   field names, types, list lengths, number ranges, text FORMATS with every letter and
   digit masked, and the values of a few enum-like fields (VEG, OPEN, ...). Fields
   whose names suggest an address, phone, email or person are never read at all.

It calls only read-only tools: get_addresses (only to obtain an address id, which is
used in memory and never printed), the searches, and one menu read. No cart, order,
checkout, address-changing, order-history or payment tool is ever called.

Run:  python spikes/capture_shapes.py --server food
      python spikes/capture_shapes.py --server im
See spikes/README.md.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime
import json
import os
import re
import sys
import webbrowser
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2
from mcp import Client
from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientMetadata
from swiggy_signin import (
    CALLBACK_PATH,
    DEFAULT_PORT,
    SIGN_IN_TIMEOUT_S,
    CallbackServer,
    MemoryStorage,
    mask_digits,
    payloads_from,
    print_failure,
    scrub,
    step,
)

SERVERS = {"food": "https://mcp.swiggy.com/food", "im": "https://mcp.swiggy.com/im"}
CALL_TIMEOUT_S = 90
QUERIES = {"food": "khichdi", "im": "moong dal"}

# Fields we never read: their values are not recorded in any form.
SENSITIVE_TOKENS = {
    "address", "addresses", "addr", "area", "locality", "landmark", "location", "city", "pin",
    "pincode", "zip", "lat", "latitude", "lng", "lon", "longitude", "phone", "mobile", "email",
    "annotation", "customer", "user", "recipient", "contact", "token", "secret",
}  # fmt: skip


def is_sensitive_key(key: str) -> bool:
    """True if any camelCase or snake_case word of the key names personal data."""
    words = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", key)
    return any(w.lower() in SENSITIVE_TOKENS for w in words)


# Free text: format only, never values.
TEXT_KEY = re.compile(r"name|title|desc|subtitle|text|message|label|tag|badge", re.IGNORECASE)
ID_KEY = re.compile(r"(^|_)id$|Id$|^id$|spin|sku", re.IGNORECASE)
ENUM_VALUE = re.compile(r"^[A-Z][A-Z0-9_]{1,23}$")
KEEP_WORDS = {
    "mins", "min", "hr", "hrs", "hour", "for", "two", "km", "kms", "m", "k", "l", "cr", "g", "kg",
    "ml", "pcs", "pc", "pack", "off", "ad", "free", "above", "only", "upto", "up", "to", "ratings",
    "rating", "new", "buy", "again", "veg", "non", "rs", "x", "per", "unit", "item", "items",
}  # fmt: skip
PHONE_LIKE = re.compile(r"\+?\d(?:[\s-]?\d){7,}")
EMAIL_LIKE = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")


# --------------------------------------------------------------------------- #
# Sign-in (same OAuth wiring as Spike A, as a reusable session)
# --------------------------------------------------------------------------- #


@contextlib.asynccontextmanager
async def session(server_url: str, port: int, open_browser: bool) -> AsyncIterator[Client]:
    redirect_uri = f"http://localhost:{port}{CALLBACK_PATH}"
    callback_server = CallbackServer(port)
    callback_server.start()

    async def redirect_handler(authorization_url: str) -> None:
        print("Opening the Swiggy sign-in page in your browser...")
        if not (open_browser and webbrowser.open(authorization_url)):
            print("Could not open a browser. Open this URL yourself:")
            print(authorization_url)

    async def callback_handler() -> AuthorizationCodeResult:
        got_it = await asyncio.to_thread(callback_server.received.wait, SIGN_IN_TIMEOUT_S)
        if not got_it:
            raise TimeoutError(f"No sign-in redirect arrived within {SIGN_IN_TIMEOUT_S} seconds")
        params = callback_server.params
        if "code" not in params:
            raise RuntimeError(f"Authorization failed: {params.get('error', 'no code')}")
        return AuthorizationCodeResult(
            code=params["code"], state=params.get("state"), iss=params.get("iss")
        )

    oauth = OAuthClientProvider(
        server_url=server_url,
        client_metadata=OAuthClientMetadata(
            client_name="MoodMeals shape capture",
            redirect_uris=[redirect_uri],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        ),
        storage=MemoryStorage(),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
    try:
        timeout = httpx2.Timeout(30.0, read=300.0)
        async with httpx2.AsyncClient(auth=oauth, timeout=timeout) as http_client:
            transport = streamable_http_client(server_url, http_client=http_client)
            step("opening MCP session (sign-in, then initialize)")
            async with Client(transport, read_timeout_seconds=60) as client:
                step("signed in, session initialised")
                yield client
    finally:
        callback_server.stop()


# --------------------------------------------------------------------------- #
# Pure helpers (tested by --selftest, no network)
# --------------------------------------------------------------------------- #


def mask_text(value: str) -> str:
    """Format only: digits -> 9, letters -> A/a, except a few harmless unit words."""
    out: list[str] = []
    for token in re.findall(r"[A-Za-z]+|\d|[^A-Za-z\d]", value):
        if token.isalpha():
            if token.lower() in KEEP_WORDS:
                out.append(token)
            else:
                out.append("".join("A" if c.isupper() else "a" for c in token))
        elif token.isdigit():
            out.append("9")
        else:
            out.append(token)
    return "".join(out)[:60]


class Stats:
    """Aggregated facts about one field path across all list elements."""

    def __init__(self) -> None:
        self.count = 0
        self.types: set[str] = set()
        self.list_len: list[int] = []
        self.num_min: float | None = None
        self.num_max: float | None = None
        self.bools: dict[str, int] = {}
        self.formats: dict[str, int] = {}
        self.enums: set[str] = set()
        self.str_len: list[int] = []
        self.redacted = False

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"count": self.count, "types": sorted(self.types)}
        if self.redacted:
            out["values"] = "not read (sensitive field name)"
            return out
        if self.list_len:
            out["list_len"] = [min(self.list_len), max(self.list_len)]
        if self.num_min is not None:
            out["number_range"] = [self.num_min, self.num_max]
        if self.bools:
            out["bools"] = self.bools
        if self.str_len:
            out["text_len"] = [min(self.str_len), max(self.str_len)]
        if self.enums:
            out["enum_values"] = sorted(self.enums)[:12]
        if self.formats:
            top = sorted(self.formats.items(), key=lambda kv: -kv[1])[:6]
            out["formats"] = [{"mask": m, "count": c} for m, c in top]
        return out


def collect(payload: Any, stats: dict[str, Stats], path: str = "$", key: str = "") -> None:
    st = stats.setdefault(path, Stats())
    st.count += 1
    if is_sensitive_key(key):
        st.redacted = True
        st.types.add(type(payload).__name__)
        return
    if isinstance(payload, dict):
        st.types.add("object")
        for k, v in payload.items():
            collect(v, stats, f"{path}.{k}", str(k))
    elif isinstance(payload, list):
        st.types.add("array")
        st.list_len.append(len(payload))
        for item in payload:
            collect(item, stats, f"{path}[]", key)
    elif isinstance(payload, bool):
        st.types.add("boolean")
        st.bools[str(payload).lower()] = st.bools.get(str(payload).lower(), 0) + 1
    elif isinstance(payload, int | float):
        st.types.add("number")
        if not ID_KEY.search(key):
            lo, hi = (payload, payload)
            st.num_min = lo if st.num_min is None else min(st.num_min, lo)
            st.num_max = hi if st.num_max is None else max(st.num_max, hi)
    elif isinstance(payload, str):
        st.types.add("string")
        st.str_len.append(len(payload))
        if PHONE_LIKE.search(payload) or EMAIL_LIKE.search(payload):
            st.formats["<phone-or-email-like value omitted>"] = (
                st.formats.get("<phone-or-email-like value omitted>", 0) + 1
            )
        elif ID_KEY.search(key):
            st.formats["<id>"] = st.formats.get("<id>", 0) + 1
        else:
            if ENUM_VALUE.match(payload) and not TEXT_KEY.search(key):
                st.enums.add(payload)
            mask = mask_text(payload)
            st.formats[mask] = st.formats.get(mask, 0) + 1
    elif payload is None:
        st.types.add("null")


def flags_in(payloads: list[Any]) -> dict[str, int]:
    """Counts of markers we care about, without keeping any text."""
    text = json.dumps(payloads, ensure_ascii=False).lower()
    return {
        "ad_marker": len(re.findall(r"\(ad\)", text)),
        "buy_again": text.count("buy again"),
        "go_to": len(re.findall(r"go[- ]to", text)),
    }


def find_list_of_dicts(payload: Any, key_hint: str) -> list[dict[str, Any]]:
    """First list of dicts stored under a key containing `key_hint` (case-insensitive)."""
    if isinstance(payload, dict):
        for k, v in payload.items():
            if key_hint in k.lower() and isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        for v in payload.values():
            found = find_list_of_dicts(v, key_hint)
            if found:
                return found
    elif isinstance(payload, list):
        for v in payload:
            found = find_list_of_dicts(v, key_hint)
            if found:
                return found
    return []


def first_id(items: list[dict[str, Any]]) -> str | None:
    for item in items:
        for k in ("addressId", "address_id", "restaurantId", "restaurant_id", "id"):
            if k in item and isinstance(item[k], str | int):
                return str(item[k])
    return None


def find_restaurant_list(payload: Any) -> list[dict[str, Any]]:
    for hint in ("restaurant", "result", "data", "item"):
        found = find_list_of_dicts(payload, hint)
        if found:
            return found
    return []


def build_args(schema: dict[str, Any], ctx: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Fill a tool's arguments from its input schema. Returns (args, unfilled_required)."""
    props: dict[str, Any] = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])
    args: dict[str, Any] = {}
    unfilled: list[str] = []
    for name, spec in props.items():
        low = name.lower()
        typ = spec.get("type") if isinstance(spec, dict) else None
        value: Any = None
        if "address" in low:
            value = ctx.get("address_id")
        elif "restaurant" in low:
            value = ctx.get("restaurant_id")
        elif re.search(r"query|search|keyword|term", low) or low in ("q", "text"):
            value = ctx.get("query")
        elif "offset" in low:
            value = 0
        elif low == "page":
            value = 1
        elif re.search(r"page.?size|limit|count|max", low):
            value = 10
        if value is None:
            if name in required:
                unfilled.append(name)
            continue
        if typ == "integer" and not isinstance(value, int):
            if name in required:
                unfilled.append(name)
            continue
        if typ == "string" and not isinstance(value, str):
            value = str(value)
        args[name] = value
    return args, unfilled


def schema_summary(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {}
    props = {}
    for name, spec in (schema.get("properties") or {}).items():
        if isinstance(spec, dict):
            props[name] = {
                "type": spec.get("type"),
                "enum": spec.get("enum"),
                "description": mask_digits(str(spec.get("description", "")))[:300],
            }
    return {
        "required": schema.get("required", []),
        "properties": props,
        "tool_description": mask_digits(str(getattr(tool, "description", "") or ""))[:600],
    }


# --------------------------------------------------------------------------- #
# Capture run
# --------------------------------------------------------------------------- #


async def call(client: Client, tool: str, args: dict[str, Any]) -> Any:
    step(f"call_tool {tool} started")
    try:
        result = await asyncio.wait_for(
            client.call_tool(tool, args, read_timeout_seconds=CALL_TIMEOUT_S + 30),
            timeout=CALL_TIMEOUT_S,
        )
    except TimeoutError:
        print(f"FAILED: TimeoutError at {tool} after {CALL_TIMEOUT_S}s")
        sys.stdout.flush()
        os._exit(1)
    step(f"call_tool {tool} returned")
    return result


async def capture(server: str, port: int, open_browser: bool, cap_dir: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "server": server,
        "date": datetime.date.today().isoformat(),
        "note": "Shape only. Sensitive-named fields are not read; text is masked.",
        "tools": {},
    }
    ctx: dict[str, Any] = {"query": QUERIES[server]}
    plan = (
        ["get_addresses", "search_restaurants", "get_restaurant_menu", "search_menu"]
        if server == "food"
        else ["get_addresses", "search_products"]
    )
    cap_dir.mkdir(parents=True, exist_ok=True)

    async with session(SERVERS[server], port, open_browser) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        step(f"list_tools finished: {len(tools)} tools")
        for name in plan:
            entry: dict[str, Any] = {}
            report["tools"][name] = entry
            if name not in tools:
                entry["status"] = "not offered by this server"
                continue
            entry["input_schema"] = schema_summary(tools[name])
            raw_schema = getattr(tools[name], "input_schema", None) or getattr(
                tools[name], "inputSchema", None
            )
            if not raw_schema:
                entry["status"] = "skipped"
                entry["note"] = "no input schema found on the tool object"
                continue
            args, unfilled = build_args(raw_schema, ctx)
            entry["args_used"] = sorted(args)  # names only, never values
            if unfilled:
                entry["status"] = "skipped"
                entry["unfilled_required"] = unfilled
                step(f"{name}: skipped, needs {unfilled}")
                continue
            result = await call(client, name, args)
            if result.is_error:
                texts = [getattr(b, "text", "") or "" for b in result.content]
                entry["status"] = "tool error"
                entry["error"] = scrub(" ".join(texts))[:300]
                continue
            payloads = payloads_from(result)
            (cap_dir / f"{name}.json").write_text(
                json.dumps(payloads, ensure_ascii=False), encoding="utf-8"
            )
            raw = json.dumps(payloads, ensure_ascii=False)
            stats: dict[str, Stats] = {}
            for p in payloads:
                collect(p, stats)
            entry.update(
                status="ok",
                raw_chars=len(raw),
                est_tokens=len(raw) // 4,
                flags=flags_in(payloads),
                fields={path: s.as_json() for path, s in stats.items()},
            )
            # Context for later calls, kept in memory only.
            if name == "get_addresses":
                ctx["address_id"] = first_id(find_list_of_dicts(payloads, "address"))
                entry["address_id_found"] = ctx["address_id"] is not None
            elif name == "search_restaurants":
                ctx["restaurant_id"] = first_id(find_restaurant_list(payloads))
                entry["restaurant_id_found"] = ctx["restaurant_id"] is not None
    return report


# --------------------------------------------------------------------------- #
# Offline self-test
# --------------------------------------------------------------------------- #


def selftest() -> None:
    assert mask_text("₹400 for two") == "₹999 for two"
    assert mask_text("10-20 MINS") == "99-99 MINS"
    assert is_sensitive_key("deliveryAddress") and is_sensitive_key("phone_number")
    assert not any(
        is_sensitive_key(k) for k in ("platform", "relatedProducts", "spinId", "quantity")
    )
    assert mask_text("5.1K+") == "9.9K+"
    assert mask_text("Sample Dhaba (Ad)") == "Aaaaaa Aaaaa (Ad)"
    sample = {
        "restaurants": [
            {
                "id": "r1",
                "name": "Sample Dhaba (Ad)",
                "ratingCount": "5.1K+",
                "veg": "VEG",
                "deliveryAddress": "Flat 12B Demo Nagar",
                "phone": "9876543210",
                "eta": "10-20 MINS",
            },
            {
                "id": "r2",
                "name": "Demo Kitchen",
                "ratingCount": "230",
                "veg": "NON_VEG",
                "deliveryAddress": "Another place",
                "phone": "9876543211",
                "eta": "25 MINS",
            },
        ],
        "pagination": {"total": 14},
    }
    stats: dict[str, Stats] = {}
    collect(sample, stats)
    blob = json.dumps({p: s.as_json() for p, s in stats.items()})
    for leaked in ("Flat 12B", "Demo Nagar", "9876543210", "Sample", "Dhaba", "Another place"):
        assert leaked not in blob, leaked
    assert "VEG" in blob and "NON_VEG" in blob
    assert flags_in([sample])["ad_marker"] == 1
    rows = find_restaurant_list(sample)
    assert first_id(rows) == "r1"
    schema = {
        "properties": {
            "addressId": {"type": "string"},
            "query": {"type": "string"},
            "offset": {"type": "integer"},
            "weird": {"type": "object"},
        },
        "required": ["addressId", "query", "weird"],
    }
    args, unfilled = build_args(schema, {"address_id": "a1", "query": "dal"})
    assert args == {"addressId": "a1", "query": "dal", "offset": 0} and unfilled == ["weird"]
    print("selftest ok")


def main() -> int:
    # Windows defaults to cp1252, which cannot encode the rupee sign.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--server", choices=sorted(SERVERS), default="food")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0
    cap_dir = Path("spikes/captures") / args.server
    try:
        report = asyncio.run(capture(args.server, args.port, not args.no_browser, cap_dir))
    except KeyboardInterrupt:
        print("Cancelled.")
        return 130
    except BaseException as exc:  # noqa: BLE001
        print_failure(exc)
        return 1
    out = Path(f"spikes/results/shapes_{args.server}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print()
    for name, entry in report["tools"].items():
        extra = f", ~{entry['est_tokens']} tokens" if entry.get("status") == "ok" else ""
        print(f"{name}: {entry.get('status')}{extra}")
        if entry.get("unfilled_required"):
            print(f"  needs: {entry['unfilled_required']}")
        if entry.get("error"):
            print(f"  error: {entry['error']}")
    print(f"\nShape report saved to {out} (safe to share; read it once first).")
    print(f"Raw responses saved to {cap_dir}\\ (local only, git-ignored).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
