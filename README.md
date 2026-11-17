# credit-decisioning-e2e

[![ci](https://github.com/samuelkanneman/credit-decisioning-e2e/actions/workflows/ci.yml/badge.svg)](https://github.com/samuelkanneman/credit-decisioning-e2e/actions/workflows/ci.yml)

End-to-end credit decisioning system: scorecard → scoring API → rules engine → monitoring, built on public Lending Club data.

The point of this repository is the engineering and the credit policy around a model, not the model's AUC: how a score is built to risk standards, served, wrapped in a policy that is versioned as code, and monitored once it is making decisions.

> **Status: work in progress.** Phases 1 to 4 are done: score, scoring API, credit policy and monitoring. Phase 5 are planned in [`docs/roadmap.md`](docs/roadmap.md) (Spanish).

## Architecture

```mermaid
flowchart LR
    raw[(Lending Club<br/>public data)] --> train[Scorecard training<br/>WoE · calibration · OOT validation]
    train --> registry[(Model registry<br/>MLflow)]
    registry --> score[Score API<br/>FastAPI]
    app([Application]) --> decision[Decision service]
    decision -->|features| score
    score -->|score · PD · reasons| decision
    policy[Policy as code<br/>GoRules JDM] --> decision
    decision -->|approve · review · reject| app
    decision --> logs[(Decision logs<br/>PostgreSQL)]
    sim[Traffic simulator] --> decision
    logs --> monitor[Monitoring<br/>PSI · KS · dashboard]
    monitor -.->|retraining criteria| train
```

## Results so far

A 15-variable scorecard trained on 2013–2015 loans and validated on loans issued in 2016, with success criteria written down before training.

| Model | AUC, out-of-time | KS, out-of-time |
| --- | --- | --- |
| FICO alone | 0.578 | 11.7% |
| **Scorecard** | **0.672** | **24.5%** |
| LightGBM challenger, same variables | 0.682 | 26.1% |

- The bad rate falls from 9.4% in the riskiest decile to 1.0% in the safest, with no inversions, on loans the model never saw.
- Gradient boosting buys one point of AUC. The scorecard keeps a points table and reason codes for that price.
- The ranking holds out of time but the PD does not: the model predicts 3.0% where 4.0% defaulted. The population did not change (score PSI 0.003); the level of risk did. That gap is what the monitoring phase is built to catch.
- The policy built on that score approves 48.6% of applicants at a 2.8% margin on matured loans, against 1.8% for a bureau-score ranking that approves as many; every cut-off is justified by the loans just above and below it, under a stress case of 34% more defaults.

- Replaying 2017 through the stack, monitoring recommends **recalibrating, not retraining**: booked loans went bad 1.27 times as often as predicted while the score distribution barely moved. With a riskier channel injected from July, approval fell 10 points in a month and a knock-out fired 58% more often, yet score PSI against development *fell*: the new applicants pulled the distribution back towards development. A fixed-reference PSI alone would have missed it, and the broker loans the policy still approved went bad at 3.4% against 1.8% for direct ones.

Details: [model card](docs/model_card.md) · [validation report](docs/validation_report.md). · [monitoring](docs/retraining_criteria.md)

![Monitoring dashboard: score PSI against development and against the previous three months, with the July drift](docs/images/dashboard-stability.png)

## Roadmap

| Phase | Deliverable | Status |
| --- | --- | --- |
| 0. Foundations | Repo skeleton, reproducible data pipeline, CI | Done |
| 1. Score | Calibrated scorecard, LightGBM challenger, out-of-time validation | Done |
| 2. Scoring API | Containerised `/score` with reason codes | Done |
| 3. Policy | Cut-off analysis, swap-set, reject inference, rules engine | Done |
| 4. Monitoring | Production traffic simulator and dashboard | Done |
| 5. MLOps | End-to-end CI with `docker compose` | Planned |

## Quickstart

### Run the stack

Requires Docker. The trained scorecard and the policy are in the repository, so no data is needed.

```bash
make up       # PostgreSQL, scoring API (:8000/docs), decision service (:8001/docs), dashboard (:8501)
make smoke    # checks both services end to end
```

Ask for a decision:

```bash
curl -s localhost:8001/decide -H 'Content-Type: application/json' -d '{
  "application_id": "demo-0002", "loan_amnt": 30000, "term": 36, "purpose": "credit_card",
  "annual_inc": 60000, "emp_length": 6, "home_ownership": "MORTGAGE", "dti": 12.0,
  "fico": 735, "acc_open_past_24mths": 2, "inq_last_6mths": 0, "mths_since_recent_inq": null,
  "mo_sin_rcnt_tl": 18, "mths_since_recent_bc": 30, "mo_sin_old_rev_tl_op": 220,
  "bc_open_to_buy": 20000, "tot_hi_cred_lim": 250000}'
```

```json
{
  "application_id": "demo-0002",
  "decision": "APPROVE", "requested_amount": 30000.0, "approved_amount": 24000.0,
  "reasons": [{"source": "policy", "code": "L1",
               "description": "Amount limited to 40% of annual income for this risk band"}],
  "score": 635, "pd": 0.006, "band": "A", "arm": "champion",
  "model_version": "scorecard-v1", "policy_version": "policy-v1",
  "trace": {"knockout": null, "decision_rule": "S3", "limit_rule": "L1", "max_loan_to_income": 0.4}
}
```

A rejection lists the policy rule first and then, when the score drove it, the variables that cost the applicant the most points. Every decision is written to PostgreSQL before the answer is returned, with the model version, the policy version and hash, and the champion or challenger arm.

The scoring API on its own (`POST :8000/score`) returns score, PD, band and reason codes; a value outside the ranges the model was trained on is a 422, never a score. Measured on the stack with 3,000 real 2017 applications: scores identical to training, decisions identical to the offline policy, 9.4 ms at p95 for a full decision.

### Replay 2017 and monitor it

Requires the real data (`make data`, below) and the running stack.

```bash
make simulate              # 10% of 2017, month by month, through the decision service
make simulate DRIFT=broker # the same, with a riskier channel from July 2017
make monitor               # docs/monitoring_report.md: alerts and recommendation as of 2019-03
make evidently             # monthly Evidently drift reports (HTML) in build/monitoring/evidently
```

The dashboard at `http://localhost:8501` reads the decision log and lets you move the "as of" month: inputs update as applications arrive, performance appears only once a cohort has matured, and only for booked loans.

### Rebuild the model

Requires [uv](https://docs.astral.sh/uv/) and `make`.

```bash
make setup
make test
make demo     # whole pipeline on synthetic data, no credentials; output in build/demo
```

To train on the real data you need a Kaggle account and an [API token](https://www.kaggle.com/docs/api), exposed as `KAGGLE_API_TOKEN` (or `KAGGLE_USERNAME` and `KAGGLE_KEY` for a legacy key):

```bash
make data     # downloads from Kaggle (~650 MB), verifies SHA-256, builds parquet
make audit    # regenerates docs/data_audit.md
make dataset  # population, target, split, features, data contract check
make binning  # monotonic WoE bins, IV, stability, variable selection
make train    # scorecard, challenger, out-of-time validation, model export, MLflow
make policy-analysis  # cut-off curve, swap-set, knock-outs, limits, reject inference
make policy-diff      # how many decisions a change in policy/ moves
```

Loan data is never committed. `make data` fetches it from the source and checks it against the hashes in [`data_manifest.json`](data_manifest.json). The synthetic data is random by construction and supports no conclusion about credit risk; it exists so tests and CI run without the real files.

## How it is built

The project follows [data-science-harness](https://github.com/samuelkanneman/data-science-harness), my method for doing data science with coding agents: an explicit data contract, fit / selection / evaluation kept apart, experiments preregistered before they run, and metrics with one canonical definition. The rules that apply here are in [`AGENTS.md`](AGENTS.md).

## Design decisions

- [Model card](docs/model_card.md)
- [ADR-001: Dataset and data access](docs/adr/001-dataset.md)
- [ADR-002: Target definition and temporal split](docs/adr/002-target-and-split.md)
- [ADR-003: Scoring runtime and model artifact](docs/adr/003-scoring-runtime.md)
- [ADR-004: Profitability, loss given default and the stress case](docs/adr/004-profitability-and-lgd.md)
- [Credit policy](docs/policy.md) and the [analysis](docs/policy_analysis.md) behind every cut-off
- [ADR-005: Rules engine and decision service](docs/adr/005-rules-engine.md)
- [ADR-006: Monitoring design](docs/adr/006-monitoring.md) and the [thresholds and retraining criteria](docs/retraining_criteria.md)
- Monitoring reports: [2017 as it happened](docs/monitoring_report.md), [with an injected channel](docs/monitoring_report_drift.md)
- [Binning and variable selection](docs/binning.md)
- [Experiment 001: scorecard and challenger](docs/experiments/001-scorecard.md), preregistered before training
- [Data audit](docs/data_audit.md): cohort maturity and first look at the target
