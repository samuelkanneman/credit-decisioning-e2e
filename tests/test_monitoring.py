import pandas as pd
import pytest
from conftest import MODEL_DIR

from credit_score import metrics, monitoring


@pytest.fixture(scope="module")
def calibrated(frames_factory):
    return frames_factory()


def test_psi_from_shares():
    assert metrics.psi_from_shares([0.5, 0.5], [0.5, 0.5]) == pytest.approx(0)
    assert metrics.psi_from_shares({"a": 0.5, "b": 0.5}, {"a": 0.8, "b": 0.2}) == pytest.approx(
        metrics.psi(pd.Series(list("ab") * 50), pd.Series(["a"] * 80 + ["b"] * 20))
    )
    assert metrics.psi_from_shares({"a": 1.0}, {"b": 1.0}) > 10  # disjoint bins: large, finite


def test_performance_waits_for_maturity(calibrated):
    assert monitoring.performance(calibrated["decisions"], calibrated["outcomes"], "2018-02")[
        "booked"
    ].empty
    first = monitoring.performance(calibrated["decisions"], calibrated["outcomes"], "2018-03")
    assert list(first["booked"].index) == ["2017-01"]
    every = monitoring.performance(calibrated["decisions"], calibrated["outcomes"], "2018-06")
    assert len(every["booked"]) == 4


def test_only_booked_loans_count_in_production_metrics(calibrated):
    perf = monitoring.performance(calibrated["decisions"], calibrated["outcomes"], "2018-06")
    dec = calibrated["decisions"]
    approved = (dec["decision"] == "APPROVE").groupby(dec["month"]).sum()
    assert perf["booked"]["loans"].tolist() == approved.tolist()
    assert (perf["everyone"]["loans"] == 1500).all()


def test_a_calibrated_model_raises_no_alert(calibrated):
    result = monitoring.run(calibrated, "2018-06", MODEL_DIR)
    perf_alerts = result["alerts"][result["alerts"]["area"] == "performance"]
    assert "observed / predicted" not in set(perf_alerts["indicator"])


def test_underpredicted_risk_asks_for_recalibration(frames_factory):
    frames = frames_factory(n=4000, bad_multiplier=2.0, seed=1)
    result = monitoring.run(frames, "2018-06", MODEL_DIR)
    calibration = result["alerts"][result["alerts"]["indicator"] == "observed / predicted"]
    assert len(calibration) >= 2
    assert result["alerts"]["indicator"].str.startswith("observed / predicted, band").any()
    assert result["recommendation"]["action"] == "recalibrate"


def test_input_shift_is_flagged_on_that_month_only(frames_factory):
    frames = frames_factory(shift={"2017-03": {"fico": 660.0, "acc_open_past_24mths": 9.0}})
    result = monitoring.run(frames, "2017-04", MODEL_DIR)
    variables = result["stability"]["variables"]
    assert variables.loc["2017-03", "fico"] > monitoring.PSI_INVESTIGATE
    assert variables.loc["2017-04", "fico"] < variables.loc["2017-03", "fico"] / 5
    flagged = result["alerts"][result["alerts"]["indicator"] == "PSI fico"]
    assert "2017-03" in set(flagged["month"])
    assert result["recommendation"]["action"] == "retrain"  # several variables above 0.25


def test_rule_rate_change_is_flagged(frames_factory):
    frames = frames_factory(shift={"2017-03": {"acc_open_past_24mths": 12.0}})
    result = monitoring.run(frames, "2017-04", MODEL_DIR)
    rules = result["alerts"][result["alerts"]["indicator"] == "rule K2 rate change"]
    assert {"2017-03", "2017-04"} <= set(rules["month"])  # jump in, then back down


def test_indicators_use_nothing_after_the_as_of_month(calibrated):
    result = monitoring.run(calibrated, "2017-02", MODEL_DIR)
    assert list(result["stability"]["monthly"].index) == ["2017-01", "2017-02"]
    assert result["performance"]["booked"].empty


def test_vintages_respect_months_on_book_and_booking_lag(calibrated):
    vint = monitoring.vintages(calibrated["decisions"], calibrated["outcomes"], "2017-12")
    assert vint.loc["2017-01"].dropna().index.max() == 11 - monitoring.BOOKING_LAG + 0
    assert vint.loc["2017-04"].dropna().index.max() == 8 - monitoring.BOOKING_LAG
    row = vint.loc["2017-01"].dropna()
    assert row.is_monotonic_increasing


def test_recommendation_rules():
    def alerts(rows):
        return pd.DataFrame(
            rows, columns=["month", "area", "indicator", "value", "threshold", "level", "message"]
        )

    ks = [(f"2017-0{m}", "performance", "KS drop (booked) pp", 6, 3, "alert", "") for m in (1, 2)]
    assert monitoring.recommendation(alerts(ks))["action"] == "retrain"
    gap = [
        ("2017-01", "performance", "KS drop (booked) pp", 6, 3, "alert", ""),
        ("2017-03", "performance", "KS drop (booked) pp", 6, 3, "alert", ""),
    ]
    assert monitoring.recommendation(alerts(gap))["action"] == "monitor"
    assert monitoring.recommendation(alerts([]))["action"] == "none"


def test_report_and_parquet_roundtrip(calibrated, tmp_path):
    monitoring.save_directory(calibrated, tmp_path)
    again = monitoring.load_directory(tmp_path)
    pd.testing.assert_frame_equal(again["decisions"], calibrated["decisions"], check_dtype=False)
    report = monitoring.build_report(monitoring.run(again, "2018-06", MODEL_DIR))
    assert report.startswith("# Monitoring report") and "## Vintages" in report


def test_metrics_do_not_depend_on_row_order(calibrated):
    shuffled = {**calibrated, "decisions": calibrated["decisions"].sample(frac=1, random_state=3)}
    a = monitoring.run(monitoring.prepare(calibrated), "2018-06", MODEL_DIR)
    b = monitoring.run(monitoring.prepare(shuffled), "2018-06", MODEL_DIR)
    pd.testing.assert_frame_equal(a["performance"]["bands"], b["performance"]["bands"])
    pd.testing.assert_frame_equal(a["performance"]["booked"], b["performance"]["booked"])
