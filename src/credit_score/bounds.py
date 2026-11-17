"""Valid ranges of every model input: the single source for both contracts.

The training data contract (pandera) and the scoring API's request schema
(pydantic) are both generated from this table, so a value the model was
never allowed to see in training cannot be scored in production.

Pure Python on purpose: the API imports it without the training stack.
"""

from typing import NamedTuple

DTI_MAX = 100.0  # the raw file uses -1 and 999 as sentinels
UTILISATION_MAX = 200.0
HOME_OWNERSHIP = ("MORTGAGE", "RENT", "OWN", "OTHER")
PURPOSES = (
    "car",
    "credit_card",
    "debt_consolidation",
    "educational",
    "home_improvement",
    "house",
    "major_purchase",
    "medical",
    "moving",
    "other",
    "renewable_energy",
    "small_business",
    "vacation",
    "wedding",
)


class Numeric(NamedTuple):
    low: float
    high: float | None = None
    nullable: bool = True
    description: str = ""


NUMERIC: dict[str, Numeric] = {
    # application
    "loan_amnt": Numeric(500, 40_000, False, "Requested loan amount, USD"),
    "term": Numeric(36, 60, False, "Loan term in months: 36 or 60"),
    "emp_length": Numeric(0, 10, True, "Years in current job, capped at 10"),
    "annual_inc": Numeric(1, None, True, "Self-reported annual income, USD"),
    "dti": Numeric(0, DTI_MAX, True, "Debt-to-income ratio excluding this loan, percent"),
    "loan_to_income": Numeric(0, None, True, "Loan amount divided by annual income"),
    # bureau
    "fico": Numeric(300, 850, False, "FICO score at application (low end of the range)"),
    "credit_history_months": Numeric(0, None, True, "Months since the earliest credit line"),
    "delinq_2yrs": Numeric(0, None, True, "Delinquencies of 30+ days in the past 2 years"),
    "inq_last_6mths": Numeric(0, None, True, "Credit inquiries in the past 6 months"),
    "mths_since_last_delinq": Numeric(0, None, True, "Months since the last delinquency"),
    "open_acc": Numeric(0, None, True, "Open credit lines"),
    "total_acc": Numeric(0, None, True, "Total credit lines"),
    "pub_rec": Numeric(0, None, True, "Derogatory public records"),
    "pub_rec_bankruptcies": Numeric(0, None, True, "Public record bankruptcies"),
    "revol_bal": Numeric(0, None, True, "Total revolving balance, USD"),
    "revol_util": Numeric(0, UTILISATION_MAX, True, "Revolving utilisation, percent"),
    "bc_util": Numeric(0, UTILISATION_MAX, True, "Bankcard utilisation, percent"),
    "percent_bc_gt_75": Numeric(0, 100, True, "Percent of bankcards above 75% of limit"),
    "mort_acc": Numeric(0, None, True, "Mortgage accounts"),
    "acc_open_past_24mths": Numeric(0, None, True, "Accounts opened in the past 24 months"),
    "mths_since_recent_inq": Numeric(0, None, True, "Months since the most recent inquiry"),
    "mo_sin_rcnt_tl": Numeric(0, None, True, "Months since the most recent account opened"),
    "mo_sin_old_rev_tl_op": Numeric(0, None, True, "Months since the oldest revolving account"),
    "num_tl_op_past_12m": Numeric(0, None, True, "Accounts opened in the past 12 months"),
    "bc_open_to_buy": Numeric(0, None, True, "Available credit on bankcards, USD"),
    "total_bc_limit": Numeric(0, None, True, "Total bankcard limit, USD"),
    "tot_cur_bal": Numeric(0, None, True, "Total current balance of all accounts, USD"),
    "avg_cur_bal": Numeric(0, None, True, "Average current balance per account, USD"),
    "tot_hi_cred_lim": Numeric(0, None, True, "Total high credit or credit limit, USD"),
    "total_bal_ex_mort": Numeric(0, None, True, "Total balance excluding mortgage, USD"),
    "num_actv_rev_tl": Numeric(0, None, True, "Active revolving accounts"),
    "num_rev_tl_bal_gt_0": Numeric(0, None, True, "Revolving accounts with a balance"),
    "mths_since_recent_bc": Numeric(0, None, True, "Months since the most recent bankcard"),
    "total_rev_hi_lim": Numeric(0, None, True, "Total revolving credit limit, USD"),
}

CATEGORICAL: dict[str, tuple[str, ...]] = {
    "purpose": PURPOSES,
    "home_ownership": HOME_OWNERSHIP,
    "verification_status": ("Verified", "Source Verified", "Not Verified"),
}

# Model inputs the API computes itself instead of asking the caller for them.
DERIVED = {"loan_to_income": ("loan_amnt", "annual_inc")}
