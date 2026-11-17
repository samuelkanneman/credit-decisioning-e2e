"""Validation of the scorecard against its baselines and its challenger.

Every model is expressed as a score where higher means lower risk, so the
same metric functions apply to all of them.
"""

import pandas as pd

from credit_score import metrics

# Criteria preregistered in docs/experiments/001-scorecard.md.
MIN_AUC_GAIN_OVER_FICO = 0.05
MAX_AUC_DROP = 0.03
MAX_SCORE_PSI = 0.10
CHALLENGER_GAP_TO_INVESTIGATE = 0.03


def summarise(bad: pd.Series, score: pd.Series) -> dict:
    return {
        "loans": int(len(bad)),
        "bads": int(bad.sum()),
        "bad rate %": float(bad.mean() * 100),
        "AUC": metrics.auc(bad, score),
        "Gini": metrics.gini(bad, score),
        "KS deciles %": metrics.ks_deciles(bad, score),
    }


def summary_table(samples: dict[str, pd.DataFrame], scores: dict[str, dict], target: str):
    """One row per model and sample. ``scores[model][sample]`` is a score series."""
    rows = {
        (model, sample): summarise(samples[sample][target], by_sample[sample])
        for model, by_sample in scores.items()
        for sample in samples
    }
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis(["model", "sample"])


def decile_report(bad: pd.Series, score: pd.Series, pd_bad: pd.Series) -> pd.DataFrame:
    """Decile table with the predicted bad rate next to the observed one."""
    table = metrics.decile_table(bad, score, pd_bad)
    out = pd.DataFrame(
        {
            "min score": table["min_score"],
            "max score": table["max_score"],
            "loans": table["loans"],
            "bads": table["bads"].astype(int),
            "observed bad %": table["bad_rate"] * 100,
            "predicted bad %": table["predicted_rate"] * 100,
        }
    )
    out["observed / predicted"] = out["observed bad %"] / out["predicted bad %"]
    return out


def is_monotonic(deciles: pd.DataFrame) -> bool:
    """Bad rate never rises as the score rises (decile 1 is the riskiest)."""
    return bool(deciles["observed bad %"].is_monotonic_decreasing)


def band_report(bad: pd.Series, bands: pd.Series, pd_bad: pd.Series) -> pd.DataFrame:
    grouped = pd.DataFrame({"bad": bad, "pd": pd_bad}).groupby(bands.to_numpy())
    out = pd.DataFrame(
        {
            "loans": grouped.size(),
            "% of loans": grouped.size() / len(bad) * 100,
            "observed bad %": grouped["bad"].mean() * 100,
            "predicted bad %": grouped["pd"].mean() * 100,
        }
    )
    return out.rename_axis("band")


def check_criteria(
    summary: pd.DataFrame, score_psi: float, oot_deciles, coefficients
) -> pd.DataFrame:
    """Evaluate the five preregistered criteria. Returns one row per criterion."""
    card_dev = summary.loc[("scorecard", "dev"), "AUC"]
    card_oot = summary.loc[("scorecard", "oot"), "AUC"]
    fico_oot = summary.loc[("fico", "oot"), "AUC"]
    betas = coefficients.drop(index="const")["coefficient"]
    rows = [
        (
            "1. AUC gain over FICO alone, out-of-time",
            f"≥ {MIN_AUC_GAIN_OVER_FICO}",
            f"{card_oot - fico_oot:.4f}",
            card_oot - fico_oot >= MIN_AUC_GAIN_OVER_FICO,
        ),
        (
            "2. AUC drop, development to out-of-time",
            f"≤ {MAX_AUC_DROP}",
            f"{card_dev - card_oot:.4f}",
            card_dev - card_oot <= MAX_AUC_DROP,
        ),
        (
            "3. Score PSI, development to out-of-time",
            f"< {MAX_SCORE_PSI}",
            f"{score_psi:.4f}",
            score_psi < MAX_SCORE_PSI,
        ),
        (
            "4. Bad rate by decile, out-of-time",
            "monotonic",
            "monotonic" if is_monotonic(oot_deciles) else "inversions",
            is_monotonic(oot_deciles),
        ),
        (
            "5. Coefficients on WoE",
            "same sign",
            "all negative" if (betas < 0).all() else "mixed",
            bool((betas < 0).all()),
        ),
    ]
    out = pd.DataFrame(rows, columns=["criterion", "threshold", "observed", "met"])
    out["met"] = out["met"].map({True: "yes", False: "no"})
    return out.set_index("criterion")
