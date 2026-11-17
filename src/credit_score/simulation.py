"""Replay 2017 as production traffic.

Applications issued in 2017 are sent month by month to the decision
service, as if they arrived that month. Their real outcomes are written to
``loan_outcomes`` with the date from which each would have been known, so
monitoring sees performance only once a cohort has matured.

    python -m credit_score.simulation --fraction 0.1 [--drift broker]

``--drift broker`` adds, from July 2017, a channel that brings riskier
applicants: real 2017 loans with a bureau score below 690 and six or more
accounts opened in two years, sampled with replacement, adding 40% to each
month's volume. Their outcomes are their own, so the drift is realistic.
"""

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from credit_score import registry, scoring
from credit_score.bounds import DERIVED
from credit_score.config import TARGET
from credit_score.target import MONTHS_TO_90DPD, charged_off, months_between

SEED = 7
MATURITY_MONTHS = 14  # 12-month window plus time for the charge-off to be booked
DRIFT_START = "2017-07"
DRIFT_SHARE = 0.40

OUTCOMES_DDL = """
CREATE TABLE IF NOT EXISTS loan_outcomes (
    application_id text PRIMARY KEY,
    issue_month    date NOT NULL,
    bad_12m        boolean NOT NULL,
    charged_off    boolean NOT NULL,
    default_mob    integer,
    matured_from   date NOT NULL
);
CREATE TABLE IF NOT EXISTS simulation_log (
    month      text PRIMARY KEY,
    sent       integer NOT NULL,
    ok         integer NOT NULL,
    client_errors integer NOT NULL,
    server_errors integer NOT NULL,
    seconds    double precision NOT NULL
);
"""


def _clean(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value


def load_applications(
    modeling: pd.DataFrame, card: dict, fraction: float, drift: str | None, seed: int = SEED
) -> pd.DataFrame:
    """The applications to send, with their payload and their real outcome."""
    rng = np.random.default_rng(seed)
    prod = modeling[modeling["split"] == "prod"].copy()
    prod["month"] = prod["issue_d"].dt.strftime("%Y-%m")
    base = prod.groupby("month", group_keys=False).sample(frac=fraction, random_state=seed)
    base["application_id"] = "lc-" + base["id"]
    parts = [base]
    if drift == "broker":
        pool = prod[(prod["fico"] < 690) & (prod["acc_open_past_24mths"] >= 6)]
        for month, part in base.groupby("month"):
            if month < DRIFT_START:
                continue
            n = int(round(len(part) * DRIFT_SHARE))
            extra = pool[pool["month"] == month].sample(n, replace=True, random_state=seed)
            extra = extra.assign(application_id=[f"lc-{i}-b{k}" for k, i in enumerate(extra["id"])])
            parts.append(extra)
    elif drift is not None:
        raise ValueError(f"unknown drift: {drift}")
    df = pd.concat(parts, ignore_index=True)
    day = rng.integers(0, 28, len(df))
    df["received_at"] = df["issue_d"] + pd.to_timedelta(day, unit="D") + pd.Timedelta(hours=12)
    df["channel"] = np.where(df["application_id"].str.contains("-b"), "broker", "direct")
    co = charged_off(df)
    paid = months_between(df["last_pymnt_d"], df["issue_d"]).fillna(0)
    df["charged_off"] = co
    df["default_mob"] = (paid + MONTHS_TO_90DPD).where(co).astype("Int64")
    df["issue_month"] = df["issue_d"].dt.to_period("M").dt.to_timestamp()
    df["matured_from"] = df["issue_month"] + pd.DateOffset(months=MATURITY_MONTHS)
    df["inputs"] = [
        {k: _clean(v) for k, v in row.items()}
        for row in df[scoring.required_inputs(card, DERIVED) + ["emp_length"]].to_dict("records")
    ]
    return df.sort_values(["month", "received_at"]).reset_index(drop=True)


def payload(row) -> dict:
    return row.inputs | {
        "application_id": row.application_id,
        "received_at": row.received_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def send_month(client, rows: pd.DataFrame, workers: int) -> dict:
    def post(row):
        try:
            return client.post("/decide", json=payload(row)).status_code
        except Exception:
            return 599

    started = time.perf_counter()
    with ThreadPoolExecutor(workers) as pool:
        codes = list(pool.map(post, rows.itertuples(index=False)))
    codes = np.asarray(codes)
    return {
        "sent": len(codes),
        "ok": int((codes == 200).sum()),
        "client_errors": int(((codes >= 400) & (codes < 500)).sum()),
        "server_errors": int((codes >= 500).sum()),
        "seconds": time.perf_counter() - started,
    }


def write_outcomes(conn, df: pd.DataFrame) -> None:
    rows = [
        (
            r.application_id,
            r.issue_month.date(),
            bool(getattr(r, TARGET)),
            bool(r.charged_off),
            None if pd.isna(r.default_mob) else int(r.default_mob),
            r.matured_from.date(),
        )
        for r in df.itertuples(index=False)
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO loan_outcomes VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (application_id) DO NOTHING",
            rows,
        )


def reset(conn) -> None:
    for table in ("decision_log", "score_log", "loan_outcomes", "simulation_log"):
        conn.execute(
            f"DO $$ BEGIN IF to_regclass('{table}') IS NOT NULL THEN "
            f"TRUNCATE {table}; END IF; END $$"
        )


def main() -> None:
    import httpx
    import psycopg

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/processed"))
    parser.add_argument("--models", type=Path, default=Path("models"))
    parser.add_argument("--fraction", type=float, default=0.1)
    parser.add_argument("--drift", choices=["broker"], default=None)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--url", default=os.environ.get("DECISION_URL", "http://127.0.0.1:8001"))
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", "postgresql://credit:credit@127.0.0.1:5432/credit"),
    )
    parser.add_argument("--keep", action="store_true", help="do not clear earlier runs")
    args = parser.parse_args()

    card = json.loads((registry.champion_dir(args.models) / "card.json").read_text())
    df = load_applications(
        pd.read_parquet(args.data / "modeling.parquet"), card, args.fraction, args.drift
    )
    print(f"{len(df):,} applications, {df['month'].nunique()} months, drift: {args.drift}")
    with psycopg.connect(args.database_url, autocommit=True) as conn:
        conn.execute(OUTCOMES_DDL)
        if not args.keep:
            reset(conn)
        limits = httpx.Limits(max_connections=args.workers)
        with httpx.Client(base_url=args.url, timeout=10, limits=limits, trust_env=False) as client:
            for month, rows in df.groupby("month"):
                stats = send_month(client, rows, args.workers)
                conn.execute(
                    "INSERT INTO simulation_log VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (month) DO UPDATE SET sent = EXCLUDED.sent, ok = EXCLUDED.ok, "
                    "client_errors = EXCLUDED.client_errors, "
                    "server_errors = EXCLUDED.server_errors, seconds = EXCLUDED.seconds",
                    (month, *stats.values()),
                )
                write_outcomes(conn, rows)
                rate = stats["sent"] / stats["seconds"]
                print(f"{month}: {stats['ok']:,}/{stats['sent']:,} decided, {rate:,.0f}/s")


if __name__ == "__main__":
    main()
