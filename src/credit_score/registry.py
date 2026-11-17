"""Which model is the champion.

``models/registry.json`` names the champion and records every promotion. The
scoring API, the decision service's checks, monitoring and the dashboard all
resolve the model through it, so promoting a model is a one-line, reviewable
change to that file.
"""

import json
import os
from pathlib import Path

MODELS_DIR = Path(os.environ.get("MODELS_DIR", Path(__file__).resolve().parents[2] / "models"))


def load(models_dir: Path = MODELS_DIR) -> dict:
    return json.loads((models_dir / "registry.json").read_text())


def champion(models_dir: Path = MODELS_DIR) -> str:
    return load(models_dir)["champion"]


def champion_dir(models_dir: Path = MODELS_DIR) -> Path:
    return models_dir / champion(models_dir)
