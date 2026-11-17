"""Model lifecycle: build a candidate, compare it with the champion, promote it.

    python -m credit_score.promotion recalibrate --base scorecard-v1 --to scorecard-v1.1
    python -m credit_score.promotion evaluate --candidate scorecard-v1.1 [--promote]

A candidate is compared with the champion on the same loans against the
criteria in docs/model_lifecycle.md. Promotion rewrites models/registry.json,
which every service reads, and records both versions in the MLflow registry
with the ``champion`` alias on the winner.
"""

import argparse
import copy
import hashlib
import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from credit_score import metrics, reference, registry, scorecard
from credit_score.config import TARGET
from credit_score.data.audit import to_markdown
from credit_score.policy_analysis import RULE_DECISION, decision_rules
from credit_score.policy_engine import load_manifest

# General promotion rules (docs/model_lifecycle.md)
MAX_AUC_LOSS = 0.005
MAX_KS_LOSS_PP = 1.0
BAND_RATIO = (0.80, 1.25)
# Experiment 002: a recalibration has no other job than the level
OVERALL_RATIO = (0.90, 1.10)


def load_card(models: Path, version: str) -> dict:
    return json.loads((models / version / "card.json").read_text())


def recalibrate(base: dict, fit: pd.DataFrame, version: str) -> dict:
    """Same points and bands, PD mapping refitted on ``fit``."""
    card = copy.deepcopy(base)
    card["version"] = version
    card["calibration"] = scorecard.fit_calibration(scorecard.score(fit, base), fit[TARGET])
    card["parent"] = base["version"]
    return card


def evaluate(card: dict, df: pd.DataFrame) -> dict:
    score = scorecard.score(df, card)
    pd_bad = scorecard.pd_from_score(score, card["calibration"])
    band = scorecard.band(score, card["bands"])
    bad = df[TARGET].astype(int)
    deciles = metrics.decile_table(bad, score, pd_bad)
    bands = pd.DataFrame(
        {
            "loans": bad.groupby(band).size(),
            "observed %": bad.groupby(band).mean() * 100,
            "predicted %": pd_bad.groupby(band).mean() * 100,
        }
    ).rename_axis("band")
    bands["observed / predicted"] = bands["observed %"] / bands["predicted %"]
    rules = decision_rules(df.assign(score=score))
    return {
        "score": score,
        "decision": rules.map(RULE_DECISION),
        "summary": {
            "loans": len(df),
            "AUC": metrics.auc(bad, score),
            "KS %": metrics.ks_deciles(bad, score),
            "observed %": bad.mean() * 100,
            "predicted %": pd_bad.mean() * 100,
            "observed / predicted": bad.mean() / pd_bad.mean(),
            "decile calibration error": float(
                (deciles["bad_rate"] / deciles["predicted_rate"] - 1).abs().mean()
            ),
        },
        "bands": bands,
        "deciles": deciles,
    }


