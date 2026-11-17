# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["evidently==0.7.23", "pandas>=2.2,<3", "pyarrow>=17"]
# ///
"""Monthly Evidently data-drift reports, in an environment of their own.

Evidently 0.7 does not run on pandas 3, which the project uses, so this script
declares its own dependencies and runs with ``uv run --script``. It reads the
parquet files that ``make monitor`` exports:

    uv run --script monitoring/evidently_report.py \
        build/monitoring/latest build/monitoring/evidently
"""

import json
import sys
from pathlib import Path

import pandas as pd
from evidently import Report
from evidently.presets import DataDriftPreset


def main(frames_dir: Path, out_dir: Path) -> None:
    reference = pd.read_parquet(frames_dir / "reference_inputs.parquet")
    decisions = pd.read_parquet(frames_dir / "decisions.parquet")
    decisions["month"] = pd.to_datetime(decisions["received_at"], utc=True).dt.strftime("%Y-%m")
    out_dir.mkdir(parents=True, exist_ok=True)
    for month, part in decisions.groupby("month"):
        current = pd.DataFrame([json.loads(a) for a in part["application"]])[reference.columns]
        snapshot = Report([DataDriftPreset()]).run(current_data=current, reference_data=reference)
        snapshot.save_html(str(out_dir / f"data-drift-{month}.html"))
        print(f"{month}: {out_dir / f'data-drift-{month}.html'}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
