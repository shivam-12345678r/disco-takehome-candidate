from __future__ import annotations

import re

from campaign.catalog import money
from campaign.types import Brief, Gender, PriceTier

ORDER_VALUE: dict[PriceTier, int] = {
    "value": 32,
    "mid": 54,
    "premium": 88,
    "luxury": 400,
    "unknown": 48,
}

QUESTIONS = {
    "empty": [
        "What do you sell?",
        "Who is it for, and about what does it cost?",
    ],
    "vague": [
        "What is the product, in concrete terms?",
        "Who is it for, and is it a premium or a value buy?",
        "What should a shopper do after they see the ad: buy, subscribe, or visit?",
    ],
    "moms": [
        "What is the product moms would actually buy?",
        "How old are the kids, and is this a gift, a staple, or a treat?",
        "What does it cost?",
    ],
}


def _empty(description: str) -> Brief:
    return Brief(description=description, assumedOrderValueUsd=ORDER_VALUE["unknown"])


def _push_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _parse_price(text: str) -> float | None:
    match = re.search(r"\$\s*(\d{1,3}(?:,\d{3})+|\d+)", text)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _is_off_catalog(text: str) -> bool:
    return bool(re.search(r"\b(b2b|saas|software|dental practices?|patient recall|workflow)\b", text, re.I))


def _is_vague(text: str) -> bool:
    words = [word for word in text.strip().split() if word]
    if re.match(r"^(idk|i don't know|dunno|test|hello|hi|asdf)\b", text, re.I):
        return True
    if re.search(r"\bjust try\b", text, re.I) and len(words) <= 6:
        return True
    if re.search(r"feel better", text, re.I) and not re.search(r"\b(sell|make|food|wear|app)\b", text, re.I):
        return True
    if re.search(r"new kind of thing", text, re.I):
        return True
    return len(words) > 0 and len(words) <= 3


def _assume_age(brief: Brief, age: tuple[int, int], assumption: str) -> None:
    if brief.age:
        return
    brief.age = age
    brief.ageSource = "assumed"
    brief.assumptions.append(assumption)


