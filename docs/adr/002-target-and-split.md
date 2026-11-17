# ADR-002: Target definition and temporal split

Status: accepted · 2026-11-17

## Context

The data is a single snapshot taken in March 2019. It holds each loan's final status and the month of its last payment, not a month-by-month delinquency history, so "90+ days past due within 12 months" cannot be computed literally. Loans issued late in the file have not had time to show their outcome. Evidence for everything below is in the [data audit](../data_audit.md).

## Decision

**Target.** A loan is *bad* when it was charged off (status `Charged Off` or `Default`) and its last payment came no later than month 8 on book. A borrower who stops paying after month 8 misses the instalment of month 9 and reaches 90 days past due at about month 12. A charged-off loan with no payment on record never paid and is bad. Everything else is *good*, including loans that prepaid early and loans that defaulted after the window.

**Window.** 12 months from issue.

**Split by issue date.**

| Set | Issued | Loans | Bads | Bad rate |
| --- | --- | --- | --- | --- |
| Development | 2013–2015 | 791,538 | 24,629 | 3.11% |
| Out-of-time validation | 2016 | 434,407 | 17,354 | 3.99% |
| Simulated production | 2017 | 443,579 | 16,710 | 3.77% |

**Exclusions.** Loans issued before 2013 (small volumes and an earlier credit policy, including the "Does not meet the credit policy" loans of 2007–2010) and loans issued in 2018 (not matured).

## Consequences

- Every cohort in the split has at least 15 months of observation, enough for a loan that went bad inside the window to have been charged off by the snapshot. Monthly bad rates stay flat through the end of 2017 and only start to truncate for loans issued from March 2018.
- With that much observation there is no indeterminate class: a loan that is merely late at the snapshot was current through its first 12 months.
- The bad rate rises from 2.65% (2013) to 3.99% (2016). The out-of-time set is riskier than development, so calibration drift is expected and is part of what validation and monitoring have to show.
- The target is a proxy. It will miss loans that reached 90 days past due inside the window and then cured, and its timing is exact only to the month. It measures early default, not lifetime loss; profitability analysis in Phase 3 uses realised payments instead.
- The monitoring simulator replays 2017 month by month and reveals each cohort's outcome 14 months after issue.

## Alternatives considered

| Option | Why not |
| --- | --- |
| 18-month window | Doubles the bad rate (about 7%), but only loans issued up to mid-2017 are matured, which leaves seven months of simulated production |
| Lifetime charge-off | Needs 36–60 months of maturity and mixes two terms with different exposure |
| Development 2014–2016, validation 2017, production 2018 | 2018 has between 3 and 14 months of observation |
