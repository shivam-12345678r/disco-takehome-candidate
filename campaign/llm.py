from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, replace
from pathlib import Path

from openai import OpenAI

from campaign.catalog import CORE_LABELS, load_publishers
from campaign.types import (
    Brief,
    Creative,
    Factor,
    ScoredPersona,
    ScoredPublisher,
)
from campaign.score_publishers import score_publishers as score_publishers_with_rules

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_OPENAI_MODEL = "gpt-6-luna"
ORDER_VALUE = {"value": 32, "mid": 54, "premium": 88, "luxury": 400, "unknown": 48}
ALLOWED_MODIFIERS = {"subscription", "sustainable", "gifting", "fitness", "wellness", "value"}
ALLOWED_TIERS = set(ORDER_VALUE)
OUTPUT_TOKEN_LIMITS = {
    "parse_brief.md": 1600,
    "rank_publishers.md": 12000,
    "select_personas_and_creatives.md": 2600,
}


def _api_key() -> str | None:
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    try:
        import streamlit as st

        secret = st.secrets.get("OPENAI_API_KEY")
    except (FileNotFoundError, ImportError):
        return None
    return secret if isinstance(secret, str) and secret else None


def _primary_model() -> str:
    return os.environ.get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL


def has_llm_credentials() -> bool:
    return bool(_api_key())


def load_local_env() -> None:
    for name in (".env.local", ".env"):
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if value and not os.environ.get(key):
                os.environ[key] = value


def load_prompt(name: str) -> str:
    return (ROOT / "prompts" / name).read_text(encoding="utf8")


