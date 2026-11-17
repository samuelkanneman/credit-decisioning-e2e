"""Decision service.

For each application: call the scoring API, assign the champion or
challenger arm, evaluate the policy graph, record the decision, answer.
The response carries the full trace: rules fired, score, model version and
policy version.
"""

import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from credit_score.policy_engine import Policy, assign_arm
from decision_service.store import DecisionLog

SCORE_TIMEOUT_S = 2.0
SCORE_DRIVEN_RULES = {"S1", "S2"}


class DecisionRequest(BaseModel):
    """The application. Fields not declared here are the scoring API's inputs and go to it."""

    model_config = ConfigDict(extra="allow")

    application_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    emp_length: float | None = Field(
        ..., ge=0, le=10, description="Years in current job, capped at 10; null if not reported"
    )
    received_at: AwareDatetime | None = Field(
        default=None, description="When the application was received; defaults to now"
    )


class Reason(BaseModel):
    source: str = Field(description="'policy' or 'score'")
    code: str
    description: str


class Trace(BaseModel):
    knockout: str | None
    decision_rule: str
    limit_rule: str | None
    max_loan_to_income: float | None
    score_points: dict[str, int]


class DecisionResponse(BaseModel):
    application_id: str
    decision: str = Field(description="APPROVE, REVIEW or REJECT")
    requested_amount: float
    approved_amount: float | None = Field(description="Set when approved; may be below the request")
    limit: float | None
    reasons: list[Reason]
    score: int
    pd: float
    band: str
    arm: str
    model_version: str
    policy_version: str
    received_at: datetime
    decided_at: datetime
    trace: Trace


def build_reasons(policy_out: dict, scored: dict, requested: float) -> list[dict]:
    """Policy reason first, then the score's reason codes when the score drove the outcome."""
    reasons = []
    if policy_out["decision"] != "APPROVE":
        reasons.append(
            {
                "source": "policy",
                "code": policy_out["decision_rule"],
                "description": policy_out["decision_description"],
            }
        )
    if policy_out["decision_rule"] in SCORE_DRIVEN_RULES:
        reasons += [
            {"source": "score", "code": r["code"], "description": r["description"]}
            for r in scored["reasons"]
        ]
    amount = policy_out["approved_amount"]
    if policy_out["decision"] == "APPROVE" and amount is not None and amount < requested:
        reasons.append(
            {
                "source": "policy",
                "code": policy_out["limit_rule"],
                "description": f"Amount limited to {policy_out['max_loan_to_income']:.0%} "
                "of annual income for this risk band",
            }
        )
    return reasons


def create_app(
    score_client: httpx.Client | None = None,
    policy: Policy | None = None,
    decision_log: DecisionLog | None = None,
):
    policy = policy or Policy()
    log = decision_log or DecisionLog()
    client = score_client or httpx.Client(
        base_url=os.environ.get("SCORE_API_URL", "http://127.0.0.1:8000"),
        timeout=SCORE_TIMEOUT_S,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        log.open()
        yield
        log.close()
        client.close()

    app = FastAPI(
        title="Credit decision service",
        version=policy.version,
        description="Scores an application and applies the credit policy.",
        lifespan=lifespan,
    )

    @app.post("/decide", response_model=DecisionResponse)
    def decide(request: DecisionRequest):
        started = time.perf_counter()
        previous = log.get(request.application_id)
        if previous is not None:  # same application, same answer
            return previous

        application = request.model_extra or {}
        try:
            scored = client.post(
                "/score", json=application, headers={"X-Request-ID": request.application_id}
            )
        except httpx.HTTPError as error:
            raise HTTPException(503, f"scoring API unavailable: {type(error).__name__}") from None
        if scored.status_code == 422:
            return JSONResponse(status_code=422, content=scored.json())
        if scored.status_code != 200:
            raise HTTPException(503, f"scoring API returned {scored.status_code}")
        scored = scored.json()

        arm = assign_arm(request.application_id, policy.challenger_share)
        context = application | {
            "emp_length": request.emp_length,
            "score": scored["score"],
            "band": scored["band"],
            "arm": arm,
        }
        out = policy.evaluate(context)
        decided_at = datetime.now(UTC)
        requested = float(application["loan_amnt"])
        response = DecisionResponse(
            application_id=request.application_id,
            decision=out["decision"],
            requested_amount=requested,
            approved_amount=out["approved_amount"] if out["decision"] == "APPROVE" else None,
            limit=out["limit"],
            reasons=build_reasons(out, scored, requested),
            score=scored["score"],
            pd=scored["pd"],
            band=scored["band"],
            arm=arm,
            model_version=scored["model_version"],
            policy_version=policy.version,
            received_at=request.received_at or decided_at,
            decided_at=decided_at,
            trace=Trace(
                knockout=out["knockout"],
                decision_rule=out["decision_rule"],
                limit_rule=out["limit_rule"],
                max_loan_to_income=out["max_loan_to_income"],
                score_points=scored["points"],
            ),
        )
        body = response.model_dump(mode="json")
        record = {
            "application_id": response.application_id,
            "received_at": response.received_at,
            "decided_at": decided_at,
            "arm": arm,
            "decision": response.decision,
            "decision_rule": out["decision_rule"],
            "limit_rule": out["limit_rule"],
            "requested_amount": requested,
            "approved_amount": response.approved_amount,
            "score": response.score,
            "pd": response.pd,
            "band": response.band,
            "model_version": response.model_version,
            "policy_version": policy.version,
            "policy_sha256": policy.sha256,
            "latency_ms": (time.perf_counter() - started) * 1000,
            "application": application | {"emp_length": request.emp_length},
            "response": body,
        }
        try:
            inserted = log.save(record)
        except Exception as error:
            raise HTTPException(503, "decision could not be recorded; no decision given") from error
        if not inserted:  # a concurrent request for the same application won the race
            return log.get(request.application_id)
        return body

    @app.get("/health")
    def health():
        try:
            score_ok = client.get("/health").status_code == 200
        except httpx.HTTPError:
            score_ok = False
        database = log.status()
        healthy = score_ok and database in ("ok", "memory")
        body = {
            "status": "ok" if healthy else "degraded",
            "policy_version": policy.version,
            "scoring_api": "ok" if score_ok else "error",
            "database": database,
        }
        return JSONResponse(status_code=200 if healthy else 503, content=body)

    @app.get("/policy-info")
    def policy_info():
        return policy.manifest

    return app


def app():
    """Factory for ``uvicorn --factory decision_service.app:app``."""
    return create_app()
