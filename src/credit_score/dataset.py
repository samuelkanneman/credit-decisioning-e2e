"""Build the modelling dataset: population, target, split and features.

Population (ADR-002): individual applications issued in the split years.
Pricing, legacy grade and outcome columns are kept next to the features for
the policy and profitability analysis; they are never model inputs.
"""

import argparse
from pathlib import Path

import pandas as pd

from credit_score.config import SPLITS, TARGET, TARGET_WINDOW_MONTHS
from credit_score.contract import MODELING_SCHEMA
from credit_score.data.schema import Role, names
from credit_score.features import FEATURES, build_features
from credit_score.target import bad_within

KEYS = ["id", "issue_d", "split", TARGET]
PASSTHROUGH = names(Role.ECONOMICS, Role.LEGACY_POLICY, Role.OUTCOME)


def assign_split(issue_d: pd.Series) -> pd.Series:
    year = issue_d.dt.year
    split = pd.Series(pd.NA, index=issue_d.index, dtype="string")
    for name, (first, last) in SPLITS.items():
        split = split.mask(year.between(first, last), name)
    return split


def build(accepted: pd.DataFrame) -> pd.DataFrame:
    df = accepted.assign(split=assign_split(accepted["issue_d"]))
    df = df[df["split"].notna() & (df["application_type"] == "Individual")]
    out = pd.concat(
        [
            df[["id", "issue_d"]],
            df["split"].astype(str),
            bad_within(df, TARGET_WINDOW_MONTHS).rename(TARGET),
            build_features(df),
            df[PASSTHROUGH],
        ],
        axis=1,
    ).reset_index(drop=True)
    return MODELING_SCHEMA.validate(out)


def summary(df: pd.DataFrame) -> pd.DataFrame:
    grouped = df.groupby("split", sort=False)
    out = pd.DataFrame(
        {
            "loans": grouped.size(),
            "bads": grouped[TARGET].sum(),
            "bad_rate_%": (grouped[TARGET].mean() * 100).round(2),
        }
    )
    return out.reindex(list(SPLITS))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/processed"))
    args = parser.parse_args()
    df = build(pd.read_parquet(args.data / "accepted.parquet"))
    df.to_parquet(args.data / "modeling.parquet", index=False)
    print(summary(df).to_string())
    print(f"{len(FEATURES)} candidate features -> {args.data / 'modeling.parquet'}")


if __name__ == "__main__":
    main()
