import json

import numpy as np
import pandas as pd
import pytest

from credit_score import policy_analysis as pa
from credit_score.config import TARGET


def _loans(n=4000, seed=0):
    rng = np.random.default_rng(seed)
    score = rng.normal(595, 20, n).round()
    charged_off = rng.random(n) < 1 / (1 + np.exp((score - 560) / 15))
    amount = rng.choice([5000.0, 10000.0, 20000.0], n)
    df = pd.DataFrame(
        {
            "score": score,
            "band": np.where(score >= 610, "A", np.where(score >= 597, "B", "C")),
            "charged_off": charged_off,
            TARGET: charged_off & (rng.random(n) < 0.3),
            "loan_amnt": amount,
            "total_pymnt": np.where(charged_off, amount * 0.6, amount * 1.15),
            "annual_inc": 60_000.0,
            "loan_to_income": amount / 60_000,
            "fico": rng.choice([655.0, 700.0, 740.0], n, p=[0.05, 0.6, 0.35]),
            "dti": rng.uniform(0, 40, n),
            "emp_length": rng.choice([np.nan, 5.0], n, p=[0.1, 0.9]),
            "acc_open_past_24mths": rng.poisson(4, n).astype(float),
            "inq_last_6mths": rng.poisson(1, n).astype(float),
            "purpose": "other",
            "sub_grade": rng.choice(["A1", "B3", "D2"], n),
        }
    )
    df["profit"] = df["total_pymnt"] - df["loan_amnt"] * (1 + pa.FUNDING_COST)
    p = df["charged_off"].mean()
    df["w_stress"] = np.where(df["charged_off"], pa.STRESS, (1 - pa.STRESS * p) / (1 - p))
    return df


def test_economics_arithmetic():
    df = _loans().head(4).copy()
    df["loan_amnt"], df["profit"] = 1000.0, [100.0, 100.0, -500.0, 100.0]
    df["charged_off"], df[TARGET] = [False, False, True, False], [False, False, True, False]
    out = pa.economics(df.head(3), total=4)
    assert out["approved %"] == 75
    assert out["charged off %"] == pytest.approx(100 / 3)
    assert out["margin % of lent"] == pytest.approx(-300 / 3000 * 100)
    assert out["profit per 1,000 applicants, $k"] == pytest.approx(-300 / 4)


def test_stress_keeps_population_and_raises_charge_offs():
    df = _loans()
    assert df["w_stress"].sum() == pytest.approx(len(df))
    base, stress = pa.economics(df, len(df)), pa.economics(df, len(df), "w_stress")
    assert stress["charged off %"] == pytest.approx(base["charged off %"] * pa.STRESS)
    assert stress["margin % of lent"] < base["margin % of lent"]


def test_cutoff_curve_and_best_cutoff():
    df = _loans()
    curve = pa.cutoff_curve(df)
    approved = curve.drop(index="approve all")["approved %"]
    assert approved.is_monotonic_decreasing and curve.loc["approve all", "approved %"] == 100
    best = pa.best_cutoff(curve)
    assert best in pa.CUTOFFS
    assert pa.best_cutoff(pa.cutoff_curve(df, "w_stress")) >= best  # stress never loosens


def test_swap_groups_partition_the_population():
    df = _loans()
    summary, detail = pa.swap_set(df, 595)
    assert detail["% of applicants"].sum() == pytest.approx(100)
    swap = detail["% of applicants"]
    assert swap.iloc[1] == pytest.approx(swap.iloc[2])  # same approval rate on both sides
    assert summary["approved %"].nunique() == 1


def test_policy_order_of_precedence():
    df = pd.DataFrame(
        {
            "score": [600, 600, 600, 600, 585, 570, 600],
            "fico": [700, 650, 700, 700, 700, 700, 650],
            "acc_open_past_24mths": [2, 2, 12, 2, 2, 2, 12],
            "emp_length": [5, 5, 5, np.nan, 5, 5, np.nan],
        }
    )
    assert pa.apply_policy(df).tolist() == [
        "APPROVE",
        "REJECT",  # bureau score outside the model's range
        "REJECT",  # too many new accounts
        "REVIEW",  # would be approved, employment not reported
        "REVIEW",  # between the cut-offs
        "REJECT",  # below the lower cut-off
        "REJECT",  # a knock-out wins over a review reason
    ]
    impact = pa.policy_impact(_loans())
    assert impact["% of applicants"].sum() == pytest.approx(100)


def test_proposed_policy_is_internally_consistent():
    assert pa.PROPOSED["reject_below"] < pa.PROPOSED["approve_from"]
    assert json.dumps(pa.PROPOSED)  # serialisable: it becomes the rules engine's input
    limits = pa.PROPOSED["max_loan_to_income"]
    assert limits["A"] >= limits["B"] >= limits["C"] >= limits["D"]
