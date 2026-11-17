"""Optimal monotonic binning, information value and variable selection.

Bins are fitted on the development sample only. The out-of-time sample is
only reported next to them, to show whether each variable's distribution and
risk ordering hold in a later period. It decides nothing: selection uses
stability measured inside development.
"""

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from optbinning import BinningProcess

from credit_score.config import TARGET
from credit_score.data.audit import to_markdown
from credit_score.features import CATEGORICAL, FEATURES
from credit_score.metrics import psi

MAX_BINS = 8
MIN_BIN_SIZE = 0.03
MIN_IV = 0.02
MAX_PSI = 0.10
MAX_CORRELATION = 0.70
STABILITY = "PSI within dev"

# Candidates left out on judgement rather than on a statistic.
EXCLUDED: dict[str, str] = {
    "verification_status": "lender action, not a borrower attribute: Lending Club verified "
    "income more often on riskier applications",
}


def fit_binning(dev: pd.DataFrame) -> BinningProcess:
    process = BinningProcess(
        FEATURES,
        categorical_variables=CATEGORICAL,
        max_n_bins=MAX_BINS,
        min_bin_size=MIN_BIN_SIZE,
        binning_fit_params={
            v: {"monotonic_trend": "auto_asc_desc"} for v in FEATURES if v not in CATEGORICAL
        },
    )
    return process.fit(dev[FEATURES], dev[TARGET].astype(int).to_numpy())


def transform(process: BinningProcess, df: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Bin indices or WoE per variable, with missing values in their own bin.

    optbinning's default sends missing values to WoE 0 and bin 0; here they
    get the WoE observed for missing values in development.
    """
    return process.transform(df[FEATURES], metric=metric, metric_missing="empirical")


def bin_labels(process: BinningProcess, variable: str) -> list[str]:
    """Bin labels in the order of the indices returned by ``transform``."""
    bins = process.get_binned_variable(variable).binning_table.build()["Bin"].iloc[:-1]
    return [b if isinstance(b, str) else ", ".join(map(str, b)) for b in bins]


def bin_table(process: BinningProcess, variable: str, samples: dict[str, pd.DataFrame]):
    """Share of loans and bad rate per bin, for each sample."""
    labels = bin_labels(process, variable)
    out = pd.DataFrame(index=pd.Index(range(len(labels)), name="bin"))
    for name, df in samples.items():
        idx = transform(process, df, "indices")[variable]
        grouped = df[TARGET].groupby(idx.to_numpy())
        out[f"% loans {name}"] = grouped.size() / len(df) * 100
        out[f"% bad {name}"] = grouped.mean() * 100
    out.insert(0, "label", labels)
    out = out.dropna(subset=[c for c in out.columns if c.startswith("% loans")], how="all")
    return out.set_index("label").rename_axis(variable).fillna(0.0)


def evaluate(process: BinningProcess, dev: pd.DataFrame, oot: pd.DataFrame) -> pd.DataFrame:
    """One row per candidate: IV, stability, and whether it is selected and why not."""
    summary = process.summary().set_index("name")
    idx_dev = transform(process, dev, "indices")
    idx_oot = transform(process, oot, "indices")
    # Stability used for selection is measured inside development (its last
    # issue year against the earlier ones). The out-of-time figure is reported
    # for information and takes no part in the decision.
    late = (dev["issue_d"].dt.year == dev["issue_d"].dt.year.max()).to_numpy()
    table = pd.DataFrame(
        {
            "bins": summary["n_bins"].astype(int),
            "IV": summary["iv"].astype(float),
            STABILITY: [psi(idx_dev[v][~late], idx_dev[v][late]) for v in summary.index],
            "PSI dev→oot": [psi(idx_dev[v], idx_oot[v]) for v in summary.index],
        },
        index=summary.index,
    ).sort_values("IV", ascending=False)

    woe = transform(process, dev, "woe")
    corr = woe.corr().abs()
    reasons: dict[str, str] = {}
    kept: list[str] = []
    for name, row in table.iterrows():  # strongest first, so the weaker of a pair is dropped
        if name in EXCLUDED:
            reasons[name] = EXCLUDED[name]
        elif row["IV"] < MIN_IV:
            reasons[name] = f"IV below {MIN_IV}"
        elif row[STABILITY] > MAX_PSI:
            reasons[name] = f"PSI above {MAX_PSI}"
        elif kept and corr.loc[name, kept].max() > MAX_CORRELATION:
            other = corr.loc[name, kept].idxmax()
            reasons[name] = f"correlation {corr.loc[name, other]:.2f} with {other}"
        else:
            kept.append(name)
    table["selected"] = ["yes" if n in kept else "no" for n in table.index]
    table["reason if dropped"] = [reasons.get(n, "") for n in table.index]
    table.index.name = "variable"
    return table


def selected(table: pd.DataFrame) -> list[str]:
    return table.index[table["selected"] == "yes"].tolist()


def build_report(process, table: pd.DataFrame, dev: pd.DataFrame, oot: pd.DataFrame) -> str:
    woe = transform(process, dev, "woe")[selected(table)]
    corr = woe.corr().abs().where(~np.eye(len(woe.columns), dtype=bool))
    top = corr.stack().sort_values(ascending=False).iloc[::2].head(5)
    pairs = pd.DataFrame(
        {"correlation": top.to_numpy()},
        index=pd.Index([f"{a} / {b}" for a, b in top.index], name="pair"),
    )
    parts = [
        "# Binning and variable selection",
        "Generated by `make binning`. Do not edit by hand.",
        f"Bins are fitted on development ({len(dev):,} loans) with a monotonic bad-rate "
        f"constraint, at most {MAX_BINS} bins and at least {MIN_BIN_SIZE:.0%} of loans per bin. "
        f"A variable is kept when IV ≥ {MIN_IV}, PSI inside development (last issue year "
        f"against the earlier ones) ≤ {MAX_PSI}, and its WoE correlation with every stronger "
        f"kept variable is ≤ {MAX_CORRELATION}. The out-of-time columns are shown for "
        "information: no bin edge and no selection decision uses them.",
        "## Candidates",
        to_markdown(table),
        "## Highest WoE correlations among selected variables",
        to_markdown(pairs),
        "## Bins of the selected variables",
        "Bad rates are shown for both samples: the ordering has to hold out of time.",
    ]
    for variable in selected(table):
        parts.append(to_markdown(bin_table(process, variable, {"dev": dev, "oot": oot})))
    return "\n\n".join(parts) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/processed"))
    parser.add_argument("--models", type=Path, default=Path("models"))
    parser.add_argument("--report", type=Path, default=Path("docs/binning.md"))
    args = parser.parse_args()

    df = pd.read_parquet(args.data / "modeling.parquet")
    dev, oot = df[df["split"] == "dev"], df[df["split"] == "oot"]
    process = fit_binning(dev)
    table = evaluate(process, dev, oot)

    args.models.mkdir(parents=True, exist_ok=True)
    (args.models / "binning.pkl").write_bytes(pickle.dumps(process))
    (args.models / "selected_features.json").write_text(json.dumps(selected(table), indent=2))
    args.report.write_text(build_report(process, table, dev, oot))
    print(table.drop(columns="reason if dropped").round(4).to_string())
    print(f"{len(selected(table))} variables selected -> {args.report}")


if __name__ == "__main__":
    main()
