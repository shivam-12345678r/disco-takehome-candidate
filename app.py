"""Campaign draft, in the same layout as the original screen."""

from __future__ import annotations

import html
import json
import math
from dataclasses import asdict

import streamlit as st

from campaign.build import build_campaign
from campaign.catalog import load_example_lines
from campaign.config import suggest_learning_cpm
from campaign.llm import has_llm_credentials, load_local_env
from campaign.types import BudgetLine, CampaignDraft

STATUS_TITLE = {
    "ready": "Draft",
    "no_placement": "No placement",
    "needs_clarification": "Need a clearer brief",
    "off_catalog": "Outside this catalog",
}

CSS = """
<style>
  :root {
    --bg: #efeae2;
    --ink: #1c1916;
    --muted: #5f584e;
    --line: #ddd4c6;
    --card: #fbfaf7;
    --green: #0e6b4f;
    --green-soft: #e6f3ed;
    --warn-soft: #f8efe3;
    --bad: #7a2e24;
    --bad-soft: #f8ebe7;
  }
  html, body, [data-testid="stAppViewContainer"], .stApp {
    background: var(--bg);
    color: var(--ink);
  }
  header[data-testid="stHeader"], .stAppHeader { display: none !important; }
  #MainMenu, footer, [data-testid="stToolbar"] { display: none !important; }
  [data-testid="stNumberInput"] { width: 160px; margin-left: auto; }
  table.plain td:first-child, table.plain th:first-child { white-space: nowrap; }
  .block-container {
    max-width: 1080px;
    padding-top: 1.6rem;
    padding-bottom: 4rem;
  }
  .eyebrow {
    margin: 0 0 4px;
    color: var(--muted);
    font-size: 12px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  h1.draft-title {
    margin: 0;
    font-family: "Iowan Old Style", Palatino, Georgia, serif;
    font-size: 40px;
    font-weight: 560;
    letter-spacing: -0.03em;
    line-height: 1;
    color: var(--ink);
  }
  .lede { margin: 8px 0 0; max-width: 46rem; color: var(--muted); }
  .st-key-composer {
    background: #fbfaf7;
    border: 1px solid #ddd4c6;
    border-radius: 14px;
    padding: 12px 16px 8px;
    margin: 18px 0 10px;
  }
  .card, .creative, .config, .summary {
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 14px;
  }
  .field-label { font-size: 13px; font-weight: 600; margin: 0 0 8px; }
  [data-testid="stTextArea"] textarea {
    border: 1px solid var(--line);
    border-radius: 10px;
    background: white;
    color: var(--ink);
    min-height: 96px;
  }
  [data-testid="stTextArea"] textarea:focus {
    border-color: var(--green);
    box-shadow: 0 0 0 1px var(--green);
  }
  .quiet { color: var(--muted); font-size: 13px; margin: 0; }
  div.stButton > button[kind="primary"], div.stButton > button[data-testid="stBaseButton-primary"] {
    background: var(--ink);
    color: white;
    border: 0;
    border-radius: 999px;
    font-weight: 600;
  }
  [data-testid="stPills"] button, [data-baseweb="button-group"] button {
    border-radius: 999px !important;
    border: 1px solid var(--line) !important;
    background: transparent !important;
    color: var(--muted) !important;
    font-size: 13px !important;
  }
  .error { color: var(--bad); margin: 8px 0 0; }
  .stack { display: grid; gap: 18px; margin-top: 8px; }
  .summary { padding: 16px 18px; }
  .summary.ready { background: var(--green-soft); border-color: #b7dccb; }
  .summary.needs_clarification, .summary.no_placement { background: var(--warn-soft); border-color: #ead7bf; }
  .summary.off_catalog { background: var(--bad-soft); border-color: #efd0c8; }
  .summary h2 {
    margin: 0 0 6px;
    font-family: "Iowan Old Style", Palatino, Georgia, serif;
    font-size: 26px;
    font-weight: 560;
    letter-spacing: -0.02em;
  }
  .summary p, .card p, .creative p, .config p { margin: 0; }
  .meta { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
  .chip, .badge {
    border-radius: 999px;
    padding: 3px 8px;
    font-size: 12px;
    background: white;
    border: 1px solid var(--line);
  }
  .badge.model { background: var(--ink); color: white; border-color: var(--ink); }
  .section-title {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 12px;
    margin: 18px 0 10px;
  }
  .section-title h3 {
    margin: 0;
    font-size: 13px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .cards, .creative-grid { display: grid; gap: 10px; }
  .card, .creative { padding: 14px 16px; }
  .card-top, .creative-top {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    align-items: baseline;
  }
  .score { font-variant-numeric: tabular-nums; font-weight: 700; color: var(--green); }
  .card h4, .creative h4 { margin: 0; font-size: 18px; }
  .reason { margin: 8px 0 0; }
  .factors { display: grid; gap: 4px; margin: 10px 0 0; padding: 0; list-style: none; }
  .factors li {
    display: grid;
    grid-template-columns: 148px 36px 1fr;
    gap: 8px;
    font-size: 13px;
    color: var(--muted);
  }
  .factor-evidence { display: block; margin-top: 2px; color: #777064; font-size: 11px; }
  .impact { font-variant-numeric: tabular-nums; color: var(--ink); }
  .impact.neg { color: var(--bad); }
  table.plain {
    width: 100%;
    border-collapse: collapse;
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 14px;
    overflow: hidden;
  }
  table.plain th, table.plain td {
    text-align: left;
    padding: 8px 12px;
    border-bottom: 1px solid var(--line);
    vertical-align: top;
    font-size: 14px;
  }
  table.plain th { font-size: 12px; color: var(--muted); font-weight: 600; }
  table.plain tr:last-child td { border-bottom: 0; }
  .questions, .assumptions { margin: 8px 0 0; padding-left: 18px; }
  .assumptions { color: var(--muted); }
  .why { margin-top: 8px; color: var(--muted); font-size: 13px; }
  .config { padding: 16px; display: grid; gap: 14px; margin-top: 8px; }
  .stat-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
  .stat { padding: 10px 12px; border-radius: 10px; background: #f3efe8; }
  .stat b {
    display: block;
    font-family: "Iowan Old Style", Palatino, Georgia, serif;
    font-size: 22px;
    font-weight: 560;
  }
  .stat span { color: var(--muted); font-size: 12px; }
  [data-testid="stNumberInput"] input {
    border: 1px solid var(--line);
    border-radius: 8px;
    background: white;
  }
  @media (max-width: 720px) {
    h1.draft-title { font-size: 32px; }
    .stat-grid { grid-template-columns: 1fr; }
    .factors li { grid-template-columns: 1fr; gap: 0; }
  }
</style>
"""


