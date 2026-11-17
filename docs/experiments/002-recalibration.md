# Experiment 002: recalibrate scorecard-v1 to scorecard-v1.1

Status: **proposed** · preregistered 2026-11-17, before the candidate was built. Everything above "Results" is as written before the run.

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

Pending.

## Decision

Pending.
