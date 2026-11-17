import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from score_api.app import create_app
from score_api.store import RequestLog

from credit_score import registry, scorecard, scoring
from credit_score.bounds import CATEGORICAL, DERIVED, NUMERIC
from credit_score.features import build_features

MODEL_DIR = registry.champion_dir()
CARD = json.loads((MODEL_DIR / "card.json").read_text())
INPUTS = scoring.required_inputs(CARD, DERIVED)

APPLICATION = {
    "acc_open_past_24mths": 4,
    "fico": 700,
    "mths_since_recent_inq": 5,
    "mo_sin_rcnt_tl": 8,
    "inq_last_6mths": 1,
    "dti": 18.5,
    "mo_sin_old_rev_tl_op": 180,
    "bc_open_to_buy": 9000,
    "purpose": "debt_consolidation",
    "tot_hi_cred_lim": 150000,
    "loan_amnt": 12000,
    "annual_inc": 65000,
    "home_ownership": "MORTGAGE",
    "mths_since_recent_bc": 14,
    "term": 36,
}


class MemoryLog(RequestLog):
    def __init__(self):
        super().__init__(database_url=None)
        self.records = []

    def write(self, record):
        self.records.append(record)


@pytest.fixture()
def api():
    log = MemoryLog()
    with TestClient(create_app(MODEL_DIR, log)) as client:
        yield client, log


def random_applications(n: int, seed: int = 0) -> list[dict]:
    """Valid applications that hit bin edges, missing values and every category."""
    rng = np.random.default_rng(seed)
    specs = {s["name"]: s for s in CARD["variables"]}
    rows = []
    for _ in range(n):
        row = {}
        for name in INPUTS:
            if name in CATEGORICAL:
                row[name] = str(rng.choice(CATEGORICAL[name]))
            elif name == "term":
                row[name] = int(rng.choice([36, 60]))
            else:
                bound = NUMERIC[name]
                splits = specs.get(name, {}).get("splits", [])
                roll = rng.random()
                if bound.nullable and roll < 0.1:
                    row[name] = None
                elif splits and roll < 0.4:  # exactly on a bin edge, or just beside it
                    row[name] = float(rng.choice(splits)) + float(rng.choice([-0.5, 0, 0.5]))
                else:
                    high = bound.high if bound.high is not None else 300_000
                    row[name] = float(rng.uniform(bound.low, high))
                if row[name] is not None:
                    row[name] = min(max(row[name], bound.low), bound.high or row[name])
        rows.append(row)
    return rows


def test_card_inputs_are_covered_by_bounds_and_reasons():
    assert set(INPUTS) == set(APPLICATION)
    for name in INPUTS:
        assert name in NUMERIC or name in CATEGORICAL
    for spec in CARD["variables"]:
        assert spec["name"] in scoring.REASONS


def test_runtime_scoring_matches_training_scoring():
    """The pure-Python runtime and the pandas implementation agree on 1,000 applications."""
    apps = random_applications(1000)
    frame = pd.DataFrame(apps).astype({n: float for n in INPUTS if n in NUMERIC})
    frame["loan_to_income"] = frame["loan_amnt"] / frame["annual_inc"]
    expected_points = scorecard.points_frame(frame, CARD)
    expected_score = scorecard.score(frame, CARD)
    expected_pd = scorecard.pd_from_score(expected_score, CARD["calibration"])
    expected_band = scorecard.band(expected_score, CARD["bands"])
    for i, app in enumerate(apps):
        result = scoring.score_application(app, CARD)
        assert result["points"] == expected_points.iloc[i].to_dict()
        assert result["score"] == expected_score.iloc[i]
        assert result["pd"] == pytest.approx(expected_pd.iloc[i], rel=1e-12)
        assert result["band"] == expected_band.iloc[i]