def _schema_object(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _schema_array(items: dict) -> dict:
    return {"type": "array", "items": items}


_string = {"type": "string"}
_integer = {"type": "integer"}
_boolean_schema = {"type": "boolean"}
_string_array = _schema_array(_string)

RESPONSE_SCHEMAS = {
    "parse_brief.md": _schema_object(
        {
            "product": _string,
            "cores": _schema_array({"type": "string", "enum": list(CORE_LABELS)}),
            "modifiers": _schema_array({"type": "string", "enum": sorted(ALLOWED_MODIFIERS)}),
            "claims": _string_array,
            "priceTier": {"type": "string", "enum": sorted(ALLOWED_TIERS)},
            "priceUsd": {"anyOf": [{"type": "number"}, {"type": "null"}]},
            "gender": {
                "type": "string",
                "enum": ["female", "male", "balanced", "unknown"],
            },
            "age": {
                "anyOf": [
                    {"type": "array", "items": _integer},
                    {"type": "null"},
                ]
            },
            "ageSource": {"type": "string", "enum": ["stated", "assumed", "unknown"]},
            "sustainable": _boolean_schema,
            "subscription": _boolean_schema,
            "gifting": _boolean_schema,
            "alcoholAlternative": _boolean_schema,
            "clinical": _boolean_schema,
            "playful": _boolean_schema,
            "grownUp": _boolean_schema,
            "valuePositioning": _boolean_schema,
            "fitness": _boolean_schema,
            "wellness": _boolean_schema,
            "offCatalog": _boolean_schema,
            "insufficient": _boolean_schema,
            "confidence": {"type": "number"},
            "ambiguities": _string_array,
            "assumptions": _string_array,
            "clarifyingQuestions": _string_array,
        }
    ),
    "rank_publishers.md": _schema_object(
        {
            "assessments": _schema_array(
                _schema_object(
                    {
                        "id": _string,
                        "score": _integer,
                        "fit": {"type": "string", "enum": ["shelf", "audience", "none"]},
                        "reason": _string,
                        "factors": _schema_array(
                            _schema_object(
                                {
                                    "name": _string,
                                    "impact": _integer,
                                    "detail": _string,
                                    "evidence": _string,
                                }
                            )
                        ),
                    }
                )
            )
        }
    ),
    "select_personas_and_creatives.md": _schema_object(
        {
            "selected": _schema_array(
                _schema_object({"personaId": _string, "why": _string})
            ),
            "variants": _schema_array(
                _schema_object(
                    {
                        "personaId": _string,
                        "headline": _string,
                        "body": _string,
                        "claimIds": _string_array,
                    }
                )
            ),
        }
    ),
}


EXPECTED_ROOT_FIELDS = {
    "parse_brief.md": "product",
    "rank_publishers.md": "assessments",
    "select_personas_and_creatives.md": "selected",
}


def _structured_json(response, prompt_name: str) -> dict:
    root_field = EXPECTED_ROOT_FIELDS[prompt_name]
    candidates: list[str] = []
    for item in reversed(response.output):
        if item.type != "message":
            continue
        chunks = [
            block.text
            for block in item.content
            if block.type == "output_text" and block.text
        ]
        candidates.extend(reversed(chunks))
        if chunks:
            candidates.append("".join(chunks))
    candidates.append(response.output_text)

    decoder = json.JSONDecoder()
    for candidate in candidates:
        cursor = 0
        last_match = None
        while cursor < len(candidate):
            while cursor < len(candidate) and candidate[cursor].isspace():
                cursor += 1
            if cursor >= len(candidate):
                break
            try:
                parsed, end = decoder.raw_decode(candidate, cursor)
            except json.JSONDecodeError:
                break
            if isinstance(parsed, dict) and root_field in parsed:
                last_match = parsed
            cursor = end
        if last_match is not None:
            return last_match

    # Preserve the useful JSON parser detail when the response has no complete
    # object for this stage; the caller will fall back to local rules.
    parsed = json.loads(response.output_text)
    if not isinstance(parsed, dict):
        raise ValueError(f"{prompt_name} must return a JSON object.")
    return parsed


def _complete_json(prompt_name: str, payload: dict, usage: list[dict] | None = None) -> dict:
    key = _api_key()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set. Add it to .env.local (see .env.example).")
    schema = RESPONSE_SCHEMAS[prompt_name]
    client = OpenAI(api_key=key, timeout=45.0, max_retries=1)
    response = client.responses.create(
        model=_primary_model(),
        reasoning={"effort": "none"},
        instructions=load_prompt(prompt_name),
        input=json.dumps(payload, ensure_ascii=False),
        max_output_tokens=OUTPUT_TOKEN_LIMITS[prompt_name],
        store=False,
        text={
            "format": {
                "type": "json_schema",
                "name": prompt_name.removesuffix(".md"),
                "strict": True,
                "schema": schema,
            }
        },
    )
    if usage is not None:
        usage_info = response.usage
        usage.append(
            {
                "stage": prompt_name.removesuffix(".md"),
                "model": response.model,
                "inputTokens": usage_info.input_tokens if usage_info else 0,
                "outputTokens": usage_info.output_tokens if usage_info else 0,
                "status": response.status,
            }
        )
    if response.status != "completed":
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None) if details else None
        suffix = f" ({reason})" if reason else ""
        raise RuntimeError(f"OpenAI response was {response.status}{suffix}.")
    if not response.output_text:
        raise RuntimeError("OpenAI returned no structured output.")
    return _structured_json(response, prompt_name)


def _strings(value: object, field: str, *, limit: int = 30) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{field} must be a list with at most {limit} items.")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must contain non-empty strings only.")
    return [item.strip() for item in value]


def _text(value: object, field: str, *, max_length: int = 500) -> str:
    if not isinstance(value, str) or len(value.strip()) > max_length:
        raise ValueError(f"{field} must be a string of at most {max_length} characters.")
    return value.strip()


def _required_text(value: object, field: str, *, max_length: int = 500) -> str:
    result = _text(value, field, max_length=max_length)
    if not result:
        raise ValueError(f"{field} cannot be empty.")
    return result


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean.")
    return value


def _number(value: object, field: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number.")
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}.")
    return number


