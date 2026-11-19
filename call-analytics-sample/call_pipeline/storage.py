"""One durable result per call and processing configuration."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                config_json TEXT NOT NULL,
                summary_json TEXT
            );
            CREATE TABLE IF NOT EXISTS results (
                work_key TEXT PRIMARY KEY,
                call_id TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt TEXT NOT NULL,
                prompt_hash TEXT NOT NULL,
                redactor_version TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('success','failed')),
                attempts INTEGER NOT NULL,
                latency_ms REAL NOT NULL,
                redacted_text TEXT NOT NULL,
                pii_counts_json TEXT NOT NULL,
                labels_json TEXT,
                usage_json TEXT NOT NULL,
                error_code TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS run_items (
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                call_id TEXT NOT NULL,
                work_key TEXT NOT NULL REFERENCES results(work_key),
                cached INTEGER NOT NULL,
                PRIMARY KEY(run_id, call_id)
            );
        """)

    def start(self, run_id, config):
        with self.connection:
            self.connection.execute("INSERT INTO runs VALUES (?, ?, NULL, ?, NULL)",
                                    (run_id, now(), json.dumps(config, sort_keys=True)))

    def cached(self, work_key):
        row = self.connection.execute(
            "SELECT * FROM results WHERE work_key=? AND status='success'", (work_key,)
        ).fetchone()
        return dict(row) if row else None

    def record(self, run_id, row, cached=False):
        with self.connection:
            if not cached:
                columns = list(row)
                # Column names come exclusively from pipeline.py, not input files.
                self.connection.execute(
                    f"INSERT INTO results ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
                    "ON CONFLICT(work_key) DO UPDATE SET " +
                    ",".join(f"{c}=excluded.{c}" for c in columns if c != "work_key"),
                    [row[c] for c in columns],
                )
            self.connection.execute("INSERT INTO run_items VALUES (?, ?, ?, ?)",
                                    (run_id, row["call_id"], row["work_key"], int(cached)))

    def finish(self, run_id, summary):
        with self.connection:
            self.connection.execute("UPDATE runs SET finished_at=?, summary_json=? WHERE run_id=?",
                                    (now(), json.dumps(summary), run_id))

    def rows(self, run_id):
        records = self.connection.execute("""
            SELECT r.*, i.cached FROM run_items i
            JOIN results r ON i.work_key=r.work_key
            WHERE i.run_id=? ORDER BY i.call_id
        """, (run_id,)).fetchall()
        return [dict(row) for row in records]

    def close(self):
        self.connection.close()

