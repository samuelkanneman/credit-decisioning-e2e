"""Reference distributions and metrics for monitoring, from the development sample.

Writes ``models/<version>/reference.json``: aggregates only (bin shares,
rates, metrics), committed next to the card so monitoring can run where
the training data is not available.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from credit_score import metrics, registry, scorecard, scoring
from credit_score.bounds import DERIVED
from credit_score.config import TARGET
from credit_score.policy_analysis import decision_rules
from credit_score.policy_engine import load_manifest

SCORE_BINS = 10


def score_bins(scores: pd.Series) -> dict:
    """Quantile bins of the reference score (profile spec): inner edges and shares."""
    edges = np.unique(np.quantile(scores, np.linspace(0, 1, SCORE_BINS + 1)))[1:-1]
    full = np.concatenate([[-np.inf], edges, [np.inf]])
    shares = np.histogram(scores, full)[0] / len(scores)
    return {"edges": edges.tolist(), "shares": shares.tolist()}


def variable_shares(df: pd.DataFrame, card: dict) -> dict:
    out = {}
    for spec in card["variables"]:
        labels = df[spec["name"]].map(lambda v, s=spec: scoring.bin_label(v, s))
        out[spec["name"]] = labels.value_counts(normalize=True).sort_index().to_dict()
    return out


def performance(df: pd.DataFrame) -> dict:
    return {
        "loans": int(len(df)),
        "bad_rate": float(df[TARGET].mean()),
        "auc": metrics.auc(df[TARGET], df["score"]),
        "ks": metrics.ks_deciles(df[TARGET], df["score"]),
    }


def build(dev: pd.DataFrame, card: dict, policy_version: str) -> dict:
    dev = dev.copy()
    dev["score"] = scorecard.score(dev, card)
    dev["band"] = scorecard.band(dev["score"], card["bands"])
    dev["pd"] = scorecard.pd_from_score(dev["score"], card["calibration"])
    rules = decision_rules(dev)
    approved = dev[rules == "S3"]
    inputs = scoring.required_inputs(card, DERIVED) + ["emp_length"]
    return {
        "model_version": card["version"],
        "policy_version": policy_version,
        "population": f"development, issued 2013-2015: {len(dev):,} individual loans",
        "score": score_bins(dev["score"]),
        "variables": variable_shares(dev, card),
        "inputs_missing": {name: float(dev[name].isna().mean()) for name in inputs},
        "performance": {"all": performance(dev), "approved": performance(approved)},
        "bands_approved": {
            band: {
                "loans": int(len(part)),
                "bad_rate": float(part[TARGET].mean()),
                "pd": float(part["pd"].mean()),
            }
            for band, part in approved.groupby("band")
        },
        "policy": {
            "approval_rate": float((rules == "S3").mean()),
            "rule_rates": rules.value_counts(normalize=True).sort_index().to_dict(),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/processed"))
    parser.add_argument("--models", type=Path, default=Path("models"))
    parser.add_argument("--model", help="model version (default: the champion)")
    args = parser.parse_args()
    card_dir = args.models / (args.model or registry.champion(args.models))
    card = json.loads((card_dir / "card.json").read_text())
    df = pd.read_parquet(args.data / "modeling.parquet")
    reference = build(df[df["split"] == "dev"], card, load_manifest()["version"])
    (card_dir / "reference.json").write_text(json.dumps(reference, indent=2) + "\n")
    print(json.dumps({k: reference[k] for k in ("performance", "policy")}, indent=2))


if __name__ == "__main__":
    main()