def _normalized(text: str) -> str:
    return " ".join(re.findall(r"[\w]+", text.casefold()))


def extract_brief_with_model(description: str, *, usage: list[dict] | None = None) -> Brief:
    raw = _complete_json(
        "parse_brief.md",
        {"description": description, "allowedProductCores": CORE_LABELS},
        usage=usage,
    )
    product = _text(raw.get("product"), "product", max_length=160)
    cores = _strings(raw.get("cores"), "cores", limit=5)
    if any(core not in CORE_LABELS for core in cores):
        raise ValueError("The model used an unknown product category.")
    modifiers = _strings(raw.get("modifiers"), "modifiers", limit=10)
    if any(modifier not in ALLOWED_MODIFIERS for modifier in modifiers):
        raise ValueError("The model used an unknown product modifier.")
    claims = _strings(raw.get("claims"), "claims", limit=12)
    source = _normalized(description)
    if any(not _normalized(claim) or _normalized(claim) not in source for claim in claims):
        raise ValueError("The model returned a claim that is not a verbatim excerpt of the brief.")

    tier = raw.get("priceTier")
    gender = raw.get("gender")
    age_source = raw.get("ageSource")
    if tier not in ALLOWED_TIERS:
        raise ValueError("The model returned an unknown price tier.")
    if gender not in {"female", "male", "balanced", "unknown"}:
        raise ValueError("The model returned an unknown gender target.")
    if age_source not in {"stated", "assumed", "unknown"}:
        raise ValueError("The model returned an unknown age source.")

    price = raw.get("priceUsd")
    if price is not None:
        price = _number(price, "priceUsd", minimum=0, maximum=1_000_000)
        stated_prices = {
            float(match.replace(",", ""))
            for match in re.findall(r"\$\s*(\d{1,3}(?:,\d{3})+|\d+)", description)
        }
        if price not in stated_prices:
            raise ValueError("The model returned a price that was not stated in the brief.")
    assumed_order = price if price is not None else ORDER_VALUE[tier]
    ambiguities = _strings(raw.get("ambiguities"), "ambiguities", limit=10)
    age_raw = raw.get("age")
    age = None
    valid_age = (
        isinstance(age_raw, list)
        and len(age_raw) == 2
        and all(type(item) is int for item in age_raw)
        and 13 <= age_raw[0] <= age_raw[1] <= 100
    )
    if valid_age and age_source != "unknown":
        age = (age_raw[0], age_raw[1])
    else:
        if age_raw is not None:
            ambiguities.append("Shopper age was unclear; no age targeting will be applied.")
        age_source = "unknown"

    insufficient = _boolean(raw.get("insufficient"), "insufficient")
    off_catalog = _boolean(raw.get("offCatalog"), "offCatalog")
    confidence = _number(raw.get("confidence"), "confidence", minimum=0, maximum=1)
    assumptions = _strings(raw.get("assumptions"), "assumptions", limit=10)
    clarifying_questions = _strings(raw.get("clarifyingQuestions"), "clarifyingQuestions", limit=5)
    if insufficient and off_catalog:
        raise ValueError("A brief cannot be both insufficient and off-catalog.")
    if confidence < 0.4 and not off_catalog:
        insufficient = True
        if not clarifying_questions:
            clarifying_questions = ["What is the product, who is it for, and what should a shopper know about it?"]
        if "The model found too little signal to draft confidently." not in ambiguities:
            ambiguities.append("The model found too little signal to draft confidently.")
    if not product and not (insufficient or off_catalog):
        raise ValueError("The model did not identify a product or request clarification.")
    if not cores and not (insufficient or off_catalog):
        raise ValueError("The model returned no product category for a placeable brief.")
    if cores and not claims:
        raise ValueError("A placeable brief must include at least one source-grounded product claim.")

    return Brief(
        description=description,
        product=product,
        cores=cores,
        modifiers=modifiers,
        claims=claims,
        priceTier=tier,
        priceUsd=price,
        assumedOrderValueUsd=assumed_order,
        gender=gender,
        age=age,
        ageSource=age_source,
        sustainable=_boolean(raw.get("sustainable"), "sustainable"),
        subscription=_boolean(raw.get("subscription"), "subscription"),
        gifting=_boolean(raw.get("gifting"), "gifting"),
        alcoholAlternative=_boolean(raw.get("alcoholAlternative"), "alcoholAlternative"),
        clinical=_boolean(raw.get("clinical"), "clinical"),
        playful=_boolean(raw.get("playful"), "playful"),
        grownUp=_boolean(raw.get("grownUp"), "grownUp"),
        valuePositioning=_boolean(raw.get("valuePositioning"), "valuePositioning"),
        fitness=_boolean(raw.get("fitness"), "fitness"),
        wellness=_boolean(raw.get("wellness"), "wellness"),
        offCatalog=off_catalog,
        insufficient=insufficient,
        confidence=confidence,
        ambiguities=ambiguities,
        assumptions=assumptions,
        clarifyingQuestions=clarifying_questions,
    )


