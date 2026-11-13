# Disco campaign draft

A Streamlit prototype that turns an advertiser's description into a reviewable campaign proposal: publisher recommendations and exclusions, persona-specific ad copy, and an editable campaign config. It uses the supplied mock catalog of 20 publishers and 10 personas. It is a proposal tool; it does not buy inventory or launch ads.

## Run the app

```bash
pip install -r requirements.txt
cp .env.example .env.local
# Replace the placeholder with OPENAI_API_KEY to enable GPT-6 Luna.
streamlit run app.py
```

`.env.local` is gitignored; never commit an API key. Without a key, the workflow uses local rules. `python3 scripts/check_examples.py` runs the 15 supplied briefs through that rules path. The app sends the brief and catalog context to OpenAI only when model mode is enabled. Do not enter sensitive or customer data.

## Approach and design decisions

### Brief parsing and refusal

The parser produces a typed brief: product, allowed product cores, price tier and price, audience details, claims, modifiers, ambiguities, assumptions, and clarifying questions. Code validates the fields and claim excerpts. Human shopper age is only targeted when the brief states it; “senior dog food” does not imply an owner's age. B2B software and workflow products are outside this consumer catalog. Vague briefs, off-catalog offers, and briefs with no viable publisher become `needs_clarification`, `off_catalog`, or `no_placement`; those statuses return no ads or campaign config.

### Publisher ranking

For the prototype's 20-row catalog, one GPT-6 Luna ranking call assesses every publisher. We chose that granularity so the reviewer can see every inclusion and exclusion without making 20 separate model calls. The model provides a relevance score and reason. Code derives the fit class from publisher taxonomy, validates cited catalog evidence, excludes publishers with no catalog fit, applies the 50-point bar, and caps recommendations at five. A malformed stage falls back to local rules; a bad publisher row can fall back independently. The result can be model, rules, or hybrid, and the UI exposes score sources plus a model/rules review log.

This is a draft-time aid, not a serving-time ranker. With a large catalog, the production design is hard eligibility checks, candidate retrieval, then an outcome-trained ranker. Do not call an LLM once per impression.

### Personas and ads

GPT-6 Luna selects three to five distinct personas and writes one short ad for each. Prompts treat the brief, catalog, and persona records as data rather than instructions. Code checks persona IDs, copy lengths, unique headlines, and whether each cited claim is an exact excerpt from the brief. Claim citations make review easier but do not prove that the ad's meaning is factually supported, so a person still needs to approve the copy. The Gifter persona is blocked unless the brief describes a gift use.

### Campaign config and UI

The output is an editable proposal. The UI lets a reviewer change the monthly pacing budget, flight length, target CPA cap, CPM learning days, weekly frequency cap, and attribution window. It calculates total flight budget as `monthly pace × flight days / 30`, recomputes the learning CPM from the CPA cap, and writes those edits into the downloadable config JSON. The publisher mix remains automatic and visible in the UI. Targeting interests come from product cores and modifiers; geos come from selected publishers. Age and gender stay broad unless the brief states them.

The current starting values are explicit heuristics:

- Monthly budget starts from price tier ($6,000 value, $12,000 premium, $10,000 luxury, or $8,000 otherwise), scales by `0.85 + 0.08 × recommended publisher count`, then rounds to $500.
- Publisher shares are proportional to `fit score² × log10(monthly impressions)`, with a 70% maximum share when more than one publisher is recommended.
- The starting CPA cap uses a category allowance against stated price or assumed order value: 8% luxury, 35% value, 28% subscription, or 22% otherwise, clamped to $12–$140. A reviewer can edit it. Check the result against contribution margin; AOV alone does not establish profitability.
- The initial CPM is about 45% of the CPA cap. Its floor is $18 when at least half of recommended publishers have high-income audiences, $12 when any have mid-high/high audiences, and $8 otherwise; the result is clamped to $6–$28. The default learning phase is seven days. These are planning settings, not predictions.
- Frequency cap defaults to five per week and the attribution window to seven days. The displayed CTR prior is 0.5%, assumed and not learned; it does not affect bids, pacing, or budget.

## Evaluation and saved results

### Adversarial checks

[`scripts/adversarial_checks.py`](scripts/adversarial_checks.py) uses mocked model responses and makes no API calls. The saved suite passes **11/11** checks in [`reports/adversarial_checks.json`](reports/adversarial_checks.json). It covers prompt injection, invented claims, a six-publisher response, a score-30 publisher, non-gift Gifter selection, direct catalog-fit enforcement, invalid evidence fallback, malformed JSON, missing API key, and handbag/backcountry no-placement cases.

Re-run it with:

```bash
uv run --python 3.11 --with-requirements requirements.txt python scripts/adversarial_checks.py
```

I also ran a rules-mode Streamlit `AppTest` through a full draft and changed all six config inputs. The JSON reflected the edits; for example, $9,000 monthly pace over a three-day flight yielded a $900 flight budget, and a five-day learning phase was clamped to the three-day flight.

### GPT-6 Luna versus rules: development-set diagnostics

[`scripts/evaluate_campaign.py`](scripts/evaluate_campaign.py) ran the 15 supplied examples plus 30 synthetic, manually labeled briefs (45 total). These briefs and labels were used while developing the prompts, rules, and validators. The following figures are the raw development-set comparison, **not held-out accuracy, independent selection evidence, or campaign lift**. Precision@5 is measured on the 33 briefs with positive publisher labels; the other field metrics use only briefs with a label for that field.

