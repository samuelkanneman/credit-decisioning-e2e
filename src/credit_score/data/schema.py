"""Single source of truth for the columns the project is allowed to read.

The raw Lending Club file has ~150 columns and most of them are only known
after origination. We work from an explicit whitelist: anything not listed
here never reaches the modelling dataset.
"""

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    KEY = "key"  # identifiers and dates used to split and join
    FEATURE = "feature"  # known at application time: candidate model input
    OUTCOME = "outcome"  # known after origination: target construction only
    ECONOMICS = "economics"  # pricing set by the lender: profitability only
    LEGACY_POLICY = "legacy_policy"  # Lending Club's own model output: swap-set baseline only


@dataclass(frozen=True)
class Column:
    name: str
    role: Role
    kind: str  # "num", "cat" or "date"


ACCEPTED_COLUMNS: tuple[Column, ...] = (
    Column("id", Role.KEY, "cat"),
    Column("issue_d", Role.KEY, "date"),
    # application
    Column("loan_amnt", Role.FEATURE, "num"),
    Column("term", Role.FEATURE, "num"),
    Column("purpose", Role.FEATURE, "cat"),
    Column("application_type", Role.FEATURE, "cat"),
    Column("emp_length", Role.FEATURE, "num"),
    Column("home_ownership", Role.FEATURE, "cat"),
    Column("annual_inc", Role.FEATURE, "num"),
    Column("verification_status", Role.FEATURE, "cat"),
    Column("addr_state", Role.FEATURE, "cat"),
    Column("dti", Role.FEATURE, "num"),
    # bureau at application
    Column("fico_range_low", Role.FEATURE, "num"),
    Column("fico_range_high", Role.FEATURE, "num"),
    Column("earliest_cr_line", Role.FEATURE, "date"),
    Column("delinq_2yrs", Role.FEATURE, "num"),
    Column("inq_last_6mths", Role.FEATURE, "num"),
    Column("mths_since_last_delinq", Role.FEATURE, "num"),
    Column("open_acc", Role.FEATURE, "num"),
    Column("total_acc", Role.FEATURE, "num"),
    Column("pub_rec", Role.FEATURE, "num"),
    Column("pub_rec_bankruptcies", Role.FEATURE, "num"),
    Column("revol_bal", Role.FEATURE, "num"),
    Column("revol_util", Role.FEATURE, "num"),
    Column("mort_acc", Role.FEATURE, "num"),
    # lender pricing and legacy risk grade: never model inputs
    Column("int_rate", Role.ECONOMICS, "num"),
    Column("installment", Role.ECONOMICS, "num"),
    Column("grade", Role.LEGACY_POLICY, "cat"),
    Column("sub_grade", Role.LEGACY_POLICY, "cat"),
    # performance
    Column("loan_status", Role.OUTCOME, "cat"),
    Column("last_pymnt_d", Role.OUTCOME, "date"),
    Column("total_pymnt", Role.OUTCOME, "num"),
    Column("recoveries", Role.OUTCOME, "num"),
)

# The rejected-applications file uses different headers; map them to ours.
REJECTED_RENAME: dict[str, str] = {
    "Amount Requested": "loan_amnt",
    "Application Date": "application_d",
    "Loan Title": "purpose",
    "Risk_Score": "risk_score",
    "Debt-To-Income Ratio": "dti",
    "State": "addr_state",
    "Employment Length": "emp_length",
    "Policy Code": "policy_code",
}


def names(*roles: Role) -> list[str]:
    """Column names for the given roles, or all whitelisted names if none given."""
    return [c.name for c in ACCEPTED_COLUMNS if not roles or c.role in roles]
