import json
import shutil

import pytest

from credit_score import dataset, promotion, registry
from credit_score.data import prepare, synthetic


@pytest.fixture(scope="module")
def modeling():
    return dataset.build(prepare.clean_accepted(synthetic.accepted(30_000).astype("string")))


@pytest.fixture()
def workspace(tmp_path, modeling, monkeypatch):
    """A models directory whose champion is the committed v1, and synthetic data."""
    models, data = tmp_path / "models", tmp_path / "data"
    data.mkdir()
    modeling.to_parquet(data / "modeling.parquet", index=False)
    shutil.copytree(registry.MODELS_DIR / "scorecard-v1", models / "scorecard-v1")
    (models / "registry.json").write_text(
        json.dumps({"champion": "scorecard-v1", "history": [{"version": "scorecard-v1"}]})
    )
    monkeypatch.chdir(tmp_path)
    return models, data


def test_recalibration_keeps_points_and_bands(workspace, modeling):
    models, _ = workspace
    base = promotion.load_card(models, "scorecard-v1")
    card = promotion.recalibrate(base, modeling[modeling["split"] == "oot"], "scorecard-v1.1")
    assert card["variables"] == base["variables"] and card["bands"] == base["bands"]
    assert card["calibration"] != base["calibration"] and card["parent"] == "scorecard-v1"
    fitted = promotion.evaluate(card, modeling[modeling["split"] == "oot"])
    assert fitted["summary"]["observed / predicted"] == pytest.approx(1.0, abs=1e-3)


def test_comparison_criteria(workspace, modeling):
    models, _ = workspace
    base = promotion.load_card(models, "scorecard-v1")
    card = promotion.recalibrate(base, modeling[modeling["split"] == "oot"], "scorecard-v1.1")
    sample = modeling[modeling["split"] == "prod"]
    a, b = promotion.evaluate(base, sample), promotion.evaluate(card, sample)
    criteria = promotion.compare(a, b)
    assert len(criteria) == 6
    assert criteria.iloc[0]["met"] == criteria.iloc[1]["met"] == "yes"  # same ranking
    assert criteria.iloc[5]["observed"].startswith("0 of")  # same score, same decisions
    worse = promotion.compare(
        b, {**b, "summary": {**b["summary"], "AUC": b["summary"]["AUC"] - 0.01}}
    )
    assert worse.iloc[0]["met"] == "no"


def test_export_promote_and_register(workspace, modeling, monkeypatch, tmp_path):
    models, data = workspace
    base = promotion.load_card(models, "scorecard-v1")
    oot = modeling[modeling["split"] == "oot"]
    card = promotion.recalibrate(base, oot, "scorecard-v1.1")
    path = promotion.export(card, models, data, "test", promotion.evaluate(card, oot))
    assert {p.name for p in path.iterdir()} == {"card.json", "metadata.json", "reference.json"}
    assert json.loads((path / "metadata.json").read_text())["parent"] == "scorecard-v1"

    reg = promotion.promote(models, "scorecard-v1.1", "evidence.md")
    assert reg["champion"] == registry.champion(models) == "scorecard-v1.1"
    assert [h["version"] for h in reg["history"]] == ["scorecard-v1", "scorecard-v1.1"]

    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_path / 'mlflow.db'}")
    promotion.register_mlflow(models, ["scorecard-v1", "scorecard-v1.1"], "scorecard-v1.1")
    promotion.register_mlflow(models, ["scorecard-v1", "scorecard-v1.1"], "scorecard-v1.1")
    from mlflow.tracking import MlflowClient

    client = MlflowClient()
    versions = client.search_model_versions("name = 'credit-scorecard'")
    assert len(versions) == 2  # registering again adds nothing
    champion = client.get_model_version_by_alias("credit-scorecard", "champion")
    assert champion.tags["card_version"] == "scorecard-v1.1"
