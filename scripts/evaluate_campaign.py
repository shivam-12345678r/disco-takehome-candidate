"""Compare local rules and GPT-6 Luna on supplied and labeled briefs."""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from campaign.build import build_campaign
from campaign.catalog import load_example_lines, load_publishers
from campaign.llm import has_llm_credentials, load_local_env
from campaign.pipeline import draft_campaign
from check_examples import EXPECTATIONS


MODEL_INPUT_USD_PER_MILLION = 0.10
MODEL_OUTPUT_USD_PER_MILLION = 0.50
OFFICIAL_FIELDS = [
    ("dog food", "pet_food", "premium", None),
    ("activewear", "activewear", "premium", None),
    ("sparkling drink", "functional_beverage", "unknown", None),
    ("candles", "home_fragrance", "unknown", None),
    ("", None, "unknown", None),
    ("outerwear", "outdoor", "luxury", 650),
    ("dental recall software", None, "unknown", None),
    ("", None, "unknown", None),
    ("cleaning products", "household_values", "unknown", None),
    ("handbags", "luxury_accessories", "luxury", 1200),
    ("protein bars", "snacks", "unknown", None),
    ("subscription box", "pet_supplies", "unknown", None),
    ("workout supplements", "supplements", "value", None),
    ("bedding", "bedding", "unknown", None),
    ("", None, "unknown", None),
]


def norm(value: str) -> str:
    return " ".join(re.findall(r"[\w]+", value.casefold()))


def load_cases() -> list[dict]:
    examples = load_example_lines()
    name_to_id = {publisher.name: publisher.id for publisher in load_publishers()}
    official = []
    for index, (description, expectation, fields) in enumerate(zip(examples, EXPECTATIONS, OFFICIAL_FIELDS)):
        product, core, price_tier, price_usd = fields
        official.append(
            {
                "id": f"takehome_{index + 1:02d}",
                "description": description,
                "expectedStatus": expectation["status"],
                "expectedProduct": product,
                "expectedCore": core,
                "expectedPriceTier": price_tier,
                "expectedPriceUsd": price_usd,
                "relevantPublisherIds": [name_to_id[name] for name in expectation.get("include", [])],
                "excludedPublisherIds": [name_to_id[name] for name in expectation.get("exclude", [])],
                "labelSource": "supplied-takehome-expectations",
            }
        )
    synthetic = json.loads((ROOT / "data" / "labeled_eval_briefs.json").read_text(encoding="utf8"))
    for case in synthetic["cases"]:
        case["labelSource"] = "synthetic-manual"
    return official + synthetic["cases"]


def publisher_precision(rows, relevant_ids: set[str]) -> float | None:
    recommended = [row.publisher.id for row in rows if row.decision == "recommend"][:5]
    if not relevant_ids:
        return None
    if not recommended:
        return 0.0
    return sum(publisher_id in relevant_ids for publisher_id in recommended) / len(recommended)


def field_hits(draft, case: dict) -> dict:
    predicted_core = set(draft.brief.cores)
    expected_product = norm(case.get("expectedProduct") or "")
    predicted_product = norm(draft.brief.product)
    return {
        "product": None if not expected_product else expected_product in predicted_product,
        "core": None if not case.get("expectedCore") else case["expectedCore"] in predicted_core,
        "priceTier": draft.brief.priceTier == case["expectedPriceTier"],
        "priceUsd": draft.brief.priceUsd == case["expectedPriceUsd"],
    }


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def metric_summary(rows: list[dict], prefix: str) -> dict:
    p5 = [row[f"{prefix}PrecisionAt5"] for row in rows if row[f"{prefix}PrecisionAt5"] is not None]
    fields = {}
    for field in ("product", "core", "priceTier", "priceUsd"):
        values = [row[f"{prefix}FieldHits"][field] for row in rows if row[f"{prefix}FieldHits"][field] is not None]
        fields[field] = {"correct": sum(values), "total": len(values), "accuracy": mean([float(value) for value in values])}
    status_hits = [row[f"{prefix}StatusCorrect"] for row in rows]
    refusal = [row[f"{prefix}StatusCorrect"] for row in rows if row["expectedStatus"] in {"no_placement", "off_catalog", "needs_clarification"}]
    return {
        "precisionAt5": {"cases": len(p5), "mean": mean(p5)},
        "fieldAccuracy": fields,
        "statusAccuracy": {"correct": sum(status_hits), "total": len(status_hits), "accuracy": mean([float(value) for value in status_hits])},
        "refusalAccuracy": {"correct": sum(refusal), "total": len(refusal), "accuracy": mean([float(value) for value in refusal])},
    }


