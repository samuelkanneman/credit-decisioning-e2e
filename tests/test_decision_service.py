"""Decision service wired to the real scoring API in process, with an in-memory log."""

import json

import httpx
import pytest
from decision_service.app import create_app
from decision_service.store import MemoryDecisionLog
from fastapi.testclient import TestClient
from score_api.app import create_app as create_score_app
from score_api.store import RequestLog

from credit_score import registry
from credit_score.policy_engine import CHALLENGER, assign_arm

MODEL_DIR = registry.champion_dir()
GOOD = {
    "acc_open_past_24mths": 2,
    "fico": 740,
    "mths_since_recent_inq": None,
    "mo_sin_rcnt_tl": 20,
    "inq_last_6mths": 0,
    "dti": 9.0,
    "mo_sin_old_rev_tl_op": 240,
    "bc_open_to_buy": 25000,
    "purpose": "credit_card",
    "tot_hi_cred_lim": 300000,
    "loan_amnt": 10000,
    "annual_inc": 90000,
    "home_ownership": "MORTGAGE",
    "mths_since_recent_bc": 30,
    "term": 36,
    "emp_length": 6,
}
WEAK = GOOD | {
    "acc_open_past_24mths": 8,
    "fico": 670,
    "mths_since_recent_inq": 1,
    "mo_sin_rcnt_tl": 2,
    "inq_last_6mths": 2,
    "dti": 30.0,
    "mo_sin_old_rev_tl_op": 60,
    "bc_open_to_buy": 500,
    "purpose": "other",
    "tot_hi_cred_lim": 30000,
    "loan_amnt": 20000,
    "annual_inc": 45000,
    "home_ownership": "RENT",
    "mths_since_recent_bc": 3,
    "term": 60,
}


def champion_id(prefix: str) -> str:
    """An application ID that hashes to the champion arm."""
    return next(
        f"{prefix}-{i}" for i in range(1000) if assign_arm(f"{prefix}-{i}", 0.1) != CHALLENGER
    )


@pytest.fixture()
def service():
    score_client = TestClient(create_score_app(MODEL_DIR, RequestLog(database_url=None)))
    log = MemoryDecisionLog()
    with score_client, TestClient(create_app(score_client, decision_log=log)) as client:
        yield client, log


def test_approval_with_full_trace(service):
    client, log = service
    app_id = champion_id("good")
    response = client.post("/decide", json=GOOD | {"application_id": app_id})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "APPROVE" and body["approved_amount"] == 10_000
    assert body["reasons"] == [] and body["arm"] == "champion"
    assert body["trace"]["decision_rule"] == "S3" and body["trace"]["limit_rule"] == "L1"
    assert body["model_version"] == registry.champion() and body["policy_version"] == "policy-v1"
    assert body["score"] == sum(body["trace"]["score_points"].values())
    record = log.rows[app_id]
    assert record["decision"] == "APPROVE" and record["application"]["emp_length"] == 6
    assert record["policy_sha256"] and record["response"] == body


def test_large_request_is_approved_for_the_limit(service):
    client, _ = service
    body = client.post(
        "/decide",
        json=GOOD | {"application_id": champion_id("big"), "loan_amnt": 40000, "annual_inc": 60000},
    ).json()
    assert body["decision"] == "APPROVE" and body["approved_amount"] == body["limit"] == 24000
    assert body["reasons"][-1]["code"] == "L1"


def test_score_driven_rejection_lists_score_reasons(service):
    client, _ = service
    body = client.post("/decide", json=WEAK | {"application_id": champion_id("weak")}).json()
    assert body["decision"] == "REJECT" and body["trace"]["decision_rule"] == "S1"
    assert body["approved_amount"] is None
    assert body["reasons"][0] == {
        "source": "policy",
        "code": "S1",
        "description": "Score below 580",
    }
    assert [r["source"] for r in body["reasons"][1:]] == ["score"] * (len(body["reasons"]) - 1)
    assert len(body["reasons"]) > 1


def test_knockout_gives_only_the_knockout_reason(service):
    client, _ = service
    body = client.post(
        "/decide", json=GOOD | {"application_id": champion_id("ko"), "acc_open_past_24mths": 11}
    ).json()
    assert body["decision"] == "REJECT" and body["trace"]["knockout"] == "K2"
    assert [r["code"] for r in body["reasons"]] == ["K2"]


def test_missing_employment_goes_to_review(service):
    client, _ = service
    body = client.post(
        "/decide", json=GOOD | {"application_id": champion_id("emp"), "emp_length": None}
    ).json()
    assert body["decision"] == "REVIEW" and body["reasons"][0]["code"] == "R1"
    assert body["approved_amount"] is None and body["limit"] is not None


def test_same_application_gets_the_same_answer(service):
    client, log = service
    payload = GOOD | {"application_id": "repeat-1"}
    first = client.post("/decide", json=payload).json()
    second = client.post("/decide", json=payload | {"fico": 660}).json()
    assert first == second and len(log.rows) == 1


def test_arm_follows_the_hash(service):
    client, _ = service
    ids = [f"arm-{i}" for i in range(200)]
    arms = {i: client.post("/decide", json=GOOD | {"application_id": i}).json()["arm"] for i in ids}
    assert arms == {i: assign_arm(i, 0.1) for i in ids}
    assert "challenger" in arms.values()


def test_received_at_is_kept(service):
    client, log = service
    body = client.post(
        "/decide",
        json=GOOD | {"application_id": "dated-1", "received_at": "2017-03-15T10:00:00Z"},
    ).json()
    assert body["received_at"].startswith("2017-03-15")
    assert log.rows["dated-1"]["received_at"].year == 2017


@pytest.mark.parametrize(
    "change",
    [
        {"fico": 900},
        {"purpose": "yacht"},
        {"emp_length": 15},
        {"application_id": "bad id!"},
        {"received_at": "2017-03-15T10:00:00"},
    ],
)
def test_invalid_applications_are_rejected_and_not_recorded(service, change):
    client, log = service
    response = client.post("/decide", json=GOOD | {"application_id": "inv-1"} | change)
    assert response.status_code == 422
    assert log.rows == {}


def test_emp_length_must_be_sent(service):
    client, _ = service
    payload = {k: v for k, v in GOOD.items() if k != "emp_length"} | {"application_id": "e-1"}
    assert client.post("/decide", json=payload).status_code == 422


def test_scoring_api_down_means_no_decision():
    def down(request):
        raise httpx.ConnectError("refused")

    score_client = httpx.Client(base_url="http://score", transport=httpx.MockTransport(down))
    log = MemoryDecisionLog()
    with TestClient(create_app(score_client, decision_log=log)) as client:
        response = client.post("/decide", json=GOOD | {"application_id": "down-1"})
        assert response.status_code == 503 and log.rows == {}
        assert client.get("/health").json()["scoring_api"] == "error"


def test_unrecorded_decision_is_not_given(service):
    client, log = service

    def fail(record):
        raise RuntimeError("disk full")

    log.save = fail
    response = client.post("/decide", json=GOOD | {"application_id": "nolog-1"})
    assert response.status_code == 503


def test_health_and_policy_info(service):
    client, _ = service
    assert client.get("/health").json() == {
        "status": "ok",
        "policy_version": "policy-v1",
        "scoring_api": "ok",
        "database": "memory",
    }
    info = client.get("/policy-info").json()
    manifest = json.loads((MODEL_DIR.parents[1] / "policy" / "manifest.json").read_text())
    assert info == manifest