def js_round(n: float) -> int:
    return int(math.floor(n + 0.5))


def money(n: float) -> str:
    return f"${js_round(n):,}"


def esc(value: object) -> str:
    return html.escape(str(value))


def short_example(text: str) -> str:
    cut = text.split(".")[0].split("!")[0]
    return f"{cut[:40]}…" if len(cut) > 42 else cut


def rescale(lines: list[BudgetLine], monthly: int, base: int) -> list[BudgetLine]:
    if monthly == base:
        return lines
    left = monthly
    scaled: list[BudgetLine] = []
    for index, line in enumerate(lines):
        last = index == len(lines) - 1
        amount = left if last else js_round((line.percent / 100) * monthly)
        left -= amount
        scaled.append(
            BudgetLine(
                publisherId=line.publisherId,
                name=line.name,
                percent=line.percent,
                monthlyUsd=amount,
                dailyUsd=js_round(amount / 30),
                rationale=line.rationale,
            )
        )
    return scaled


def audience_phrase(gender: str) -> str:
    if gender == "female":
        return "women"
    if gender == "male":
        return "men"
    if gender == "balanced":
        return "all genders"
    return "any gender"


def factor_list(factors) -> str:
    items = []
    for factor in factors:
        if factor.impact == 0:
            continue
        sign = f"+{factor.impact}" if factor.impact > 0 else str(factor.impact)
        klass = "impact neg" if factor.impact < 0 else "impact"
        evidence = (
            f'<small class="factor-evidence">Catalog evidence: {esc(factor.evidence)}</small>'
            if factor.evidence
            else ""
        )
        items.append(
            "<li>"
            f"<span>{esc(factor.name)}</span>"
            f'<span class="{klass}">{esc(sign)}</span>'
            f"<span>{esc(factor.detail)}{evidence}</span>"
            "</li>"
        )
    return f'<ul class="factors">{"".join(items)}</ul>'


