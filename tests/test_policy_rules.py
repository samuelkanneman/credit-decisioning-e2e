"""The policy graph in policy/: one test per rule boundary, and parity with the analysis."""

import json
import shutil

import numpy as np
import pandas as pd
import pytest

from credit_score import policy_analysis as pa
from credit_score import registry
from credit_score.policy_engine import CHALLENGER, CHAMPION, Policy, assign_arm, graph_bytes

BASE = {
    "fico": 700,
    "acc_open_past_24mths": 3,
    "score": 600,
    "band": "B",
    "arm": CHAMPION,
    "emp_length": 5,
    "annual_inc": 60_000,
    "loan_amnt": 10_000,
}


@pytest.fixture(scope="module")
def policy():
    return Policy()


def decide(policy, **change):
    return policy.evaluate(BASE | change)


@pytest.mark.parametrize(
    ("change", "decision", "rule"),
    [
        ({}, "APPROVE", "S3"),
        # K1: bureau score below 660
        ({"fico": 659}, "REJECT", "K1"),
        ({"fico": 660}, "APPROVE", "S3"),
        # K2: 10 or more accounts opened in 24 months
        ({"acc_open_past_24mths": 10}, "REJECT", "K2"),
        ({"acc_open_past_24mths": 9}, "APPROVE", "S3"),
        # a knock-out wins over a good score, and K1 is checked first
        ({"fico": 650, "acc_open_past_24mths": 12, "score": 640, "band": "A"}, "REJECT", "K1"),
        # S1: score below 580
        ({"score": 579, "band": "D"}, "REJECT", "S1"),
        ({"score": 580, "band": "D"}, "REVIEW", "S2"),
        # S2 / S3 boundary, champion
        ({"score": 594, "band": "C"}, "REVIEW", "S2"),
        ({"score": 595, "band": "C"}, "APPROVE", "S3"),
        # S2 / S3 boundary, challenger
        ({"score": 589, "band": "C", "arm": CHALLENGER}, "REVIEW", "S2"),
        ({"score": 590, "band": "C", "arm": CHALLENGER}, "APPROVE", "S3"),
        ({"score": 579, "band": "D", "arm": CHALLENGER}, "REJECT", "S1"),
        # R1 and R2: missing information sends an approval to review, never a rejection
        ({"emp_length": None}, "REVIEW", "R1"),
        ({"annual_inc": None}, "REVIEW", "R2"),
        ({"emp_length": None, "score": 570, "band": "E"}, "REJECT", "S1"),
        ({"emp_length": 0}, "APPROVE", "S3"),  # "< 1 year" is reported, not missing
    ],
)
def test_decision_rules(policy, change, decision, rule):
    out = decide(policy, **change)
    assert (out["decision"], out["decision_rule"]) == (decision, rule)
    assert out["decision_description"]


@pytest.mark.parametrize(
    ("change", "limit_rule", "share"),
    [
        ({"score": 615, "band": "A"}, "L1", 0.40),
        ({"score": 600, "band": "B"}, "L2", 0.40),
        ({"score": 595, "band": "C"}, "L3", 0.30),
        ({"score": 590, "band": "C", "arm": CHALLENGER}, "L4", 0.10),
        ({"score": 590, "band": "C"}, "L4", 0.10),  # review
        ({"score": 582, "band": "D"}, "L4", 0.10),  # review
        ({"fico": 650}, None, None),  # rejected: no limit
    ],
)
def test_limit_rules(policy, change, limit_rule, share):
    out = decide(policy, **change)
    assert out["limit_rule"] == limit_rule
    assert out["max_loan_to_income"] == share


@pytest.mark.parametrize(
    ("income", "requested", "limit", "approved"),
    [
        (60_000, 10_000, 24_000, 10_000),  # below the limit: what was asked
        (60_000, 30_000, 24_000, 24_000),  # above the limit: approved for the limit
        (200_000, 40_000, 40_000, 40_000),  # product maximum
        (2_000, 1_000, 1_000, 1_000),  # product minimum
        (60_001, 30_000, 24_000, 24_000),  # rounded down to whole dollars
    ],
)
def test_amounts(policy, income, requested, limit, approved):
    out = decide(policy, annual_inc=income, loan_amnt=requested)
    assert (out["limit"], out["approved_amount"]) == (limit, approved)


