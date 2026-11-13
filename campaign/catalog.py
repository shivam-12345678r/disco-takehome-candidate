from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from campaign.types import Audience, GenderSplit, Persona, PriceTier, Publisher

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

CORE_LABELS = {
    "pet_food": "Pet food",
    "pet_supplies": "Pet supplies",
    "activewear": "Activewear",
    "functional_beverage": "Functional beverage",
    "supplements": "Supplements",
    "bedding": "Bedding",
    "household_values": "Sustainable household",
    "snacks": "Snacks",
    "luxury_accessories": "Luxury accessories",
    "outdoor": "Technical outdoor",
    "home_fragrance": "Home fragrance",
    "beauty": "Beauty",
}


def read_data(name: str):
    return json.loads((DATA / name).read_text(encoding="utf8"))


@lru_cache(maxsize=1)
def load_publishers() -> tuple[Publisher, ...]:
    rows = []
    for raw in read_data("publishers.json"):
        audience = raw["audience"]
        split = audience["gender_split"]
        rows.append(
            Publisher(
                id=raw["id"],
                name=raw["name"],
                category=raw["category"],
                subcategories=list(raw["subcategories"]),
                monthly_impressions=raw["monthly_impressions"],
                avg_order_value_usd=raw["avg_order_value_usd"],
                audience=Audience(
                    age_skew=audience["age_skew"],
                    gender_split=GenderSplit(
                        female=split["female"],
                        male=split["male"],
                        other=split["other"],
                    ),
                    top_geos=list(audience["top_geos"]),
                    income_tier=audience["income_tier"],
                ),
                notes=raw["notes"],
            )
        )
    return tuple(rows)


@lru_cache(maxsize=1)
def load_personas() -> tuple[Persona, ...]:
    rows = []
    for raw in read_data("shopper_personas.json"):
        rows.append(
            Persona(
                id=raw["id"],
                name=raw["name"],
                age_range=raw["age_range"],
                gender_skew=raw["gender_skew"],
                description=raw["description"],
                category_affinities=list(raw["category_affinities"]),
                price_sensitivity=raw["price_sensitivity"],
                messaging_preferences=list(raw["messaging_preferences"]),
                disinterested_in=list(raw["disinterested_in"]),
                typical_aov_usd=raw["typical_aov_usd"],
            )
        )
    return tuple(rows)


def load_example_lines() -> list[str]:
    raw = (DATA / "example_advertisers.txt").read_text(encoding="utf8")
    lines = []
    for line in raw.split("\n"):
        if re.match(r"^\d+\.\s", line):
            lines.append(re.sub(r"^\d+\.\s*", "", line).strip())
    return lines


def parse_age_skew(skew: str) -> tuple[int, int]:
    match = re.search(r"(\d+)\s*-\s*(\d+)", skew)
    if not match:
        return (18, 65)
    return (int(match.group(1)), int(match.group(2)))


def income_rank(tier: str) -> int:
    if tier == "low":
        return 1
    if tier == "mid":
        return 2
    if tier == "mid-high":
        return 3
    if tier == "high":
        return 4
    return 2


def aov_band(aov: float) -> int:
    if aov < 45:
        return 1
    if aov < 95:
        return 2
    if aov < 170:
        return 3
    return 4


def tier_rank(tier: PriceTier) -> int | None:
    if tier == "value":
        return 1
    if tier == "mid":
        return 2
    if tier == "premium":
        return 3
    if tier == "luxury":
        return 4
    return None


def money(n: float) -> str:
    if n % 1 == 0:
        return f"${int(n):,}"
    return f"${n:,.2f}"