| Measure | Rules | GPT-6 Luna |
| --- | ---: | ---: |
| Publisher precision@5 (33 labeled briefs) | 66.7% | 80.8% |
| Product field (41 labeled fields) | 51.2% | 85.4% |
| Product category/core (38 labeled fields) | 73.7% | 100% |
| Price tier (45 briefs) | 66.7% | 100% |
| Explicit USD price, including expected nulls (45 briefs) | 100% | 100% |
| Overall status (45 briefs) | 75.6% | 91.1% |
| Refusals: no-placement, off-catalog, or unclear (12 briefs) | 100% | 91.7% |

Operationally, 1 of 114 model stages fell back to rules (0.9%); no complete stage was rejected by the validator. At publisher-row level, 17 of 760 rows used rules after local validation (2.2%). The run used 263,667 input and 94,699 output tokens, took 22.37 seconds per draft on average (21.44 seconds median), and cost an estimated $0.073716 total ($0.001638 per brief). Cost uses the report's explicit assumption of $0.10 per million input tokens and $0.50 per million output tokens; it is not a billing record. One persona/creative stage hit an API `BadRequestError` and fell back to rules.

The full output is [`reports/campaign_eval.json`](reports/campaign_eval.json). The 212-row [`publisher review log`](reports/model_rules_disagreements.json) records per-case model and rules fits, scores, decisions, reasons, validation issues, and the judgment used for review. A score disagreement is not itself evidence that either side predicts campaign outcomes better.

Re-running this comparison uses the API key from `.env.local` and makes model calls for all 45 briefs:

```bash
uv run --python 3.11 --with-requirements requirements.txt python scripts/evaluate_campaign.py
```

### Logistic regression ranking baseline

[`data/synthetic_ranker_cases.json`](data/synthetic_ranker_cases.json) contains 40 structured, manually labeled synthetic briefs. A listed publisher is positive; every catalog-eligible publisher not listed is treated as negative. That convention can mark an unknown publisher as bad, so these are subjective catalog-match labels, not exposure or conversion labels. The experiment uses explicitly annotated core and price tier; it does not test brief parsing.

The logistic-regression features include catalog fit, product core and price tier, stated human-age overlap, income distance, basket-band distance, price-to-publisher-AOV ratio, publisher reach, existing factor impacts, and penalties. The rule baseline is the existing hand-tuned score. Both rank only catalog-eligible publishers. Five-fold `GroupKFold` keeps all publisher rows for each brief in the same fold. On 33 placement briefs and 152 eligible brief/publisher pairs, held-out mean P@5 was **66.8% for rules and 66.2% for logistic regression**. The baseline did not improve on rules and is not used by the app.

Out-of-fold calibration was also inspected. Brier score was 0.0762 and log loss 0.2752. Five probability bins ranged from mean predicted/observed positive rates of 0.018/0.032, 0.130/0.097, 0.618/0.700, 0.970/0.900, and 0.998/1.000. With only 40 subjective briefs and correlated publisher rows, this plot does not establish that a 0.7 score means 70% real-world success. See the [calibration plot](reports/synthetic_ranker_calibration.png) and [`reports/synthetic_ranker_eval.json`](reports/synthetic_ranker_eval.json).

### CTR prior and response-curve simulation

The config carries a Beta(1, 199) CTR prior with mean 0.5%. `campaign.config.beta_binomial_ctr(clicks, impressions)` shows the update structure; no impression/click stream is connected and the value does not affect decisions.

[`scripts/simulate_budget_response.py`](scripts/simulate_budget_response.py) generates fixed-seed conversion observations from concave log-shaped curves, fits the curves, and assigns a simulated $12,000 budget in $100 increments. In that run, the optimizer allocated $3,700 / $2,900 / $3,400 / $2,000 across four mock lanes. The fitted curves imply 1,122 conversions versus 1,113 for an equal split, using a fictional $80 order value. Every point, curve, allocation, conversion, and ROAS in this exercise is simulated, not measured. See the [simulation report](reports/simulated_budget_response.json) and [plot](reports/simulated_budget_response.png).

Re-run both ML demonstrations with the optional dependencies in `requirements-ml.txt`:

```bash
uv run --python 3.11 --with-requirements requirements-ml.txt python scripts/evaluate_synthetic_ranker.py
uv run --python 3.11 --with-requirements requirements-ml.txt python scripts/simulate_budget_response.py
```

## Deployment and next steps

`Dockerfile` and `render.yaml` define a Render service; set `OPENAI_API_KEY` as a server-side secret. The prototype has no login, live inventory, event collection, auction bidder, spend controller, or campaign-launch action. CPA and CPM are not trained bids or forecasts. For the proposed production architecture, data requirements, bidding, pacing, and rollout gates, see [`PRODUCTION_PLAN.md`](PRODUCTION_PLAN.md). For the detailed ML label and calibration caveats, see [`ML_EXPERIMENTS.md`](ML_EXPERIMENTS.md).

The hardest parts are matching product and audience context, handling contradictions, refusing bad placements, and making each decision reviewable. If extended, the next work is an adjudicated evaluation set, consented impression/conversion logging, shadow ranking, semantic claim checks, and a controlled rollout with spend limits. Live auction execution, media creatives, and feedback-driven bidding are intentionally out of scope.
