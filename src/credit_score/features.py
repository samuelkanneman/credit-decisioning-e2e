"""Model inputs: the candidate list, derived variables and cleaning rules.

Everything here uses only information available at application time.
"""

import numpy as np
import pandas as pd

from credit_score.target import months_between

NUMERIC = [
    "loan_amnt",
    "term",
    "emp_length",
    "annual_inc",
    "dti",
    "fico",
    "credit_history_months",
    "loan_to_income",
    "delinq_2yrs",
    "inq_last_6mths",
    "mths_since_last_delinq",
    "open_acc",
    "total_acc",
    "pub_rec",
    "pub_rec_bankruptcies",
    "revol_bal",
    "revol_util",
    "mort_acc",
    "acc_open_past_24mths",
    "mths_since_recent_inq",
    "mo_sin_rcnt_tl",
    "mo_sin_old_rev_tl_op",
    "num_tl_op_past_12m",
    "bc_util",
    "percent_bc_gt_75",
    "bc_open_to_buy",
    "total_bc_limit",
    "tot_cur_bal",
    "avg_cur_bal",
    "tot_hi_cred_lim",
    "total_bal_ex_mort",
    "num_actv_rev_tl",
    "num_rev_tl_bal_gt_0",
    "mths_since_recent_bc",
    "total_rev_hi_lim",
]
CATEGORICAL = ["purpose", "home_ownership", "verification_status"]
FEATURES = NUMERIC + CATEGORICAL

DTI_MAX = 100.0  # the raw file uses -1 and 999 as sentinels
REVOL_UTIL_MAX = 200.0
UTILISATION = ("revol_util", "bc_util")  # percentages; a few raw values are absurd
HOME_OWNERSHIP = ("MORTGAGE", "RENT", "OWN")


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Return the ``FEATURES`` columns computed from whitelisted raw columns.

    Values that cannot be right are set to missing rather than dropped: the
    scorecard treats missing as its own bin, and so will the scoring API.
    """
    out = pd.DataFrame(index=df.index)
    for name in NUMERIC + CATEGORICAL:
        if name in df.columns:
            out[name] = df[name]
    out["fico"] = df["fico_range_low"]
    out["credit_history_months"] = months_between(df["issue_d"], df["earliest_cr_line"])
    out["dti"] = df["dti"].where(df["dti"].between(0, DTI_MAX))
    for name in UTILISATION:
        out[name] = df[name].where(df[name].between(0, REVOL_UTIL_MAX))
    income = df["annual_inc"].where(df["annual_inc"] > 0)
    out["annual_inc"] = income
    out["loan_to_income"] = (df["loan_amnt"] / income).replace([np.inf, -np.inf], np.nan)
    out["home_ownership"] = df["home_ownership"].where(
        df["home_ownership"].isin(HOME_OWNERSHIP), "OTHER"
    )
    return out[FEATURES]
