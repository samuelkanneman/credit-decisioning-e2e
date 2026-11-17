"""Download the Lending Club files from Kaggle and verify their SHA-256.

Credentials come from the standard Kaggle locations (``~/.kaggle`` or the
``KAGGLE_*`` environment variables); they are never stored in this repository.

Expected hashes live in ``data_manifest.json``. A file whose hash is still
``null`` there fails the check unless ``--record`` is passed, which writes the
hash of what was downloaded so the change can be reviewed and committed.
"""

import argparse
import hashlib
import json
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[3] / "data_manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(out: Path, manifest: dict, record: bool) -> list[str]:
    """Return a list of problems; with ``record`` fill in missing hashes instead."""
    problems = []
    for name, expected in manifest["files"].items():
        matches = sorted(out.rglob(name))
        if not matches:
            problems.append(f"{name}: not found under {out}")
            continue
        actual = sha256(matches[0])
        if expected is None and record:
            manifest["files"][name] = actual
        elif expected is None:
            problems.append(f"{name}: no hash recorded (got {actual}); rerun with --record")
        elif actual != expected:
            problems.append(f"{name}: hash mismatch, expected {expected}, got {actual}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/raw"))
    parser.add_argument("--record", action="store_true", help="record hashes that are still null")
    parser.add_argument("--skip-download", action="store_true", help="only verify existing files")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text())
    if not args.skip_download:
        from kaggle.api.kaggle_api_extended import KaggleApi

        api = KaggleApi()
        api.authenticate()
        args.out.mkdir(parents=True, exist_ok=True)
        api.dataset_download_files(manifest["dataset"], path=str(args.out), unzip=True)

    problems = verify(args.out, manifest, args.record)
    if args.record:
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    if problems:
        raise SystemExit("Data verification failed:\n  " + "\n  ".join(problems))
    print(f"Verified {len(manifest['files'])} files in {args.out}")


if __name__ == "__main__":
    main()
