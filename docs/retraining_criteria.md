# Monitoring thresholds and retraining criteria

Status: accepted · 2026-11-17 · applies to `scorecard-v1` under `policy-v1`.

Monitoring runs on what production records: the decision log, and loan outcomes as servicing reports them. Implemented in `credit_score.monitoring`, shown in the dashboard and in [`monitoring_report.md`](monitoring_report.md).

## When each indicator is known

| Indicator | Known | Population |
| --- | --- | --- |
| Volume, score distribution, PSI, missing inputs | When the month's applications arrive | Every application |
| Approval rate, rule firing, champion against challenger, latency | When the month's applications are decided | Every application |
| KS, AUC, observed against predicted bad rate | 14 months after issue: 12 of observation, 2 for the charge-off to be booked | Booked loans only |
| Vintage curves | Month by month, two months behind | Booked loans only |

Performance is measured on **booked** loans because in production a rejected applicant has no outcome. Booking only approved loans restricts the range of the score, so the reference for each metric is development measured on the loans the same policy approves: KS 17.5% and AUC 0.617, against 26.7% and 0.685 on every development loan. Comparing a booked-only KS with the full-population figure would raise a false alarm every month.

## Thresholds

| Area | Indicator | Threshold | Level |
| --- | --- | --- | --- |
| Stability | Score PSI against development | > 0.10 · > 0.25 | watch · investigate |
| Stability | Score PSI against the previous three months | > 0.05 | watch |
| Stability | PSI of a scorecard variable against development | > 0.10 · > 0.25 | watch · investigate |
| Performance | KS on booked loans below development | > 3 pp · > 5 pp | investigate · alert |
| Performance | Observed / predicted bad rate on booked loans | outside 0.80–1.25 | alert |
| Performance | Observed / predicted by band, matured cohorts pooled | outside 0.80–1.25 | investigate |
| Policy | Approval rate against development | > 10 pp | investigate |
| Policy | Approval rate change in one month | > 5 pp | investigate |
| Policy | Firing rate of a knock-out or review rule, month on month | > 50% relative, from a base of 1% or more | investigate |

Performance alerts are evaluated only for months with at least 30 bads among booked loans; below that a monthly KS or ratio is noise. At a 10% simulation sample, monthly cohorts hold 20 to 40 bads, so several months are silent by design.

PSI bands of 0.10 and 0.25 are the profile's heuristics. The rolling PSI threshold of 0.05 is not: it was set from the simulation of 2017 without injected drift, whose largest monthly value was 0.010, and then checked against the run with drift, where July reached 0.077. It is a noise-based threshold for this volume and should be re-derived if volume changes.

## What triggers what

| Action | Condition | Why |
| --- | --- | --- |
| **Retrain** | KS alert (more than 5 pp below development) in two consecutive matured months; or score PSI above 0.25 in any month; or three or more variables above 0.25 in the same month | The ranking is failing, or the model is scoring a population it was not built for |
| **Recalibrate** | Observed / predicted outside 0.80–1.25 in two consecutive matured months, with no retrain condition | The ranking holds; the level of risk moved. Refit the score-to-PD mapping on recent matured loans, keep the points and the bands |
| **Investigate** | Any other alert | A single month, a policy-level signal or a variable drifting alone. Diagnose mix, channel and policy before touching the model |

Before calling any change model decay, check what the harness profile asks for: changes in mix, in policy and in approval rate, and range restriction. A policy change alters who is booked and therefore every booked-only metric.

## What the simulation showed

**2017 as it happened** ([report](monitoring_report.md)): recommendation **recalibrate**. Booked 2017 loans went bad 1.27 times as often as the model predicted, consistently across six matured months, while the score distribution stayed close to development (PSI below 0.05 every month) and KS stayed within 5 pp of development (one month, June, 4.6 pp below). The same gap appeared out of time in experiment 001. No false alarm from the policy indicators.

**After recalibration** ([report](monitoring_report_v1.1.md)): the same replay served by `scorecard-v1.1` ([experiment 002](experiments/002-recalibration.md)) gives **monitor**. The monthly calibration alerts are gone. What remains: variable PSI watches and the single June KS month, which do not depend on the PD mapping, and band C over-predicted on booked loans (0.78 on 46 bads), because the new mapping was fitted on every 2016 loan rather than on the loans this policy books. Worth refitting on booked loans once enough of them have matured.

**2017 with a riskier channel from July** ([report](monitoring_report_drift.md)): a broker channel adds 40% to each month's volume with applicants scoring below 690 on the bureau and with six or more accounts opened in two years. In July:

- approval fell 9.8 points in one month, from 47% to 37%;
- knock-out K2 fired 58% more often;
- score PSI against the previous three months reached 0.077;
- the PSI of accounts opened in 24 months rose to 0.19.

**Score PSI against development fell**, from about 0.03 to 0.01. 2017 applicants scored better than development, and the riskier inflow pulled the distribution back towards it. A fixed reference measures distance from development, not change: on its own it would have missed the shift. That is why the rolling PSI and the policy-level indicators exist.

The policy rejected 62% of the new channel and approved 11%, against 24% and 48% for direct applicants. But the broker loans it did approve went bad at 3.4% within 12 months, against 1.8% for approved direct loans: the score ranks these applicants correctly against each other and still underestimates them as a group. The recommendation stays **recalibrate**, because no retrain condition is met, yet the right response is a segment review: monitor approved loans by channel, and consider channel as a policy rule or a model variable at the next development. Indicators computed over the whole book would take months to show this; the per-month policy alerts showed it in the first month.
