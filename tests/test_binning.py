import numpy as np
import pytest

from credit_score import binning, dataset
from credit_score.config import TARGET
from credit_score.data import prepare, synthetic


@pytest.fixture(scope="module")
def fitted():
    accepted = prepare.clean_accepted(synthetic.accepted(30_000).astype("string"))
    df = dataset.build(accepted)
    dev, oot = df[df["split"] == "dev"], df[df["split"] == "oot"]
    process = binning.fit_binning(dev)
    return process, dev, oot


def test_missing_values_get_their_own_bin(fitted):
    process, dev, oot = fitted
    table = binning.bin_table(process, "emp_length", {"dev": dev, "oot": oot})
    assert "Missing" in table.index
    assert table.loc["Missing", "% loans dev"] == pytest.approx(
        dev["emp_length"].isna().mean() * 100
    )
    assert table["% loans dev"].sum() == pytest.approx(100.0)


def test_numeric_bins_are_monotonic_in_bad_rate(fitted):
    process, dev, _ = fitted
    table = binning.bin_table(process, "fico", {"dev": dev})
    rates = table.loc[[i for i in table.index if i != "Missing"], "% bad dev"].to_numpy()
    assert len(rates) >= 2
    assert (np.diff(rates) <= 0).all() or (np.diff(rates) >= 0).all()


def test_selection_rules(fitted):
    process, dev, oot = fitted
    table = binning.evaluate(process, dev, oot)
    chosen = binning.selected(table)
    assert "fico" in chosen  # the synthetic default rate depends on it
    assert "verification_status" not in chosen
    assert (table.loc[chosen, "IV"] >= binning.MIN_IV).all()
    assert (table.loc[chosen, binning.STABILITY] <= binning.MAX_PSI).all()
    assert table.loc[table["selected"] == "no", "reason if dropped"].ne("").all()
    report = binning.build_report(process, table, dev, oot)
    assert "## Candidates" in report and TARGET not in chosen
