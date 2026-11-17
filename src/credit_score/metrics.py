"""Stability and discrimination metrics shared by training and monitoring."""

import numpy as np
import pandas as pd


def psi(expected: pd.Series, actual: pd.Series, floor: float = 1e-4) -> float:
    """Population stability index between two samples of a discrete variable.

    Both inputs hold category or bin labels. Shares are floored so an empty
    bin on one side gives a large but finite contribution.
    """
    e = expected.value_counts(normalize=True)
    a = actual.value_counts(normalize=True)
    e, a = e.align(a, fill_value=0.0)
    e, a = e.clip(lower=floor), a.clip(lower=floor)
    return float(((a - e) * np.log(a / e)).sum())