def _detect_product(text: str, brief: Brief) -> None:
    lower = text.lower()

    if re.search(r"subscription box", lower) and re.search(r"cat", lower):
        brief.product = "New-cat subscription box"
        _push_unique(brief.cores, "pet_supplies")
        brief.playful = True
        brief.subscription = True
        brief.priceTier = "value"
        _push_unique(brief.modifiers, "subscription")
        brief.assumptions.append("A starter box of toys and samples is treated as a value purchase.")
        _assume_age(brief, (25, 40), "New cat owners are treated as roughly 25–40.")
        brief.claims.append("First months of a new cat: toys, food samples, and a short guide")
        return

    if re.search(r"\b(dog food|kibble|canine|senior dogs?)\b", text, re.I) or (
        re.search(r"dog", lower) and re.search(r"food", lower)
    ):
        brief.product = "Premium dog food" if re.search(r"premium", lower) else "Dog food"
        _push_unique(brief.cores, "pet_food")
        brief.clinical = True
        if re.search(r"\b(senior|older)\b|\bjoint\b", lower):
            brief.product = "Premium senior dog food" if re.search(r"premium", lower) else "Senior dog food"
            _assume_age(brief, (30, 65), "Senior-dog owners are treated as roughly 30–65.")
        elif re.search(r"\baging\b", lower):
            _assume_age(
                brief,
                (30, 65),
                "Aging dogs are treated as a senior-owner audience, roughly 30–65. The advertiser did not say senior.",
            )
        if re.search(r"premium|vet|grain[-\s]?free|joint|orthopedic|hips?", lower):
            brief.priceTier = "premium"
        if re.search(r"joint", lower):
            brief.claims.append("Formulated for joint health")
        if re.search(r"\bhips?\b|orthopedic", lower):
            brief.claims.append("Focused on hips")
        if re.search(r"grain[-\s]?free", lower):
            brief.claims.append("Grain-free")
        if re.search(r"vet", lower):
            brief.claims.append("Vet-formulated")
        if re.search(r"longevity", lower):
            brief.claims.append("Aimed at longevity")
        return

    if re.search(r"activewear|leggings|yoga pants", lower):
        brief.product = "Sustainable women's activewear"
        _push_unique(brief.cores, "activewear")
        brief.fitness = True
        _push_unique(brief.modifiers, "fitness")
        if re.search(r"women", lower):
            brief.gender = "female"
        if re.search(r"sustainable|recycled|ocean plastic", lower):
            brief.sustainable = True
            _push_unique(brief.modifiers, "sustainable")
        if re.search(r"ocean plastic", lower):
            brief.claims.append("Made from recycled ocean plastic")
        if re.search(r"lululemon|girlfriend collective", lower):
            brief.priceTier = "premium"
            brief.claims.append("Priced between Lululemon and Girlfriend Collective")
        return

    if re.search(r"non-alcoholic|adaptogen|sparkling drink|functional cocktail", lower):
        brief.product = "Non-alcoholic adaptogen sparkling drink"
        _push_unique(brief.cores, "functional_beverage")
        brief.alcoholAlternative = True
        brief.wellness = True
        _push_unique(brief.modifiers, "wellness")
        brief.priceTier = "mid"
        _assume_age(brief, (22, 40), "Functional-drink buyers are treated as roughly 22–40.")
        brief.claims.append("A sparkling, non-alcoholic drink with adaptogens")
        if re.search(r"hangover|feel good", lower):
            brief.claims.append("Meant to feel good without a hangover")
        return

    if re.search(r"candle", lower):
        brief.product = "Handmade soy candles"
        _push_unique(brief.cores, "home_fragrance")
        brief.gifting = bool(re.search(r"gift", lower))
        if brief.gifting:
            _push_unique(brief.modifiers, "gifting")
        if re.search(r"soy|natural|no synthetic", lower):
            brief.sustainable = True
            brief.claims.append("Soy wax and no synthetic fragrance")
        if re.search(r"vermont|hand|small-batch", lower):
            brief.claims.append("Poured by hand in small batches")
        if re.search(r"gift", lower):
            brief.claims.append("Bought mostly as gifts")
        brief.priceTier = "mid"
        return

    if re.search(r"\b(skis?|outerwear|backcountry|patrollers?)\b", lower):
        brief.product = "Technical ski outerwear"
        _push_unique(brief.cores, "outdoor")
        brief.priceTier = "luxury"
        _assume_age(brief, (25, 50), "Backcountry skiers are treated as roughly 25–50.")
        brief.claims.append("Shells worn by patrollers, built for serious backcountry use")
        return

    if re.search(r"clean(ing|er)|refillable", lower):
        brief.product = "Refillable cleaning concentrates"
        _push_unique(brief.cores, "household_values")
        brief.sustainable = True
        _push_unique(brief.modifiers, "sustainable")
        brief.priceTier = "mid"
        brief.claims.append("Refillable concentrates that skip single-use plastic")
        if re.search(r"big brands", lower):
            brief.claims.append("Cleans as well as the big brands")
        if re.search(r"sustainab", lower):
            brief.claims.append("Meant for shoppers who already care about sustainability")
        return

    if re.search(r"handbag|purse|leather bag", lower):
        brief.product = "Custom leather handbags"
        _push_unique(brief.cores, "luxury_accessories")
        brief.gender = "female"
        brief.priceTier = "luxury"
        brief.grownUp = True
        _assume_age(brief, (30, 60), "A $1,200 handbag buyer is treated as roughly 30–60.")
        brief.claims.append("Italian-made, handcrafted in Florence, custom fit")
        if re.search(r"6 weeks", lower):
            brief.claims.append("Ships in about six weeks")
        return

    if re.search(r"protein bar", lower):
        brief.product = "Protein bars"
        _push_unique(brief.cores, "snacks")
        brief.fitness = True
        brief.playful = True
        brief.priceTier = "mid"
        brief.assumptions.append("No price was given, so the bar is treated as a mid-tier food.")
        brief.claims.append("A protein bar pitched on taste, not on looking like health food")
        return

    if re.search(r"supplement|pre-workout|creatine|protein powder", lower):
        brief.product = "Workout supplements"
        _push_unique(brief.cores, "supplements")
        brief.fitness = True
        _push_unique(brief.modifiers, "fitness")
        brief.valuePositioning = True
        brief.priceTier = "value"
        _assume_age(
            brief,
            (22, 42),
            "Workout-supplement buyers are treated as roughly 22–42, matching this catalog's fitness audiences.",
        )
        brief.claims.append("Pre-workout, creatine, and protein")
        if re.search(r"half the cost|compete on price", lower):
            brief.claims.append("Same formulations as expensive brands, at about half the price")
        return

    if re.search(r"bedding|linen|sheets|duvet", lower):
        brief.product = "Linen bedding"
        _push_unique(brief.cores, "bedding")
        brief.grownUp = True
        brief.priceTier = "premium"
        if re.search(r"linen|portugal|breathable", lower):
            brief.sustainable = True
            _push_unique(brief.modifiers, "sustainable")
            brief.claims.append("Breathable linen, made in Portugal")
        if re.search(r"grown-up|brooklinen|parachute", lower):
            brief.claims.append("A quieter alternative to the Brooklinen and Parachute look")
        _assume_age(brief, (32, 60), "The 'grown-up bedding' buyer is treated as roughly 32–60.")
        return

    if re.search(r"skincare|serum|makeup|lipstick", lower):
        brief.product = "Beauty product"
        _push_unique(brief.cores, "beauty")
        brief.gender = "female" if re.search(r"women|female", lower) else "unknown"


