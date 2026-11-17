# ADR-001: Dataset and data access

Status: accepted · 2026-11-17

## Context

The project needs public data that supports three things at once: a real out-of-time split, profitability analysis, and reject inference.

## Decision

Use the Lending Club accepted-loans and rejected-applications files (2007–2018), fetched from a Kaggle mirror through the Kaggle API.

- It has an origination month, so the split can be temporal and later months can be replayed as production traffic.
- It has amount, rate and term, so expected profit per cut-off can be computed.
- It ships a separate file of rejected applications.

Data is not committed to the repository. `make data` downloads it, verifies a SHA-256 per file against `data_manifest.json`, and converts it to parquet. Tests and CI run on a synthetic sample generated with the same schema.

Columns are read through an explicit whitelist (`src/credit_score/data/schema.py`) where each column carries a role. Only `feature` columns may enter a model.

## Consequences

- Anyone reproducing the training needs their own Kaggle account. Tests, CI and the demo stack do not.
- The repository redistributes no loan-level Lending Club data. What it does commit is aggregate: reports and the fitted scorecard.
- `grade`, `sub_grade`, `int_rate` and `installment` are outputs of Lending Club's own underwriting. They are excluded from the score and kept only as the legacy-policy baseline and for profitability.
- The file is a final snapshot without monthly performance history, and the rejected file has few columns. Both constrain the target definition and reject inference; they are confirmed in the data audit and addressed in later ADRs.
- The model is trained on applications Lending Club approved, so it carries that selection bias.

## Alternatives considered

| Dataset | Why not |
| --- | --- |
| Home Credit Default Risk | No real dates, no rejected applications |
| Freddie Mac Single-Family | Mortgage only, no rejected applications, registration required |
| Give Me Some Credit | No dates, no economics, few variables |
| German Credit | 1,000 rows |
| Committing the CSVs (or Git LFS) | Over 1 GB, and redistribution depends on the mirror's licence |
