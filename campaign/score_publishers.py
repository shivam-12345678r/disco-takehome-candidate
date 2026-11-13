from __future__ import annotations

import math
import re

from campaign.catalog import aov_band, income_rank, load_publishers, parse_age_skew, tier_rank
from campaign.types import Brief, Factor, Fit, Publisher, ScoredPublisher

RECOMMEND_THRESHOLD = 50
MAX_RECOMMENDED = 5
SHELF_POINTS = 40
AUDIENCE_POINTS = 9


def _shelf_fit(core: str, publisher: Publisher) -> bool:
    subs = publisher.subcategories
    if core == "pet_food":
        return "pet_food" in subs or "pet_pharmacy" in subs
    if core == "pet_supplies":
        return any(tag in subs for tag in ("pet_supplies", "toys", "treats"))
    if core == "activewear":
        return "activewear" in subs
    if core == "functional_beverage":
        return publisher.category == "beverages" or "functional_beverages" in subs
    if core == "supplements":
        return "supplements" in subs or "vitamins" in subs
    if core == "bedding":
        return "bedding" in subs or "home_textiles" in subs
    if core == "household_values":
        return "non_toxic" in subs or "organic" in subs or "natural" in subs
    if core == "snacks":
        return "pantry" in subs or "organic" in subs
    if core == "beauty":
        return publisher.category == "beauty"
    return False


def _audience_fit(core: str, publisher: Publisher) -> bool:
    subs = publisher.subcategories
    if core in ("pet_food", "pet_supplies"):
        return publisher.category == "pet"
    if core == "activewear":
        return publisher.category == "apparel" or publisher.category == "wellness_services"
    if core == "outdoor":
        outdoor_terms = r"\b(?:backcountry|ski(?:ing)?|hiking|trail|alpine|technical outerwear)\b"
        return any(tag in subs for tag in ("outdoor", "skiing", "hiking")) or bool(
            re.search(outdoor_terms, _notes_blob(publisher), re.I)
        )
    if core == "functional_beverage":
        return (
            publisher.category == "groceries"
            or publisher.category == "wellness_dtc"
            or "alcohol" in subs
        )
    if core == "supplements":
        return (
            publisher.category == "wellness_services"
            or "activewear" in subs
            or publisher.category == "groceries"
        )
    if core in ("bedding", "home_fragrance"):
        return publisher.category == "home" or "gifting" in subs
    if core == "household_values":
        return "household" in subs or publisher.category == "home" or publisher.category == "groceries"
    if core == "snacks":
        return (
            publisher.category == "meal_kits"
            or publisher.category == "instant_delivery"
            or "groceries" in subs
        )
    if core == "luxury_accessories":
        accessory_inventory = {"handbags", "bags", "accessories", "luxury_accessories"}
        return (
            publisher.category == "apparel"
            and income_rank(publisher.audience.income_tier) >= 3
            and bool(accessory_inventory.intersection(subs))
        )
    if core == "beauty":
        return publisher.category == "wellness_dtc" or publisher.category == "apparel"
    return False


def _fit_for(brief: Brief, publisher: Publisher) -> Fit:
    if any(_shelf_fit(core, publisher) for core in brief.cores):
        return "shelf"
    if any(_audience_fit(core, publisher) for core in brief.cores):
        return "audience"
    return "none"


def _overlap_ratio(a: tuple[int, int], b: tuple[int, int]) -> float:
    start = max(a[0], b[0])
    end = min(a[1], b[1])
    if end < start:
        return 0
    span = min(a[1] - a[0], b[1] - b[0]) or 1
    return (end - start) / span


def _shelf_detail(brief: Brief, publisher: Publisher, fit: Fit) -> str:
    if fit == "shelf":
        return f"{publisher.name} actually sells in this product's category."
    if fit == "audience":
        return f"{publisher.name} reaches a related shopper, but does not sell {brief.product.lower()}."
    return f"No category overlap with {brief.product.lower()}."


def _notes_blob(publisher: Publisher) -> str:
    return f"{publisher.notes} {' '.join(publisher.subcategories)} {publisher.category}"


def _clamp(n: float, low: float, high: float) -> int:
    return int(max(low, min(high, n)))


