import json
import pickle

import numpy as np
import pandas as pd
import pytest

from credit_score import binning, dataset, scorecard, train, validation
from credit_score.config import TARGET
from credit_score.data import prepare, synthetic


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    """A full synthetic run: dataset, binning, selection, on disk like `make` leaves it."""
    data, models = tmp_path_factory.mktemp("data"), tmp_path_factory.mktemp("models")
    accepted = prepare.clean_accepted(synthetic.accepted(40_000).astype("string"))
    df = dataset.build(accepted)
    df.to_parquet(data / "modeling.parquet", index=False)
    dev, oot = df[df["split"] == "dev"], df[df["split"] == "oot"]
    process = binning.fit_binning(dev)
    selected = binning.selected(binning.evaluate(process, dev, oot))
    (models / "binning.pkl").write_bytes(pickle.dumps(process))
    (models / "selected_features.json").write_text(json.dumps(selected))
    return data, models, process, dev, selected


@pytest.fixture(scope="module")
def trained(workspace):
    _, _, process, dev, selected = workspace
    return scorecard.train(process, dev, selected, TARGET)


def test_scaling_constants():
    # 600 points at 50:1 odds, and 20 more points double the odds
    assert scorecard.OFFSET + scorecard.FACTOR * np.log(50) == pytest.approx(600)
    assert scorecard.OFFSET + scorecard.FACTOR * np.log(100) == pytest.approx(620)


def test_card_reproduces_the_regression_without_the_binning_library(workspace, trained):
    """Points looked up from the exported card equal points computed from optbinning's WoE."""
    _, _, process, dev, _ = workspace
    card, coefficients, _ = trained
    names = [s["name"] for s in card["variables"]]
    woe = binning.transform(process, dev, "woe")[names]
    base = (scorecard.OFFSET - scorecard.FACTOR * coefficients.loc["const", "coefficient"]) / len(
        names
    )
    expected = (base - scorecard.FACTOR * woe * coefficients.loc[names, "coefficient"]).round()
    actual = scorecard.points_frame(dev, card)
    pd.testing.assert_frame_equal(
        actual.astype(float), expected.astype(float), check_names=False, atol=1e-9
    )


def test_coefficients_have_the_right_sign_and_are_significant(trained):
    _, coefficients, _ = trained
    betas = coefficients.drop(index="const")
    assert (betas["coefficient"] < 0).all()
    assert (betas["p-value"] <= scorecard.MAX_P_VALUE).all()


def test_missing_and_unseen_values_get_points(trained):
    card, _, _ = trained
    for spec in card["variables"]:
        if spec["type"] == "categorical":
            values = pd.Series(["never-seen-category", None])
            assert scorecard.variable_points(values, spec).tolist() == [
                spec["other_points"],
                spec["missing_points"],
            ]
        else:
            values = pd.Series([np.nan, -1e12, 1e12])
            assert scorecard.variable_points(values, spec).tolist() == [
                spec["missing_points"],
                spec["points"][0],
                spec["points"][-1],
            ]


def test_numeric_bins_are_closed_on_the_left():
    spec = {"type": "numerical", "splits": [10.0, 20.0], "points": [1, 2, 3], "missing_points": 0}
    values = pd.Series([9.99, 10.0, 19.99, 20.0])
    assert scorecard.variable_points(values, spec).tolist() == [1, 2, 2, 3]


def test_pd_falls_as_score_rises_and_bands_are_ordered(workspace, trained):
    _, _, _, dev, _ = workspace
    card, _, _ = trained
    scores = scorecard.score(dev, card)
    pds = scorecard.pd_from_score(pd.Series([500, 600, 700]), card["calibration"])
    assert pds.is_monotonic_decreasing and pds.between(0, 1).all()
    assert pds.mean() > 0 and scorecard.pd_from_score(scores, card["calibration"]).mean() == (
        pytest.approx(dev[TARGET].mean(), rel=1e-3)
    )
    bands = scorecard.band(scores, card["bands"])
    mean_by_band = scores.groupby(bands.to_numpy()).mean()
    assert list(mean_by_band.sort_values().index) == [b for b in "EDCBA" if b in mean_by_band]
    # a score exactly on a cut-off belongs to the better band
    cut = card["bands"][1]["min_score"]
    assert scorecard.band(pd.Series([cut - 1, cut]), card["bands"]).tolist() == ["E", "D"]


def test_score_from_pd_matches_the_scale():
    assert scorecard.score_from_pd([1 / 51]).iloc[0] == pytest.approx(600)


def test_full_training_run_exports_a_loadable_model(workspace, tmp_path):
    data, models, _, dev, _ = workspace
    out = train.run(data, models)
    assert {"fico", "lc_sub_grade", "scorecard", "lightgbm"} == set(
        out["summary"].index.get_level_values("model")
    )
    assert list(out["criteria"]["met"].isin(["yes", "no"])) == [True] * 5
    assert "## Preregistered criteria" in train.build_report(out)

    artifact = train.export(out, data, models)
    card = json.loads((artifact / "card.json").read_text())
    metadata = json.loads((artifact / "metadata.json").read_text())
    pd.testing.assert_series_equal(scorecard.score(dev, card), scorecard.score(dev, out["card"]))
    assert metadata["model_version"] == card["version"]
    assert len(metadata["data_sha256"]["modeling.parquet"]) == 64


def test_decile_report_and_monotonic_check():
    rng = np.random.default_rng(0)
    score = pd.Series(rng.normal(600, 30, 20_000).round())
    pd_bad = 1 / (1 + np.exp((score - 520) / 25))
    bad = pd.Series(rng.random(len(score)) < pd_bad).astype(int)
    report = validation.decile_report(bad, score, pd_bad)
    assert validation.is_monotonic(report)
    assert report["observed / predicted"].between(0.7, 1.3).all()
    report.loc[5, "observed bad %"] = 99.0
    assert not validation.is_monotonic(report)
