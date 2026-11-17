# credit-decisioning-e2e

[![ci](https://github.com/samuelkanneman/credit-decisioning-e2e/actions/workflows/ci.yml/badge.svg)](https://github.com/samuelkanneman/credit-decisioning-e2e/actions/workflows/ci.yml)

End-to-end credit decisioning system: scorecard → scoring API → rules engine → monitoring, built on public Lending Club data.

The point of this repository is the engineering and the credit policy around a model, not the model's AUC: how a score is built to risk standards, served, wrapped in a policy that is versioned as code, and monitored once it is making decisions.

> **Status: work in progress.** The score (Phase 1) is done; later phases are planned in [`docs/roadmap.md`](docs/roadmap.md) (Spanish).

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

Details: [model card](docs/model_card.md) · [validation report](docs/validation_report.md).

## Roadmap

| Phase | Deliverable | Status |
| --- | --- | --- |
| 0. Foundations | Repo skeleton, reproducible data pipeline, CI | Done |
| 1. Score | Calibrated scorecard, LightGBM challenger, out-of-time validation | Done |
| 2. Scoring API | Containerised `/score` with reason codes | Planned |
| 3. Policy | Cut-off analysis, swap-set, reject inference, rules engine | Planned |
| 4. Monitoring | Production traffic simulator and dashboard | Planned |
| 5. MLOps | End-to-end CI with `docker compose` | Planned |

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and `make`.

```bash
make setup
make sample   # synthetic data with the real schema: no credentials needed
make test
```

To work with the real data you need a Kaggle account and an [API token](https://www.kaggle.com/docs/api), exposed as `KAGGLE_API_TOKEN` (or `KAGGLE_USERNAME` and `KAGGLE_KEY` for a legacy key):

```bash
make data     # downloads from Kaggle (~650 MB), verifies SHA-256, builds parquet
make audit    # regenerates docs/data_audit.md
make dataset  # population, target, split, features, data contract check
make binning  # monotonic WoE bins, IV, stability, variable selection
make train    # scorecard, challenger, out-of-time validation, model export, MLflow
```

Data is never committed. `make data` fetches it from the source and checks it against the hashes in [`data_manifest.json`](data_manifest.json). The synthetic sample is random by construction and supports no conclusion about credit risk; it exists so tests and CI run without the real files.

## How it is built

The project follows [data-science-harness](https://github.com/samuelkanneman/data-science-harness), my method for doing data science with coding agents: an explicit data contract, fit / selection / evaluation kept apart, experiments preregistered before they run, and metrics with one canonical definition. The rules that apply here are in [`AGENTS.md`](AGENTS.md).

## Design decisions

- [Model card](docs/model_card.md)
- [ADR-001: Dataset and data access](docs/adr/001-dataset.md)
- [ADR-002: Target definition and temporal split](docs/adr/002-target-and-split.md)
- [Binning and variable selection](docs/binning.md)
- [Experiment 001: scorecard and challenger](docs/experiments/001-scorecard.md), preregistered before training
- [Data audit](docs/data_audit.md): cohort maturity and first look at the target