def _explain(decision: str, fit: Fit, factors: list[Factor], brief: Brief, publisher: Publisher) -> str:
    if decision == "recommend":
        positives = [factor.detail.rstrip(".") for factor in sorted(
            (factor for factor in factors if factor.impact > 0),
            key=lambda factor: factor.impact,
            reverse=True,
        )[:2]]
        return ". ".join(positives) + "."
    worst = min(factors, key=lambda factor: factor.impact) if factors else None
    if fit != "shelf":
        shelf_line = _shelf_detail(brief, publisher, fit).rstrip(".")
        if worst and worst.impact < 0:
            return f"{shelf_line}. {worst.detail}"
        return shelf_line + "."
    return worst.detail if worst else "Score stayed under the recommendation bar."


def score_publisher(brief: Brief, publisher: Publisher) -> ScoredPublisher:
    factors: list[Factor] = []
    fit = _fit_for(brief, publisher)
    notes = _notes_blob(publisher)

    if fit == "none":
        factors.append(Factor(name="Category", impact=0, detail=_shelf_detail(brief, publisher, fit)))
        return ScoredPublisher(
            publisher=publisher,
            score=0,
            decision="exclude",
            fit=fit,
            factors=factors,
            reason=_shelf_detail(brief, publisher, fit),
            heldOut=False,
        )

    factors.append(
        Factor(
            name="Shelf" if fit == "shelf" else "Adjacent audience",
            impact=SHELF_POINTS if fit == "shelf" else AUDIENCE_POINTS,
            detail=_shelf_detail(brief, publisher, fit),
        )
    )

    if fit == "shelf":
        mods: list[Factor] = []
        if brief.subscription and re.search(r"subscription", notes, re.I):
            mods.append(
                Factor(
                    name="Subscription",
                    impact=4,
                    detail="Both the product and this publisher run on repeat orders.",
                )
            )
        if brief.sustainable and re.search(
            r"sustainab|clean-ingredient|non-toxic|non_toxic|organic|natural", notes, re.I
        ):
            mods.append(
                Factor(
                    name="Sustainability",
                    impact=6,
                    detail="The publisher's own notes say this audience responds to sustainability or clean-ingredient claims.",
                )
            )
        if brief.clinical and (
            re.search(r"health|premium positioning", publisher.notes, re.I)
            or "pet_pharmacy" in publisher.subcategories
        ):
            mods.append(
                Factor(
                    name="Health positioning",
                    impact=4,
                    detail=(
                        "Pet pharmacy is on this shelf, which fits a clinical food."
                        if "pet_pharmacy" in publisher.subcategories
                        else "The notes describe a health-conscious shopper, which fits a clinical claim."
                    ),
                )
            )
        if brief.playful and re.search(r"playful|fun", publisher.notes, re.I):
            mods.append(Factor(name="Tone", impact=4, detail="A playful publisher voice matches this product."))
        if brief.priceTier in ("premium", "luxury") and re.search(
            r"premium|quality|design-conscious|affluent|high-aov", publisher.notes, re.I
        ):
            mods.append(
                Factor(
                    name="Premium context",
                    impact=4,
                    detail="The publisher is already a quality or design buy, not a discount aisle.",
                )
            )
        if brief.gifting and re.search(r"gift", notes, re.I):
            mods.append(Factor(name="Gifting", impact=4, detail="This publisher has a real gifting audience."))
        if brief.fitness and re.search(r"fitness|workout|active", publisher.notes, re.I):
            mods.append(Factor(name="Fitness context", impact=3, detail="Notes describe a fitness-engaged shopper."))
        mods.sort(key=lambda factor: factor.impact, reverse=True)
        used = 0
        for mod in mods:
            if used + mod.impact > 14:
                continue
            factors.append(mod)
            used += mod.impact

    if brief.age:
        publisher_age = parse_age_skew(publisher.audience.age_skew)
        ratio = _overlap_ratio(brief.age, publisher_age)
        assumed = "Assumed age. " if brief.ageSource == "assumed" else ""
        if ratio >= 0.4:
            factors.append(
                Factor(
                    name="Age",
                    impact=8,
                    detail=f"{assumed}{brief.age[0]}–{brief.age[1]} overlaps this {publisher.audience.age_skew} audience.",
                )
            )
        elif ratio > 0:
            factors.append(
                Factor(
                    name="Age",
                    impact=3,
                    detail=f"{assumed}Only a thin overlap with the {publisher.audience.age_skew} audience.",
                )
            )
        else:
            factors.append(
                Factor(
                    name="Age",
                    impact=-4,
                    detail=f"{assumed}{brief.age[0]}–{brief.age[1]} does not overlap {publisher.audience.age_skew}.",
                )
            )

    female = publisher.audience.gender_split.female
    if brief.gender == "female":
        if female >= 0.75:
            factors.append(
                Factor(
                    name="Gender",
                    impact=8,
                    detail=f"{round(female * 100)}% of this audience is women.",
                )
            )
        elif female >= 0.55:
            factors.append(
                Factor(
                    name="Gender",
                    impact=3,
                    detail=f"Women are a majority here ({round(female * 100)}%), but not the whole audience.",
                )
            )
        elif female < 0.35:
            factors.append(
                Factor(
                    name="Gender",
                    impact=-6,
                    detail=f"This audience is mostly men ({round((1 - female) * 100)}%).",
                )
            )
    elif brief.gender == "male":
        male = publisher.audience.gender_split.male
        factors.append(
            Factor(
                name="Gender",
                impact=6 if male >= 0.55 else -4,
                detail=(
                    f"{round(male * 100)}% of this audience is men."
                    if male >= 0.55
                    else "This audience does not skew male."
                ),
            )
        )

    brief_income = 2 if brief.priceTier == "value" else 4 if brief.priceTier == "luxury" else None if brief.priceTier == "unknown" else 3
    if brief_income is not None:
        diff = abs(income_rank(publisher.audience.income_tier) - brief_income)
        factors.append(
            Factor(
                name="Income",
                impact=5 if diff == 0 else 2 if diff == 1 else -3,
                detail=f"Publisher income is {publisher.audience.income_tier}; the product reads as {brief.priceTier}.",
            )
        )

    product_band = tier_rank(brief.priceTier)
    if product_band is not None:
        diff = abs(aov_band(publisher.avg_order_value_usd) - product_band)
        impact = 12 if diff == 0 else 8 if diff == 1 else 6 if diff == 2 else -6
        factors.append(
            Factor(
                name="Basket size",
                impact=impact,
                detail=f"Shoppers here spend about ${publisher.avg_order_value_usd} per order.",
            )
        )

    if brief.priceUsd is not None:
        ratio = brief.priceUsd / publisher.avg_order_value_usd
        if ratio >= 4:
            factors.append(
                Factor(
                    name="Price gap",
                    impact=-22,
                    detail=(
                        f"${brief.priceUsd:,.0f} is {ratio:.1f}× the "
                        f"${publisher.avg_order_value_usd} this audience usually spends."
                    ),
                )
            )
        elif ratio >= 2.5:
            factors.append(
                Factor(
                    name="Price gap",
                    impact=-8,
                    detail=(
                        f"${brief.priceUsd:,.0f} is {ratio:.1f}× a typical order here "
                        f"(${publisher.avg_order_value_usd})."
                    ),
                )
            )

    if brief.alcoholAlternative and "alcohol" in publisher.subcategories:
        factors.append(
            Factor(
                name="Contradiction",
                impact=-24,
                detail="This publisher's shelf includes alcohol, which fights a non-alcoholic pitch.",
            )
        )
    if brief.sustainable and re.search(r"impulse", publisher.notes, re.I):
        factors.append(
            Factor(
                name="Contradiction",
                impact=-12,
                detail="An impulse, late-night checkout is the wrong context for a sustainability pitch.",
            )
        )
    if brief.clinical and re.search(r"playful|fun,", publisher.notes, re.I):
        factors.append(
            Factor(
                name="Contradiction",
                impact=-8,
                detail="The publisher converts on a playful voice. This product is clinical.",
            )
        )
    if brief.valuePositioning and re.search(r"skeptical of unsubstantiated", publisher.notes, re.I):
        factors.append(
            Factor(
                name="Skepticism",
                impact=-6,
                detail="This audience doubts me-too health claims. A 'same formula, half the price' pitch has to earn it.",
            )
        )

    score = _clamp(sum(factor.impact for factor in factors), 0, 99)
    decision = "recommend" if score >= RECOMMEND_THRESHOLD else "exclude"
    return ScoredPublisher(
        publisher=publisher,
        score=score,
        decision=decision,
        fit=fit,
        factors=factors,
        reason=_explain(decision, fit, factors, brief, publisher),
        heldOut=False,
    )


def score_publishers(brief: Brief) -> list[ScoredPublisher]:
    scored = [score_publisher(brief, publisher) for publisher in load_publishers()]
    scored.sort(key=lambda row: (-row.score, row.publisher.name))
    passing = [row for row in scored if row.decision == "recommend"]
    for row in passing[MAX_RECOMMENDED:]:
        row.decision = "exclude"
        row.heldOut = True
        row.reason = (
            f"Cleared the bar at {row.score}, and was held out so a first test stays on "
            f"at most {MAX_RECOMMENDED} publishers."
        )
    return scored
