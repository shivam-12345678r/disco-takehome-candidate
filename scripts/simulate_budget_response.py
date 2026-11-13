"""Fit and optimize a deliberately synthetic diminishing-returns response curve."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import curve_fit


ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "simulated_budget_response.json"
PLOT_PATH = ROOT / "reports" / "simulated_budget_response.png"
RANDOM_SEED = 260613
ORDER_VALUE_USD = 80.0
TOTAL_BUDGET_USD = 12000
PUBLISHER_CAP_USD = 6000
STEP_USD = 100

# These arbitrary parameters generate teaching data only. They are not estimates.
SIMULATED_LANES = {
    "Pawline": {"a": 410.0, "b": 2500.0},
    "Ruffco": {"a": 350.0, "b": 2100.0},
    "Pantrygood": {"a": 480.0, "b": 4200.0},
    "Pop & Sip": {"a": 300.0, "b": 2600.0},
}
OBSERVED_SPEND_USD = np.array([0, 500, 1000, 2000, 3500, 5000, 6500, 8000], dtype=float)


def response(spend: np.ndarray | float, a: float, b: float):
    """Synthetic concave response: conversions = a * log(1 + spend / b)."""
    return a * np.log1p(np.asarray(spend) / b)


def fit_curves() -> tuple[dict, dict]:
    rng = np.random.default_rng(RANDOM_SEED)
    fit_params = {}
    observations = {}
    for name, truth in SIMULATED_LANES.items():
        expected = response(OBSERVED_SPEND_USD, truth["a"], truth["b"])
        observed = rng.poisson(expected).astype(float)
        params, _ = curve_fit(
            response,
            OBSERVED_SPEND_USD,
            observed,
            p0=(max(50.0, truth["a"] * 0.9), truth["b"] * 1.1),
            bounds=([1.0, 100.0], [2000.0, 20000.0]),
            maxfev=20000,
        )
        fit_params[name] = {"a": float(params[0]), "b": float(params[1])}
        observations[name] = {
            "spendUsd": OBSERVED_SPEND_USD.astype(int).tolist(),
            "syntheticConversions": observed.astype(int).tolist(),
        }
    return fit_params, observations


def optimize_budget(fit_params: dict) -> dict[str, int]:
    allocation = {name: 0 for name in fit_params}
    remaining = TOTAL_BUDGET_USD
    while remaining > 0:
        best_name = None
        best_incremental_conversions = -1.0
        chunk = min(STEP_USD, remaining)
        for name, spend in allocation.items():
            if spend + chunk > PUBLISHER_CAP_USD:
                continue
            params = fit_params[name]
            extra = response(spend + chunk, **params) - response(spend, **params)
            if extra > best_incremental_conversions:
                best_name = name
                best_incremental_conversions = float(extra)
        if best_name is None:
            raise RuntimeError("Budget exceeds the combined publisher caps")
        allocation[best_name] += chunk
        remaining -= chunk
    return allocation


def curve_conversions(spend: float, params: dict) -> float:
    return float(response(spend, **params))


def marginal_roas(spend: float, params: dict) -> float:
    # Revenue returned by the next dollar, using the assumed $80 order value.
    derivative = params["a"] / (params["b"] + spend)
    return float(ORDER_VALUE_USD * derivative)


def portfolio_summary(allocation: dict[str, int], fit_params: dict) -> dict:
    lane_rows = {}
    total_conversions = 0.0
    total_revenue = 0.0
    for name, spend in allocation.items():
        conversions = curve_conversions(spend, fit_params[name])
        revenue = conversions * ORDER_VALUE_USD
        total_conversions += conversions
        total_revenue += revenue
        step_start = max(0, spend - STEP_USD)
        step_roas = (
            ORDER_VALUE_USD
            * (curve_conversions(spend, fit_params[name]) - curve_conversions(step_start, fit_params[name]))
            / STEP_USD
            if spend
            else marginal_roas(0, fit_params[name])
        )
        lane_rows[name] = {
            "spendUsd": spend,
            "curveImpliedConversions": conversions,
            "curveImpliedRevenueUsd": revenue,
            "marginalRoasAtStart": marginal_roas(0, fit_params[name]),
            "marginalRoasAtAllocatedSpend": marginal_roas(spend, fit_params[name]),
            "marginalRoasForLast100Usd": step_roas,
        }
    return {
        "allocationByPublisher": lane_rows,
        "totalSpendUsd": sum(allocation.values()),
        "curveImpliedConversions": total_conversions,
        "curveImpliedRevenueUsd": total_revenue,
        "curveImpliedRoas": total_revenue / max(1, sum(allocation.values())),
    }


def main() -> None:
    fit_params, observations = fit_curves()
    optimized_allocation = optimize_budget(fit_params)
    equal_allocation = {
        name: TOTAL_BUDGET_USD // len(fit_params) for name in fit_params
    }
    optimized = portfolio_summary(optimized_allocation, fit_params)
    equal = portfolio_summary(equal_allocation, fit_params)

    fig, axis = plt.subplots(figsize=(8.2, 5.2))
    spend_line = np.linspace(0, 8000, 200)
    colors = ["#0b6e4f", "#bb5500", "#2864a5", "#7f4a88"]
    for (name, params), color in zip(fit_params.items(), colors):
        axis.scatter(
            observations[name]["spendUsd"],
            observations[name]["syntheticConversions"],
            color=color,
            alpha=0.7,
            s=25,
        )
        axis.plot(spend_line, response(spend_line, **params), color=color, label=f"{name} simulated fit")
        axis.axvline(optimized_allocation[name], color=color, alpha=0.18, linestyle=":")
    axis.set(xlabel="Monthly spend per publisher (USD)", ylabel="Simulated conversions")
    axis.set_title("Simulated diminishing-returns curves\nNot measured campaign response")
    axis.grid(alpha=0.2)
    axis.legend()
    fig.tight_layout()
    PLOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(PLOT_PATH, dpi=160)
    plt.close(fig)

    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "simulated_not_measured",
        "description": "Fixed-seed Poisson samples generated from arbitrary log-shaped response curves, then refit. All spend and conversion values are synthetic.",
        "formula": "conversions(spend) = a * log(1 + spend / b)",
        "randomSeed": RANDOM_SEED,
        "orderValueUsdAssumption": ORDER_VALUE_USD,
        "totalBudgetUsd": TOTAL_BUDGET_USD,
        "perPublisherCapUsd": PUBLISHER_CAP_USD,
        "observations": observations,
        "fittedCurveParameters": fit_params,
        "optimizer": {
            "method": f"allocate ${STEP_USD} increments to the publisher with the largest next-step fitted conversions, subject to per-publisher caps",
            "optimizedAllocation": optimized,
            "equalSplitBaseline": equal,
            "differenceInCurveImpliedConversions": optimized["curveImpliedConversions"] - equal["curveImpliedConversions"],
        },
        "plot": str(PLOT_PATH.relative_to(ROOT)),
        "caveat": "This shows how an optimizer can consume a response curve. It is not a performance forecast, an ML bid, or evidence that any real publisher responds this way.",
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf8")
    print(f"Saved {REPORT_PATH.relative_to(ROOT)}")
    print(f"Saved {PLOT_PATH.relative_to(ROOT)}")
    print("Status: simulated, not measured")
    print("Optimized allocation:", optimized_allocation)
    print(
        f"Curve-implied conversions: optimized={optimized['curveImpliedConversions']:.1f}; "
        f"equal split={equal['curveImpliedConversions']:.1f}"
    )


if __name__ == "__main__":
    main()
