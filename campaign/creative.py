from __future__ import annotations

import re

from campaign.types import Brief, Creative, ScoredPersona


def _clip(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= limit:
        return clean
    cut = clean[: limit - 1]
    last_space = cut.rfind(" ")
    trimmed = cut[:last_space] if last_space > 24 else cut
    return f"{trimmed.rstrip()}…"


def _sentence(text: str) -> str:
    trimmed = re.sub(r"\.+$", "", text.strip())
    return f"{trimmed}." if trimmed else ""


def _copy_for(persona_id: str, brief: Brief) -> tuple[str, str]:
    product = brief.product
    first = brief.claims[0] if brief.claims else product
    extra = next((claim for claim in brief.claims if claim != first), "")
    lead = _sentence(first)
    nxt = _sentence(extra) if extra else ""

    if persona_id == "persona_001":
        return _clip(first, 66), _clip(f"{lead} {nxt or _sentence(product)}".strip(), 240)
    if persona_id == "persona_002":
        headline = f"{product}, handled monthly" if brief.subscription else f"{product}, one less errand"
        extra_line = "A recurring order option." if brief.subscription else "See whether it fits your routine."
        return _clip(headline, 66), _clip(f"{lead} {extra_line} {nxt}".strip(), 240)
    if persona_id == "persona_003":
        return _clip(extra or first, 66), _clip(f"{product}. {lead}".strip(), 240)
    if persona_id == "persona_004":
        if len(first) > 48:
            headline = f"{product}, label first"
        else:
            headline = f"Read the label: {first[:1].lower()}{first[1:]}"
        return _clip(headline, 66), _clip(f"{lead} {nxt} For the animal who counts as family.".strip(), 240)
    if persona_id == "persona_005":
        return _clip(product, 66), _clip(f"{lead} {nxt} Take a closer look.".strip(), 240)
    if persona_id == "persona_006":
        return (
            _clip(f"{first.rstrip('.')}, in detail", 66),
            _clip(f"{lead} {nxt} Read the details behind the stated materials.".strip(), 240),
        )
    if persona_id == "persona_007":
        headline = f"{product} on repeat" if brief.subscription else f"Start {product.lower()}"
        extra_line = "A recurring order option." if brief.subscription else "See whether it fits your routine."
        return _clip(headline, 66), _clip(f"{lead} {extra_line}".strip(), 240)
    if persona_id == "persona_008":
        headline = extra or "The expensive formula, for less" if brief.valuePositioning else f"Compare it: {product}"
        body_extra = "The offer is the price." if brief.valuePositioning else nxt
        return _clip(headline, 66), _clip(f"{lead} {body_extra}".strip(), 240)
    if persona_id == "persona_009":
        return _clip(product, 66), _clip(f"{lead} {nxt} Bought to be used.".strip(), 240)
    if persona_id == "persona_010":
        return _clip(f"Give {product.lower()}", 66), _clip(f"{lead} {nxt or 'It should look like a gift.'}".strip(), 240)
    return _clip(product, 66), _clip(lead, 240)


def write_creatives_from_rules(brief: Brief, personas: list[ScoredPersona]) -> list[Creative]:
    used: set[str] = set()
    creatives: list[Creative] = []
    for row in personas:
        if not row.selected:
            continue
        headline, body = _copy_for(row.persona.id, brief)
        if len(headline) < 8:
            headline = _clip(f"{brief.product}: {row.persona.name}", 66)
        if len(body) < 20:
            body = _clip(f"{_sentence(brief.product)} Learn more about {brief.product.lower()}.", 240)
        if headline.lower() in used:
            headline = _clip(f"{row.persona.messaging_preferences[0]}: {headline}", 66)
        used.add(headline.lower())
        creatives.append(
            Creative(
                personaId=row.persona.id,
                personaName=row.persona.name,
                headline=headline,
                body=body,
                why=row.reason,
                source="rules",
            )
        )
    return creatives
