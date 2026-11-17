"""Scoring API.

Loads one exported scorecard at start-up and scores applications against it.
The model is a fixed artifact (``card.json`` plus ``metadata.json``): the
service has no dependency on the training stack or on MLflow at runtime.
"""

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header

from credit_score.bounds import DERIVED
from credit_score.scoring import required_inputs, score_application
from score_api.schemas import Health, ModelInfo, ScoreResponse, build_request_model
from score_api.store import RequestLog

DEFAULT_MODEL_DIR = "models/scorecard-v1"


def create_app(model_dir: str | Path | None = None, request_log: RequestLog | None = None):
    model_dir = Path(model_dir or os.environ.get("MODEL_DIR", DEFAULT_MODEL_DIR))
    card = json.loads((model_dir / "card.json").read_text())
    metadata = json.loads((model_dir / "metadata.json").read_text())
    if card["version"] != metadata["model_version"]:
        raise RuntimeError("card.json and metadata.json describe different model versions")
    log = request_log or RequestLog()
    ScoreRequest = build_request_model(card)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        log.open()
        yield
        log.close()

    app = FastAPI(
        title="Credit scoring API",
        version=card["version"],
        description="Scores one loan application with a points-based scorecard.",
        lifespan=lifespan,
    )

    @app.post("/score", response_model=ScoreResponse)
    def score(
        application: ScoreRequest,  # type: ignore[valid-type]
        background: BackgroundTasks,
        x_request_id: str | None = Header(default=None, max_length=64),
    ) -> ScoreResponse:
        started = time.perf_counter()
        payload = application.model_dump()
        result = score_application(payload, card)
        response = ScoreResponse(
            request_id=x_request_id or str(uuid.uuid4()),
            model_version=card["version"],
            **result,
        )
        background.add_task(
            log.write,
            {
                "request_id": response.request_id,
                "received_at": datetime.now(UTC),
                "model_version": response.model_version,
                "score": response.score,
                "pd": response.pd,
                "band": response.band,
                "latency_ms": (time.perf_counter() - started) * 1000,
                "request": payload,
                "response": response.model_dump(),
            },
        )
        return response

    @app.get("/health", response_model=Health)
    def health() -> Health:
        database = log.status()
        return Health(
            status="degraded" if database == "error" else "ok",
            model_version=card["version"],
            database=database,
        )

    @app.get("/model-info", response_model=ModelInfo)
    def model_info() -> ModelInfo:
        return ModelInfo(
            model_version=metadata["model_version"],
            trained_at=metadata["trained_at"],
            code_commit=metadata["code_commit"],
            target=metadata["target"],
            variables=metadata["variables"],
            inputs=required_inputs(card, DERIVED),
            scaling=card["scaling"],
            bands=card["bands"],
            validation_metrics=metadata["metrics"],
        )

    return app


def app():
    """Factory for ``uvicorn --factory score_api.app:app``."""
    return create_app()
