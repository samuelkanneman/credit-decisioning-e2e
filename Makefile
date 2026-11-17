.PHONY: setup data sample dataset binning train audit lint test

RAW := data/raw
PROCESSED := data/processed

setup:
	uv sync

## Download the real Lending Club files (needs Kaggle credentials), verify hashes, build parquet
data:
	uv run --group data python -m credit_score.data.download --out $(RAW)
	uv run python -m credit_score.data.prepare --raw $(RAW) --out $(PROCESSED)

## Build a synthetic dataset with the same schema: no credentials, no real data
sample:
	uv run python -m credit_score.data.synthetic --out $(RAW) --rows 20000
	uv run python -m credit_score.data.prepare --raw $(RAW) --out $(PROCESSED)

## Build the modelling dataset: population, target, split, features, contract check
dataset:
	uv run python -m credit_score.dataset --data $(PROCESSED)

## Fit monotonic bins, compute IV and stability, select variables
binning:
	uv run python -m credit_score.binning --data $(PROCESSED)

## Fit scorecard and challenger, validate on out-of-time, export the model, log to MLflow
train:
	uv run python -m credit_score.train --data $(PROCESSED) $(TRAIN_ARGS)

## Regenerate docs/data_audit.md from the processed data
audit:
	uv run python -m credit_score.data.audit --data $(PROCESSED)

lint:
	uv run ruff check .
	uv run ruff format --check .

test:
	uv run pytest -q
