# ML experiments without outcome data

## What this work can and cannot show

The mock catalog has no impression, click, conversion, auction, or spend history. There is no basis here for a production CTR/CVR model, trained bidding policy, or outcome-grounded publisher ranker. The experiments below demonstrate methods and expose assumptions; they do not drive campaign recommendations, bids, pacing, or budget. All publisher labels and response observations are synthetic.

## Logistic regression publisher reranker

### Dataset and labels

[`data/synthetic_ranker_cases.json`](data/synthetic_ranker_cases.json) contains 40 structured briefs. Thirty reuse the manually labeled examples already prepared for the project; ten additional cases extend catalog coverage. The `core`, `priceTier`, `priceUsd`, and any target age/gender are explicit structured annotations for this experiment. The script does not evaluate parsing.

`relevantPublisherIds` lists positive catalog matches. For each brief, every publisher whose deterministic catalog fit is not `none` is a candidate. Any eligible publisher absent from the positive list is treated as negative. This is a deliberate simplification, not reliable negative feedback: a missing ID can mean “not judged” rather than “bad.” The no-fit cases remain useful refusals but do not create eligible training pairs or enter P@5.

### Features and model

The logistic regression operates on brief/publisher pairs. Predictive features include:

- catalog fit class and brief product core / price tier;
- stated human age overlap and gender-factor impact;
- income-tier distance, basket-band distance, and stated product price relative to publisher AOV;
- publisher reach and average order value;
- the existing rules scorer's factor impacts (including shelf, age, income, basket, price gap and contradictions) and total negative penalties.

Publisher IDs are labels only, not model features. The comparison baseline is the existing hand-tuned score. Both ranking methods sort only catalog-eligible publishers; P@5 is computed as hits in the top `min(5, eligible candidates)` for each labeled placement brief. This is a ranking experiment, not a deployed `recommend`/`exclude` threshold.

### Held-out evaluation and calibration

The script uses five-fold `GroupKFold` with `caseId` as the group. All publisher rows for a brief stay together, so no candidate from a held-out brief appears in that fold's training data. Across 40 briefs, 33 have positive placement labels and produce 152 eligible brief/publisher pairs (82 positive pairs).

| Held-out metric | Hand-tuned rules | Logistic regression |
| --- | ---: | ---: |
| Mean P@5 across 33 placement briefs | 66.8% | 66.2% |

Logistic regression did not beat the rules baseline in this run. That small difference on subjective labels does not show either approach is better in a live campaign.

The logistic out-of-fold probability diagnostics are Brier score **0.0762** and log loss **0.2752**. Quantile-bin calibration is:

| Pair count | Mean predicted match probability | Observed synthetic positive-label rate |
| ---: | ---: | ---: |
| 31 | 0.018 | 0.032 |
| 31 | 0.130 | 0.097 |
| 30 | 0.618 | 0.700 |
| 30 | 0.970 | 0.900 |
| 30 | 0.998 | 1.000 |

These are held-out predictions about the synthetic label policy, not observed campaign success. Pair rows from the same brief share context, the label set is small, and the labels are subjective. In particular, the bins do not support saying that a 0.7 model output means a 70% chance of a real buy succeeding. The calibration chart is an inspection aid, not a production calibration claim. Full folds, per-brief scores, bins, and coefficient sample are saved in [`reports/synthetic_ranker_eval.json`](reports/synthetic_ranker_eval.json); the plot is [`reports/synthetic_ranker_calibration.png`](reports/synthetic_ranker_calibration.png).

Run with optional ML dependencies:

```bash
uv run --python 3.11 --with-requirements requirements-ml.txt python scripts/evaluate_synthetic_ranker.py
```

## Assumed CTR prior

The editable campaign config includes a measurement placeholder labeled **“Prior CTR 0.5% (assumed, not learned)”**. The Beta prior is `Beta(1, 199)`, whose mean is `1 / 200 = 0.5%`. The update helper in `campaign/config.py` accepts aggregate click and impression counts and returns:

```text
posterior = Beta(1 + clicks, 199 + impressions - clicks)
```

For example, 10 clicks in 1,000 impressions would yield an illustrative posterior mean of `11 / 1,200 = 0.9167%`. This is only a demonstration of the update. The app does not collect or ingest clicks/impressions, choose a population for the prior, or use the value for ranking, bids, pacing, or budgets. A real prior would need a justified population and would need to account for publisher, placement, device, and campaign differences.

## Simulated budget response curve

[`scripts/simulate_budget_response.py`](scripts/simulate_budget_response.py) creates a deterministic teaching dataset with seed `260613`. Four arbitrary lanes get Poisson-generated conversions from concave log curves:

```text
conversions(spend) = a × log(1 + spend / b)
```

The script refits `a` and `b` from generated observations, then assigns a $12,000 budget in $100 increments to whichever lane has the largest next-step fitted conversion gain. Each lane has a $6,000 cap. It calculates marginal ROAS by multiplying the fitted marginal conversions per dollar by a fictional $80 order value.

| Synthetic allocation | Pawline | Ruffco | Pantrygood | Pop & Sip | Curve-implied conversions |
| --- | ---: | ---: | ---: | ---: | ---: |
| Marginal-gain optimizer | $3,700 | $2,900 | $3,400 | $2,000 | 1,122.2 |
| Equal split | $3,000 | $3,000 | $3,000 | $3,000 | 1,113.2 |

These values are generated by a fixed-seed simulation, not observed publisher response, predicted campaign results, or evidence that optimization improves real performance. Full generated samples, fitted parameters, marginal ROAS and allocations are in [`reports/simulated_budget_response.json`](reports/simulated_budget_response.json); the chart is [`reports/simulated_budget_response.png`](reports/simulated_budget_response.png).

Run with:

```bash
uv run --python 3.11 --with-requirements requirements-ml.txt python scripts/simulate_budget_response.py
```

## What would make these models production evidence

Collect consented impression logs joined to auction outcomes, actual spend, clicks, and deduplicated conversions after the attribution window matures. Record candidate eligibility, selected publisher, position, price, time, model version, feature values, and selection propensity. Define time-based holdouts and keep a prospective holdout or controlled experiment. Measure ranking quality, calibration, incremental value, margin-adjusted ROAS, spend safety and drift. Until then, the logistic weights, CTR prior, and simulated response curves must stay out of live decisions.
