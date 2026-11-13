from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PriceTier = Literal["value", "mid", "premium", "luxury", "unknown"]
Gender = Literal["female", "male", "balanced", "unknown"]
AgeSource = Literal["stated", "assumed", "unknown"]
Fit = Literal["shelf", "audience", "none"]
DraftStatus = Literal["ready", "no_placement", "needs_clarification", "off_catalog"]


@dataclass
class GenderSplit:
    female: float
    male: float
    other: float


@dataclass
class Audience:
    age_skew: str
    gender_split: GenderSplit
    top_geos: list[str]
    income_tier: str


@dataclass
class Publisher:
    id: str
    name: str
    category: str
    subcategories: list[str]
    monthly_impressions: int
    avg_order_value_usd: int
    audience: Audience
    notes: str


@dataclass
class Persona:
    id: str
    name: str
    age_range: str
    gender_skew: str
    description: str
    category_affinities: list[str]
    price_sensitivity: str
    messaging_preferences: list[str]
    disinterested_in: list[str]
    typical_aov_usd: int


@dataclass
class Brief:
    description: str
    product: str = ""
    cores: list[str] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)
    priceTier: PriceTier = "unknown"
    priceUsd: float | None = None
    assumedOrderValueUsd: float = 48
    gender: Gender = "unknown"
    age: tuple[int, int] | None = None
    ageSource: AgeSource = "unknown"
    sustainable: bool = False
    subscription: bool = False
    gifting: bool = False
    alcoholAlternative: bool = False
    clinical: bool = False
    playful: bool = False
    grownUp: bool = False
    valuePositioning: bool = False
    fitness: bool = False
    wellness: bool = False
    offCatalog: bool = False
    insufficient: bool = False
    confidence: float = 0
    ambiguities: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    clarifyingQuestions: list[str] = field(default_factory=list)


@dataclass
class Factor:
    name: str
    impact: int
    detail: str
    evidence: str | None = None


@dataclass
class ScoredPublisher:
    publisher: Publisher
    score: int
    decision: Literal["recommend", "exclude"]
    fit: Fit
    factors: list[Factor]
    reason: str
    heldOut: bool = False
    modelScore: int | None = None
    modelFit: Fit | None = None
    modelReason: str | None = None
    ruleScore: int | None = None
    ruleDecision: Literal["recommend", "exclude"] | None = None
    ruleReason: str | None = None
    scoringSource: Literal["model", "rules"] = "rules"
    explanationSource: Literal["model", "rules"] = "rules"
    validationIssues: list[str] = field(default_factory=list)


@dataclass
class ScoredPersona:
    persona: Persona
    score: int
    factors: list[Factor]
    reason: str
    selected: bool = False


@dataclass
class Creative:
    personaId: str
    personaName: str
    headline: str
    body: str
    why: str
    source: Literal["model", "rules"]
    claimIds: list[str] = field(default_factory=list)


@dataclass
class BudgetLine:
    publisherId: str
    name: str
    percent: float
    monthlyUsd: int
    dailyUsd: int
    rationale: str


@dataclass
class CampaignConfig:
    objective: Literal["conversions"]
    kpi: str
    bidStrategy: dict
    budget: dict
    targeting: dict
    publishers: list[dict]
    exclusions: list[dict]
    measurementPriors: dict
    assumptions: list[str]
    confidence: float


@dataclass
class CampaignDraft:
    status: DraftStatus
    summary: str
    brief: Brief
    publishers: list[ScoredPublisher]
    personas: list[ScoredPersona]
    creatives: list[Creative]
    config: CampaignConfig | None
    parser: Literal["model", "rules"]
    generation: Literal["model", "rules"]
    warning: str | None
    ranking: Literal["model", "rules", "hybrid", "skipped"] = "rules"
    personaSelection: Literal["model", "rules", "skipped"] = "rules"
    modelUsage: list[dict] = field(default_factory=list)
    disagreementLog: list[dict] = field(default_factory=list)
