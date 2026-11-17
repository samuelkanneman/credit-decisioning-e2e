# Credit policy

Status: **proposed**, version `policy-v1` · 2026-11-17. Applies to individual applications scored with `scorecard-v1`. Implemented in [`policy/credit-policy.json`](../policy/credit-policy.json) and served by the decision service.

Every number below comes from the generated [policy analysis](policy_analysis.md): individual 36-month loans issued 2013–2015 that ran their full term, profit measured from realised cash flows ([ADR-004](adr/004-profitability-and-lgd.md)). "Under stress" means 1.34 times as many charge-offs.

## The decision

Rules are evaluated in this order. The first knock-out that fires decides.

| # | Rule | Outcome | Owner |
| --- | --- | --- | --- |
| K1 | Bureau score below 660 | Reject | Model risk |
| K2 | 10 or more accounts opened in the last 24 months | Reject | Credit policy |
| S1 | Score below 580 | Reject | Credit policy |
| S2 | Score from 580 to 594 | Review | Credit policy |
| R1 | Score 595 or above, employment length not reported | Review | Credit policy |
| R2 | Score 595 or above, annual income not reported | Review | Credit policy |
| S3 | Score 595 or above | Approve, with the limit of its band | Credit policy |

On the analysis population this approves 48.6% of applicants, sends 28.6% to review and rejects 22.8%.

| Decision | Share | Charged off | Margin on amount lent | Margin under stress |
| --- | --- | --- | --- | --- |
| Approve | 48.6% | 8.2% | 2.83% | 1.39% |
| Review | 28.6% | 16.2% | 1.62% | −1.20% |
| Reject | 22.8% | 24.0% | −1.68% | −5.64% |

## Why each rule

**K1. Bureau score below 660: reject.** The scorecard has never seen such an applicant: Lending Club approved none. In the nine months where the rejected file is comparable, 288,103 applications were below 660, with a mean score of 601. Scoring them would be extrapolation. This is a limit of the model, not a view on those applicants.

**K2. Ten or more accounts opened in 24 months: reject.** Among loans the score would approve, this group (1.8% of them) has a margin of −0.02% and −1.89% under stress, against 2.70% and 1.21% for all approved loans. It is the only candidate rule that removes loans which pass the score and still lose money.

**S1. Score below 580: reject.** Each 5-point slice of score below 580 has a negative margin even in the base case, from −0.10% just under 580 to −17.5% below 540. Total profit peaks at a cut-off of 580.

**S2. Score 580 to 594: review.** These loans make money in the base case (0.77% to 2.10%) and lose it under stress (−2.50% to −0.46%). Approving them automatically bets that conditions stay as in 2013–2015; rejecting them gives up 54 thousand dollars of profit per 1,000 applicants. A reviewer decides, with a conservative limit.

**R1. Employment length not reported: review.** Among score-approved loans this group (5.6%) has a margin of 1.13% and −1.07% under stress, and 13.9% charged off against 8.6%. Not enough to reject, enough to ask.

**R2. Annual income not reported: review.** Without income there is no limit to apply. Lending Club required income, so the analysis population has no such case; the rule exists for the engine, not because the data asked for it.

**S3. Score 595 or above: approve.** Profit under stress peaks at a cut-off of 595, and every slice from 595 up keeps a positive margin under stress.

## Limits

Maximum loan as a share of annual income, capped at the product maximum of 40,000 and never below 1,000. An application above its limit is approved for the limit, not rejected.

| Band | Score | Maximum loan-to-income | Basis |
| --- | --- | --- | --- |
| A | 610 and above | 40% | Positive under stress in every bucket (1.5% to 2.2%); 2.4% of approved A loans ask for more |
| B | 597 to 609 | 40% | Positive under stress in every bucket (0.4% to 1.4%); 3.8% ask for more |
| C | 595 to 596 (approved) | 30% | The slice 595–599 holds +0.18% under stress; band C as a whole does not beyond 10% of income, so this is the least supported limit. 15.7% ask for more |
| C and D | 580 to 594 (after review) | 10% | Only the lowest loan-to-income bucket stays close to break-even under stress |

