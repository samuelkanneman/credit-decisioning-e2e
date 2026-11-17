"""Gradient-boosting challenger on the raw selected variables.

It exists to measure what the scorecard gives up by being a points table.
Hyperparameters are chosen by cross-validation inside development, with
folds grouped by issue quarter so no quarter is on both sides of a fold.
"""

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

SEED = 42
NUM_LEAVES_GRID = (7, 15, 31)
MAX_ROUNDS = 600
EARLY_STOPPING = 40
BASE_PARAMS = {
    "objective": "binary",
    "metric": "auc",
    "learning_rate": 0.1,
    "min_child_samples": 500,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "seed": SEED,
    "deterministic": True,
    "force_row_wise": True,
    "verbose": -1,
}


def monotone_constraints(card_variables: list[dict], variables: list[str]) -> list[int]:
    """Direction of risk per variable, taken from the WoE trend of its bins.

    WoE rising with the variable means risk falls, so the constraint on the
    probability of bad is -1. Categorical variables are unconstrained.
    """
    trend = {}
    for spec in card_variables:
        if spec["type"] != "categorical" and len(spec["woe"]) > 1:
            trend[spec["name"]] = -1 if spec["woe"][-1] > spec["woe"][0] else 1
    return [trend.get(v, 0) for v in variables]


def _matrix(df: pd.DataFrame, variables: list[str], categories: dict[str, list]) -> pd.DataFrame:
    x = df[variables].copy()
    for name, levels in categories.items():
        x[name] = pd.Categorical(x[name], categories=levels)
    return x


def fit(dev: pd.DataFrame, variables: list[str], target: str, constraints: list[int]) -> dict:
    categories = {
        v: sorted(dev[v].dropna().unique())
        for v in variables
        if dev[v].dtype == object or pd.api.types.is_string_dtype(dev[v])
    }
    x, y = _matrix(dev, variables, categories), dev[target].astype(int)
    quarter = dev["issue_d"].dt.to_period("Q").astype(str)
    n_folds = min(4, quarter.nunique())
    folds = list(GroupKFold(n_splits=n_folds).split(x, y, groups=quarter))
    data = lgb.Dataset(x, y, free_raw_data=False)

    search = []
    for num_leaves in NUM_LEAVES_GRID:
        params = {**BASE_PARAMS, "num_leaves": num_leaves, "monotone_constraints": constraints}
        cv = lgb.cv(
            params,
            data,
            num_boost_round=MAX_ROUNDS,
            folds=folds,
            callbacks=[lgb.early_stopping(EARLY_STOPPING, verbose=False)],
        )
        aucs = cv["valid auc-mean"]
        search.append({"num_leaves": num_leaves, "rounds": len(aucs), "cv_auc": float(aucs[-1])})
    best = max(search, key=lambda r: r["cv_auc"])
    params = {**BASE_PARAMS, "num_leaves": best["num_leaves"], "monotone_constraints": constraints}
    booster = lgb.train(params, data, num_boost_round=best["rounds"])
    return {
        "booster": booster,
        "variables": variables,
        "categories": categories,
        "search": search,
        "best": best,
    }


def predict(model: dict, df: pd.DataFrame) -> pd.Series:
    x = _matrix(df, model["variables"], model["categories"])
    return pd.Series(model["booster"].predict(x), index=df.index)


def importance(model: dict, df: pd.DataFrame, sample: int = 50_000) -> pd.Series:
    """Mean absolute contribution of each variable to the log-odds (tree SHAP)."""
    part = df.sample(min(sample, len(df)), random_state=SEED)
    x = _matrix(part, model["variables"], model["categories"])
    contrib = model["booster"].predict(x, pred_contrib=True)[:, :-1]
    return pd.Series(np.abs(contrib).mean(axis=0), index=model["variables"]).sort_values(
        ascending=False
    )
