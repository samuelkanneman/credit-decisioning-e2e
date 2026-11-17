import json

import numpy as np
import pandas as pd
import pytest

from credit_score import policy_analysis, registry, scoring
from credit_score.bounds import DERIVED

MODEL_DIR = registry.champion_dir()


def _value_in_bin(label: str, spec: dict, low: float, rng) -> object:
    """A value that falls in the named bin of a scorecard variable."""
    if label == "missing":
        return None
    position = int(label.split()[1]) if label.startswith("bin") else 0
    if spec["type"] == "categorical":
        return spec["categories"][position][0]
    edges = [-np.inf, *spec["splits"], np.inf]
    lo, hi = edges[position], edges[position + 1]
    lo = max(lo, low)
    hi = lo + 10 if not np.isfinite(hi) else hi
    return float(rng.uniform(lo, hi))


def make_frames(
    months=("2017-01", "2017-02", "2017-03", "2017-04"),
    n=1500,
    bad_multiplier=1.0,
    shift: dict | None = None,
    seed=0,
) -> dict[str, pd.DataFrame]:
    """Decision log, outcomes and run log as the stack would record them.

    Each variable is drawn bin by bin from the development reference, so a month
    with no ``shift`` is stable by construction. Outcomes are drawn from the
    model's own PD times ``bad_multiplier``: calibrated when it is 1. ``shift``
    maps a month to input overrides applied to every application of that month.
    """
    from credit_score.bounds import NUMERIC

    card = json.loads((MODEL_DIR / "card.json").read_text())
    reference = json.loads((MODEL_DIR / "reference.json").read_text())
    specs = {spec["name"]: spec for spec in card["variables"]}
    inputs = scoring.required_inputs(card, DERIVED)
    rng = np.random.default_rng(seed)
    rows, outcomes = [], []
    for month in months:
        for i in range(n):
            drawn = {}
            for name, spec in specs.items():
                shares = reference["variables"][name]
                label = rng.choice(
                    list(shares), p=np.array(list(shares.values())) / sum(shares.values())
                )
                low = NUMERIC[name].low if name in NUMERIC else 0
                drawn[name] = _value_in_bin(label, spec, low, rng)
            income = drawn["annual_inc"] or float(rng.lognormal(11.1, 0.4))
            ratio = drawn.pop("loan_to_income") or 0.2
            app = drawn | {
                "annual_inc": drawn["annual_inc"],
                "loan_amnt": float(np.clip(round(ratio * income), 500, 40_000)),
                "emp_length": None if rng.random() < 0.023 else float(rng.integers(0, 11)),
            }
            app["term"] = 60 if app["term"] > 48 else 36
            app["fico"] = max(app["fico"], 660.0)
            app |= (shift or {}).get(month, {})
            scored = scoring.score_application(app, card)
            app_id = f"t-{month}-{i}"
            rows.append(
                {
                    "application_id": app_id,
                    "received_at": pd.Timestamp(f"{month}-15T12:00:00Z"),
                    "arm": "challenger" if i % 10 == 0 else "champion",
                    "score": scored["score"],
                    "pd": scored["pd"],
                    "band": scored["band"],
                    "latency_ms": float(rng.uniform(5, 15)),
                    "model_version": card["version"],
                    "policy_version": "policy-v1",
                    "requested_amount": app["loan_amnt"],
                    "approved_amount": None,
                    "application": {k: app.get(k) for k in [*inputs, "emp_length"]},
                    **{
                        k: app[k]
                        for k in ("fico", "acc_open_past_24mths", "emp_length", "annual_inc")
                    },
                }
            )
            bad = rng.random() < min(1.0, scored["pd"] * bad_multiplier)
            issue = pd.Timestamp(f"{month}-01")
            outcomes.append(
                {
                    "application_id": app_id,
                    "issue_month": issue,
                    "bad_12m": bad,
                    "charged_off": bad,
                    "default_mob": int(rng.integers(4, 13)) if bad else None,
                    "matured_from": issue + pd.DateOffset(months=14),
                }
            )
    dec = pd.DataFrame(rows)
    dec["decision_rule"] = None
    for arm in ("champion", "challenger"):
        mask = dec["arm"] == arm
        dec.loc[mask, "decision_rule"] = policy_analysis.decision_rules(dec[mask], arm=arm)
    dec["decision"] = dec["decision_rule"].map(policy_analysis.RULE_DECISION)
    dec = dec.drop(columns=["fico", "acc_open_past_24mths", "emp_length", "annual_inc"])
    runs = pd.DataFrame(
        {
            "month": list(months),
            "sent": n,
            "ok": n,
            "client_errors": 0,
            "server_errors": 0,
            "seconds": 10.0,
        }
    )
    out = pd.DataFrame(outcomes)
    out["default_mob"] = out["default_mob"].astype("Int64")
    from credit_score.monitoring import prepare

    return prepare({"decisions": dec, "outcomes": out, "runs": runs})


@pytest.fixture(scope="session")
def frames_factory():
    return make_frames