def _publisher_evidence_values(publisher) -> list[str]:
    audience = publisher.audience
    return [
        publisher.name,
        publisher.category,
        *publisher.subcategories,
        publisher.notes,
        audience.age_skew,
        audience.income_tier,
        *audience.top_geos,
        str(publisher.avg_order_value_usd),
        str(publisher.monthly_impressions),
        str(audience.gender_split.female),
        str(audience.gender_split.male),
        str(audience.gender_split.other),
    ]


def _fit_evidence(brief: Brief, publisher) -> str:
    for core in brief.cores:
        if core in publisher.subcategories:
            return core
    return publisher.category


def score_publishers_with_model(
    brief: Brief,
    *,
    usage: list[dict] | None = None,
) -> list[ScoredPublisher]:
    catalog = [asdict(publisher) for publisher in load_publishers()]
    raw = _complete_json(
        "rank_publishers.md",
        {"publishers": catalog, "brief": asdict(brief)},
        usage=usage,
    )
    assessments = raw.get("assessments")
    if not isinstance(assessments, list) or len(assessments) != len(catalog):
        raise ValueError("The model must assess every catalog publisher exactly once.")

    by_id = {publisher.id: publisher for publisher in load_publishers()}
    rules_by_id = {
        row.publisher.id: row for row in score_publishers_with_rules(brief)
    }

    def rules_fallback(publisher_id: str, issue: str, model_score=None, model_fit=None, model_reason=None):
        row = rules_by_id[publisher_id]
        return replace(
            row,
            modelScore=model_score if isinstance(model_score, int) and not isinstance(model_score, bool) else None,
            modelFit=model_fit if isinstance(model_fit, str) and model_fit in {"shelf", "audience", "none"} else None,
            modelReason=model_reason if isinstance(model_reason, str) else None,
            ruleScore=row.score,
            ruleDecision=row.decision,
            ruleReason=row.reason,
            scoringSource="rules",
            explanationSource="rules",
            validationIssues=[issue],
        )
    seen: set[str] = set()
    scored: list[ScoredPublisher] = []
    for item in assessments:
        if not isinstance(item, dict):
            raise ValueError("A publisher assessment is not an object.")
        publisher_id = item.get("id")
        if publisher_id not in by_id or publisher_id in seen:
            raise ValueError("The model returned a missing, unknown, or duplicate publisher ID.")
        seen.add(publisher_id)
        publisher = by_id[publisher_id]
        rules_row = rules_by_id[publisher_id]
        score = item.get("score")
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 99:
            scored.append(rules_fallback(publisher_id, "invalid_model_score", score, item.get("fit"), item.get("reason")))
            continue
        model_fit = item.get("fit")
        if model_fit not in {"shelf", "audience", "none"}:
            scored.append(rules_fallback(publisher_id, "invalid_model_fit", score, None, item.get("reason")))
            continue
        fit = rules_row.fit
        validation_issues: list[str] = []
        if fit == "none":
            reason = rules_row.reason + f" Catalog category: {publisher.category}."
            if score >= 50:
                reason += f" LLM score {score} was suppressed by the catalog-fit guard."
            factors = [Factor(
                name="Catalog eligibility",
                impact=0,
                detail=f"No catalog taxonomy match for {publisher.category}.",
                evidence=publisher.category,
            )]
            explanation_source = "rules"
        elif model_fit != fit:
            reason = f"{rules_row.reason} Model fit label {model_fit} was corrected to catalog fit {fit}."
            factors = [Factor(
                name="Catalog fit",
                impact=0,
                detail=f"Catalog taxonomy classifies this inventory as a {fit} fit.",
                evidence=_fit_evidence(brief, publisher),
            )]
            explanation_source = "rules"
        else:
            try:
                reason = _required_text(item.get("reason"), "publisher reason", max_length=320)
                raw_factors = item.get("factors")
                if not isinstance(raw_factors, list) or not 1 <= len(raw_factors) <= 6:
                    raise ValueError("Each publisher must have between one and six factors.")
                factors = []
                valid_evidence = [value.casefold() for value in _publisher_evidence_values(publisher)]
                for factor in raw_factors:
                    if not isinstance(factor, dict):
                        raise ValueError("A publisher factor is not an object.")
                    impact = factor.get("impact")
                    if isinstance(impact, bool) or not isinstance(impact, int) or not -50 <= impact <= 50:
                        raise ValueError("Publisher factor impacts must be integers from -50 to 50.")
                    evidence = _required_text(factor.get("evidence"), "factor evidence", max_length=100)
                    if not any(evidence.casefold() in value for value in valid_evidence):
                        raise ValueError(f"Publisher {publisher_id} factor cited evidence outside the catalog.")
                    factors.append(
                        Factor(
                            name=_required_text(factor.get("name"), "factor name", max_length=50),
                            impact=impact,
                            detail=_required_text(factor.get("detail"), "factor detail", max_length=240),
                            evidence=evidence,
                        )
                    )
                explanation_source = "model"
            except ValueError as error:
                scored.append(rules_fallback(publisher_id, str(error), score, model_fit, item.get("reason")))
                continue
        scored.append(
            ScoredPublisher(
                publisher=publisher,
                score=score,
                decision="recommend" if score >= 50 and fit != "none" else "exclude",
                fit=fit,
                factors=factors,
                reason=reason,
                modelScore=score,
                modelFit=model_fit,
                modelReason=item.get("reason") if isinstance(item.get("reason"), str) else None,
                ruleScore=rules_row.score,
                ruleDecision=rules_row.decision,
                ruleReason=rules_row.reason,
                scoringSource="model",
                explanationSource=explanation_source,
                validationIssues=validation_issues,
            )
        )
    if seen != set(by_id):
        raise ValueError("The model omitted one or more catalog publishers.")

    scored.sort(key=lambda row: (-row.score, row.publisher.name))
    passing = [row for row in scored if row.decision == "recommend"]
    for row in passing[5:]:
        row.decision = "exclude"
        row.heldOut = True
        row.reason = f"Cleared the bar at {row.score}, and was held out to keep the test to five publishers."
    return scored


