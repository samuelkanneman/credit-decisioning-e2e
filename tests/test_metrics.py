import numpy as np
import pandas as pd
import pytest

from credit_score import metrics


def _sample(n=20_000, seed=0):
    rng = np.random.default_rng(seed)
    score = rng.normal(600, 40, n).round()
    bad = rng.random(n) < 1 / (1 + np.exp((score - 540) / 25))
    return bad.astype(int), score


def test_auc_orientation_and_gini():
    bad, score = _sample()
    value = metrics.auc(bad, score)
    assert 0.6 < value < 1.0  # higher score, lower risk
    assert metrics.auc(bad, -score) == pytest.approx(1 - value)
    assert metrics.gini(bad, score) == pytest.approx(2 * value - 1)


def test_perfect_and_useless_scores():
    bad = np.array([1] * 500 + [0] * 500)
    assert metrics.auc(bad, np.arange(1000)) == pytest.approx(1.0)
    assert metrics.ks_deciles(bad, np.arange(1000)) == pytest.approx(100.0)
    assert metrics.ks_deciles(bad, np.zeros(1000)) < 10  # ties broken by jitter only


def test_decile_table_shape_and_order():
    bad, score = _sample()
    table = metrics.decile_table(bad, score)
    assert len(table) == 10 and table["loans"].sum() == len(bad)
    assert table["loans"].max() - table["loans"].min() <= 1  # jitter gives equal buckets
    assert table["min_score"].is_monotonic_increasing
    assert table.loc[1, "bad_rate"] > table.loc[10, "bad_rate"]  # decile 1 is the riskiest
    assert metrics.ks_deciles(bad, score) == pytest.approx(
        100 * (table["cum_bads"] - table["cum_goods"]).abs().max()
    )


def test_missing_scores_are_excluded_and_single_class_fails():
    bad, score = _sample(2000)
    with_gap = score.copy()
    with_gap[:100] = np.nan
    assert metrics.auc(bad, with_gap) == pytest.approx(metrics.auc(bad[100:], score[100:]))
    with pytest.raises(ValueError):
        metrics.auc(np.zeros(10), np.arange(10))


def test_psi_numeric():
    rng = np.random.default_rng(1)
    ref = rng.normal(600, 40, 50_000)
    assert metrics.psi_numeric(ref, rng.normal(600, 40, 50_000)) < 0.01
    assert metrics.psi_numeric(ref, rng.normal(630, 40, 50_000)) > 0.25
    assert metrics.psi_numeric(ref, ref) == pytest.approx(0.0, abs=1e-12)
    assert np.isfinite(metrics.psi_numeric(ref, np.full(100, 1000.0)))
    with pytest.raises(ValueError):
        metrics.psi_numeric(ref, [np.nan])


def test_psi_discrete_matches_formula():
    base = pd.Series(["a"] * 50 + ["b"] * 50)
    shifted = pd.Series(["a"] * 80 + ["b"] * 20)
    expected = (0.8 - 0.5) * np.log(0.8 / 0.5) + (0.2 - 0.5) * np.log(0.2 / 0.5)
    assert metrics.psi(base, shifted) == pytest.approx(expected)


def test_auc_matches_sklearn_including_ties():
    from sklearn.metrics import roc_auc_score

    bad, score = _sample(5000, seed=4)
    rounded = (score / 10).round()  # heavy ties
    assert metrics.auc(bad, score) == pytest.approx(roc_auc_score(bad, -score), abs=1e-12)
    assert metrics.auc(bad, rounded) == pytest.approx(roc_auc_score(bad, -rounded), abs=1e-12)