def publisher_card(row) -> str:
    category = row.publisher.category.replace("_", " ")
    audience = row.publisher.audience
    fit_label = {"shelf": "Direct shelf", "audience": "Adjacent audience", "none": "No catalog fit"}[row.fit]
    score_source = "Model score" if row.scoringSource == "model" else "Rules fallback score"
    return (
        '<article class="card">'
        '<div class="card-top">'
        f"<h4>{esc(row.publisher.name)}</h4>"
        f'<span class="score">{row.score}</span>'
        "</div>"
        f'<p class="quiet">{esc(fit_label)} · {esc(score_source)} · {esc(category)} · {esc(audience.age_skew)} · {esc(audience.income_tier)} income · '
        f"${row.publisher.avg_order_value_usd} average order</p>"
        f'<p class="reason">{esc(row.reason)}</p>'
        f"{factor_list(row.factors)}"
        "</article>"
    )


def render_summary(draft: CampaignDraft) -> str:
    brief = draft.brief
    chips = []
    if brief.product:
        chips.append(f'<span class="chip">{esc(brief.product)}</span>')
    if brief.priceTier != "unknown":
        chips.append(f'<span class="chip">{esc(brief.priceTier)}</span>')
    chips.append(f'<span class="chip">Confidence {round(brief.confidence * 100)}%</span>')
    if draft.creatives:
        badge = "Model-written ads" if draft.generation == "model" else "Rule-written ads"
        chips.append(f'<span class="badge {esc(draft.generation)}">{badge}</span>')
    for label, source in (
        ("Brief", draft.parser),
        ("Publisher ranking", draft.ranking),
        ("Persona selection", draft.personaSelection),
    ):
        mode = {"model": "LLM", "rules": "Rules", "hybrid": "LLM + rules", "skipped": "Not run"}[source]
        chips.append(f'<span class="chip">{esc(label)} · {mode}</span>')
    if draft.modelUsage:
        input_tokens = sum(item["inputTokens"] for item in draft.modelUsage)
        output_tokens = sum(item["outputTokens"] for item in draft.modelUsage)
        chips.append(
            f'<span class="chip">API tokens · {input_tokens:,} in / {output_tokens:,} out</span>'
        )
    assumptions = ""
    if brief.assumptions:
        items = "".join(f"<li>{esc(item)}</li>" for item in brief.assumptions)
        assumptions = f'<ul class="assumptions">{items}</ul>'
    warning = f'<p class="reason">{esc(draft.warning)}</p>' if draft.warning else ""
    questions = ""
    if brief.clarifyingQuestions:
        items = "".join(f"<li>{esc(question)}</li>" for question in brief.clarifyingQuestions)
        questions = f'<ol class="questions">{items}</ol>'
    return (
        f'<section class="summary {esc(draft.status)}">'
        f"<h2>{esc(STATUS_TITLE[draft.status])}</h2>"
        f"<p>{esc(draft.summary)}</p>"
        f'<div class="meta">{"".join(chips)}</div>'
        f"{assumptions}{warning}{questions}"
        "</section>"
    )


def render_publishers(draft: CampaignDraft) -> str:
    recommended = [row for row in draft.publishers if row.decision == "recommend"]
    excluded = [row for row in draft.publishers if row.decision == "exclude"]
    parts = []
    if recommended:
        cards = "".join(publisher_card(row) for row in recommended)
        parts.append(
            "<section>"
            '<div class="section-title"><h3>Buy</h3>'
            f"<span>{'Model relevance score' if draft.ranking in {'model', 'hybrid'} else 'Score is the sum of factors'} · 50 is the bar</span></div>"
            f'<div class="cards">{cards}</div></section>'
        )
    if excluded:
        rows = "".join(
            "<tr>"
            f"<td>{esc(row.publisher.name)}</td>"
            f"<td>{row.score}</td>"
            f"<td>{esc(row.reason)}{factor_list(row.factors)}</td>"
            "</tr>"
            for row in excluded
        )
        parts.append(
            "<section>"
            '<div class="section-title"><h3>Excluded</h3>'
            f"<span>{len(excluded)} publishers left out</span></div>"
            '<table class="plain"><thead><tr><th>Publisher</th><th>Score</th><th>Why</th></tr></thead>'
            f"<tbody>{rows}</tbody></table></section>"
        )
    return "".join(parts)


