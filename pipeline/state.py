"""SQLite-backed persistent state: row statuses, results, checkpoints.

A single connection guarded by a lock keeps writes simple and safe with the
thread-pool runner. WAL mode keeps readers non-blocking.
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone

from . import config

STATUSES = ("pending", "processing", "completed", "manual_review", "failed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rows (
    id INTEGER PRIMARY KEY,
    account TEXT NOT NULL,
    address TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    filename TEXT,
    source_url TEXT,
    source_domain TEXT,
    website TEXT,
    parent_system TEXT,
    confidence REAL,
    verification_notes TEXT,
    review_reason TEXT,
    attempts_json TEXT,
    candidates_json TEXT,
    error TEXT,
    processed_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_rows_status ON rows(status);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class State:
    def __init__(self, path=None):
        self.path = str(path or config.DB_PATH)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    # --- ingest ---------------------------------------------------------
    def seed_rows(self, rows):
        """Insert workbook rows; never overwrites existing progress."""
        with self._lock:
            for r in rows:
                self._conn.execute(
                    "INSERT OR IGNORE INTO rows (id, account, address, city, state, zip, updated_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (r["id"], r["account"], r["address"], r["city"], r["state"], r["zip"], _now()),
                )
            self._conn.commit()

    def reset_stuck_processing(self):
        with self._lock:
            cur = self._conn.execute(
                "UPDATE rows SET status='pending', updated_at=? WHERE status='processing'", (_now(),)
            )
            self._conn.commit()
            return cur.rowcount

    # --- runtime ----------------------------------------------------------
    def pending_ids(self, limit=None, include_failed=False):
        q = "SELECT id FROM rows WHERE status='pending'"
        if include_failed:
            q = "SELECT id FROM rows WHERE status IN ('pending','failed')"
        q += " ORDER BY id"
        if limit:
            q += f" LIMIT {int(limit)}"
        with self._lock:
            return [r["id"] for r in self._conn.execute(q).fetchall()]

    def get_row(self, row_id):
        with self._lock:
            r = self._conn.execute("SELECT * FROM rows WHERE id=?", (row_id,)).fetchone()
        return dict(r) if r else None

    def mark_processing(self, row_id):
        self.update_row(row_id, status="processing")

    def update_row(self, row_id, **fields):
        fields["updated_at"] = _now()
        for k in ("attempts_json", "candidates_json"):
            if k in fields and not isinstance(fields[k], (str, type(None))):
                fields[k] = json.dumps(fields[k], ensure_ascii=False)
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values()) + [row_id]
        with self._lock:
            self._conn.execute(f"UPDATE rows SET {cols} WHERE id=?", vals)
            self._conn.commit()

    def counts(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM rows GROUP BY status"
            ).fetchall()
        out = {s: 0 for s in STATUSES}
        for r in rows:
            out[r["status"]] = r["n"]
        out["total"] = sum(out[s] for s in STATUSES)
        return out

    def all_rows(self):
        with self._lock:
            return [dict(r) for r in self._conn.execute("SELECT * FROM rows ORDER BY id").fetchall()]

    def set_meta(self, key, value):
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
            self._conn.commit()

    def get_meta(self, key, default=None):
        with self._lock:
            r = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def close(self):
        with self._lock:
            self._conn.commit()
            self._conn.close()