def _detect_flags(text: str, brief: Brief) -> None:
    lower = text.lower()
    if re.search(r"subscription|monthly plan", lower):
        brief.subscription = True
        _push_unique(brief.modifiers, "subscription")
    if re.search(r"sustainab|recycled|ocean plastic|refill", lower):
        brief.sustainable = True
        _push_unique(brief.modifiers, "sustainable")
    if re.search(r"\bgift", lower):
        brief.gifting = True
        _push_unique(brief.modifiers, "gifting")
    if re.search(r"\bwomen\b", lower) and brief.gender == "unknown":
        brief.gender = "female"
    if re.search(r"\bmen\b", lower) and not re.search(r"women", lower):
        brief.gender = "male"

    price = _parse_price(text)
    if price is not None:
        brief.priceUsd = price
        if price >= 500:
            brief.priceTier = "luxury"
        elif price >= 100 and brief.priceTier != "luxury":
            brief.priceTier = "premium"
        elif brief.priceTier == "unknown":
            brief.priceTier = "mid" if price >= 40 else "value"
        brief.claims.append(f"Starts around {money(price)}")

    if re.search(r"premium", lower) and brief.priceTier == "unknown":
        brief.priceTier = "premium"
    if re.search(r"half the cost|compete on price|cheap", lower):
        brief.valuePositioning = True
        brief.priceTier = "value"
        _push_unique(brief.modifiers, "value")


def _finish(brief: Brief) -> None:
    if brief.priceTier == "unknown" and brief.cores:
        brief.priceTier = "mid"
        brief.assumptions.append("No price was given, so this is treated as a mid-tier product.")
    brief.assumedOrderValueUsd = brief.priceUsd if brief.priceUsd is not None else ORDER_VALUE[brief.priceTier]
    if brief.priceUsd is None and brief.cores:
        brief.assumptions.append(
            f"Bid math uses an assumed order value of ${int(brief.assumedOrderValueUsd)} for a {brief.priceTier} product."
        )

    words = len([word for word in brief.description.split() if word])
    if brief.insufficient:
        brief.confidence = 0.08
    elif brief.offCatalog:
        brief.confidence = 0.34
    elif not brief.cores:
        brief.confidence = 0.2
    else:
        confidence = 0.58
        if len(brief.claims) >= 2:
            confidence += 0.14
        if brief.priceUsd is not None:
            confidence += 0.1
        if brief.gender != "unknown":
            confidence += 0.04
        if words >= 12:
            confidence += 0.08
        brief.confidence = min(0.92, confidence)

    if brief.ageSource == "assumed":
        brief.ambiguities.append("Age is assumed from the product, not stated by the advertiser.")
    if not brief.insufficient and not brief.offCatalog and brief.cores and len(brief.claims) < 2:
        brief.ambiguities.append(
            "The brief is thin, so the draft leans on the product category more than on specific claims."
        )


def extract_heuristic(description: str) -> Brief:
    text = re.sub(r"\s+", " ", description).strip()[:2000]
    brief = _empty(text)

    if not text:
        brief.insufficient = True
        brief.clarifyingQuestions = list(QUESTIONS["empty"])
        brief.confidence = 0.05
        brief.ambiguities.append("Nothing was submitted.")
        return brief

    if _is_off_catalog(text):
        brief.offCatalog = True
        brief.product = "Dental-practice software" if re.search(r"dental", text, re.I) else "B2B software"
        brief.ambiguities.append(
            "This catalog is consumer publishers. A business-to-business product has no shelf here."
        )
        _finish(brief)
        return brief

    _detect_product(text, brief)
    _detect_flags(text, brief)

    if not brief.cores and _is_vague(text):
        brief.insufficient = True
        brief.clarifyingQuestions = list(QUESTIONS["moms"] if re.search(r"moms", text, re.I) else QUESTIONS["vague"])
        brief.ambiguities.append("The description does not name a product this catalog can place.")
        _finish(brief)
        return brief

    if not brief.cores:
        brief.insufficient = True
        brief.clarifyingQuestions = list(QUESTIONS["vague"])
        brief.ambiguities.append("No catalog category matched this description.")
        _finish(brief)
        return brief

    if brief.product == "":
        brief.product = brief.cores[0].replace("_", " ")
    if not brief.claims:
        brief.claims.append(brief.product)
    _finish(brief)
    return brief


def tier_label(tier: PriceTier) -> str:
    if tier == "unknown":
        return "Unspecified price"
    return tier[0].upper() + tier[1:]


def gender_label(gender: Gender) -> str:
    if gender == "unknown":
        return "Any"
    if gender == "balanced":
        return "All genders"
    if gender == "female":
        return "Women"
    return "Men"