def render_ads(draft: CampaignDraft) -> str:
    if not draft.creatives:
        return ""
    selected = [row for row in draft.personas if row.selected]
    skipped = [row for row in draft.personas if not row.selected and row.score > 0][:4]
    cards = []
    for creative in draft.creatives:
        source = "Model" if creative.source == "model" else "Rules"
        claims = {f"claim_{index + 1:03d}": claim for index, claim in enumerate(draft.brief.claims)}
        cited = [claims[claim_id] for claim_id in creative.claimIds if claim_id in claims]
        grounding = (
            f'<p class="why"><strong>Brief claims:</strong> {esc(" · ".join(cited))}</p>'
            if cited
            else ""
        )
        cards.append(
            '<article class="creative">'
            '<div class="creative-top">'
            f"<h4>{esc(creative.personaName)}</h4>"
            f'<span class="quiet">{source}</span></div>'
            f"<p><strong>{esc(creative.headline)}</strong></p>"
            f"<p>{esc(creative.body)}</p>"
            f'<p class="why">{esc(creative.why)}</p>'
            f"{grounding}"
            "</article>"
        )
    skipped_line = ""
    if skipped:
        names = ", ".join(f"{row.persona.name} ({row.score})" for row in skipped)
        skipped_line = f'<p class="why">Considered and skipped: {esc(names)}.</p>'
    return (
        "<section>"
        '<div class="section-title"><h3>Ads</h3>'
        f"<span>{len(selected)} personas selected · {'model' if draft.personaSelection == 'model' else 'rules'}</span></div>"
        f'<div class="creative-grid">{"".join(cards)}</div>'
        f"{skipped_line}</section>"
    )


def render_config(config: dict, monthly: int, flight_days: int, allocation: list[BudgetLine]) -> str:
    gender = audience_phrase(config["targeting"]["gender"])
    age = config["targeting"]["age"]
    age_bit = f" Age {esc(age)}." if age else ""
    geos = ", ".join(config["targeting"]["geos"])
    ctr_prior = config["measurementPriors"]["ctr"]
    daily_budget = js_round(monthly / 30)
    total_test_budget = js_round(monthly * flight_days / 30)
    rows = "".join(
        "<tr>"
        f"<td>{esc(line.name)}</td><td>{line.percent}%</td>"
        f"<td>{money(line.monthlyUsd)}</td><td>{money(line.dailyUsd)}</td>"
        f"<td>{money(js_round(line.monthlyUsd * flight_days / 30))}</td>"
        "</tr>"
        for line in allocation
    )
    return (
        '<section class="config">'
        '<div class="stat-grid">'
        '<div class="stat">'
        f"<b>{money(total_test_budget)}</b>"
        f"<span>total test budget · {flight_days} days</span></div>"
        '<div class="stat">'
        f"<b>{money(daily_budget)}</b>"
        "<span>daily pacing target</span></div>"
        '<div class="stat">'
        f"<b>${config['bidStrategy']['steady']['capUsd']} CPA</b>"
        "<span>cap after learning</span></div>"
        "</div>"
        '<p class="quiet">Planning settings only. CPM and CPA values are assumptions, not live-auction bids or performance forecasts.</p>'
        f"<p>{esc(config['bidStrategy']['rationale'])}</p>"
        f"<p class=\"quiet\">{esc(ctr_prior['label'])}. No click or impression data is connected; this prior does not set bids or budget.</p>"
        f"<p class=\"quiet\">{esc(config['budget']['allocationRule'])}</p>"
        '<table class="plain"><thead><tr><th>Publisher</th><th>Share</th><th>Monthly pace</th><th>Daily pace</th><th>Flight total</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
        f"<p>{esc(config['kpi'])} Frequency cap {config['targeting']['frequencyCapPerWeek']} a week. "
        f"Attribution window {config['targeting']['attributionWindowDays']} days.{age_bit} "
        f"Audience: {esc(gender)}. Geos {esc(geos)}.</p>"
        "</section>"
    )


