import json

import pandas as pd
import pytest

from credit_score.data import download, prepare, synthetic
from credit_score.data.schema import ACCEPTED_COLUMNS, Role, names


@pytest.fixture(scope="module")
def processed(tmp_path_factory):
    raw = tmp_path_factory.mktemp("raw")
    out = tmp_path_factory.mktemp("processed")
    synthetic.accepted(3000).to_csv(raw / "accepted_synthetic.csv", index=False)
    synthetic.rejected(1000).to_csv(raw / "rejected_synthetic.csv", index=False)
    # The real file ends with footer rows that are not loans.
    with (raw / "accepted_synthetic.csv").open("a") as fh:
        fh.write("Total amount funded in policy code 1: 123" + "," * (len(ACCEPTED_COLUMNS) - 1))
    counts = prepare.prepare(raw, out)
    return (
        counts,
        pd.read_parquet(out / "accepted.parquet"),
        pd.read_parquet(out / "rejected.parquet"),
    )


def test_footer_rows_are_dropped(processed):
    counts, accepted, rejected = processed
    assert counts == {"accepted": 3000, "rejected": 1000}
    assert len(accepted) == 3000 and len(rejected) == 1000


def test_accepted_has_only_whitelisted_typed_columns(processed):
    _, accepted, _ = processed
    assert list(accepted.columns) == names()
    for col in ACCEPTED_COLUMNS:
        dtype = accepted[col.name].dtype
        if col.kind == "num":
            assert pd.api.types.is_numeric_dtype(dtype), col.name
        elif col.kind == "date":
            assert pd.api.types.is_datetime64_any_dtype(dtype), col.name


def test_raw_text_formats_are_parsed(processed):
    _, accepted, rejected = processed
    assert set(accepted["term"].unique()) == {36, 60}
    assert accepted["issue_d"].between("2013-01-01", "2018-12-31").all()
    assert accepted["emp_length"].dropna().between(0, 10).all()
    assert (accepted["emp_length"] == 0).any()  # "< 1 year"
    assert rejected["dti"].notna().all()  # "23.50%" -> 23.5


def test_lender_outputs_are_never_features():
    features = set(names(Role.FEATURE))
    assert not features & {"grade", "sub_grade", "int_rate", "installment"}
    assert not features & set(names(Role.OUTCOME))


def test_hash_verification(tmp_path):
    (tmp_path / "a.csv").write_text("x")
    good = download.sha256(tmp_path / "a.csv")
    assert download.verify(tmp_path, {"files": {"a.csv": good}}, record=False) == []
    assert "mismatch" in download.verify(tmp_path, {"files": {"a.csv": "0" * 64}}, False)[0]
    assert "no hash" in download.verify(tmp_path, {"files": {"a.csv": None}}, False)[0]
    assert "not found" in download.verify(tmp_path, {"files": {"b.csv": good}}, False)[0]
    manifest = {"files": {"a.csv": None}}
    assert download.verify(tmp_path, manifest, record=True) == []
    assert manifest["files"]["a.csv"] == good


def test_manifest_is_well_formed():
    manifest = json.loads(download.MANIFEST.read_text())
    assert manifest["dataset"] and manifest["files"]


def test_proxy_bad_uses_months_on_book():
    from credit_score.data import audit

    df = pd.DataFrame(
        {
            "issue_d": pd.to_datetime(["2016-01-01"] * 4),
            "last_pymnt_d": pd.to_datetime(["2016-09-01", "2016-10-01", None, "2016-03-01"]),
            "loan_status": ["Charged Off", "Charged Off", "Charged Off", "Fully Paid"],
        }
    )
    out = audit.add_proxy_bad(df)
    # last payment at month 8 -> 90 dpd at month 12: inside the window; month 9 is outside
    assert out["bad_12m"].tolist() == [True, False, True, False]
    assert out["bad_18m"].tolist() == [True, True, True, False]


def test_audit_report_builds(processed):
    from credit_score.data import audit

    _, accepted, rejected = processed
    report = audit.build_report(accepted, rejected)
    assert "## Cohorts by issue year" in report and "| 2016 |" in report
