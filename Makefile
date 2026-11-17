.PHONY: setup data sample audit lint test

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

## Regenerate docs/data_audit.md from the processed data
audit:
	uv run python -m credit_score.data.audit --data $(PROCESSED)

lint:
	uv run ruff check .
	uv run ruff format --check .

test:
	uv run pytest -q
