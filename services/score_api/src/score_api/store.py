"""Request log: one row per scored application.

Every request is written as a JSON line to stdout. When ``DATABASE_URL`` is
set it is also inserted into PostgreSQL, which is what monitoring reads.
"""

import json
import logging
import os

logger = logging.getLogger("score_api")

DDL = """
CREATE TABLE IF NOT EXISTS score_log (
    request_id    text PRIMARY KEY,
    received_at   timestamptz NOT NULL,
    model_version text NOT NULL,
    score         integer NOT NULL,
    pd            double precision NOT NULL,
    band          text NOT NULL,
    latency_ms    double precision NOT NULL,
    request       jsonb NOT NULL,
    response      jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS score_log_received_at ON score_log (received_at);
"""

INSERT = """
INSERT INTO score_log
    (request_id, received_at, model_version, score, pd, band, latency_ms, request, response)
VALUES (%(request_id)s, %(received_at)s, %(model_version)s, %(score)s, %(pd)s, %(band)s,
        %(latency_ms)s, %(request)s, %(response)s)
ON CONFLICT (request_id) DO NOTHING
"""


class RequestLog:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.environ.get("DATABASE_URL")
        self.pool = None

    def open(self) -> None:
        if not self.database_url:
            return
        from psycopg_pool import ConnectionPool

        self.pool = ConnectionPool(self.database_url, min_size=1, max_size=4, open=True)
        with self.pool.connection() as conn:
            conn.execute(DDL)

    def close(self) -> None:
        if self.pool is not None:
            self.pool.close()

    def status(self) -> str:
        if self.pool is None:
            return "disabled"
        try:
            with self.pool.connection(timeout=2) as conn:
                conn.execute("SELECT 1")
            return "ok"
        except Exception:
            return "error"

    def write(self, record: dict) -> None:
        """Called after the response is sent. A failed write is logged, never raised."""
        logger.info(json.dumps(record, default=str))
        if self.pool is None:
            return
        from psycopg.types.json import Jsonb

        row = {**record, "request": Jsonb(record["request"]), "response": Jsonb(record["response"])}
        try:
            with self.pool.connection() as conn:
                conn.execute(INSERT, row)
        except Exception:
            logger.exception("could not write request %s to the database", record["request_id"])
