"""Evaluate a logistic publisher ranker on synthetic brief level labels."""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from campaign.catalog import aov_band, income_rank, load_publishers, parse_age_skew, tier_rank
from campaign.extract import extract_heuristic
from campaign.score_publishers import _overlap_ratio, score_publisher
from campaign.types import Brief


CASE_PATH = ROOT / "data" / "synthetic_ranker_cases.json"
REPORT_PATH = ROOT / "reports" / "synthetic_ranker_eval.json"
PLOT_PATH = ROOT / "reports" / "synthetic_ranker_calibration.png"
FOLDS = 5
TOP_K = 5


def load_cases() -> list[dict]:
    return json.loads(CASE_PATH.read_text(encoding="utf8"))["cases"]


def make_brief(case: dict) -> Brief:
    parsed = extract_heuristic(case["description"])
    target_age = case.get("targetAge")
    return Brief(
        description=case["description"],
        product=parsed.product,
        cores=[case["core"]] if case.get("core") else [],
        modifiers=parsed.modifiers,
        claims=parsed.claims,
        priceTier=case.get("priceTier", "unknown"),
        priceUsd=case.get("priceUsd"),
        assumedOrderValueUsd=parsed.assumedOrderValueUsd,
        gender=case.get("targetGender", "unknown"),
        age=tuple(target_age) if target_age else None,
        ageSource="stated" if target_age else "unknown",
        sustainable=parsed.sustainable,
        subscription=parsed.subscription,
        gifting=parsed.gifting,
        alcoholAlternative=parsed.alcoholAlternative,
        clinical=parsed.clinical,
        playful=parsed.playful,
        grownUp=parsed.grownUp,
        valuePositioning=parsed.valuePositioning,
        fitness=parsed.fitness,
        wellness=parsed.wellness,
    )


def _impact(rows, name: str) -> float:
    return float(sum(factor.impact for factor in rows.factors if factor.name == name))


def make_features(brief: Brief, scored) -> dict[str, float]:
    publisher = scored.publisher
    features: dict[str, float] = {
        f"brief_core={core}": 1.0 for core in brief.cores
    }
    features.update(
        {
            f"price_tier={brief.priceTier}": 1.0,
            f"fit={scored.fit}": 1.0,
            "age_overlap": 0.0,
            "income_distance": 0.0,
            "basket_band_distance": 0.0,
            "price_to_aov_ratio_log": 0.0,
            "log10_monthly_impressions": math.log10(max(publisher.monthly_impressions, 1)),
            "log10_publisher_aov": math.log10(max(publisher.avg_order_value_usd, 1)),
            "has_stated_target_age": float(brief.age is not None),
            "has_stated_product_price": float(brief.priceUsd is not None),
        }
    )
    if brief.age:
        features["age_overlap"] = _overlap_ratio(brief.age, parse_age_skew(publisher.audience.age_skew))
    target_income = {
        "value": 2,
        "mid": 2,
        "premium": 3,
        "luxury": 4,
    }.get(brief.priceTier)
    if target_income is not None:
        features["income_distance"] = float(
            abs(income_rank(publisher.audience.income_tier) - target_income)
        )
    target_band = tier_rank(brief.priceTier)
    if target_band is not None:
        features["basket_band_distance"] = float(abs(aov_band(publisher.avg_order_value_usd) - target_band))
    if brief.priceUsd is not None:
        features["price_to_aov_ratio_log"] = math.log1p(
            max(0.0, brief.priceUsd / max(1, publisher.avg_order_value_usd))
        )

    factor_names = (
        "Age",
        "Gender",
        "Income",
        "Basket size",
        "Price gap",
        "Contradiction",
        "Skepticism",
        "Subscription",
        "Sustainability",
        "Health positioning",
        "Premium context",
        "Gifting",
        "Fitness context",
        "Tone",
    )
    for factor_name in factor_names:
        key = "factor_" + factor_name.casefold().replace(" ", "_")
        features[key] = _impact(scored, factor_name)
    features["total_penalty"] = float(
        sum(min(0, factor.impact) for factor in scored.factors)
    )
    return features