def test_derived_inputs_match_feature_engineering():
    raw = pd.DataFrame(
        {
            "loan_amnt": [10_000.0, 10_000.0],
            "annual_inc": [50_000.0, 0.0],
            "fico_range_low": [700.0, 700.0],
            "issue_d": pd.to_datetime(["2016-01-01"] * 2),
            "earliest_cr_line": pd.to_datetime(["2000-01-01"] * 2),
            "dti": [10.0, 10.0],
            "revol_util": [10.0, 10.0],
            "bc_util": [10.0, 10.0],
            "home_ownership": ["RENT", "RENT"],
        }
    )
    trained = build_features_subset(raw)
    assert scoring.derive({"loan_amnt": 10_000, "annual_inc": 50_000})["loan_to_income"] == (
        pytest.approx(trained.iloc[0])
    )
    assert scoring.derive({"loan_amnt": 10_000, "annual_inc": None})["loan_to_income"] is None
    assert pd.isna(trained.iloc[1])


def build_features_subset(raw: pd.DataFrame) -> pd.Series:
    from credit_score.features import FEATURES

    padded = raw.reindex(columns=[*raw.columns, *[f for f in FEATURES if f not in raw]])
    return build_features(padded)["loan_to_income"]


def test_score_endpoint(api):
    client, log = api
    response = client.post("/score", json=APPLICATION, headers={"X-Request-ID": "req-123"})
    assert response.status_code == 200
    body = response.json()
    expected = scoring.score_application(APPLICATION, CARD)
    assert body["request_id"] == "req-123"
    assert body["model_version"] == CARD["version"]
    assert body["score"] == expected["score"] == sum(body["points"].values())
    assert body["band"] in "ABCDE" and 0 < body["pd"] < 1
    assert 1 <= len(body["reasons"]) <= scoring.MAX_REASONS
    lost = [r["points_lost"] for r in body["reasons"]]
    assert lost == sorted(lost, reverse=True) and min(lost) > 0
    # the request is logged with what went in and what came out
    assert len(log.records) == 1
    record = log.records[0]
    assert record["request_id"] == "req-123" and record["request"] == APPLICATION | {
        k: float(v) for k, v in APPLICATION.items() if isinstance(v, int) and k != "term"
    }
    assert record["response"]["score"] == body["score"] and record["latency_ms"] >= 0


def test_request_id_is_generated_when_absent(api):
    client, _ = api
    first = client.post("/score", json=APPLICATION).json()["request_id"]
    second = client.post("/score", json=APPLICATION).json()["request_id"]
    assert first != second and len(first) == 36


def test_explicit_null_is_scored_as_missing(api):
    client, _ = api
    body = client.post("/score", json=APPLICATION | {"mths_since_recent_inq": None}).json()
    spec = next(s for s in CARD["variables"] if s["name"] == "mths_since_recent_inq")
    assert body["points"]["mths_since_recent_inq"] == spec["missing_points"]


@pytest.mark.parametrize(
    "change",
    [
        {"fico": 900},  # above the valid range
        {"fico": None},  # not nullable
        {"dti": -1},  # the raw file's sentinel is not a value
        {"dti": 999},
        {"term": 48},
        {"purpose": "yacht"},
        {"home_ownership": "ANY"},
        {"loan_amnt": 100},
        {"annual_inc": 0},
        {"grade": "A"},  # unknown field
        {"fico": "seven hundred"},
    ],
)
def test_out_of_contract_input_is_rejected(api, change):
    client, log = api
    assert client.post("/score", json=APPLICATION | change).status_code == 422
    assert log.records == []


def test_omitted_field_is_rejected_even_if_nullable(api):
    client, _ = api
    payload = {k: v for k, v in APPLICATION.items() if k != "mths_since_recent_inq"}
    assert client.post("/score", json=payload).status_code == 422


def test_health_and_model_info(api):
    client, _ = api
    health = client.get("/health").json()
    assert health == {"status": "ok", "model_version": CARD["version"], "database": "disabled"}
    info = client.get("/model-info").json()
    assert info["model_version"] == CARD["version"]
    assert info["inputs"] == INPUTS and len(info["variables"]) == len(CARD["variables"])
    assert 0.5 < info["validation_metrics"]["scorecard_oot_auc"] < 1


def test_reasons_are_empty_at_maximum_points_and_capped_otherwise():
    best = {s["name"]: scoring.max_points(s) for s in CARD["variables"]}
    assert scoring.reasons(best, CARD) == []
    worst = {s["name"]: min(s["points"]) for s in CARD["variables"]}
    reasons = scoring.reasons(worst, CARD)
    assert len(reasons) == scoring.MAX_REASONS
    assert len({r["code"] for r in reasons}) == scoring.MAX_REASONS
