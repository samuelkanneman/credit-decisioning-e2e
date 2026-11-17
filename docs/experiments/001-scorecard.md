# Experiment 001: application scorecard and gradient-boosting challenger

Status: **proposed** · preregistered 2026-11-17, before any model was trained. Results are added below once it runs; the criteria above them are not edited afterwards.

## Hypothesis

A logistic scorecard on WoE-binned application and bureau variables ranks 12-month default risk clearly better than the bureau score alone, and holds that ranking on loans issued a year after the development period. A gradient-boosting model on the same raw variables ranks somewhat better, by a margin too small to justify giving up a points table and reason codes.

## Data

Defined in ADR-002 and `AGENTS.md`: individual Lending Club loans, target `bad_12m`, development 2013–2015 (791,027 loans, 24,613 bads), out-of-time 2016 (425,618 loans, 17,088 bads). The 2017 loans are not touched by this experiment; they are the simulated production of Phase 4.

Candidate variables are the 17 selected in `docs/binning.md`.

## Baselines and design

| Model | Role |
| --- | --- |
| FICO alone (`fico`, the low end of the range at application) | Baseline to beat |
| Lending Club sub-grade | Reference only: it is the lender's own model and uses information this project excludes, so it is not a target to beat |
| Logistic regression on WoE, scaled to points (600 = 50:1 odds, 20 points to double the odds) | Champion candidate |
| LightGBM on the raw selected variables, with monotonic constraints matching the WoE trends | Challenger |

- Everything is fitted on development. Hyperparameters of the challenger are chosen by cross-validation inside development, grouped by issue quarter. Seed 42.
- Variables whose coefficient is negative on WoE, or not significant at 1%, are removed and the model refitted, still on development.
- PD calibration is fitted on development. Out-of-time is scored once, at the end.

## Criteria fixed in advance

Primary metric: AUC on out-of-time.

| # | Criterion | Threshold |
| --- | --- | --- |
| 1 | Scorecard AUC on out-of-time exceeds FICO alone | by at least 0.05 |
| 2 | Scorecard AUC falls from development to out-of-time | by no more than 0.03 |
| 3 | PSI of the score, development to out-of-time | below 0.10 |
| 4 | Bad rate by score decile on out-of-time | monotonic, no inversions |
| 5 | All scorecard coefficients on WoE | same sign |

The scorecard is adopted if it meets all five. If it fails criterion 1 the variable set is revisited; if it fails 2 to 4 the unstable variables are diagnosed before anything else.

The challenger replaces nothing in this experiment. It is reported, and it would change the plan only if its out-of-time AUC exceeded the scorecard's by more than 0.03, in which case the gap is investigated (interactions, bins) rather than the champion swapped.

Calibration is reported, not gated: the default rate rises from 3.11% in development to 4.01% out of time, so a model calibrated on development is expected to under-predict there. The size of that gap is a result of the experiment and an input to the monitoring design.

Reported for every model and sample: loans, bads, AUC, Gini, canonical KS by deciles, the decile table, and observed against predicted bad rate by decile.

## Known deviations before the run

- `docs/binning.md` already shows, for information, the out-of-time bad rate of each bin. No bin edge or selection decision used it, and selection stability is measured inside development, but the author of the experiment has seen those figures.
- An earlier version of the selection used PSI between development and out-of-time as a filter. No variable was removed by it, and it has been replaced by PSI inside development.

## Results

Pending.

## Decision

Pending.
