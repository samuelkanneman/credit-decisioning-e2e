"""Data contract for the modelling dataset, generated from ``bounds.py``.

The same table backs the scoring API's request schema, so a value the model
never saw in training cannot be scored silently.
"""

import pandera.pandas as pa

from credit_score.bounds import CATEGORICAL, NUMERIC
from credit_score.config import SPLITS, TARGET
from credit_score.features import FEATURES


def _column(name: str) -> pa.Column:
    if name in CATEGORICAL:
        return pa.Column(str, pa.Check.isin(list(CATEGORICAL[name])))
    spec = NUMERIC[name]
    checks = [pa.Check.ge(spec.low)] + ([pa.Check.le(spec.high)] if spec.high is not None else [])
    if name == "term":
        checks = [pa.Check.isin([36, 60])]
    return pa.Column(float, checks, nullable=spec.nullable, coerce=True)


MODELING_SCHEMA = pa.DataFrameSchema(
    {
        "id": pa.Column(str, unique=True),
        "issue_d": pa.Column("datetime64[ns]"),
        "split": pa.Column(str, pa.Check.isin(list(SPLITS))),
        TARGET: pa.Column(bool),
        **{name: _column(name) for name in FEATURES},
    },
    strict=False,  # pricing and outcome columns ride along for the policy analysis
)
