"""Smoke test against a running API: python services/score_api/smoke_test.py [base_url]

Standard library only, so it runs anywhere the stack does.
"""

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
APPLICATION = {
    "acc_open_past_24mths": 4,
    "fico": 700,
    "mths_since_recent_inq": None,
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
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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
status, info = call("/model-info")
assert status == 200 and info["model_version"] == health["model_version"], info

for i in range(20):
    status, scored = call("/score", APPLICATION | {"fico": 660 + 5 * i})
    assert status == 200, scored
    assert scored["model_version"] == health["model_version"]
    assert scored["score"] == sum(scored["points"].values()) and scored["band"] in "ABCDE"

status, _ = call("/score", APPLICATION | {"fico": 900})
assert status == 422, status
print(f"ok: {health['model_version']}, database {health['database']}, last score {scored['score']}")
