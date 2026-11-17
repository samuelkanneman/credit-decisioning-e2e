"""Discrimination and stability metrics shared by training and monitoring.

Definitions follow the credit-risk profile of data-science-harness
(docs/perfiles/riesgo-crediticio/DEFINICIONES.md, sections 4 to 6) so that
numbers here are comparable with the author's other work.

Orientation: ``score`` arguments are credit scores where higher means better
credit quality. ``bad`` is 1 for a bad loan.
"""

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

PSI_FLOOR = 1e-6
KS_JITTER = 0.01  # in score points
KS_SEED = 42


def _evaluable(bad, score) -> tuple[np.ndarray, np.ndarray]:
    bad, score = np.asarray(bad, dtype=float), np.asarray(score, dtype=float)
    ok = np.isfinite(bad) & np.isfinite(score)
    bad, score = bad[ok], score[ok]
    if len(np.unique(bad)) < 2:
        raise ValueError("AUC and KS are undefined with a single class")
    return bad, score


def auc(bad, score) -> float:
    """AUC as a fraction in [0, 1]; a higher score must mean lower risk."""
    bad, score = _evaluable(bad, score)
    return float(roc_auc_score(bad, -score))


def gini(bad, score) -> float:
    return 2 * auc(bad, score) - 1


def decile_table(bad, score, predicted=None) -> pd.DataFrame:
    """Deciles of the score, decile 1 being the riskiest.

    Ties are broken with a reproducible jitter of ±0.01 score points before
    cutting, so the buckets have equal size even when scores repeat.
    ``predicted`` optionally adds the mean predicted probability of bad.
    """
    frame = pd.DataFrame({"bad": np.asarray(bad, dtype=float), "score": np.asarray(score, float)})
    if predicted is not None:
        frame["predicted"] = np.asarray(predicted, dtype=float)
    frame = frame[np.isfinite(frame["bad"]) & np.isfinite(frame["score"])]
    if frame["bad"].nunique() < 2:
        raise ValueError("AUC and KS are undefined with a single class")
    jitter = np.random.RandomState(KS_SEED).uniform(-KS_JITTER, KS_JITTER, len(frame))
    bucket = pd.qcut(frame["score"].to_numpy() + jitter, q=10, duplicates="drop")
    grouped = frame.groupby(bucket, observed=True)
    table = pd.DataFrame(
        {
            "min_score": grouped["score"].min(),
            "max_score": grouped["score"].max(),
            "loans": grouped.size(),
            "bads": grouped["bad"].sum(),
        }
    )
    if predicted is not None:
        table["predicted_rate"] = grouped["predicted"].mean()
    table = table.sort_values("min_score")
    table.index = pd.RangeIndex(1, len(table) + 1, name="decile")
    table["bad_rate"] = table["bads"] / table["loans"]
    goods = table["loans"] - table["bads"]
    table["cum_bads"] = table["bads"].cumsum() / table["bads"].sum()
    table["cum_goods"] = goods.cumsum() / goods.sum()
    return table


def ks_deciles(bad, score) -> float:
    """Canonical KS: by deciles, in percent."""
    table = decile_table(bad, score)
    return float(100 * (table["cum_bads"] - table["cum_goods"]).abs().max())


def psi(expected: pd.Series, actual: pd.Series) -> float:
    """PSI between two samples of a discrete variable (bin or category labels).

    Shares are floored at 1e-6 without renormalising, so an empty bin on one
    side gives a large but finite contribution.
    """
    e = expected.value_counts(normalize=True)
    a = actual.value_counts(normalize=True)
    e, a = e.align(a, fill_value=0.0)
    e, a = e.clip(lower=PSI_FLOOR), a.clip(lower=PSI_FLOOR)
    return float(((a - e) * np.log(a / e)).sum())


def psi_numeric(reference, new) -> float:
    """PSI of a numeric variable using 10 quantile bins of the reference sample.

    Bin edges come from the reference only, with open ends, and repeated
    edges are dropped. Non-finite values are excluded; report their share
    separately, since dropping them can hide a change in missingness.
    """
    reference = np.asarray(reference, dtype=float)
    new = np.asarray(new, dtype=float)
    reference, new = reference[np.isfinite(reference)], new[np.isfinite(new)]
    if not len(reference) or not len(new):
        raise ValueError("PSI needs two non-empty samples")
    edges = np.unique(np.quantile(reference, np.linspace(0, 1, 11)))
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(reference, edges)[0] / len(reference)
    a = np.histogram(new, edges)[0] / len(new)
    e, a = np.clip(e, PSI_FLOOR, None), np.clip(a, PSI_FLOOR, None)
    return float(((a - e) * np.log(a / e)).sum())