def test_review_without_income_has_no_amount(policy):
    out = decide(policy, annual_inc=None)
    assert out["limit"] is None and out["approved_amount"] is None


def test_graph_matches_the_analysis_that_justifies_it(policy):
    """Decisions and limits from the JSON graph equal the code behind docs/policy.md."""
    rng = np.random.default_rng(3)
    n = 3000
    score = rng.integers(560, 640, n)
    df = pd.DataFrame(
        {
            "score": score,
            "band": pd.cut(
                score, [-np.inf, 575, 587, 597, 610, np.inf], right=False, labels=list("EDCBA")
            ).astype(str),
            "fico": rng.choice([655, 660, 700, 760], n),
            "acc_open_past_24mths": rng.integers(0, 14, n),
            "emp_length": np.where(rng.random(n) < 0.1, np.nan, rng.integers(0, 11, n)),
            "annual_inc": np.where(rng.random(n) < 0.05, np.nan, rng.integers(10_000, 150_000, n)),
            "loan_amnt": rng.integers(1_000, 40_001, n),
        }
    )
    for arm in (CHAMPION, CHALLENGER):
        expected = pa.apply_policy(df, arm=arm)
        rules = pa.decision_rules(df, arm=arm)
        share = pa.max_loan_to_income(df).where(expected != "REJECT")
        for i, row in enumerate(df.itertuples(index=False)):
            context = {k: (None if pd.isna(v) else v) for k, v in row._asdict().items()}
            out = policy.evaluate(context | {"arm": arm})
            assert out["decision"] == expected.iloc[i], (arm, context)
            assert out["decision_rule"] == rules.iloc[i], (arm, context)
            want = share.iloc[i]
            assert out["max_loan_to_income"] == (None if pd.isna(want) else pytest.approx(want))


def test_challenger_settings_agree(policy):
    assert pa.PROPOSED["challenger_approve_from"] == 590
    assert policy.challenger_share == 0.10


def test_arm_assignment_is_deterministic_and_close_to_the_share():
    ids = [f"app-{i}" for i in range(50_000)]
    arms = [assign_arm(i, 0.10) for i in ids]
    assert arms == [assign_arm(i, 0.10) for i in ids]
    assert 0.09 < arms.count(CHALLENGER) / len(ids) < 0.11
    assert all(assign_arm(i, 0.0) == CHAMPION for i in ids[:1000])


def test_a_changed_graph_without_a_new_version_does_not_load(tmp_path):
    policy_dir = tmp_path / "policy"
    shutil.copytree(graph_bytes.__globals__["POLICY_DIR"], policy_dir)
    graph = policy_dir / "credit-policy.json"
    graph.write_text(graph.read_text().replace('"< 580"', '"< 575"'))
    with pytest.raises(ValueError, match="does not match"):
        Policy(policy_dir)


def test_manifest_points_at_the_document_and_the_model():
    manifest = json.loads((graph_bytes.__globals__["POLICY_DIR"] / "manifest.json").read_text())
    assert manifest["version"].startswith("policy-") and registry.champion().startswith(
        manifest["scorecard"]
    )


def test_policy_diff_counts_moved_decisions(tmp_path):
    from credit_score import policy_diff

    base = Policy()
    changed = tmp_path / "policy"
    shutil.copytree(graph_bytes.__globals__["POLICY_DIR"], changed)
    graph = changed / "credit-policy.json"
    graph.write_text(graph.read_text().replace('"< 595"', '"< 600"'))
    manifest = json.loads((changed / "manifest.json").read_text())
    import hashlib

    manifest |= {"version": "policy-v2", "sha256": hashlib.sha256(graph.read_bytes()).hexdigest()}
    (changed / "manifest.json").write_text(json.dumps(manifest))
    df, description = policy_diff.sample(tmp_path / "no-data", tmp_path)
    df = df.head(2000)
    text = policy_diff.report(base, Policy(changed), df, description)
    assert "policy-v1 → policy-v2" in text and "synthetic" in text
    old = policy_diff.evaluate(base, df, CHAMPION)["decision"]
    moved = ((df["score"] >= 595) & (df["score"] < 600) & (old == "APPROVE")).sum()
    assert f"Decisions that change: **{moved:,}**" in text
    assert policy_diff.report(base, base, df, description).endswith("unchanged.\n")
