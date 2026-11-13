from __future__ import annotations

import math

from campaign.catalog import CORE_LABELS, money
from campaign.types import Brief, BudgetLine, CampaignConfig, ScoredPublisher

CTR_PRIOR_ALPHA = 1.0
CTR_PRIOR_BETA = 199.0


def _round_money(n: float) -> int:
    return int(math.floor(n + 0.5))


def beta_binomial_ctr(clicks: int = 0, impressions: int = 0) -> dict:
    """Return an illustrative assumed CTR prior updated with observed counts."""
    if isinstance(clicks, bool) or isinstance(impressions, bool):
        raise ValueError("clicks and impressions must be non-negative integers")
    if not isinstance(clicks, int) or not isinstance(impressions, int):
        raise ValueError("clicks and impressions must be non-negative integers")
    if clicks < 0 or impressions < 0 or clicks > impressions:
        raise ValueError("require 0 <= clicks <= impressions")
    alpha = CTR_PRIOR_ALPHA + clicks
    beta = CTR_PRIOR_BETA + impressions - clicks
    return {
        "label": "Prior CTR 0.5% (assumed, not learned)",
        "priorAlpha": CTR_PRIOR_ALPHA,
        "priorBeta": CTR_PRIOR_BETA,
        "observedClicks": clicks,
        "observedImpressions": impressions,
        "posteriorAlpha": alpha,
        "posteriorBeta": beta,
        "posteriorMean": alpha / (alpha + beta),
        "status": "assumed_not_learned",
        "usedForBidding": False,
        "updateRule": "Beta(alpha + clicks, beta + impressions - clicks)",
    }


def suggest_monthly_budget(brief: Brief, publisher_count: int) -> int:
    if brief.priceTier == "value":
        base = 6000
    elif brief.priceTier == "premium":
        base = 12000
    elif brief.priceTier == "luxury":
        base = 10000
    else:
        base = 8000
    scaled = base * (0.85 + publisher_count * 0.08)
    return _round_money(scaled / 500) * 500


def _format_impressions(n: int) -> str:
    if n >= 1_000_000:
        digits = 0 if n >= 10_000_000 else 1
        return f"{n / 1_000_000:.{digits}f}M"
    return f"{_round_money(n / 1000)}k"


def allocate_budget(publishers: list[ScoredPublisher], monthly_usd: float) -> list[BudgetLine]:
    chosen = [row for row in publishers if row.decision == "recommend"]
    if not chosen or monthly_usd <= 0:
        return []

    weights = [
        (row.score ** 2) * math.log10(max(row.publisher.monthly_impressions, 10)) for row in chosen
    ]
    total = sum(weights)
    percents = [weight / total for weight in weights]

    cap = 0.7
    if len(percents) > 1:
        for _pass in range(4):
            excess = 0.0
            free = 0.0
            next_percents = []
            for share in percents:
                if share > cap:
                    excess += share - cap
                    next_percents.append(cap)
                else:
                    free += share
                    next_percents.append(share)
            percents = next_percents
            if excess <= 0.0001 or free <= 0:
                break
            percents = [
                share if share >= cap - 0.0001 else share + excess * (share / free) for share in percents
            ]

    daily = monthly_usd / 30
    monthly_left = monthly_usd
    percent_left = 100.0
    lines: list[BudgetLine] = []
    for index, row in enumerate(chosen):
        last = index == len(chosen) - 1
        if last:
            percent = _round_money(percent_left * 10) / 10
            monthly = monthly_left
        else:
            percent = _round_money(percents[index] * 1000) / 10
            monthly = _round_money((percent / 100) * monthly_usd)
        monthly_left -= monthly
        percent_left -= percent
        lines.append(
            BudgetLine(
                publisherId=row.publisher.id,
                name=row.publisher.name,
                percent=percent,
                monthlyUsd=int(monthly),
                dailyUsd=_round_money((percent / 100) * daily),
                rationale=(
                    f"{row.score} fit, {_format_impressions(row.publisher.monthly_impressions)} monthly impressions."
                ),
            )
        )
    return lines


def _unique(values: list[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def _clamp(n: float, low: float, high: float) -> int:
    return int(max(low, min(high, n)))


def build_config(
    brief: Brief,
    publishers: list[ScoredPublisher],
    monthly_usd: float | None = None,
) -> CampaignConfig:
    recommended = [row for row in publishers if row.decision == "recommend"]
    monthly = monthly_usd if monthly_usd is not None else suggest_monthly_budget(brief, len(recommended))
    allocation = allocate_budget(publishers, monthly)
    order = brief.priceUsd if brief.priceUsd is not None else brief.assumedOrderValueUsd
    if brief.priceTier == "luxury":
        take_rate = 0.08
    elif brief.priceTier == "value":
        take_rate = 0.35
    elif brief.subscription:
        take_rate = 0.28
    else:
        take_rate = 0.22
    cap = _clamp(_round_money(order * take_rate), 12, 140)
    income_ranks = [row.publisher.audience.income_tier for row in recommended]
    high_share = sum(1 for tier in income_ranks if tier == "high")
    if high_share * 2 >= len(income_ranks):
        floor = 18
    elif any(tier in ("mid-high", "high") for tier in income_ranks):
        floor = 12
    else:
        floor = 8
    learning_cpm = _clamp(max(floor, _round_money(cap * 0.45)), 6, 28)

    geos = _unique([geo for row in recommended for geo in row.publisher.audience.top_geos])
    interests = _unique(
        [CORE_LABELS.get(core, core) for core in brief.cores]
        + [mod.replace("_", " ") for mod in brief.modifiers]
    )

    price_basis = "stated price" if brief.priceUsd is not None else "assumed order value"
    return CampaignConfig(
        objective="conversions",
        kpi=f"First order at or below {money(cap)}.",
        bidStrategy={
            "learning": {"model": "CPM", "bidUsd": learning_cpm, "days": 7},
            "steady": {"model": "CPA", "capUsd": cap},
            "rationale": (
                f"Seven days on a ${learning_cpm} CPM while the publishers have no conversion history "
                f"for this advertiser, then a ${cap} CPA cap. The cap is {round(take_rate * 100)}% of the "
                f"{price_basis} ({money(order)})."
            ),
        },
        budget={
            "suggestedMonthlyUsd": monthly,
            "suggestedDailyUsd": _round_money(monthly / 30),
            "flightDays": 30,
            "totalTestBudgetUsd": monthly,
            "allocation": allocation,
            "allocationRule": (
                "Share is score squared times log of monthly impressions, capped so one publisher "
                "cannot take more than 70% of a test."
            ),
        },
        targeting={
            "age": (
                None
                if not brief.age
                else f"{brief.age[0]}–{brief.age[1]}{' (assumed)' if brief.ageSource == 'assumed' else ''}"
            ),
            "gender": brief.gender,
            "geos": ["nationwide"] if "nationwide" in geos else geos,
            "interests": interests,
            "frequencyCapPerWeek": 5,
            "attributionWindowDays": 7,
        },
        publishers=[{"id": row.publisher.id, "name": row.publisher.name, "role": "buy"} for row in recommended],
        exclusions=[
            {"id": row.publisher.id, "name": row.publisher.name, "reason": row.reason}
            for row in publishers
            if row.decision == "exclude"
        ],
        measurementPriors={"ctr": beta_binomial_ctr()},
        assumptions=list(brief.assumptions),
        confidence=brief.confidence,
    )
