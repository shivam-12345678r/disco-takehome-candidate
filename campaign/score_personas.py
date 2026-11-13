from __future__ import annotations

import re

from campaign.catalog import load_personas
from campaign.types import Brief, Factor, Persona, ScoredPersona

SELECT_THRESHOLD = 36
MAX_PERSONAS = 5
TAG_CAP = 48

AFFINITY_ALIASES = {
    "clean_beauty": "beauty",
    "classic_apparel": "apparel",
    "family_products": "household",
    "fitness_services": "fitness",
    "functional_beverages": "functional_beverage",
    "gourmet_food": "grocery",
    "home_decor": "home",
    "home_goods": "home",
    "instant_delivery": "convenience",
    "meal_kits": "grocery",
    "organic_grocery": "grocery",
    "pet_health": "pet_food",
    "premium_basics": "apparel",
    "premium_grocery": "grocery",
    "quick_commerce": "convenience",
    "recovery": "fitness",
    "refillable_products": "household",
    "subscription_boxes": "subscription",
    "subscription_services": "subscription",
    "sustainable_apparel": "sustainable",
}

TOO_GENERIC_ALONE = {"grocery", "premium", "apparel", "home", "subscription"}

PRICE_TABLE = {
    "low": {"value": -4, "mid": 8, "premium": 16, "luxury": 18, "unknown": 4},
    "low-medium": {"value": 2, "mid": 12, "premium": 14, "luxury": 8, "unknown": 4},
    "medium": {"value": 8, "mid": 14, "premium": 8, "luxury": -4, "unknown": 6},
    "medium-high": {"value": 12, "mid": 10, "premium": 2, "luxury": -12, "unknown": 4},
    "high": {"value": 18, "mid": 6, "premium": -10, "luxury": -22, "unknown": 0},
}


def _meaningful_tags(matched: list[str]) -> list[str]:
    specific = [tag for tag in matched if tag not in TOO_GENERIC_ALONE]
    return matched if specific else []


def _persona_tags(persona: Persona) -> list[str]:
    tags = {AFFINITY_ALIASES.get(tag, tag) for tag in persona.category_affinities}
    return sorted(tags)


def _brief_tags(brief: Brief) -> list[str]:
    tags: set[str] = set()
    for core in brief.cores:
        if core == "household_values":
            tags.add("household")
        elif core == "luxury_accessories":
            tags.add("luxury")
        elif core == "home_fragrance":
            tags.add("home")
        elif core == "outdoor":
            tags.add("fitness")
        elif core == "bedding":
            tags.add("bedding")
            tags.add("home")
        else:
            tags.add(core)
    if brief.sustainable:
        tags.add("sustainable")
    if brief.subscription:
        tags.add("subscription")
    if brief.gifting:
        tags.add("gifting")
    if brief.fitness:
        tags.add("fitness")
    if brief.wellness:
        tags.add("wellness")
    if brief.clinical and "pet_food" in brief.cores:
        tags.add("pet_health")
    if brief.valuePositioning:
        tags.add("value")
    if brief.priceTier in ("premium", "luxury"):
        tags.add("premium")
    if "activewear" in brief.cores:
        tags.add("apparel")
    if "snacks" in brief.cores:
        tags.add("grocery")
    return sorted(tags)


def _price_impact(sensitivity: str, tier: str) -> tuple[int, str]:
    impact = PRICE_TABLE.get(sensitivity, {}).get(tier, 0)
    if impact >= 0:
        detail = f"{sensitivity} price sensitivity fits a {tier} product."
    else:
        detail = f"{sensitivity} price sensitivity fights a {tier} product."
    return impact, detail


