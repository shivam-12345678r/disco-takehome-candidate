from __future__ import annotations

import re
from collections.abc import Callable

from campaign.creative import write_creatives_from_rules
from campaign.extract import extract_heuristic
from campaign.llm import (
    extract_brief_with_model,
    has_llm_credentials,
    load_local_env,
    score_publishers_with_model,
    select_personas_and_write_with_model,
)
from campaign.pipeline import compose_draft, draft_campaign
from campaign.score_personas import score_personas
from campaign.score_publishers import score_publishers
from campaign.types import CampaignDraft


def _model_warning(error: BaseException) -> str:
    message = str(error)
    if re.search(r"timeout|aborted", message, re.I):
        return "The model took too long; local rules handled this stage."
    if isinstance(error, ValueError):
        detail = re.sub(r"\s+", " ", message).strip()
        return f"The model response failed validation ({detail[:180]}); local rules handled this stage."
    if "incomplete" in message.casefold():
        detail = re.sub(r"\s+", " ", message).strip()
        return f"The model response was incomplete ({detail[:120]}); local rules handled this stage."
    return f"The model could not complete this stage ({type(error).__name__}); local rules handled it."


def _ranking_disagreements(rows) -> list[dict]:
    disagreements = []
    for row in rows:
        model_score = row.modelScore
        rule_score = row.ruleScore
        model_fit = row.modelFit
        rule_fit = row.fit
        model_decision = "recommend" if model_score is not None and model_score >= 50 else "exclude"
        rule_decision = row.ruleDecision
        final_decision = row.decision
        if (
            model_score == rule_score
            and model_fit == rule_fit
            and model_decision == rule_decision
            and final_decision == model_decision
            and not row.validationIssues
        ):
            continue

        if row.scoringSource == "rules" and row.validationIssues:
            judgment = "This publisher failed its local output checks; use its rules score and explanation, then review the logged model value."
        elif rule_fit == "none" and model_score is not None and model_score >= 50:
            judgment = "Catalog-fit guard wins: exclude this publisher despite the LLM score."
        elif model_fit != rule_fit:
            judgment = "Use catalog-derived fit; keep the LLM score advisory for human review."
        elif final_decision != rule_decision:
            judgment = "No outcome data resolves this decision; review before treating it as a buy."
        else:
            judgment = "Scores differ; use this as an evaluation case, not performance evidence."

        disagreements.append(
            {
                "publisherId": row.publisher.id,
                "publisher": row.publisher.name,
                "modelFit": model_fit,
                "catalogFit": rule_fit,
                "modelScore": model_score,
                "rulesScore": rule_score,
                "modelDecision": model_decision,
                "rulesDecision": rule_decision,
                "finalDecision": final_decision,
                "modelReason": row.modelReason or row.reason,
                "rulesReason": row.ruleReason,
                "scoringSource": row.scoringSource,
                "explanationSource": row.explanationSource,
                "validationIssues": row.validationIssues,
                "judgment": judgment,
            }
        )
    return disagreements


def build_campaign(
    description: str,
    *,
    progress: Callable[[str], None] | None = None,
    use_model: bool | None = None,
) -> CampaignDraft:
    def report(message: str) -> None:
        if progress:
            progress(message)

    load_local_env()
    model_enabled = has_llm_credentials() if use_model is None else use_model and has_llm_credentials()
    if not model_enabled:
        report("OpenAI is disabled or not configured; drafting with local rules.")
        return draft_campaign(description)

    warnings: list[str] = []
    usage: list[dict] = []
    disagreement_log: list[dict] = []
    report("Parsing the advertiser brief with GPT-6 Luna…")
    try:
        brief = extract_brief_with_model(description[:2000], usage=usage)
        parser = "model"
    except Exception as error:
        brief = extract_heuristic(description[:2000])
        parser = "rules"
        warnings.append(f"Brief parsing: {_model_warning(error)}")
        report("Brief parsing fell back to local rules.")

    if brief.insufficient or brief.offCatalog:
        report("The brief needs clarification or is outside this consumer catalog.")
        draft = compose_draft(brief, parser, " ".join(warnings) or None)
        draft.modelUsage = usage
        return draft

    report("Ranking all 20 publishers with GPT-6 Luna…")
    try:
        publisher_rows = score_publishers_with_model(brief, usage=usage)
        sources = {row.scoringSource for row in publisher_rows}
        ranking = "hybrid" if sources == {"model", "rules"} else "rules" if sources == {"rules"} else "model"
        disagreement_log = _ranking_disagreements(publisher_rows)
    except Exception as error:
        publisher_rows = score_publishers(brief)
        ranking = "rules"
        warnings.append(f"Publisher ranking: {_model_warning(error)}")
        report("Publisher ranking fell back to local rules.")

    if not any(row.decision == "recommend" for row in publisher_rows):
        report("No publisher cleared the placement bar.")
        draft = compose_draft(
            brief,
            parser,
            " ".join(warnings) or None,
            publisher_rows=publisher_rows,
            ranking=ranking,
        )
        draft.modelUsage = usage
        draft.disagreementLog = disagreement_log
        return draft

    persona_rows = score_personas(brief)
    report("Selecting shopper personas and writing ads with GPT-6 Luna…")
    try:
        persona_rows, creative_rows = select_personas_and_write_with_model(
            brief,
            persona_rows,
            usage=usage,
        )
        persona_selection = "model"
    except Exception as error:
        creative_rows = write_creatives_from_rules(brief, persona_rows)
        persona_selection = "rules"
        warnings.append(f"Persona and creative stage: {_model_warning(error)}")
        report("Persona selection and ads fell back to local rules.")

    report("Building budget, bid strategy, and targeting config…")
    draft = compose_draft(
        brief,
        parser,
        " ".join(warnings) or None,
        publisher_rows=publisher_rows,
        persona_rows=persona_rows,
        creative_rows=creative_rows,
        ranking=ranking,
        persona_selection=persona_selection,
    )
    draft.modelUsage = usage
    draft.disagreementLog = disagreement_log
    return draft
