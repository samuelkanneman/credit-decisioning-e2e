"""Scorecard: logistic regression on WoE, scaled to integer points.

The fitted model is exported as a plain points table (``card``). Scoring
needs only that table and pandas, not the binning library, so the scoring
API can reproduce training scores exactly.
"""

import numpy as np
import pandas as pd
import statsmodels.api as sm
from optbinning import BinningProcess

from credit_score.binning import transform

PDO = 20.0
BASE_SCORE = 600.0
BASE_ODDS = 50.0  # good:bad odds at the base score
FACTOR = PDO / np.log(2)
OFFSET = BASE_SCORE - FACTOR * np.log(BASE_ODDS)
MAX_P_VALUE = 0.01
BANDS = ("E", "D", "C", "B", "A")  # riskiest to safest, one per development quintile


def fit_logit(woe: pd.DataFrame, bad: pd.Series):
    """Logistic regression of ``bad`` on WoE with backward elimination.

    WoE is positive towards goods, so every coefficient must be negative.
    A variable with the wrong sign or a p-value above ``MAX_P_VALUE`` is
    dropped, one at a time, and the model refitted.
    """
    variables, dropped = list(woe.columns), {}
    while True:
        result = sm.Logit(bad.astype(float), sm.add_constant(woe[variables])).fit(disp=0)
        wrong_sign = [v for v in variables if result.params[v] >= 0]
        weak = [v for v in variables if result.pvalues[v] > MAX_P_VALUE]
        if wrong_sign:
            worst = max(wrong_sign, key=lambda v: result.params[v])
            dropped[worst] = "coefficient with the wrong sign"
        elif weak:
            worst = max(weak, key=lambda v: result.pvalues[v])
            dropped[worst] = f"p-value above {MAX_P_VALUE}"
        else:
            return result, dropped
        if len(variables) == 1:
            raise ValueError("No variable survives the sign and significance checks")
        variables.remove(worst)


def _bins(process: BinningProcess, variable: str) -> dict:
    """Bin definition and WoE of one variable, read from the fitted binning."""
    optb = process.get_binned_variable(variable)
    table = optb.binning_table.build()
    n_bins = len(optb.splits) + (0 if optb.dtype == "categorical" else 1)
    woe = table["WoE"].iloc[:n_bins].astype(float).tolist()
    missing = table[table["Bin"].astype(str) == "Missing"].iloc[0]
    spec = {
        "name": variable,
        "type": optb.dtype,
        "woe": woe,
        # A value never seen missing in development gets a neutral WoE of 0.
        "missing_woe": float(missing["WoE"]) if missing["Count"] > 0 else 0.0,
    }
    if optb.dtype == "categorical":
        spec["categories"] = [[str(c) for c in group] for group in optb.splits]
    else:
        spec["splits"] = [float(s) for s in optb.splits]
    return spec


def build_card(process: BinningProcess, result) -> dict:
    """Turn the fitted regression into a table of integer points per bin.

    score = OFFSET + FACTOR * ln(odds of good), split evenly across variables
    so that each variable's points can be read on their own.
    """
    variables = [v for v in result.params.index if v != "const"]
    base = (OFFSET - FACTOR * result.params["const"]) / len(variables)

    def points(woe: float, beta: float) -> int:
        return int(round(base - FACTOR * beta * woe))

    card = []
    for name in variables:
        spec, beta = _bins(process, name), float(result.params[name])
        spec["coefficient"] = beta
        spec["points"] = [points(w, beta) for w in spec["woe"]]
        spec["missing_points"] = points(spec["missing_woe"], beta)
        if spec["type"] == "categorical":
            spec["other_points"] = points(0.0, beta)  # category unseen in development
        card.append(spec)
    return {
        "scaling": {"pdo": PDO, "base_score": BASE_SCORE, "base_odds": BASE_ODDS},
        "variables": card,
    }


