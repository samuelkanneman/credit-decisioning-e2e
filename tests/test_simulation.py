import json

import pandas as pd
from conftest import MODEL_DIR

from credit_score import dataset, simulation
from credit_score.data import prepare, synthetic


def _modeling():
    return dataset.build(prepare.clean_accepted(synthetic.accepted(30_000).astype("string")))


def test_applications_carry_payload_outcome_and_maturity():
    card = json.loads((MODEL_DIR / "card.json").read_text())
    df = simulation.load_applications(_modeling(), card, fraction=0.5, drift=None)
    assert df["issue_d"].dt.year.eq(2017).all() and df["application_id"].is_unique
    assert (df["received_at"].dt.strftime("%Y-%m") == df["month"]).all()
    assert (df["matured_from"] == df["issue_month"] + pd.DateOffset(months=14)).all()
    assert df.loc[~df["charged_off"], "default_mob"].isna().all()
    assert (df.loc[df["charged_off"], "default_mob"] >= simulation.MONTHS_TO_90DPD).all()
    payload = simulation.payload(next(df.itertuples(index=False)))
    assert {"application_id", "received_at", "emp_length", "fico", "loan_amnt"} <= set(payload)
    assert "loan_to_income" not in payload  # derived by the scoring API
    json.dumps(payload)


def test_broker_drift_adds_riskier_volume_from_july():
    card = json.loads((MODEL_DIR / "card.json").read_text())
    base = simulation.load_applications(_modeling(), card, fraction=0.5, drift=None)
    drift = simulation.load_applications(_modeling(), card, fraction=0.5, drift="broker")
    broker = drift[drift["channel"] == "broker"]
    assert broker["month"].min() >= simulation.DRIFT_START
    assert (broker["fico"] < 690).all() and (broker["acc_open_past_24mths"] >= 6).all()
    assert drift["application_id"].is_unique
    direct = drift[drift["channel"] == "direct"]
    pd.testing.assert_series_equal(
        direct["application_id"].reset_index(drop=True),
        base["application_id"].reset_index(drop=True),
    )
    july = drift[drift["month"] == "2017-07"]
    assert (
        abs(
            (july["channel"] == "broker").sum() / (july["channel"] == "direct").sum()
            - simulation.DRIFT_SHARE
        )
        < 0.05
    )