def config_payload(
    draft: CampaignDraft,
    monthly: int,
    flight_days: int,
    target_cpa: int,
    learning_days: int,
    frequency_cap: int,
    attribution_window: int,
    allocation: list[BudgetLine],
) -> dict:
    config = asdict(draft.config)
    flight_days = max(1, flight_days)
    learning_days = max(1, min(learning_days, flight_days, 30))
    config["budget"]["suggestedMonthlyUsd"] = monthly
    config["budget"]["suggestedDailyUsd"] = js_round(monthly / 30)
    config["budget"]["flightDays"] = flight_days
    config["budget"]["totalTestBudgetUsd"] = js_round(monthly * flight_days / 30)
    config["budget"]["allocation"] = [asdict(line) for line in allocation]
    config["bidStrategy"]["steady"]["capUsd"] = target_cpa
    config["kpi"] = f"First order at or below ${target_cpa}."
    learning_cpm = suggest_learning_cpm(target_cpa, draft.publishers)
    config["bidStrategy"]["learning"]["bidUsd"] = learning_cpm
    config["bidStrategy"]["learning"]["days"] = learning_days
    default_cpa = int(draft.config.bidStrategy["steady"]["capUsd"])
    if target_cpa == default_cpa:
        original_rationale = draft.config.bidStrategy["rationale"]
        _, cap_context = original_rationale.split(". The cap is ", 1)
        cap_context = f"The cap is {cap_context}"
    else:
        cap_context = "Check the CPA cap against contribution margin before launch."
    config["bidStrategy"]["rationale"] = (
        f"{learning_days} days on a ${learning_cpm} CPM while the publishers have no conversion history "
        f"for this advertiser, then a ${target_cpa} CPA cap. {cap_context} "
        "The learning CPM is a cold-start heuristic near 45% of the CPA cap, subject to an audience-based "
        "floor and a $6–$28 clamp; it is not a performance prediction."
    )
    config["targeting"]["frequencyCapPerWeek"] = frequency_cap
    config["targeting"]["attributionWindowDays"] = attribution_window
    return config


def draft_now(text: str, use_model: bool) -> None:
    cleaned = text.strip()
    if not cleaned:
        st.session_state.error = "Describe the business first."
        st.session_state.draft = None
        return
    progress = None
    try:
        with st.status("Drafting campaign…", expanded=True) as progress:
            draft = build_campaign(
                cleaned[:2000],
                progress=lambda stage: progress.write(stage),
                use_model=use_model,
            )
            progress.update(label="Draft ready", state="complete", expanded=False)
        st.session_state.draft = draft
        st.session_state.error = None
    except Exception as error:
        if progress:
            progress.update(label="Draft failed", state="error", expanded=False)
        st.session_state.draft = None
        st.session_state.error = str(error) or "The draft failed."


