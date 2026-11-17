# Experiment 001: application scorecard and gradient-boosting challenger

Status: **adopted** · preregistered 2026-11-17, before any model was trained; criteria confirmed by the author and run the same day. Everything above "Results" is as it was written before the run.

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

Full tables are in the generated [validation report](../validation_report.md). Model `scorecard-v1`, 15 variables.

| # | Criterion | Threshold | Observed | Met |
| --- | --- | --- | --- | --- |
| 1 | AUC gain over FICO alone, out-of-time | ≥ 0.05 | 0.0938 (0.6720 against 0.5782) | yes |
| 2 | AUC drop, development to out-of-time | ≤ 0.03 | 0.0129 (0.6849 to 0.6720) | yes |
| 3 | Score PSI, development to out-of-time | < 0.10 | 0.0026 | yes |
| 4 | Bad rate by decile, out-of-time | monotonic | 9.39% in decile 1 down to 1.02% in decile 10, no inversions | yes |
| 5 | Coefficients on WoE | same sign | all 15 negative | yes |

| Model | AUC dev | AUC out-of-time | KS deciles out-of-time |
| --- | --- | --- | --- |
| FICO alone | 0.5808 | 0.5782 | 11.72% |
| Scorecard | 0.6849 | 0.6720 | 24.48% |
| LightGBM challenger | 0.7002 | 0.6819 | 26.15% |
| Lending Club sub-grade (reference) | 0.7003 | 0.7072 | 29.58% |

- **Challenger.** It beats the scorecard by 0.0099 AUC out of time, well inside the 0.03 margin set in advance, and loses more from development to out-of-time (0.0183 against 0.0129). Its ranking of variables by contribution agrees with the scorecard's.
- **Calibration.** As expected, the model under-predicts out of time: 3.01% predicted against 4.01% observed, a ratio of 1.34 that is nearly flat across deciles (1.25 to 1.43). The score distribution did not move (PSI 0.0026, mean score 592.8 to 593.6), so this is a shift in the level of risk that the application variables do not explain, not a change in who applied. Ranking survives it; the PD does not.
- **Reference.** Lending Club's sub-grade ranks better than both models (0.7072). It is the lender's own model, built on more information than this project allows itself, so the gap measures the cost of excluding it rather than a defect.
- **Variables dropped while fitting.** `mort_acc` and `total_rev_hi_lim`, both not significant at 1% next to the other variables.

### Deviations from the plan

- The design says variables "whose coefficient is negative on WoE" are removed. That was a wording error: with WoE positive towards goods and bad as the event, the expected sign is negative, and the rule applied was "wrong sign", which is what criterion 5 states. No variable had the wrong sign.
- Out-of-time was meant to be scored once. The full pipeline ran twice: the second run fixed number formatting in the report and the MLflow configuration. Nothing in the model changed between them (the exported card is byte-identical) and no decision was taken in between.

## Decision

Adopt `scorecard-v1` as the champion. It meets the five criteria fixed in advance.

Two consequences carry forward:

1. The PD needs recalibration before it is used for anything priced or provisioned. Phase 3 uses the score for ranking and cut-offs and treats PD by band as a development-period estimate; Phase 4 monitors observed against predicted by band and defines when to recalibrate.
2. The challenger stays as a monitored benchmark, not a candidate. A gap above 0.03 AUC on a matured production month would reopen the question.
