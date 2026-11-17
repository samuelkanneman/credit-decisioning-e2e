# Experiment 002: recalibrate scorecard-v1 to scorecard-v1.1

Status: **adopted** · preregistered 2026-11-17 and committed before the candidate was built. Everything above "Results" is as written before the run.

## Why

Monitoring of simulated 2017 production recommends recalibration ([criteria](../retraining_criteria.md)): booked loans went bad 1.27 times as often as predicted for six matured months, while the score distribution and its ranking held. Experiment 001 had already shown the same gap out of time (1.34 on 2016 loans). The points work; the mapping from points to PD does not.

## Hypothesis

Refitting only the score-to-PD mapping on the most recent fully matured loans brings predicted and observed bad rates together on later loans, without changing a single score, band or decision.

## Candidate

`scorecard-v1.1`: the points table and band cut-offs of `scorecard-v1`, unchanged. The Platt mapping (a logistic regression of `bad_12m` on the score) is refitted on loans issued in 2016: 425,618 individual loans, every one matured, all of which were booked under the lender's historical policy.

## Evaluation

On loans issued in 2017 (401,154 individual loans, at least 15 months observed), comparing champion `scorecard-v1` and candidate on the same loans. These loans have been used by the monitoring simulation, which motivated the experiment but fitted nothing.

## Promotion criteria, fixed in advance

| # | Criterion | Threshold |
| --- | --- | --- |
| 1 | Ranking: AUC of the candidate against the champion | not lower by more than 0.005 |
| 2 | Ranking: KS of the candidate against the champion | not lower by more than 1 pp |
| 3 | Overall observed / predicted bad rate, candidate | between 0.90 and 1.10 |
| 4 | Observed / predicted by risk band, candidate | every band between 0.80 and 1.25 |
| 5 | Mean absolute calibration error by decile, \|observed / predicted − 1\| | lower for the candidate than for the champion |
| 6 | Decisions under `policy-v1` on the evaluation loans | report how many change; the policy uses the score, so none is expected |

Criteria 1, 2, 4 and 5 are the general promotion rules in [the model lifecycle](../model_lifecycle.md); criterion 3 is tighter than the monitoring band because a recalibration has no other job.

## Results

Generated tables: [promotion report](../promotion_scorecard-v1.1.md).

| # | Criterion | Threshold | Observed | Met |
| --- | --- | --- | --- | --- |
| 1 | AUC, candidate against champion | loss ≤ 0.005 | +0.0000 | yes |
| 2 | KS, candidate against champion | loss ≤ 1 pp | +0.00 pp | yes |
| 3 | Overall observed / predicted | 0.90–1.10 | 1.015 | yes |
| 4 | Observed / predicted by band | every band 0.80–1.25 | 0.950–1.037 | yes |
| 5 | Decile calibration error | lower than the champion | 0.031 against 0.376 | yes |
| 6 | Decisions that change under `policy-v1` | report | 0 of 401,154 | — |

The candidate predicts 2017 bad rates within 5% in every band. The champion predicted 2.77% where 3.77% of loans went bad, short by about a quarter in every band. Ranking and decisions are identical by construction.

Replaying 2017 through the stack with the candidate served, monitoring moves from **recalibrate** to **monitor** ([report](../monitoring_report_v1.1.md)). That replay uses the evaluation loans again, so it confirms the wiring rather than adding evidence. It also shows something the full-population evaluation does not: on the loans the policy books, the recalibrated model over-predicts (pooled observed / predicted 0.90; band A 0.96, band B 0.90, band C 0.78 on 1,816 loans and 46 bads). Monitoring alerts on the monthly pooled ratio, which stays inside 0.80–1.25, so it does not fire; band C would, if checked on its own. The mapping was fitted on every 2016 loan, most of which the current policy would not approve.

## Decision

Promoted. `scorecard-v1.1` is the champion in `models/registry.json` and carries the `champion` alias in the MLflow registry. The policy is unchanged.

Limit: the level of risk was refitted on 2016, the worst year of the modelling period (4.0% bad, against 2.7% to 3.8% in the other years from 2013 to 2017). If credit conditions improve, the new mapping will over-predict, and monitoring is what will show it.