The data does not support a lower limit for B than for A: loan size relative to income adds little once the score is known, because it is already a scorecard variable.

## Rules that were tested and left out

| Candidate | Margin on score-approved loans | Under stress | Why not |
| --- | --- | --- | --- |
| DTI above 35 | 2.98% | 0.64% | Profitable; DTI is already in the score |
| DTI above 30 | 3.16% | 1.22% | As good as the average approved loan |
| 3 or more inquiries in 6 months | 2.86% | 0.94% | Profitable; already in the score |
| Loan above 35% of income | 3.22% | 1.31% | Better than average at these prices |
| Annual income below 25,000 | 2.32% | −0.27% | Marginal under stress only; too close to call on 1.9% of loans |
| Purpose: small business | 3.65% | 0.85% | Higher loss, higher price |

A knock-out on a variable the score already uses mostly removes loans the score had priced correctly. These stay out until monitoring shows otherwise.

## Against the previous way of deciding

Approving the same 52.4% of applicants by ranking on bureau score (lower DTI first on ties) instead of on this score:

| Policy | Charged off | Margin | Profit per 1,000 applicants |
| --- | --- | --- | --- |
| Score ≥ 595 | 8.55% | 2.70% | $195.9k |
| Bureau score ranking | 10.60% | 1.79% | $130.7k |

The two disagree on a third of applicants. The 16.3% approved only by the score have a mean bureau score of 671 and a margin of 3.62%; the 16.3% approved only by the bureau ranking have a mean bureau score of 703 and a margin of 0.32%.

Ranking by Lending Club's own sub-grade gives a slightly lower charge-off rate (8.41%) and much less profit ($113.9k), because the grade also sets the price: its best loans are its cheapest.

## Champion and challenger

10% of applications go to a challenger policy that approves from 590 instead of 595. The arm comes from the SHA-256 of the application ID, so the same application always gets the same policy, and it is recorded with every decision.

| Arm | Approve | Review | Reject | Margin on approved | Under stress |
| --- | --- | --- | --- | --- | --- |
| Champion (from 595) | 48.6% | 28.6% | 22.8% | 2.83% | 1.39% |
| Challenger (from 590) | 57.4% | 19.8% | 22.8% | 2.77% | 1.17% |

The challenger approves the slice 590–594, which makes money in the base case and loses it under stress. It answers whether the stress case is too cautious, at a bounded cost. Its extra approvals sit in band C below 595 and get the conservative limit of 10% of income (rule L4), not the 30% of approved band C.

## What this policy does not know

- **Rejected applicants.** Reject inference on the two variables the files share suggests that the bad rate measured on approved loans (2.75% at a bureau score of 660 or more) understates the rate for everyone who applied: between 3.4% and 8.5%, depending on how much worse rejected applicants are assumed to be. The cut-offs are set on approved loans only. That is one reason the approval cut-off is the stress optimum and not the base one.
- **Prices.** Margins are at Lending Club's interest rates.
- **60-month loans.** Their economics are not measured; the same cut-offs apply.
- **Reviewers.** The review outcome is not modelled. The simulation treats a review as a pending decision.

## Changing this policy

- **Where the rules live.** [`policy/credit-policy.json`](../policy/credit-policy.json) is a GoRules decision graph: a knock-out table, a cut-off table, a limit table and an expression for the amount. It opens in the GoRules visual editor. [`policy/manifest.json`](../policy/manifest.json) holds the version and the SHA-256 of the graph; the engine refuses to load a graph that does not match, so a rule cannot change without the version changing.
- **Tests.** `tests/test_policy_rules.py` checks every rule on both sides of its boundary, the limits and amounts, and that the graph gives the same decision and limit as the code that produced the numbers in this document, for both arms.
- **Impact.** A pull request that touches `policy/` gets a table of how many decisions move, on 20,000 applications, in the CI summary (`make policy-diff` locally).
- **Trace.** Every decision stores the policy version and hash, the model version, the arm and the rules that fired.
