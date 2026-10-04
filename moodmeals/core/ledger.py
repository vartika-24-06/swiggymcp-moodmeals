"""The ledger: every normalised entity the tools returned in this run.

The validator checks plans against it, so a plan can only use things the agent
actually saw (R8.1). Serialisable, so it can live inside the run state.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from moodmeals.models.types import MenuItem, Product, Restaurant


class Ledger(BaseModel):
    restaurants: dict[str, Restaurant] = Field(default_factory=dict)
    menu_items: dict[str, MenuItem] = Field(default_factory=dict)
    products: dict[str, Product] = Field(default_factory=dict)

    def add_restaurants(self, items: list[Restaurant]) -> None:
        self.restaurants.update({r.id: r for r in items})

    def add_menu_items(self, items: list[MenuItem]) -> None:
        self.menu_items.update({m.id: m for m in items})

    def add_products(self, items: list[Product]) -> None:
        self.products.update({p.id: p for p in items})
