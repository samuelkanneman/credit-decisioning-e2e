"""Smoke test against a running stack: python services/decision_service/smoke_test.py [base_url]

Sends 20 applications that cover every outcome and checks each answer.
Standard library only.
"""

import json
import sys
import urllib.error
import urllib.request
import uuid

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
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
CASES = [
    ({}, "APPROVE"),
    ({"acc_open_past_24mths": 12}, "REJECT"),
    ({"emp_length": None}, "REVIEW"),
    (
        {
            "fico": 670,
            "acc_open_past_24mths": 8,
            "mths_since_recent_inq": 1,
            "mo_sin_rcnt_tl": 2,
            "dti": 30.0,
            "bc_open_to_buy": 500,
            "purpose": "other",
            "term": 60,
            "annual_inc": 45000,
            "loan_amnt": 20000,
            "home_ownership": "RENT",
            "mths_since_recent_bc": 3,
        },
        "REJECT",
    ),
]


def call(path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data, {"Content-Type": "application/json"})
    try:
        with OPENER.open(request, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


status, health = call("/health")
assert status == 200 and health["status"] == "ok", health
run = uuid.uuid4().hex[:8]
for i in range(20):
    change, expected = CASES[i % len(CASES)]
    status, body = call("/decide", GOOD | change | {"application_id": f"smoke-{run}-{i}"})
    assert status == 200, body
    assert body["decision"] == expected, (change, body)
    assert body["policy_version"] == health["policy_version"] and body["model_version"]
    assert (body["approved_amount"] is not None) == (expected == "APPROVE")
status, _ = call("/decide", GOOD | {"application_id": f"smoke-{run}-bad", "fico": 900})
assert status == 422, status
print(f"ok: {health['policy_version']}, 20 decisions recorded")
