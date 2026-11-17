"""Generate a synthetic dataset in the raw Lending Club format.

It exists so tests, CI and the demo stack run without Kaggle credentials and
without redistributing real data. Values are random draws with a mild,
made-up relationship between risk drivers and default: good enough to exercise
the pipeline, meaningless for any conclusion about credit risk.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from credit_score.data.schema import ACCEPTED_COLUMNS, REJECTED_RENAME

STATES = ["CA", "TX", "NY", "FL", "IL", "OH", "GA", "WA"]
PURPOSES = ["debt_consolidation", "credit_card", "home_improvement", "major_purchase", "other"]
EMP_LENGTHS = ["< 1 year", "1 year", "3 years", "5 years", "8 years", "10+ years", None]


def _months(rng: np.random.Generator, start: str, end: str, n: int) -> pd.Series:
    months = pd.period_range(start, end, freq="M")
    return pd.Series(months[rng.integers(0, len(months), n)])


def accepted(rows: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    fico = (rng.normal(695, 32, rows).clip(660, 845) // 5 * 5).astype(int)
    dti = rng.gamma(4.0, 4.5, rows).clip(0, 60).round(2)
    inq = rng.poisson(0.7, rows)
    issue = _months(rng, "2013-01", "2018-12", rows)
    term = rng.choice([36, 60], rows, p=[0.72, 0.28])

    logit = -1.9 - 0.016 * (fico - 695) + 0.03 * (dti - 18) + 0.25 * inq + 0.4 * (term == 60)
    bad = rng.random(rows) < 1 / (1 + np.exp(-logit))
    # Loans issued close to the snapshot date have not had time to resolve.
    age = (pd.Period("2019-03", freq="M") - issue).map(lambda d: d.n).to_numpy()
    resolved = rng.random(rows) < np.clip(age / term, 0.05, 1.0)
    status = np.where(resolved, np.where(bad, "Charged Off", "Fully Paid"), "Current")
    months_paid = np.minimum(np.where(bad, rng.integers(1, 24, rows), term), np.maximum(age, 1))

    grade_idx = np.clip(((760 - fico) // 18) + rng.integers(-1, 2, rows), 0, 6)
    amount = (rng.lognormal(9.4, 0.6, rows).clip(1000, 40000) // 25 * 25).astype(int)
    rate = (6.0 + 3.2 * grade_idx + rng.normal(0, 0.6, rows)).clip(5.3, 31).round(2)
    installment = (amount * (rate / 1200) / (1 - (1 + rate / 1200) ** -term)).round(2)

    df = pd.DataFrame(
        {
            "id": np.arange(1, rows + 1),
            "issue_d": issue.dt.strftime("%b-%Y"),
            "loan_amnt": amount,
            "term": [f" {t} months" for t in term],
            "purpose": rng.choice(PURPOSES, rows, p=[0.55, 0.22, 0.08, 0.05, 0.10]),
            "application_type": rng.choice(["Individual", "Joint App"], rows, p=[0.95, 0.05]),
            "emp_length": rng.choice(np.array(EMP_LENGTHS, dtype=object), rows),
            "home_ownership": rng.choice(["RENT", "MORTGAGE", "OWN"], rows, p=[0.4, 0.5, 0.1]),
            "annual_inc": rng.lognormal(11.1, 0.5, rows).round(0),
            "verification_status": rng.choice(
                ["Verified", "Source Verified", "Not Verified"], rows
            ),
            "addr_state": rng.choice(STATES, rows),
            "dti": dti,
            "fico_range_low": fico,
            "fico_range_high": fico + 4,
            "earliest_cr_line": _months(rng, "1985-01", "2012-12", rows).dt.strftime("%b-%Y"),
            "delinq_2yrs": rng.poisson(0.3, rows),
            "inq_last_6mths": inq,
            "mths_since_last_delinq": np.where(
                rng.random(rows) < 0.5, np.nan, rng.integers(1, 80, rows)
            ),
            "open_acc": rng.poisson(11, rows) + 1,
            "total_acc": rng.poisson(24, rows) + 2,
            "pub_rec": rng.poisson(0.2, rows),
            "pub_rec_bankruptcies": rng.poisson(0.1, rows),
            "revol_bal": rng.lognormal(9.2, 0.9, rows).round(0),
            "revol_util": (rng.beta(2.2, 2.2, rows) * 100).round(1),
            "mort_acc": rng.poisson(1.5, rows),
            "int_rate": rate,
            "installment": installment,
            "grade": np.array(list("ABCDEFG"))[grade_idx],
            "sub_grade": [
                f"{'ABCDEFG'[g]}{n}"
                for g, n in zip(grade_idx, rng.integers(1, 6, rows), strict=True)
            ],
            "loan_status": status,
            "last_pymnt_d": (issue + months_paid).dt.strftime("%b-%Y"),
            "total_pymnt": (installment * months_paid).round(2),
            "recoveries": np.where(status == "Charged Off", (amount * 0.08).round(2), 0.0),
        }
    )
    return df[[c.name for c in ACCEPTED_COLUMNS]]


def rejected(rows: int, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.to_datetime("2013-01-01") + pd.to_timedelta(rng.integers(0, 2190, rows), unit="D")
    df = pd.DataFrame(
        {
            "Amount Requested": (rng.lognormal(9.2, 0.8, rows).clip(500, 40000)).round(0),
            "Application Date": dates.strftime("%Y-%m-%d"),
            "Loan Title": rng.choice(PURPOSES, rows),
            "Risk_Score": np.where(rng.random(rows) < 0.3, np.nan, rng.normal(630, 60, rows)).round(
                0
            ),
            "Debt-To-Income Ratio": [f"{v:.2f}%" for v in rng.gamma(3.0, 12.0, rows)],
            "Zip Code": [f"{z:03d}xx" for z in rng.integers(0, 999, rows)],
            "State": rng.choice(STATES, rows),
            "Employment Length": rng.choice(np.array(EMP_LENGTHS, dtype=object), rows),
            "Policy Code": 0,
        }
    )
    assert set(REJECTED_RENAME) <= set(df.columns)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/raw"))
    parser.add_argument("--rows", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    accepted(args.rows, args.seed).to_csv(args.out / "accepted_synthetic.csv", index=False)
    rejected(args.rows * 2, args.seed + 1).to_csv(args.out / "rejected_synthetic.csv", index=False)
    print(f"Synthetic raw files written to {args.out}")


if __name__ == "__main__":
    main()
