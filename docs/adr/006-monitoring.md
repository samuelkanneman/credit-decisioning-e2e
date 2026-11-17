# ADR-006: Monitoring design

Status: accepted · 2026-11-17

## Context

Monitoring has to show what a lender would see: inputs immediately, outcomes only once loans mature and only for loans that were booked. The simulation knows every outcome at once, which makes it easy to build a monitor that could never exist.

## Decision

- **Everything is computed "as of" a month** from two tables production would have: the decision log, written by the decision service, and loan outcomes, written by the simulator in the role of the servicing system with the date from which each outcome is known.
- **Performance uses booked loans only.** The reference for KS, AUC and calibration is development measured on loans the same policy approves (`models/scorecard-v1/reference.json`). Full-population metrics are shown, labelled as available only in a simulation.
- **Stability has two references.** PSI against development, as the profile defines it, and PSI against the previous three months, because a mix shift can move the score towards development.
- **One implementation, three outputs.** `credit_score.monitoring` computes the indicators, alerts and recommendation; `make monitor` writes the Markdown report, the Streamlit dashboard renders the same result, and Evidently adds monthly HTML drift reports of the raw inputs.
- **The reference is committed.** `reference.json` holds aggregates only (bin shares, rates, metrics), so the dashboard runs where the training data is not available.

## Consequences

- The dashboard can be pointed at any month and shows exactly what was knowable then. Performance appears for January 2017 from March 2018.
- At a 10% sample, monthly cohorts have 20 to 40 bads among booked loans, too few for a stable monthly KS. Alerts need 30 bads; pooled views (by band, by decile) carry the signal.
- Evidently 0.7 does not run on pandas 3. Its reports run from a script with its own pinned environment (`uv run --script`), reading parquet files exported by `make monitor`.
- The dashboard image is about 710 MB (Streamlit, pandas, pyarrow); the scoring and decision images stay without them.

## Alternatives considered

| Option | Why not |
| --- | --- |
| Measure performance on every simulated application | Uses outcomes production would never have; KS would look 9 points better than any real monitor could show |
| Evidently as the monitoring engine | Its drift tests flag almost everything at these volumes (44% of columns in the July drift month) and it has no notion of booked-only performance or policy rules |
| A fixed-reference PSI only | Missed the injected channel entirely in the simulation |
