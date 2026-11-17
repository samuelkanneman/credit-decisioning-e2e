# ADR-003: Scoring runtime and model artifact

Status: accepted · 2026-11-17

## Context

The scoring API has to return exactly the score training produced, start without the training stack, and say which model it is serving.

## Decision

- **The model is a file, not a library object.** Training exports `card.json`: bins, points, calibration and band cut-offs as plain JSON, plus `metadata.json` with the version, code commit, input hashes and validation metrics. Both are committed under `models/scorecard-v1/`.
- **Scoring is pure Python.** `credit_score.scoring` scores one application from the card with the standard library only. The `credit_score` package has no runtime dependencies; pandas, optbinning, LightGBM and MLflow live in a dependency group used for training.
- **One table of valid ranges.** `credit_score.bounds` generates both the pandera contract of the training data and the pydantic request model of the API.
- **Nullable inputs must be sent as `null`.** An omitted field is a client error (422); a null is a value the scorecard has points for.
- **Requests are logged after the response is sent**, as a JSON line and, when a database is configured, as a row in PostgreSQL. A failed write is logged and does not fail the request.

## Consequences

- The image is about 210 MB and contains none of the training libraries.
- Two implementations of scoring exist: the vectorised one used in training and validation, and the runtime one. A test holds them together on 1,000 applications built to hit bin edges, missing values and every category, and the container was checked against 3,000 real loans with no differences.
- Serving a new model means building a new image with a new `card.json`. There is no hot reload and no call to MLflow at runtime; MLflow records how each artifact was produced.
- The committed model contains aggregates only (bin edges and points), not loan-level data.
- Because logging is asynchronous, a crash between response and write can lose a log row. The decision service of Phase 3 writes its own decision record before answering, which is the audit trail that matters.

## Alternatives considered

| Option | Why not |
| --- | --- |
| Load the model from an MLflow server at start-up | A runtime dependency on a tracking server for something that fits in a 10 KB file |
| Pickle the optbinning and regression objects | Ties the API to the training libraries and their versions, and a pickle is not reviewable |
| Score with pandas in the API | Hundreds of megabytes of dependencies to add fifteen integers |
