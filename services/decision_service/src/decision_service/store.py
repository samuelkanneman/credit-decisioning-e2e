"""Decision log: the audit record of every decision.

Unlike the scoring API's request log, this record is written before the
response is returned. If it cannot be written, no decision is given.
"""

import json
import logging
import os

logger = logging.getLogger("decision_service")

DDL = """
CREATE TABLE IF NOT EXISTS decision_log (
    application_id   text PRIMARY KEY,
    received_at      timestamptz NOT NULL,
    decided_at       timestamptz NOT NULL,
    arm              text NOT NULL,
    decision         text NOT NULL,
    decision_rule    text NOT NULL,
    limit_rule       text,
    requested_amount double precision NOT NULL,
    approved_amount  double precision,
    score            integer NOT NULL,
    pd               double precision NOT NULL,
    band             text NOT NULL,
    model_version    text NOT NULL,
    policy_version   text NOT NULL,
    policy_sha256    text NOT NULL,
    latency_ms       double precision NOT NULL,
    application      jsonb NOT NULL,
    response         jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS decision_log_received_at ON decision_log (received_at);
"""

COLUMNS = (
    "application_id",
    "received_at",
    "decided_at",
    "arm",
    "decision",
    "decision_rule",
    "limit_rule",
    "requested_amount",
    "approved_amount",
    "score",
    "pd",
    "band",
    "model_version",
    "policy_version",
    "policy_sha256",
    "latency_ms",
    "application",
    "response",
)
INSERT = (
    f"INSERT INTO decision_log ({', '.join(COLUMNS)}) "
    f"VALUES ({', '.join('%(' + c + ')s' for c in COLUMNS)}) "
    "ON CONFLICT (application_id) DO NOTHING RETURNING application_id"
)


class DecisionLog:
    """PostgreSQL-backed log. ``save`` returns False when the application already exists."""

    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.environ.get("DATABASE_URL")
        self.pool = None

    def open(self) -> None:
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is required: decisions must be recorded")
        from psycopg_pool import ConnectionPool

        self.pool = ConnectionPool(self.database_url, min_size=1, max_size=8, open=True)
        with self.pool.connection() as conn:
            conn.execute(DDL)

    def close(self) -> None:
        if self.pool is not None:
            self.pool.close()

    def status(self) -> str:
        try:
            with self.pool.connection(timeout=2) as conn:
                conn.execute("SELECT 1")
            return "ok"
        except Exception:
            return "error"

    def get(self, application_id: str) -> dict | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT response FROM decision_log WHERE application_id = %s", (application_id,)
            ).fetchone()
        return row[0] if row else None

    def save(self, record: dict) -> bool:
        from psycopg.types.json import Jsonb

        row = record | {
            "application": Jsonb(record["application"]),
            "response": Jsonb(record["response"]),
        }
        with self.pool.connection() as conn:
            inserted = conn.execute(INSERT, row).fetchone()
        logger.info(json.dumps({k: record[k] for k in COLUMNS[:16]}, default=str))
        return inserted is not None


class MemoryDecisionLog(DecisionLog):
    """In-process log for tests and local runs without a database."""

    def __init__(self):
        super().__init__(database_url=None)
        self.rows: dict[str, dict] = {}

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def status(self) -> str:
        return "memory"

    def get(self, application_id: str) -> dict | None:
        row = self.rows.get(application_id)
        return row["response"] if row else None

    def save(self, record: dict) -> bool:
        if record["application_id"] in self.rows:
            return False
        self.rows[record["application_id"]] = record
        return True
