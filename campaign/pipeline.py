from __future__ import annotations

from campaign.config import build_config
from campaign.creative import write_creatives_from_rules
from campaign.extract import extract_heuristic
from campaign.score_personas import score_personas
from campaign.score_publishers import score_publishers
from campaign.types import Brief, CampaignDraft, Creative, ScoredPersona, ScoredPublisher


def compose_draft(
    brief: Brief,
    parser: str,
    warning: str | None,
    *,
    publisher_rows: list[ScoredPublisher] | None = None,
    persona_rows: list[ScoredPersona] | None = None,
    creative_rows: list[Creative] | None = None,
    ranking: str = "rules",
    persona_selection: str = "rules",
) -> CampaignDraft:
    if brief.insufficient:
        return CampaignDraft(
            status="needs_clarification",
            summary="This description does not name a product, so there is no campaign to draft.",
            brief=brief,
            publishers=[],
            personas=[],
            creatives=[],
            config=None,
            parser=parser,  # type: ignore[arg-type]
            generation="rules",
            warning=warning,
            ranking="skipped",
            personaSelection="skipped",
        )

    if brief.offCatalog:
        return CampaignDraft(
            status="off_catalog",
            summary=(
                f"{brief.product} does not belong on this publisher list. Every title here sells to "
                "consumers, and none of them can place a software workflow."
            ),
            brief=brief,
            publishers=[],
            personas=[],
            creatives=[],
            config=None,
            parser=parser,  # type: ignore[arg-type]
            generation="rules",
            warning=warning,
            ranking="skipped",
            personaSelection="skipped",
        )

    publishers = publisher_rows if publisher_rows is not None else score_publishers(brief)
    recommended = [row for row in publishers if row.decision == "recommend"]

    if not recommended:
        nearest = [row for row in publishers if row.fit != "none"][:3]
        nearest_text = (
            "Nothing in the catalog is even adjacent."
            if not nearest
            else "Closest, and still not recommended: " + ", ".join(row.publisher.name for row in nearest) + "."
        )
        return CampaignDraft(
            status="no_placement",
            summary=f"No publisher clears the bar for {brief.product}. {nearest_text}",
            brief=brief,
            publishers=publishers,
            personas=[],
            creatives=[],
            config=None,
            parser=parser,  # type: ignore[arg-type]
            generation="rules",
            warning=warning,
            ranking=ranking,  # type: ignore[arg-type]
            personaSelection="skipped",
        )

    personas = persona_rows if persona_rows is not None else score_personas(brief)
    creatives = creative_rows if creative_rows is not None else write_creatives_from_rules(brief, personas)
    names = [row.publisher.name for row in recommended]
    noun = "publisher" if len(names) == 1 else "publishers"
    return CampaignDraft(
        status="ready",
        summary=f"{len(names)} {noun} clear the bar for {brief.product}: {', '.join(names)}.",
        brief=brief,
        publishers=publishers,
        personas=personas,
        creatives=creatives,
        config=build_config(brief, publishers),
        parser=parser,  # type: ignore[arg-type]
        generation="model" if any(item.source == "model" for item in creatives) else "rules",
        warning=warning,
        ranking=ranking,  # type: ignore[arg-type]
        personaSelection=persona_selection,  # type: ignore[arg-type]
    )


def draft_campaign(description: str) -> CampaignDraft:
    return compose_draft(extract_heuristic(description), "rules", None)