def main() -> None:
    load_local_env()
    ai_available = has_llm_credentials()
    st.set_page_config(page_title="Campaign draft", page_icon="◇", layout="centered")
    st.markdown(CSS, unsafe_allow_html=True)

    examples = load_example_lines()
    labels = []
    by_label: dict[str, str] = {}
    for index, example in enumerate(examples):
        label = short_example(example)
        if label in by_label:
            label = f"{label} ({index + 1})"
        labels.append(label)
        by_label[label] = example

    if "brief" not in st.session_state:
        st.session_state.brief = examples[0] if examples else ""
    if "draft" not in st.session_state:
        st.session_state.draft = None
    if "error" not in st.session_state:
        st.session_state.error = None
    if "use_ai" not in st.session_state:
        st.session_state.use_ai = ai_available

    picked_now = st.session_state.get("sample")
    if picked_now and picked_now != st.session_state.get("applied_pill"):
        st.session_state.applied_pill = picked_now
        st.session_state.brief = by_label[picked_now]
        st.session_state.pending_draft = by_label[picked_now]

    st.markdown(
        """
        <p class="eyebrow">Disco take-home</p>
        <h1 class="draft-title">Campaign draft</h1>
        <p class="lede">Describe the business. The draft names which publishers to buy, which to skip, who the ads are for, and the config that would run the test.</p>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="composer"):
        st.markdown('<p class="field-label">Advertiser description</p>', unsafe_allow_html=True)
        st.text_area(
            "Advertiser description",
            key="brief",
            max_chars=2000,
            height=110,
            placeholder="We sell premium dog food for senior dogs…",
            label_visibility="collapsed",
        )
        note, action = st.columns([3, 1])
        with note:
            st.markdown(
                '<p class="quiet">GPT-6 Luna can parse the brief, rank all 20 publishers, select personas, and write ads. Your brief and catalog context are sent to OpenAI only when you enable AI for this draft.</p>',
                unsafe_allow_html=True,
            )
            st.checkbox("Use GPT-6 Luna", key="use_ai", disabled=not ai_available)
        with action:
            submit = st.button("Draft campaign", type="primary", use_container_width=True)
        st.pills("Sample briefs", labels, selection_mode="single", key="sample", label_visibility="collapsed")

    if submit:
        draft_now(st.session_state.brief, st.session_state.use_ai)
    elif st.session_state.get("pending_draft"):
        pending = st.session_state.pending_draft
        st.session_state.pending_draft = None
        draft_now(pending, st.session_state.use_ai)

    if st.session_state.error:
        st.markdown(f'<p class="error">{esc(st.session_state.error)}</p>', unsafe_allow_html=True)

    draft: CampaignDraft | None = st.session_state.draft
    if not draft:
        return

    st.markdown(render_summary(draft) + render_publishers(draft) + render_ads(draft), unsafe_allow_html=True)

    if draft.disagreementLog:
        with st.expander(f"Model vs rules review log ({len(draft.disagreementLog)} differences)"):
            st.dataframe(draft.disagreementLog, width="stretch", hide_index=True)
            st.download_button(
                "Download disagreement log",
                data=json.dumps(draft.disagreementLog, indent=2),
                file_name="model_rules_disagreements.json",
                mime="application/json",
                key="download_disagreement_log",
            )

    if not draft.config:
        return

    base = int(draft.config.budget["suggestedMonthlyUsd"])
    if st.session_state.get("config_for") != draft.brief.description:
        st.session_state.config_for = draft.brief.description
        st.session_state.monthly = base
        st.session_state.flight_days = 30
        st.session_state.target_cpa = int(draft.config.bidStrategy["steady"]["capUsd"])
        st.session_state.learning_days = int(draft.config.bidStrategy["learning"]["days"])
        st.session_state.frequency_cap = int(draft.config.targeting["frequencyCapPerWeek"])
        st.session_state.attribution_window = int(draft.config.targeting["attributionWindowDays"])
    st.markdown(
        '<div class="section-title"><h3>Campaign config</h3><span>Editable test settings</span></div>',
        unsafe_allow_html=True,
    )
    budget_col, flight_col = st.columns(2)
    with budget_col:
        monthly = int(
            st.number_input(
                "Monthly pacing budget ($)",
                min_value=500,
                max_value=1_000_000,
                step=500,
                key="monthly",
            )
        )
    with flight_col:
        flight_days = int(
            st.number_input(
                "Flight length (days)",
                min_value=1,
                max_value=365,
                step=1,
                key="flight_days",
            )
        )
    st.caption(
        f"Monthly pacing converts to about ${js_round(monthly / 30):,} a day. "
        f"At {flight_days} days, the total test budget is about ${js_round(monthly * flight_days / 30):,}."
    )
    if st.session_state.learning_days > flight_days:
        st.session_state.learning_days = min(7, flight_days)
    with st.expander("Bid and measurement settings"):
        cpa_col, learning_col = st.columns(2)
        with cpa_col:
            target_cpa = int(
                st.number_input(
                    "Target CPA cap ($)",
                    min_value=5,
                    max_value=2_000,
                    step=5,
                    key="target_cpa",
                )
            )
        with learning_col:
            learning_days = int(
                st.number_input(
                    "CPM learning phase (days)",
                    min_value=1,
                    max_value=min(30, flight_days),
                    step=1,
                    key="learning_days",
                )
            )
        frequency_col, attribution_col = st.columns(2)
        with frequency_col:
            frequency_cap = int(
                st.number_input(
                    "Frequency cap per week",
                    min_value=1,
                    max_value=20,
                    step=1,
                    key="frequency_cap",
                )
            )
        with attribution_col:
            attribution_window = int(
                st.number_input(
                    "Attribution window (days)",
                    min_value=1,
                    max_value=30,
                    step=1,
                    key="attribution_window",
                )
            )
        st.caption(
            "The default CPA cap uses stated or assumed order value and a category allowance. "
            "Check it against contribution margin. The learning CPM uses roughly 45% of the CPA cap, "
            "with an audience-based floor and a $6–$28 clamp. These are draft assumptions, not learned auction settings."
        )
    allocation = rescale(draft.config.budget["allocation"], monthly, base)
    config = config_payload(
        draft,
        monthly,
        flight_days,
        target_cpa,
        learning_days,
        frequency_cap,
        attribution_window,
        allocation,
    )
    st.markdown(render_config(config, monthly, flight_days, allocation), unsafe_allow_html=True)
    with st.expander("Config JSON"):
        st.code(json.dumps(config, indent=2), language="json")


main()