def variable_points(values: pd.Series, spec: dict) -> pd.Series:
    """Points one variable contributes to each row."""
    if spec["type"] == "categorical":
        lookup = {
            c: p for group, p in zip(spec["categories"], spec["points"], strict=True) for c in group
        }
        out = values.astype(object).map(lookup).fillna(spec["other_points"])
    else:
        idx = np.searchsorted(spec["splits"], values.to_numpy(dtype=float), side="right")
        out = pd.Series(np.asarray(spec["points"])[idx], index=values.index)
    return out.where(values.notna(), spec["missing_points"]).astype(int)


def points_frame(df: pd.DataFrame, card: dict) -> pd.DataFrame:
    return pd.DataFrame({s["name"]: variable_points(df[s["name"]], s) for s in card["variables"]})


def score(df: pd.DataFrame, card: dict) -> pd.Series:
    """Total score: higher means lower risk."""
    return points_frame(df, card).sum(axis=1)


def fit_calibration(scores: pd.Series, bad: pd.Series) -> dict:
    """Map score to PD with a one-variable logistic regression (Platt scaling)."""
    result = sm.Logit(bad.astype(float), sm.add_constant(scores.astype(float))).fit(disp=0)
    return {"intercept": float(result.params.iloc[0]), "slope": float(result.params.iloc[1])}


def pd_from_score(scores, calibration: dict) -> pd.Series:
    z = calibration["intercept"] + calibration["slope"] * pd.Series(scores, dtype=float)
    return 1 / (1 + np.exp(-z))


def score_from_pd(pd_bad) -> pd.Series:
    """Put any PD on the scorecard's point scale, to compare models like for like."""
    p = pd.Series(pd_bad, dtype=float).clip(1e-9, 1 - 1e-9)
    return OFFSET + FACTOR * np.log((1 - p) / p)


def fit_bands(scores: pd.Series) -> list[dict]:
    """Risk bands with fixed cut-offs: the quintiles of the development score."""
    cuts = [int(np.floor(scores.quantile(q))) for q in (0.2, 0.4, 0.6, 0.8)]
    lows = [None, *cuts]
    return [{"band": b, "min_score": lo} for b, lo in zip(BANDS, lows, strict=True)]


def band(scores: pd.Series, bands: list[dict]) -> pd.Series:
    cuts = [b["min_score"] for b in bands[1:]]
    idx = np.searchsorted(cuts, scores.to_numpy(), side="right")
    return pd.Series(np.asarray([b["band"] for b in bands])[idx], index=scores.index)


def train(process: BinningProcess, dev: pd.DataFrame, variables: list[str], target: str):
    """Fit everything on development and return the card with its diagnostics."""
    woe = transform(process, dev, "woe")[variables]
    result, dropped = fit_logit(woe, dev[target])
    card = build_card(process, result)
    dev_score = score(dev, card)
    card["calibration"] = fit_calibration(dev_score, dev[target])
    card["bands"] = fit_bands(dev_score)
    coefficients = pd.DataFrame(
        {"coefficient": result.params, "std error": result.bse, "p-value": result.pvalues}
    ).rename_axis("term")
    return card, coefficients, dropped


def points_table(card: dict) -> pd.DataFrame:
    """The scorecard as people read it: one row per variable and bin."""
    rows = []
    for spec in card["variables"]:
        if spec["type"] == "categorical":
            labels = [", ".join(g) for g in spec["categories"]] + ["(other)"]
            pts = [*spec["points"], spec["other_points"]]
        else:
            edges = [-np.inf, *spec["splits"], np.inf]
            labels = [f"[{a:g}, {b:g})" for a, b in zip(edges[:-1], edges[1:], strict=True)]
            pts = list(spec["points"])
        labels.append("Missing")
        pts.append(spec["missing_points"])
        rows += [(spec["name"], label, p) for label, p in zip(labels, pts, strict=True)]
    return pd.DataFrame(rows, columns=["variable", "bin", "points"]).set_index("variable")
