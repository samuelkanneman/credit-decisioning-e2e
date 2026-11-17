# Model lifecycle

Status: accepted · 2026-11-17

How a model gets from a training run to serving decisions, and how it is replaced.

## Where a model lives

| What | Where | Role |
| --- | --- | --- |
| The served artifact | `models/<version>/card.json`, `metadata.json`, `reference.json` | Points, calibration, bands; provenance; monitoring reference. Committed |
| Which version serves | `models/registry.json` | Names the champion and records every promotion with its evidence |
| How it was produced | MLflow (`sqlite:///mlflow.db`, local) | Runs with parameters, input hashes and metrics; the model registry holds every version, the `champion` alias marks the one serving |

The scoring API, monitoring and the dashboard resolve the model through `registry.json`. Images copy every committed model and the registry, so promotion is a one-line change, reviewed as a pull request like a policy change.

## Versions

- **Minor (`v1.1`)**: the same points and bands, a new score-to-PD mapping. Rankings and decisions cannot change; the policy, written against the score, stays valid.
- **Major (`v2`)**: new bins, variables or coefficients. The score scale moves, so the policy's cut-offs have to be re-derived (`make policy-analysis`) and the policy version bumped with it.

## Promotion

1. **Preregister** the experiment in `docs/experiments/`: why, hypothesis, data, criteria. Commit it before building the candidate.
2. **Build the candidate** (`make train` for a new model, `make recalibrate CANDIDATE=…` for a minor version). Nothing is served yet.
3. **Compare** candidate and champion on the same loans, the most recent matured period the candidate was not fitted on (`make promote CANDIDATE=…`).
4. **Promote** only if every criterion is met. The command rewrites `registry.json`, records both versions in MLflow and moves the `champion` alias. Otherwise it exits with an error and changes nothing.
5. **Review and deploy**: the change to `models/` and `registry.json` goes through a pull request; `make up` rebuilds the images.
6. **Monitor**: regenerate the reference if the PD mapping changed (it is part of the promoted directory) and watch the first matured months.

## Criteria

| Criterion | Threshold | Applies to |
| --- | --- | --- |
| AUC against the champion | not lower by more than 0.005 | every candidate |
| KS against the champion | not lower by more than 1 pp | every candidate |
| Observed / predicted by risk band | every band between 0.80 and 1.25 | every candidate |
| Mean absolute calibration error by decile | lower than the champion's | every candidate |
| Overall observed / predicted | between 0.90 and 1.10 | recalibrations |
| Score PSI against the champion's development reference | below 0.10 | new models (major versions) |
| Decisions that change under the current policy | reported | every candidate |

A major version also needs its own preregistered experiment with out-of-time criteria, like [experiment 001](experiments/001-scorecard.md), before it is compared with the champion.

## History

| Version | Promoted | Change | Evidence |
| --- | --- | --- | --- |
| `scorecard-v1` | 2026-11-17 | First model | [Experiment 001](experiments/001-scorecard.md) |
| `scorecard-v1.1` | 2026-11-17 | PD mapping refitted on 2016 loans; points unchanged | [Experiment 002](experiments/002-recalibration.md), [promotion report](promotion_scorecard-v1.1.md) |