def make_model() -> Pipeline:
    return Pipeline(
        [
            ("features", DictVectorizer(sparse=False)),
            ("scale", StandardScaler()),
            ("logistic", LogisticRegression(max_iter=2000, random_state=17)),
        ]
    )


def precision_at_k(rows, relevant_ids: set[str], *, score_key: str) -> float:
    ordered = sorted(rows, key=lambda row: (-row[score_key], row["publisherId"]))
    top = ordered[: min(TOP_K, len(ordered))]
    if not top:
        return 0.0
    return sum(row["publisherId"] in relevant_ids for row in top) / len(top)


def main() -> None:
    cases = load_cases()
    publishers = load_publishers()
    case_by_id = {case["id"]: case for case in cases}
    rows: list[dict] = []
    skipped_cases = []

    for case in cases:
        brief = make_brief(case)
        relevant_ids = set(case["relevantPublisherIds"])
        scored_rows = [score_publisher(brief, publisher) for publisher in publishers]
        eligible_ids = {row.publisher.id for row in scored_rows if row.fit != "none"}
        unavailable = sorted(relevant_ids - eligible_ids)
        if unavailable:
            raise ValueError(f"{case['id']} labels publishers as good but catalog gates them: {unavailable}")
        eligible = [row for row in scored_rows if row.fit != "none"]
        if not eligible:
            skipped_cases.append(case["id"])
            continue
        for row in eligible:
            rows.append(
                {
                    "caseId": case["id"],
                    "publisherId": row.publisher.id,
                    "label": int(row.publisher.id in relevant_ids),
                    "ruleScore": float(row.score),
                    "features": make_features(brief, row),
                }
            )

    groups = np.asarray([row["caseId"] for row in rows])
    labels = np.asarray([row["label"] for row in rows], dtype=int)
    feature_dicts = [row["features"] for row in rows]
    if len(set(labels)) < 2:
        raise ValueError("Need both positive and negative labeled publisher rows")

    gkf = GroupKFold(n_splits=FOLDS)
    oof_probability = np.full(len(rows), np.nan)
    fold_results = []
    rule_case_p5 = []
    model_case_p5 = []
    for fold_number, (train_idx, test_idx) in enumerate(
        gkf.split(np.zeros(len(rows)), labels, groups), start=1
    ):
        if len(np.unique(labels[train_idx])) < 2:
            raise ValueError(f"Fold {fold_number} training data has only one label class")
        model = make_model()
        model.fit([feature_dicts[index] for index in train_idx], labels[train_idx])
        probabilities = model.predict_proba([feature_dicts[index] for index in test_idx])[:, 1]
        oof_probability[test_idx] = probabilities
        heldout_cases = sorted(set(groups[test_idx]))
        per_fold = {"fold": fold_number, "heldOutBriefs": len(heldout_cases), "cases": []}
        for case_id in heldout_cases:
            case = case_by_id[case_id]
            relevant = set(case["relevantPublisherIds"])
            if not relevant:
                continue
            case_indices = [index for index in test_idx if groups[index] == case_id]
            case_rows = [
                {
                    "publisherId": rows[index]["publisherId"],
                    "ruleScore": rows[index]["ruleScore"],
                    "modelScore": float(oof_probability[index]),
                }
                for index in case_indices
            ]
            if not case_rows:
                continue
            rule_p5 = precision_at_k(case_rows, relevant, score_key="ruleScore")
            model_p5 = precision_at_k(case_rows, relevant, score_key="modelScore")
            rule_case_p5.append(rule_p5)
            model_case_p5.append(model_p5)
            per_fold["cases"].append(
                {
                    "caseId": case_id,
                    "rulePrecisionAt5": round(rule_p5, 4),
                    "logisticPrecisionAt5": round(model_p5, 4),
                }
            )
        fold_results.append(per_fold)

    if np.isnan(oof_probability).any():
        raise ValueError("Some eligible publisher rows did not receive an out-of-fold prediction")
    brier = float(brier_score_loss(labels, oof_probability))
    oof_log_loss = float(log_loss(labels, oof_probability, labels=[0, 1]))
    calibration_bins = []
    sorted_indices = np.argsort(oof_probability, kind="stable")
    for index, bin_indices in enumerate(np.array_split(sorted_indices, 5)):
        if not len(bin_indices):
            continue
        predicted = float(np.mean(oof_probability[bin_indices]))
        observed = float(np.mean(labels[bin_indices]))
        calibration_bins.append(
            {
                "bin": index + 1,
                "count": len(bin_indices),
                "meanPredictedProbability": predicted,
                "observedPositiveRate": observed,
            }
        )

    fig, axis = plt.subplots(figsize=(6.4, 5.2))
    axis.plot([0, 1], [0, 1], linestyle="--", color="#777777", label="Ideal calibration")
    axis.plot(
        [item["meanPredictedProbability"] for item in calibration_bins],
        [item["observedPositiveRate"] for item in calibration_bins],
        marker="o",
        linewidth=2,
        color="#087f5b",
        label="5-fold held-out",
    )
    axis.set(xlim=(0, 1), ylim=(0, 1), xlabel="Mean predicted match probability", ylabel="Observed positive label rate")
    axis.set_title("Logistic ranker calibration\n40 synthetic briefs, held out by brief")
    axis.grid(alpha=0.2)
    axis.legend(loc="upper left")
    fig.tight_layout()
    PLOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(PLOT_PATH, dpi=160)
    plt.close(fig)

    all_data_model = make_model().fit(feature_dicts, labels)
    vectorizer = all_data_model.named_steps["features"]
    logistic = all_data_model.named_steps["logistic"]
    feature_names = vectorizer.get_feature_names_out()
    coefficients = logistic.coef_[0]
    top_coefficients = sorted(
        [
            {"feature": str(name), "standardizedLogOddsWeight": float(value)}
            for name, value in zip(feature_names, coefficients)
        ],
        key=lambda item: abs(item["standardizedLogOddsWeight"]),
        reverse=True,
    )[:15]

    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "experiment": "logistic regression publisher reranker versus existing hand-tuned score",
        "labelNotes": json.loads(CASE_PATH.read_text(encoding="utf8"))["labelNotes"],
        "briefCount": len(cases),
        "candidatePairCount": len(rows),
        "positivePairCount": int(labels.sum()),
        "positiveRate": float(labels.mean()),
        "briefsWithNoCatalogEligibleCandidates": skipped_cases,
        "features": [
            "catalog-derived fit class and brief category/price tier",
            "age overlap, income distance, basket-band distance, and price-to-AOV ratio",
            "publisher reach and AOV",
            "existing scorer factor impacts and total penalties",
        ],
        "evaluation": {
            "method": "5-fold GroupKFold; all candidates for a brief are held out together. These are held-out synthetic judgments, not campaign outcomes.",
            "folds": fold_results,
            "placementBriefsScored": len(model_case_p5),
            "precisionAt5": {
                "handTunedMean": float(np.mean(rule_case_p5)) if rule_case_p5 else None,
                "logisticRegressionMean": float(np.mean(model_case_p5)) if model_case_p5 else None,
                "handTunedPerBrief": rule_case_p5,
                "logisticPerBrief": model_case_p5,
            },
            "outOfFoldCalibration": {
                "brierScore": brier,
                "logLoss": oof_log_loss,
                "bins": calibration_bins,
                "plot": str(PLOT_PATH.relative_to(ROOT)),
            },
        },
        "fullDataCoefficientSample": {
            "warning": "Fit on all 40 synthetic briefs for feature inspection only; do not use as model performance evidence or production weights.",
            "largestAbsoluteStandardizedWeights": top_coefficients,
        },
        "productionUse": False,
        "caveat": "Synthetic labels encode human judgments from a 20-row mock catalog. The unlisted eligible publishers are treated as negatives. Grouped cross-validation avoids brief leakage but does not create real-world outcome evidence.",
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf8")
    print(f"Saved {REPORT_PATH.relative_to(ROOT)}")
    print(f"Saved {PLOT_PATH.relative_to(ROOT)}")
    print(
        "5-fold held-out synthetic P@5: "
        f"rules={np.mean(rule_case_p5):.3f}, logistic={np.mean(model_case_p5):.3f} "
        f"({len(model_case_p5)} placement briefs)"
    )
    print(f"Out-of-fold Brier={brier:.4f}; log loss={oof_log_loss:.4f}; pairs={len(rows)}")


if __name__ == "__main__":
    main()
