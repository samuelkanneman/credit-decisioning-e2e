"""Performance target (see docs/adr/002-target-and-split.md).

The data is a single snapshot with the final loan status and the month of the
last payment, not a monthly delinquency history. "Bad within W months" is
approximated: a charged-off loan whose last payment came at month ``m`` on
book reached 90 days past due at about ``m + 4``.
"""

import pandas as pd

MONTHS_TO_90DPD = 4  # last payment at month m -> first missed at m+1 -> 90 dpd at m+4


def months_between(later: pd.Series, earlier: pd.Series) -> pd.Series:
    return (later.dt.year - earlier.dt.year) * 12 + (later.dt.month - earlier.dt.month)


def charged_off(df: pd.DataFrame) -> pd.Series:
    return df["loan_status"].str.contains("Charged Off|Default", na=False).astype(bool)


def bad_within(df: pd.DataFrame, window_months: int) -> pd.Series:
    """True for loans that reached 90+ days past due within the window (proxy)."""
    # A charged-off loan with no payment date never paid at all.
    paid_months = months_between(df["last_pymnt_d"], df["issue_d"]).fillna(0)
    return charged_off(df) & (paid_months + MONTHS_TO_90DPD <= window_months).astype(bool)