def select_personas_and_write_with_model(
    brief: Brief,
    personas: list[ScoredPersona],
    *,
    usage: list[dict] | None = None,
) -> tuple[list[ScoredPersona], list[Creative]]:
    claim_rows = [
        {"id": f"claim_{index + 1:03d}", "text": claim}
        for index, claim in enumerate(brief.claims)
    ]
    payload_personas = [
        {
            "id": row.persona.id,
            "name": row.persona.name,
            "age_range": row.persona.age_range,
            "gender_skew": row.persona.gender_skew,
            "description": row.persona.description,
            "category_affinities": row.persona.category_affinities,
            "price_sensitivity": row.persona.price_sensitivity,
            "messaging_preferences": row.persona.messaging_preferences,
            "disinterested_in": row.persona.disinterested_in,
            "typical_aov_usd": row.persona.typical_aov_usd,
        }
        for row in personas
    ]
    raw = _complete_json(
        "select_personas_and_creatives.md",
        {"personas": payload_personas, "brief": asdict(brief), "claims": claim_rows},
        usage=usage,
    )
    selected_raw = raw.get("selected")
    variants_raw = raw.get("variants")
    if not isinstance(selected_raw, list) or not 3 <= len(selected_raw) <= 5:
        raise ValueError("The model must select three to five personas for this campaign.")
    persona_by_id = {row.persona.id: replace(row, selected=False) for row in personas}
    selected_ids: list[str] = []
    selected_personas: list[ScoredPersona] = []
    for item in selected_raw:
        if not isinstance(item, dict):
            raise ValueError("A persona selection is not an object.")
        persona_id = item.get("personaId")
        if persona_id not in persona_by_id or persona_id in selected_ids:
            raise ValueError("The model returned a missing, unknown, or duplicate persona ID.")
        if persona_id == "persona_010" and not brief.gifting:
            raise ValueError("The Gifter persona is only eligible for gift-oriented briefs.")
        row = replace(
            persona_by_id[persona_id],
            selected=True,
            reason=_required_text(item.get("why"), "persona reason", max_length=280),
        )
        selected_ids.append(persona_id)
        selected_personas.append(row)

    if not isinstance(variants_raw, list) or len(variants_raw) != len(selected_ids):
        raise ValueError("The model must return one ad for every selected persona.")
    variant_by_id: dict[str, dict] = {}
    for variant in variants_raw:
        if not isinstance(variant, dict):
            raise ValueError("A creative variant is not an object.")
        persona_id = variant.get("personaId")
        if persona_id not in selected_ids or persona_id in variant_by_id:
            raise ValueError("A creative has an unknown or duplicate persona ID.")
        variant_by_id[persona_id] = variant

    creatives: list[Creative] = []
    used_headlines: set[str] = set()
    used_openings: set[str] = set()
    allowed_claim_ids = {claim["id"] for claim in claim_rows}
    for row in selected_personas:
        variant = variant_by_id.get(row.persona.id)
        if not variant:
            raise ValueError("The model omitted a selected persona's ad.")
        headline = _text(variant.get("headline"), "headline", max_length=70)
        body = _text(variant.get("body"), "body", max_length=280)
        if not 8 <= len(headline) <= 70 or not 20 <= len(body) <= 280:
            raise ValueError("A creative did not meet the required headline or body length.")
        if headline.casefold() in used_headlines:
            raise ValueError("The model returned duplicate ad headlines.")
        if "#" in headline or "#" in body or re.search(r"\bintroducing\b", f"{headline} {body}", re.I):
            raise ValueError("A creative used a forbidden ad-copy format.")
        used_headlines.add(headline.casefold())
        opening = " ".join(body.casefold().split()[:8])
        if opening in used_openings:
            raise ValueError("The model reused an ad opening across personas.")
        used_openings.add(opening)
        claim_ids = _strings(variant.get("claimIds"), "claimIds", limit=12)
        if not claim_ids or not set(claim_ids).issubset(allowed_claim_ids):
            raise ValueError("A creative must cite one or more claims from the brief.")
        creatives.append(
            Creative(
                personaId=row.persona.id,
                personaName=row.persona.name,
                headline=headline,
                body=body,
                why=row.reason,
                source="model",
                claimIds=claim_ids,
            )
        )
    selected_by_id = {row.persona.id: row for row in selected_personas}
    updated_personas = [
        selected_by_id.get(row.persona.id, replace(row, selected=False))
        for row in personas
    ]
    return updated_personas, creatives
