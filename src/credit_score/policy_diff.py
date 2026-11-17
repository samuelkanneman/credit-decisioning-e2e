"""Impact of a policy change: how many decisions move between two versions.

    python -m credit_score.policy_diff --base origin/main

Evaluates the policy graph at a git revision and the one in the working
tree on the same applications and prints where decisions and amounts move.
With the processed data present the applications are the 2016 out-of-time
loans, scored with the current scorecard; otherwise a synthetic sample.
"""

import argparse
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from credit_score import registry
from credit_score.policy_engine import CHALLENGER, CHAMPION, INPUTS, POLICY_DIR, Policy

SAMPLE = 20_000
SEED = 42


def policy_at(revision: str, workdir: Path) -> Policy:
    """Materialise policy/ as it was at ``revision`` and load it."""
    target = workdir / revision.replace("/", "_")
    target.mkdir(parents=True)
    for name in ("manifest.json", "credit-policy.json"):
        content = subprocess.run(
            ["git", "show", f"{revision}:policy/{name}"], capture_output=True, check=True
        ).stdout
        (target / name).write_bytes(content)
    return Policy(target)


def sample(data: Path, models: Path) -> tuple[pd.DataFrame, str]:
    path = data / "modeling.parquet"
    if path.exists():
        import json

        from credit_score import scorecard

        card = json.loads((registry.champion_dir(models) / "card.json").read_text())
        df = pd.read_parquet(path)
        df = df[df["split"] == "oot"].sample(SAMPLE, random_state=SEED)
        df["score"] = scorecard.score(df, card)
        df["band"] = scorecard.band(df["score"], card["bands"])
        return df, f"{SAMPLE:,} loans issued in 2016, scored with {card['version']}"
    rng = np.random.default_rng(SEED)
    score = rng.normal(595, 18, SAMPLE).round()
    df = pd.DataFrame(
        {
            "score": score,
            "band": pd.cut(
                score, [-np.inf, 575, 587, 597, 610, np.inf], right=False, labels=list("EDCBA")
            ).astype(str),
            "fico": rng.choice([655, 670, 700, 740], SAMPLE, p=[0.05, 0.35, 0.4, 0.2]),
            "acc_open_past_24mths": rng.poisson(4, SAMPLE),
            "emp_length": np.where(rng.random(SAMPLE) < 0.06, np.nan, 5.0),
            "annual_inc": rng.lognormal(11.1, 0.5, SAMPLE).round(),
            "loan_amnt": rng.integers(1_000, 40_001, SAMPLE),
        }
    )
    return df, f"{SAMPLE:,} synthetic applications (processed data not found)"


def evaluate(policy: Policy, df: pd.DataFrame, arm: str) -> pd.DataFrame:
    rows = []
    for record in df[[c for c in INPUTS if c != "arm"]].to_dict("records"):
        context = {k: (None if pd.isna(v) else v) for k, v in record.items()} | {"arm": arm}
        out = policy.evaluate(context)
        rows.append(
            (out["decision"], out["approved_amount"] if out["decision"] == "APPROVE" else 0)
        )
    return pd.DataFrame(rows, columns=["decision", "amount"], index=df.index)


def report(base: Policy, head: Policy, df: pd.DataFrame, description: str) -> str:
    lines = [
        f"## Policy impact: {base.version} → {head.version}",
        f"Applications: {description}.",
    ]
    if base.sha256 == head.sha256:
        return "\n\n".join([*lines, "The policy graph is unchanged."]) + "\n"
    for arm in (CHAMPION, CHALLENGER):
        old, new = evaluate(base, df, arm), evaluate(head, df, arm)
        moved = old["decision"] != new["decision"]
        table = pd.crosstab(old["decision"], new["decision"], margins=True, margins_name="total")
        table.index.name, table.columns.name = f"{base.version} \\ {head.version}", None
        rows = [
            "| " + " | ".join([table.index.name, *map(str, table.columns)]) + " |",
            "|" + " --- |" * (len(table.columns) + 1),
        ]
        rows += [f"| {i} | " + " | ".join(f"{v:,}" for v in r) + " |" for i, r in table.iterrows()]
        amount = new["amount"].sum() / max(old["amount"].sum(), 1) - 1
        lines += [
            f"### {arm.capitalize()} arm",
            f"Decisions that change: **{moved.sum():,}** ({moved.mean():.1%}). "
            f"Approval rate {(old['decision'] == 'APPROVE').mean():.1%} → "
            f"{(new['decision'] == 'APPROVE').mean():.1%}. Amount approved {amount:+.1%}.",
            "\n".join(rows),
        ]
    return "\n\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="git revision to compare against")
    parser.add_argument("--data", type=Path, default=Path("data/processed"))
    parser.add_argument("--models", type=Path, default=Path("models"))
    parser.add_argument("--out", type=Path, help="also write the report to this file")
    args = parser.parse_args()
    df, description = sample(args.data, args.models)
    with tempfile.TemporaryDirectory() as workdir:
        try:
            base = policy_at(args.base, Path(workdir))
        except subprocess.CalledProcessError:
            print(f"No policy at {args.base}: nothing to compare against.")
            return
        text = report(base, Policy(POLICY_DIR), df, description)
    print(text)
    if args.out:
        args.out.write_text(text)


if __name__ == "__main__":
    main()