def _disinterest_impact(persona: Persona, brief: Brief) -> list[Factor]:
    factors: list[Factor] = []
    for item in persona.disinterested_in:
        text = item.lower()
        if re.search(r"luxury", text) and brief.priceTier == "luxury":
            factors.append(Factor("Disinterest", -28, "This persona opts out of luxury positioning."))
        elif re.search(r"luxury-only|vague premium", text) and brief.priceTier == "premium" and len(brief.claims) < 2:
            factors.append(Factor("Disinterest", -16, "This persona ignores premium claims that are not specific."))
        elif re.search(r"ultra-cheap|generic pet", text) and brief.priceTier == "value" and "pet_food" in brief.cores:
            factors.append(Factor("Disinterest", -20, "This pet buyer avoids cheap, generic food."))
        elif re.search(r"subscription-only", text) and brief.subscription and not brief.gifting:
            factors.append(Factor("Disinterest", -22, "Gifting mode fades when the product is subscription-only."))
        elif re.search(r"novelty", text) and (brief.grownUp or brief.clinical):
            factors.append(
                Factor("Disinterest", -18, "This persona wants novelty. The product is deliberately quiet or clinical.")
            )
        elif re.search(r"trendy|influencer|loud", text) and brief.playful and not brief.grownUp:
            factors.append(Factor("Disinterest", -14, "Trend-led language would miss this buyer."))
        elif re.search(r"performance claims without vibe", text) and brief.clinical and not brief.playful:
            factors.append(Factor("Disinterest", -14, "A clinical claim with no tone will not land here."))
        elif re.search(r"sedentary", text) and "bedding" in brief.cores and brief.grownUp:
            factors.append(Factor("Disinterest", -8, "A fitness identity is a weak frame for grown-up bedding."))
    return factors


def score_persona(brief: Brief, persona: Persona) -> ScoredPersona:
    if persona.id == "persona_010" and not brief.gifting:
        return ScoredPersona(
            persona=persona,
            score=0,
            factors=[
                Factor(
                    name="Mode",
                    impact=0,
                    detail="The Gifter is a seasonal mode. This brief is not a gift purchase.",
                )
            ],
            reason="Not a gift purchase, so the gifting mode stays off.",
            selected=False,
        )

    tags = _brief_tags(brief)
    affinities = _persona_tags(persona)
    matched = _meaningful_tags([tag for tag in affinities if tag in tags])
    factors: list[Factor] = []
    tag_points = 0 if not matched else min(TAG_CAP, 26 + (len(matched) - 1) * 14)
    factors.append(
        Factor(
            name="Affinity",
            impact=tag_points,
            detail=f"Overlaps on {', '.join(matched)}." if matched else "No category overlap with this persona.",
        )
    )

    impact, detail = _price_impact(persona.price_sensitivity, brief.priceTier)
    if matched:
        factors.append(Factor(name="Price", impact=impact, detail=detail))
        factors.extend(_disinterest_impact(persona, brief))

    score = max(0, min(99, sum(factor.impact for factor in factors)))
    worst = min(factors, key=lambda factor: factor.impact)
    if not matched:
        reason = "No category overlap."
    elif worst.impact < 0:
        reason = f"{factors[0].detail.rstrip('.')}. {worst.detail}"
    else:
        short_name = " ".join(persona.name.split(" ")[1:])
        reason = f"{short_name} fits {', '.join(matched[:3])}. {detail}"

    return ScoredPersona(persona=persona, score=score, factors=factors, reason=reason, selected=False)


def score_personas(brief: Brief) -> list[ScoredPersona]:
    scored = [score_persona(brief, persona) for persona in load_personas()]
    scored.sort(key=lambda row: (-row.score, row.persona.name))
    leader = scored[0].score if scored else 0
    floor = max(SELECT_THRESHOLD, leader - 16)
    picked = [row for row in scored if row.score >= floor][:MAX_PERSONAS]
    if len(picked) < 3:
        picked_ids = {row.persona.id for row in picked}
        picked.extend(
            row for row in scored
            if row.persona.id not in picked_ids
            and (row.persona.id != "persona_010" or brief.gifting)
        )
        picked = picked[:3]
    for row in picked:
        row.selected = True
    return scored
