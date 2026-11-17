"""Load and evaluate the credit policy graph in ``policy/``.

The graph is a GoRules JDM file, editable in the GoRules visual editor. The
manifest next to it carries the policy version and the SHA-256 of the graph;
loading refuses a graph that does not match its manifest, so a rule cannot
change without the version changing with it.
"""

import hashlib
import json
import os
from pathlib import Path

# In the repository the graph sits in policy/; a container points POLICY_DIR at its copy.
POLICY_DIR = Path(os.environ.get("POLICY_DIR", Path(__file__).resolve().parents[2] / "policy"))
CHAMPION, CHALLENGER = "champion", "challenger"

# Fields the graph reads. Everything else in the application is ignored by it.
INPUTS = (
    "fico",
    "acc_open_past_24mths",
    "score",
    "band",
    "arm",
    "emp_length",
    "annual_inc",
    "loan_amnt",
)
OUTPUTS = (
    "decision",
    "decision_rule",
    "decision_description",
    "knockout",
    "max_loan_to_income",
    "limit_rule",
    "limit",
    "approved_amount",
)


def load_manifest(policy_dir: Path = POLICY_DIR) -> dict:
    return json.loads((policy_dir / "manifest.json").read_text())


def graph_bytes(policy_dir: Path = POLICY_DIR) -> bytes:
    manifest = load_manifest(policy_dir)
    content = (policy_dir / manifest["graph"]).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != manifest["sha256"]:
        raise ValueError(
            f"{manifest['graph']} does not match policy/manifest.json (sha256 {digest}). "
            "Bump the policy version and record the new hash."
        )
    return content


def assign_arm(application_id: str, challenger_share: float) -> str:
    """Deterministic arm: the same application always gets the same policy."""
    bucket = int(hashlib.sha256(application_id.encode()).hexdigest()[:8], 16) % 100
    return CHALLENGER if bucket < round(challenger_share * 100) else CHAMPION


class Policy:
    def __init__(self, policy_dir: Path = POLICY_DIR):
        import zen  # imported here so the scoring runtime never needs it

        self.manifest = load_manifest(policy_dir)
        self.version = self.manifest["version"]
        self.sha256 = self.manifest["sha256"]
        self.challenger_share = self.manifest["champion_challenger"]["challenger_share"]
        self._decision = zen.ZenEngine().create_decision(graph_bytes(policy_dir).decode())

    def evaluate(self, context: dict) -> dict:
        """Run the graph. Every output key is present; a rule that sets nothing gives None."""
        missing = [k for k in INPUTS if k not in context]
        if missing:
            raise KeyError(f"policy inputs missing: {missing}")
        result = self._decision.evaluate({k: context[k] for k in INPUTS})["result"]
        return {k: result.get(k) for k in OUTPUTS}
