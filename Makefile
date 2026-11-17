.PHONY: setup data demo dataset binning train reference policy-analysis policy-diff simulate monitor evidently audit lint test up down smoke loadtest

RAW := data/raw
PROCESSED := data/processed
MODELS := models
DOCS := docs
DEMO := build/demo

setup:
	uv sync

## Download the real Lending Club files (needs Kaggle credentials), verify hashes, build parquet
data:
	uv run --group data python -m credit_score.data.download --out $(RAW)
	uv run python -m credit_score.data.prepare --raw $(RAW) --out $(PROCESSED)

## Build the modelling dataset: population, target, split, features, contract check
dataset:
	uv run python -m credit_score.dataset --data $(PROCESSED)

## Fit monotonic bins, compute IV and stability, select variables
binning:
	uv run python -m credit_score.binning --data $(PROCESSED) --models $(MODELS) \
		--report $(DOCS)/binning.md

## Fit scorecard and challenger, validate on out-of-time, export the model, log to MLflow
train:
	uv run python -m credit_score.train --data $(PROCESSED) --models $(MODELS) \
		--report $(DOCS)/validation_report.md $(TRAIN_ARGS)

## Development distributions and metrics monitoring compares against (models/scorecard-v1/reference.json)
reference:
	uv run python -m credit_score.reference --data $(PROCESSED) --models $(MODELS)

## Cut-off curve, swap-set, knock-outs, limits and reject inference
policy-analysis:
	uv run python -m credit_score.policy_analysis --data $(PROCESSED) --models $(MODELS) \
		--report $(DOCS)/policy_analysis.md

## How many decisions a policy change moves, against BASE (default origin/main)
policy-diff:
	uv run python -m credit_score.policy_diff --base $(or $(BASE),origin/main) --data $(PROCESSED)

## Regenerate docs/data_audit.md from the processed data
audit:
	uv run python -m credit_score.data.audit --data $(PROCESSED) --out $(DOCS)/data_audit.md

## Run the whole pipeline on synthetic data, without credentials. Everything goes to
## build/demo: the committed model and the reports in docs/ are left untouched.
demo:
	mkdir -p $(DEMO)/docs
	uv run python -m credit_score.data.synthetic --out $(DEMO)/raw --rows 20000
	uv run python -m credit_score.data.prepare --raw $(DEMO)/raw --out $(DEMO)/processed
	$(MAKE) dataset binning train PROCESSED=$(DEMO)/processed MODELS=$(DEMO)/models \
		DOCS=$(DEMO)/docs TRAIN_ARGS=--no-mlflow

## Start PostgreSQL, scoring API (:8000/docs), decision service (:8001/docs), dashboard (:8501)
up:
	docker compose up -d --build --wait

down:
	docker compose down -v

## Check a running stack end to end
smoke:
	python3 services/score_api/smoke_test.py
	python3 services/decision_service/smoke_test.py

## 20 concurrent users for 25 seconds against a running stack
loadtest:
	uv run --group load locust -f services/score_api/loadtest/locustfile.py \
		--host http://127.0.0.1:8000 --headless -u 20 -r 20 -t 25s --only-summary

## Replay 2017 through the running stack. FRACTION of each month (default 0.1); DRIFT=broker
## adds a riskier channel from July 2017.
simulate:
	uv run python -m credit_score.simulation --data $(PROCESSED) --fraction $(or $(FRACTION),0.1) \
		$(if $(DRIFT),--drift $(DRIFT))

## Monitoring report as of AS_OF (default 2019-03) from the decision log; frames to build/monitoring/latest
monitor:
	uv run python -m credit_score.monitoring --as-of $(or $(AS_OF),2019-03) --data $(PROCESSED) \
		--export-dir build/monitoring/latest --report $(or $(REPORT),$(DOCS)/monitoring_report.md)

## Monthly Evidently data-drift reports (HTML) for the last `make monitor` run
evidently:
	uv run --script monitoring/evidently_report.py build/monitoring/latest build/monitoring/evidently

lint:
	uv run ruff check .
	uv run ruff format --check .

test:
	uv run pytest -q