def run() -> dict:
    load_local_env()
    api_available = has_llm_credentials()
    publishers = {publisher.id: publisher.name for publisher in load_publishers()}
    case_results = []
    stage_attempts = 0
    stage_fallbacks = 0
    validator_rejections = 0
    publisher_rows_total = 0
    publisher_rows_rules_fallback = 0
    validator_rejected_publisher_rows = 0
    model_latencies: list[float] = []
    input_tokens = output_tokens = 0
    total_cost = 0.0
    disagreement_rows = []

    for index, case in enumerate(load_cases(), start=1):
        baseline = draft_campaign(case["description"])
        started = time.perf_counter()
        model = build_campaign(case["description"], use_model=True)
        latency = time.perf_counter() - started
        model_latencies.append(latency)

        attempted = []
        for stage, mode in (
            ("parser", model.parser),
            ("ranking", model.ranking),
            ("personas", model.personaSelection),
        ):
            if mode != "skipped":
                attempted.append((stage, mode))
        if api_available:
            stage_attempts += len(attempted)
            stage_fallbacks += sum(mode == "rules" for _, mode in attempted)
            if model.ranking != "skipped":
                publisher_rows_total += len(model.publishers)
                if model.ranking == "rules":
                    publisher_rows_rules_fallback += len(model.publishers)
                else:
                    publisher_rows_rules_fallback += sum(row.scoringSource == "rules" for row in model.publishers)
                validator_rejected_publisher_rows += sum(bool(row.validationIssues) for row in model.publishers)
        warnings = model.warning or ""
        if re.search(r"failed validation", warnings, re.I):
            validator_rejections += len(re.findall(r"failed validation", warnings, re.I))

        usage = model.modelUsage
        case_input = sum(item["inputTokens"] for item in usage)
        case_output = sum(item["outputTokens"] for item in usage)
        input_tokens += case_input
        output_tokens += case_output
        case_cost = (
            case_input * MODEL_INPUT_USD_PER_MILLION
            + case_output * MODEL_OUTPUT_USD_PER_MILLION
        ) / 1_000_000
        total_cost += case_cost

        relevant = set(case["relevantPublisherIds"])
        model_hits = field_hits(model, case)
        rules_hits = field_hits(baseline, case)
        result = {
            "id": case["id"],
            "labelSource": case["labelSource"],
            "description": case["description"],
            "expectedStatus": case["expectedStatus"],
            "expectedCore": case.get("expectedCore"),
            "expectedPriceTier": case["expectedPriceTier"],
            "modelStatus": model.status,
            "rulesStatus": baseline.status,
            "modelStatusCorrect": model.status == case["expectedStatus"],
            "rulesStatusCorrect": baseline.status == case["expectedStatus"],
            "modelFieldHits": model_hits,
            "rulesFieldHits": rules_hits,
            "modelPrecisionAt5": publisher_precision(model.publishers, relevant),
            "rulesPrecisionAt5": publisher_precision(baseline.publishers, relevant),
            "modelRecommended": [row.publisher.id for row in model.publishers if row.decision == "recommend"],
            "rulesRecommended": [row.publisher.id for row in baseline.publishers if row.decision == "recommend"],
            "modelRecommendedNames": [row.publisher.name for row in model.publishers if row.decision == "recommend"],
            "rulesRecommendedNames": [row.publisher.name for row in baseline.publishers if row.decision == "recommend"],
            "modelStages": {"parser": model.parser, "ranking": model.ranking, "personas": model.personaSelection},
            "warning": model.warning,
            "usage": usage,
            "latencySeconds": round(latency, 3),
            "tokenCostUsd": round(case_cost, 8),
            "disagreements": model.disagreementLog,
        }
        case_results.append(result)
        for difference in model.disagreementLog:
            disagreement_rows.append({"caseId": case["id"], **difference})
        print(
            f"[{index:02d}/{len(load_cases())}] {case['id']} "
            f"status={model.status} stages={model.parser}/{model.ranking}/{model.personaSelection} "
            f"tokens={case_input}+{case_output} latency={latency:.1f}s",
            flush=True,
        )

    model_summary = metric_summary(case_results, "model")
    rules_summary = metric_summary(case_results, "rules")
    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "caseCount": len(case_results),
        "apiKeyConfigured": api_available,
        "labelCaveat": "The 30 additional cases are synthetic and manually labeled from the supplied mock catalog; their relevance judgments are not outcome-grounded campaign truth.",
        "evaluationStatus": "development_set_not_held_out",
        "evaluationCaveat": "Prompts, rules, and validators were iterated during development using these cases. Quality metrics are development-set diagnostics, not held-out accuracy or evidence of campaign lift.",
        "pricingAssumption": {
            "model": "gpt-6-luna",
            "inputUsdPerMillion": MODEL_INPUT_USD_PER_MILLION,
            "outputUsdPerMillion": MODEL_OUTPUT_USD_PER_MILLION,
        },
        "rules": rules_summary,
        "model": model_summary,
        "modelOperations": {
            "attemptedStages": stage_attempts,
            "fallbackStages": stage_fallbacks,
            "fallbackRate": stage_fallbacks / stage_attempts if stage_attempts else None,
            "validatorRejectedStages": validator_rejections,
            "validatorRejectRate": validator_rejections / stage_attempts if stage_attempts else None,
            "publisherRows": publisher_rows_total,
            "publisherRowsUsingRules": publisher_rows_rules_fallback,
            "publisherRowFallbackRate": publisher_rows_rules_fallback / publisher_rows_total if publisher_rows_total else None,
            "publisherRowsRejectedByLocalValidation": validator_rejected_publisher_rows,
            "publisherRowValidationRejectRate": validator_rejected_publisher_rows / publisher_rows_total if publisher_rows_total else None,
            "totalInputTokens": input_tokens,
            "totalOutputTokens": output_tokens,
            "totalTokenCostUsd": round(total_cost, 6),
            "meanTokenCostPerBriefUsd": round(total_cost / len(case_results), 6) if case_results else None,
            "meanLatencySeconds": round(mean(model_latencies) or 0, 3),
            "p50LatencySeconds": round(sorted(model_latencies)[len(model_latencies) // 2], 3) if model_latencies else None,
        },
        "cases": case_results,
    }
    reports_dir = ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "campaign_eval.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    (reports_dir / "model_rules_disagreements.json").write_text(json.dumps(disagreement_rows, indent=2) + "\n", encoding="utf8")
    print("\nRules metrics:", json.dumps(rules_summary, separators=(",", ":")))
    print("Model metrics:", json.dumps(model_summary, separators=(",", ":")))
    print("Model operations:", json.dumps(report["modelOperations"], separators=(",", ":")))
    print("Saved reports/campaign_eval.json and reports/model_rules_disagreements.json")
    return report


if __name__ == "__main__":
    run()