def compare(champion: dict, candidate: dict) -> pd.DataFrame:
    a, b = champion["summary"], candidate["summary"]
    moved = int((champion["decision"] != candidate["decision"]).sum())
    lo, hi = BAND_RATIO
    olo, ohi = OVERALL_RATIO
    ratios = candidate["bands"]["observed / predicted"]
    rows = [
        (
            "1. AUC, candidate against champion",
            f"loss ≤ {MAX_AUC_LOSS}",
            f"{b['AUC'] - a['AUC']:+.4f}",
            b["AUC"] >= a["AUC"] - MAX_AUC_LOSS,
        ),
        (
            "2. KS, candidate against champion",
            f"loss ≤ {MAX_KS_LOSS_PP:g} pp",
            f"{b['KS %'] - a['KS %']:+.2f} pp",
            b["KS %"] >= a["KS %"] - MAX_KS_LOSS_PP,
        ),
        (
            "3. Overall observed / predicted, candidate",
            f"{olo}–{ohi}",
            f"{b['observed / predicted']:.3f}",
            olo <= b["observed / predicted"] <= ohi,
        ),
        (
            "4. Observed / predicted by band, candidate",
            f"every band {lo}–{hi}",
            f"{ratios.min():.3f}–{ratios.max():.3f}",
            bool(ratios.between(lo, hi).all()),
        ),
        (
            "5. Decile calibration error, candidate against champion",
            "lower",
            f"{b['decile calibration error']:.3f} vs {a['decile calibration error']:.3f}",
            b["decile calibration error"] < a["decile calibration error"],
        ),
        (
            "6. Decisions that change under the current policy",
            "report",
            f"{moved:,} of {len(champion['decision']):,}",
            True,
        ),
    ]
    out = pd.DataFrame(rows, columns=["criterion", "threshold", "observed", "met"])
    out["met"] = out["met"].map({True: "yes", False: "no"})
    return out.set_index("criterion")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def export(card: dict, models: Path, data: Path, fit_note: str, result: dict) -> Path:
    """Write card, metadata and monitoring reference for a candidate."""
    target = models / card["version"]
    target.mkdir(parents=True, exist_ok=True)
    (target / "card.json").write_text(json.dumps(card, indent=2) + "\n")
    parent = json.loads((models / card["parent"] / "metadata.json").read_text())
    summary = result["summary"]
    metadata = {
        "model_version": card["version"],
        "parent": card["parent"],
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "code_commit": _commit(),
        "change": fit_note,
        "data_sha256": {"modeling.parquet": _sha256(data / "modeling.parquet")},
        "target": parent["target"],
        "variables": parent["variables"],
        "metrics": parent["metrics"]
        | {
            "candidate_eval_auc": summary["AUC"],
            "candidate_eval_ks": summary["KS %"],
            "candidate_eval_observed_over_predicted": summary["observed / predicted"],
        },
    }
    (target / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    df = pd.read_parquet(data / "modeling.parquet")
    ref = reference.build(df[df["split"] == "dev"], card, load_manifest()["version"])
    (target / "reference.json").write_text(json.dumps(ref, indent=2) + "\n")
    return target


def promote(models: Path, version: str, evidence: str) -> dict:
    reg = registry.load(models)
    reg["champion"] = version
    reg["history"].append(
        {"version": version, "promoted_on": date.today().isoformat(), "evidence": evidence}
    )
    (models / "registry.json").write_text(json.dumps(reg, indent=2) + "\n")
    return reg


def register_mlflow(models: Path, versions: list[str], champion: str) -> None:
    """Record each version in the MLflow model registry; the champion gets the alias."""
    import os

    import mlflow
    from mlflow.tracking import MlflowClient

    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db"))
    mlflow.set_experiment("credit-scorecard")
    client = MlflowClient()
    name = "credit-scorecard"
    if not client.search_registered_models(filter_string=f"name = '{name}'"):
        client.create_registered_model(name)
    known = {
        v.tags.get("card_version"): v for v in client.search_model_versions(f"name = '{name}'")
    }
    for version in versions:
        if version in known:
            continue
        with mlflow.start_run(run_name=f"register-{version}") as run:
            mlflow.log_artifacts(str(models / version), artifact_path="model")
            metadata = json.loads((models / version / "metadata.json").read_text())
            mlflow.log_metrics(
                {k: v for k, v in metadata["metrics"].items() if isinstance(v, (int, float))}
            )
        known[version] = client.create_model_version(
            name,
            f"{run.info.artifact_uri}/model",
            run_id=run.info.run_id,
            tags={"card_version": version},
        )
    client.set_registered_model_alias(name, "champion", known[champion].version)


def build_report(
    champion: str,
    candidate: str,
    criteria: pd.DataFrame,
    results: dict,
    promoted: bool,
    sample: str,
) -> str:
    summary = pd.DataFrame({v: r["summary"] for v, r in results.items()}).T.rename_axis("model")
    bands = pd.concat({v: r["bands"] for v, r in results.items()}, names=["model"])
    bands.index = [f"{m} / {b}" for m, b in bands.index]
    deciles = pd.DataFrame(
        {
            "observed %": results[champion]["deciles"]["bad_rate"] * 100,
            f"predicted % {champion}": results[champion]["deciles"]["predicted_rate"] * 100,
            f"predicted % {candidate}": results[candidate]["deciles"]["predicted_rate"] * 100,
        }
    )
    verdict = "**promoted**" if promoted else "**not promoted**"
    return (
        "\n\n".join(
            [
                f"# Promotion: {champion} → {candidate}",
                "Generated by `make promote`. Do not edit by hand.",
                f"Evaluation sample: {sample}. Outcome: {verdict}.",
                "## Criteria",
                to_markdown(criteria),
                "## Summary",
                to_markdown(summary, decimals=4),
                "## By band",
                to_markdown(bands.rename_axis("model / band"), decimals=3),
                "## By decile (1 = riskiest)",
                to_markdown(deciles, decimals=3),
            ]
        )
        + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("recalibrate")
    rec.add_argument("--base", default=None, help="default: the champion")
    rec.add_argument("--to", required=True)
    rec.add_argument("--fit-split", default="oot")
    ev = sub.add_parser("evaluate")
    ev.add_argument("--candidate", required=True)
    ev.add_argument("--split", default="prod")
    ev.add_argument("--promote", action="store_true")
    ev.add_argument("--evidence", default="docs/experiments/002-recalibration.md")
    for p in (rec, ev):
        p.add_argument("--data", type=Path, default=Path("data/processed"))
        p.add_argument("--models", type=Path, default=Path("models"))
    args = parser.parse_args()

    df = pd.read_parquet(args.data / "modeling.parquet")
    if args.command == "recalibrate":
        base = load_card(args.models, args.base or registry.champion(args.models))
        fit = df[df["split"] == args.fit_split]
        card = recalibrate(base, fit, args.to)
        note = (
            f"Recalibration of {base['version']}: points and bands unchanged, PD mapping "
            f"refitted on the '{args.fit_split}' split ({len(fit):,} loans)."
        )
        result = evaluate(card, fit)
        path = export(card, args.models, args.data, note, result)
        print(
            f"{note}\nCandidate written to {path}; observed / predicted on the fit sample "
            f"{result['summary']['observed / predicted']:.3f}"
        )
        return

    champion = registry.champion(args.models)
    sample = df[df["split"] == args.split]
    results = {v: evaluate(load_card(args.models, v), sample) for v in (champion, args.candidate)}
    criteria = compare(results[champion], results[args.candidate])
    passed = bool((criteria["met"] == "yes").all())
    promoted = passed and args.promote
    if promoted:
        promote(args.models, args.candidate, args.evidence)
        register_mlflow(args.models, [champion, args.candidate], args.candidate)
    description = f"the '{args.split}' split, {len(sample):,} loans"
    report = Path("docs") / f"promotion_{args.candidate}.md"
    report.write_text(
        build_report(champion, args.candidate, criteria, results, promoted, description)
    )
    pd.set_option("display.width", 200)
    print(criteria.to_string())
    print(
        (
            "promoted"
            if promoted
            else "criteria met, not promoted (no --promote)"
            if passed
            else "not promoted"
        )
        + f" -> {report}"
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    np.random.seed(42)
    main()
