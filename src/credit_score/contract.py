"""Data contract for the modelling dataset.

The same ranges will back the scoring API's input schema in Phase 2, so a
value the model never saw in training cannot be scored silently.
"""

import pandera.pandas as pa

from credit_score.config import SPLITS, TARGET
from credit_score.features import DTI_MAX, HOME_OWNERSHIP, REVOL_UTIL_MAX


def _num(lo: float, hi: float | None = None, nullable: bool = False) -> pa.Column:
    checks = [pa.Check.ge(lo)] + ([pa.Check.le(hi)] if hi is not None else [])
    return pa.Column(float, checks, nullable=nullable, coerce=True)


BUREAU_DETAIL = [
    "acc_open_past_24mths",
    "mths_since_recent_inq",
    "mo_sin_rcnt_tl",
    "mo_sin_old_rev_tl_op",
    "num_tl_op_past_12m",
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

MODELING_SCHEMA = pa.DataFrameSchema(
    {
        "id": pa.Column(str, unique=True),
        "issue_d": pa.Column("datetime64[ns]"),
        "split": pa.Column(str, pa.Check.isin(list(SPLITS))),
        TARGET: pa.Column(bool),
        # application
        "loan_amnt": _num(500, 40_000),
        "term": pa.Column(float, pa.Check.isin([36, 60]), coerce=True),
        "emp_length": _num(0, 10, nullable=True),
        "annual_inc": _num(1, nullable=True),
        "dti": _num(0, DTI_MAX, nullable=True),
        "loan_to_income": _num(0, nullable=True),
        "purpose": pa.Column(str),
        "home_ownership": pa.Column(str, pa.Check.isin([*HOME_OWNERSHIP, "OTHER"])),
        "verification_status": pa.Column(str),
        # bureau
        "fico": _num(300, 850),
        "credit_history_months": _num(0, nullable=True),
        "delinq_2yrs": _num(0, nullable=True),
        "inq_last_6mths": _num(0, nullable=True),
        "mths_since_last_delinq": _num(0, nullable=True),
        "open_acc": _num(0, nullable=True),
        "total_acc": _num(0, nullable=True),
        "pub_rec": _num(0, nullable=True),
        "pub_rec_bankruptcies": _num(0, nullable=True),
        "revol_bal": _num(0, nullable=True),
        "revol_util": _num(0, REVOL_UTIL_MAX, nullable=True),
        "bc_util": _num(0, REVOL_UTIL_MAX, nullable=True),
        "percent_bc_gt_75": _num(0, 100, nullable=True),
        "mort_acc": _num(0, nullable=True),
        **{name: _num(0, nullable=True) for name in BUREAU_DETAIL},
    },
    strict=False,  # pricing and outcome columns ride along for the policy analysis
)
