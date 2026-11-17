# ADR-004: Profitability, loss given default and the stress case

Status: accepted · 2026-11-17

## Context

A cut-off is a business decision: it needs a measure of what each approved loan is worth. The score predicts default within 12 months, but a loan is approved for its whole life, and most charge-offs happen after month 12 (14.1% of 36-month loans are eventually charged off against 2.7% that go bad in the first year).

## Decision

**Measure profit on loans that have run their full term, instead of modelling it.** The population is individual 36-month loans issued 2013–2015 that are fully paid or charged off (545,779 loans). For each loan:

```text
profit = total cash received (principal, interest, fees, recoveries)
       − amount lent
       − funding and servicing cost
```

Three assumptions, fixed before the cut-off curve was run:

| Assumption | Value | Basis |
| --- | --- | --- |
| Loss given default | 90% of the balance at default | Measured: 88.96% over 76,782 charged-off loans, between 88.6% and 89.3% by issue year. Rounded up. |
| Funding and servicing cost | 6.5% of the amount lent, over the life of the loan | Assumed: about 4% a year (3% cost of funds, 1% servicing) on the average balance of a 36-month amortising loan, roughly 53% of the amount over three years. Not observable in the data. |
| Stress | 1.34 times as many charge-offs at every score | Measured: observed against predicted 12-month bad rate on 2016 loans (experiment 001). Applied by reweighting, so the population size does not change. |

LGD is not needed to draw the cut-off curve, because realised cash flows already contain the losses. It is used for the expected loss attached to each decision:

```text
expected 12-month loss = PD × exposure at default × LGD
exposure at default    = 88% of the amount, the balance of loans that go bad in the first year
```

## Consequences

- The cut-off analysis has no PD, LGD or prepayment model inside it, so it cannot be wrong about them. Its weak point is the funding cost, which is an assumption and moves the answer: without it every score slice from about 555 up looks profitable, against 580 with it.
- The economics are in-sample for the scorecard (development years). The model's ranking held out of time, so the ordering of the curve is reliable; its level is what the stress case covers.
- Margins are at Lending Club's prices, which were set by a grade this project does not use. A lender pricing by this score would see different margins.
- 60-month loans are excluded from the economics because most had not matured by the snapshot. They are riskier at the same score, and term is a scorecard variable, so the score cut-off already moves them down; their economics are not verified.
- Lifetime charge-off is a different event from the 12-month target. Reports show both.

## Alternatives considered

| Option | Why not |
| --- | --- |
| Expected profit = interest − PD × LGD × exposure on a 12-month horizon | Counts one year of losses against a commitment of three: it would approve almost everyone |
| A lifetime PD model plus prepayment and LGD models | Three more models to validate in order to estimate something the matured loans already show |
| Include 2016 loans to capture the worse period | Not matured: their profit is unknown for a third of them. The stress case stands in. |
| No funding cost | Makes the margin a gross yield and removes the reason to have a cut-off |
