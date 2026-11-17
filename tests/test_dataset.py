import pandas as pd
import pandera.errors
import pytest

from credit_score import dataset
from credit_score.config import SPLITS, TARGET
from credit_score.data import prepare, synthetic
from credit_score.data.schema import Role, names
from credit_score.features import FEATURES, build_features


@pytest.fixture(scope="module")
def accepted():
    return prepare.clean_accepted(synthetic.accepted(4000).astype("string"))


def test_split_follows_issue_year():
    dates = pd.Series(pd.to_datetime(["2012-12-01", "2013-01-01", "2016-06-01", "2017-12-01"]))
    assert dataset.assign_split(dates).tolist() == [pd.NA, "dev", "oot", "prod"]
    assert dataset.assign_split(pd.Series(pd.to_datetime(["2018-01-01"]))).isna().all()


def test_population_and_columns(accepted):
    df = dataset.build(accepted)
    assert set(df["split"]) == set(SPLITS)
    assert df["issue_d"].dt.year.between(2013, 2017).all()
    assert (
        len(df)
        == (
            accepted["issue_d"].dt.year.between(2013, 2017)
            & (accepted["application_type"] == "Individual")
        ).sum()
    )
    assert df[TARGET].dtype == bool and 0 < df[TARGET].mean() < 0.5
    assert list(dataset.summary(df).index) == list(SPLITS)


def test_features_exclude_pricing_grade_and_outcomes():
    forbidden = set(names(Role.ECONOMICS, Role.LEGACY_POLICY, Role.OUTCOME))
    assert not forbidden & set(FEATURES)


def test_impossible_values_become_missing(accepted):
    raw = accepted.head(4).copy()
    raw["dti"] = [-1.0, 999.0, 25.0, 0.0]
    raw["annual_inc"] = [0.0, 50_000.0, 50_000.0, 50_000.0]
    raw["revol_util"] = [892.3, 50.0, 50.0, 50.0]
    raw["home_ownership"] = ["ANY", "NONE", "RENT", "OWN"]
    out = build_features(raw)
    assert out["dti"].isna().tolist() == [True, True, False, False]
    assert out["annual_inc"].isna().tolist() == [True, False, False, False]
    assert out["loan_to_income"].isna().tolist() == [True, False, False, False]
    assert out["revol_util"].isna().tolist() == [True, False, False, False]
    assert out["home_ownership"].tolist() == ["OTHER", "OTHER", "RENT", "OWN"]


def test_contract_rejects_out_of_range_values(accepted):
    df = dataset.build(accepted)
    broken = df.copy()
    broken.loc[0, "fico"] = 900
    with pytest.raises(pandera.errors.SchemaError):
        dataset.MODELING_SCHEMA.validate(broken)
