# Model card: scorecard-v1.1

Serving version: `scorecard-v1.1`, the points and bands of `scorecard-v1` with the PD mapping refitted on 2016 loans ([experiment 002](experiments/002-recalibration.md)). Scores, bands, reason codes and decisions are identical between the two; only the PD differs. Figures below are for `scorecard-v1` unless marked.

## Purpose

Rank individual personal-loan applications by the risk of early default, to support an approve / review / reject decision and a credit limit. Output: a score (higher is safer), a risk band from A to E, a probability of default and the reasons behind a low score.

Built as a portfolio project on public data. It is not a production model and must not be used to make real lending decisions.

## Data

- Lending Club accepted loans, public Kaggle mirror (CC0), snapshot of March 2019.
- Population: individual applications. Development 2013–2015 (791,027 loans), out-of-time validation 2016 (425,618 loans). Loans issued in 2017 are held back to simulate production.
- Target: bad within 12 months of issue, a proxy for 90+ days past due built from the final loan status and the month of the last payment ([ADR-002](adr/002-target-and-split.md)). Bad rate: 3.11% in development, 4.01% out of time.

## Model

Logistic regression on weight-of-evidence bins, scaled to points: 600 points at 50:1 good:bad odds, 20 points to double the odds. Scores range from 514 to 676. Fifteen variables, all known at application:

| Group | Variables |
| --- | --- |
| Recent credit seeking | accounts opened in 24 months, months since last inquiry, months since last account opened, inquiries in 6 months, months since last bankcard opened |
| Capacity | debt-to-income, loan amount to income, annual income, available bankcard credit, total credit limit |
| History | FICO at application, age of oldest revolving account |
| Application | purpose, term, home ownership |

Bins are monotonic in bad rate and fitted on development only. Missing values have their own bin. The full points table is in the [validation report](validation_report.md).

Excluded on purpose: Lending Club's grade and interest rate (the lender's own risk assessment), income verification status (a lender action), and anything known only after origination.

## Performance

| Metric | Development | Out-of-time |
| --- | --- | --- |
| AUC | 0.6849 | 0.6720 |
| Gini | 0.3697 | 0.3439 |
| KS by deciles | 26.74% | 24.48% |
| Bad rate, riskiest decile | 7.64% | 9.39% |
| Bad rate, safest decile | 0.65% | 1.02% |

Score PSI between the two periods: 0.0026. FICO alone reaches an AUC of 0.5782 out of time; a LightGBM challenger on the same variables reaches 0.6819. Criteria were fixed before training and all were met ([experiment 001](experiments/001-scorecard.md)).

## Risk bands

Cut-offs are the quintiles of the development score and stay fixed: E below 575, D from 575, C from 587, B from 597, A from 610.

| Band | Bad rate, development | Bad rate, out-of-time |
| --- | --- | --- |
| A | 0.91% | 1.40% |
| B | 1.81% | 2.52% |
| C | 2.70% | 3.70% |
| D | 3.87% | 5.04% |
| E | 6.60% | 8.34% |

## Limitations

- **The PD level depends on the period it was fitted on.** `scorecard-v1`, calibrated on 2013–2015, predicted 3.01% for 2016 loans against 4.01% observed. `scorecard-v1.1`, recalibrated on 2016, predicts 3.72% for 2017 loans against 3.77% observed, within 5% in every band. 2016 was the worst year of the period, so if conditions improve v1.1 will over-predict; monitoring watches observed against predicted by band.
- **Discrimination is moderate.** Lending Club had already screened the population (minimum FICO of 660), which restricts the range of every variable, and the lender's grade is excluded.
- **Selection bias.** The model saw only loans Lending Club approved. Its behaviour on applicants that lender rejected is unknown; reject inference is addressed in the policy phase.
- **The target is a proxy** for early default, exact to the month, and misses loans that reached 90 days past due and then cured. It is not a lifetime loss measure.
- **Scope.** Individual applications only; joint applications need their own scorecard. US unsecured personal loans, 2013–2016 credit conditions.
- **Fairness has not been assessed.** The data carries no protected attributes, and state and employment length were left out of the model, but proxies have not been tested.

## Use and monitoring

Use the score and band for ranking and cut-offs. Monitor score PSI, KS and AUC on matured cohorts, and observed against predicted bad rate by band. A move of more than 3 points in AUC or KS against a comparable period is diagnosed (mix, policy, range restriction) before it is treated as model decay.

## Reproduce

```bash
make data dataset binning train
```

Each run records the code commit and the SHA-256 of its inputs in `models/scorecard-v1/metadata.json` and in MLflow.
