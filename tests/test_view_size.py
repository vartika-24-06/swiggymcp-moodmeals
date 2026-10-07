"""The model's request must not grow with every search (free tiers cap one request).

Groq's free tier answered HTTP 413 to a request of about 8k tokens in the first real smoke run
(S-04, which searches restaurants and Instamart). Older tool results now shrink to a note and
each result shows a capped number of items.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_loop import make, products, search  # noqa: E402

from moodmeals.core.view import (  # noqa: E402
    MAX_PRODUCTS_IN_VIEW,
    MAX_RESTAURANTS_IN_VIEW,
    RECENT_RESULTS_IN_FULL,
    trim_tool_log,
)


def run_searches():
    script = [search("a"), search("b"), products("c"), products("d"), products("e"), products("f")]
    agent, state, llm, _ = make(script)
    agent.strict_stop = False
    with contextlib.suppress(AssertionError):  # the script runs out after the last search
        agent.run(state)  # we only need the views it produced
    return state, llm


def test_the_view_stops_growing_once_older_results_are_trimmed():
    state, llm = run_searches()
    sizes = [len(json.dumps(v)) for v in llm.views]
    assert len(sizes) >= RECENT_RESULTS_IN_FULL + 3
    assert sizes[-1] <= sizes[-2] * 1.05  # flat between the last turns, not still climbing
    shown = len(json.dumps(llm.views[-1]["untrusted_data"]["tool_results"]))
    assert shown < len(json.dumps(state.tool_log)) * 0.75  # clearly smaller than untrimmed
    assert sizes[-1] < 12_000  # about 3k tokens of view: room under a ~6k-token request cap


def test_only_the_most_recent_results_are_shown_in_full():
    _, llm = run_searches()
    results = llm.views[-1]["untrusted_data"]["tool_results"]
    full = [r for r in results if "omitted" not in r["result"]]
    older = [r for r in results if "omitted" in r["result"]]
    assert len(full) == RECENT_RESULTS_IN_FULL and older
    assert all("total" in r["result"] for r in older)  # the note still says how many there were


def test_each_result_shows_a_capped_number_of_items():
    _, llm = run_searches()
    first = llm.views[1]["untrusted_data"]["tool_results"][0]["result"]
    assert len(first["restaurants"]) <= MAX_RESTAURANTS_IN_VIEW and first["total"] >= 10
    for view in llm.views:
        for r in view["untrusted_data"]["tool_results"]:
            assert len(r["result"].get("products", [])) <= MAX_PRODUCTS_IN_VIEW


def test_the_ledger_still_knows_items_the_view_no_longer_shows():
    state, _ = run_searches()
    assert len(state.ledger.products) > MAX_PRODUCTS_IN_VIEW  # kept for the validator


def test_errors_are_never_trimmed_and_short_logs_are_untouched():
    log = [{"tool": "search_restaurants", "params": {}, "error": "timeout"}] * 5
    assert trim_tool_log(log) == log
    short = [{"tool": "t", "params": {}, "result": {"total": 1, "items": [1]}}]
    assert trim_tool_log(short) == short
