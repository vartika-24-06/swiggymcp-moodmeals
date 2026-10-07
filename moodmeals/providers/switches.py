"""Failure and quirk switches for the mock world (tasks T2.2; requirements R9, design 16).

Every switch turns on one real-world behaviour we observed or expect, so evals can
test how the agent copes. All defaults are the calm case.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Switches:
    # tool name -> "timeout" or "error": that tool fails every time
    fail_tools: dict[str, str] = field(default_factory=dict)
    empty_search: bool = False  # searches succeed but return nothing
    partial_menu: bool = False  # menus are truncated and some prices are unreadable
    all_closed: bool = False  # every restaurant is CLOSED
    out_of_stock: frozenset[str] = frozenset()  # item or product ids; "*" means everything
    ad_rate: float = 0.3  # share of search results marked "(Ad)" or promoted (real sample: 0.8)
    invalid_veg_rate: float = 0.0  # share of products with VEG_CLASSIFIER_INVALID
    max_qty: int | None = None  # force every pack's per-order maximum
    buy_again_badges: bool = True  # history-revealing badges on some products (R7.2)
    price_scale: float = 1.0  # raise to make "nothing within budget" scenarios
    # Text hidden in the name of the first dish a menu or dish search returns. It tests prompt
    # injection (design 10.4): dish descriptions are not shown to the model, names are.
    inject_text: str = ""
