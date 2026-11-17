"""Convert the raw Lending Club CSVs into typed parquet files.

Only whitelisted columns are read (see ``schema.py``). The accepted-loans file
is streamed in batches so it never has to fit in memory.
"""

import argparse
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from credit_score.data.schema import ACCEPTED_COLUMNS, REJECTED_RENAME

ACCEPTED_GLOB = "accepted*.csv*"
REJECTED_GLOB = "rejected*.csv*"


def _to_number(s: pd.Series) -> pd.Series:
    """Parse numbers that may come as ' 36 months', '13.5%' or plain text."""
    cleaned = s.astype("string").str.replace(r"[%,]|months", "", regex=True).str.strip()
    return pd.to_numeric(cleaned, errors="coerce")


def _to_month(s: pd.Series) -> pd.Series:
    """Parse Lending Club month stamps such as 'Dec-2015'."""
    return pd.to_datetime(s, format="%b-%Y", errors="coerce")


def _emp_length_years(s: pd.Series) -> pd.Series:
    """'10+ years' -> 10, '< 1 year' -> 0, 'n/a' -> NaN."""
    s = s.astype("string").str.strip()
    years = pd.to_numeric(s.str.extract(r"(\d+)")[0], errors="coerce")
    return years.mask(s.str.startswith("<", na=False), 0)


def clean_accepted(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for col in ACCEPTED_COLUMNS:
        raw = df[col.name]
        if col.name == "emp_length":
            out[col.name] = _emp_length_years(raw)
        elif col.kind == "num":
            out[col.name] = _to_number(raw)
        elif col.kind == "date":
            out[col.name] = _to_month(raw)
        else:
            out[col.name] = raw.astype("string").str.strip()
    # The raw file ends with footer rows that are not loans.
    return out[out["loan_amnt"].notna() & out["issue_d"].notna()].reset_index(drop=True)


def clean_rejected(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=REJECTED_RENAME)[list(REJECTED_RENAME.values())]
    out = pd.DataFrame(index=df.index)
    out["application_d"] = pd.to_datetime(df["application_d"], errors="coerce")
    for name in ("loan_amnt", "risk_score", "dti", "policy_code"):
        out[name] = _to_number(df[name])
    out["emp_length"] = _emp_length_years(df["emp_length"])
    for name in ("purpose", "addr_state"):
        out[name] = df[name].astype("string").str.strip()
    return out[out["application_d"].notna()].reset_index(drop=True)


def _convert(src: Path, dst: Path, columns: list[str], clean) -> int:
    reader = pacsv.open_csv(
        src,
        read_options=pacsv.ReadOptions(block_size=64 << 20),
        convert_options=pacsv.ConvertOptions(
            include_columns=columns,
            column_types={c: pa.string() for c in columns},
        ),
    )
    writer, rows = None, 0
    try:
        for batch in reader:
            table = pa.Table.from_pandas(clean(batch.to_pandas()), preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(dst, table.schema)
            writer.write_table(table.cast(writer.schema))
            rows += table.num_rows
    finally:
        if writer is not None:
            writer.close()
    return rows


def _find(raw: Path, pattern: str) -> Path:
    matches = sorted(raw.rglob(pattern))
    if not matches:
        raise FileNotFoundError(f"No file matching {pattern!r} under {raw}. Run `make data`.")
    return matches[0]


def prepare(raw: Path, out: Path) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    return {
        "accepted": _convert(
            _find(raw, ACCEPTED_GLOB),
            out / "accepted.parquet",
            [c.name for c in ACCEPTED_COLUMNS],
            clean_accepted,
        ),
        "rejected": _convert(
            _find(raw, REJECTED_GLOB),
            out / "rejected.parquet",
            list(REJECTED_RENAME),
            clean_rejected,
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=Path("data/raw"))
    parser.add_argument("--out", type=Path, default=Path("data/processed"))
    args = parser.parse_args()
    for name, rows in prepare(args.raw, args.out).items():
        print(f"{name}: {rows:,} rows -> {args.out / (name + '.parquet')}")


if __name__ == "__main__":
    main()
