"""
SQLite persistence for the dashboard.

Holds two things: the latest full snapshot of the fabric (so every web worker
serves identical data without touching vManage) and a time series of health
samples (so the UI can answer "when did this start?").

Every call opens its own connection. At dashboard polling rates that costs
nothing and sidesteps SQLite's thread-affinity rules entirely.
"""

import json
import sqlite3
import time
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS latest (
    id                   INTEGER PRIMARY KEY CHECK (id = 1),
    payload              TEXT    NOT NULL,
    fetched_at           REAL    NOT NULL,
    error                TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS snapshots (
    ts               REAL PRIMARY KEY,
    score            INTEGER,
    grade            TEXT,
    total_devices    INTEGER,
    reachable        INTEGER,
    unreachable      INTEGER,
    bfd_up           INTEGER,
    bfd_down         INTEGER,
    alarms_critical  INTEGER,
    alarms_major     INTEGER,
    alarms_minor     INTEGER
);

CREATE TABLE IF NOT EXISTS device_samples (
    ts           REAL NOT NULL,
    system_ip    TEXT NOT NULL,
    hostname     TEXT,
    reachability TEXT,
    cpu          REAL,
    memory       REAL,
    PRIMARY KEY (ts, system_ip)
);

CREATE INDEX IF NOT EXISTS idx_device_samples_ip ON device_samples(system_ip, ts);

CREATE TABLE IF NOT EXISTS alert_state (
    key           TEXT PRIMARY KEY,
    severity      TEXT,
    message       TEXT,
    first_seen    REAL,
    last_notified REAL
);

-- Login throttling lives in the database, not in memory: under gunicorn each
-- worker has its own memory, so an in-process counter would let an attacker
-- get N attempts per worker instead of N in total.
CREATE TABLE IF NOT EXISTS login_attempts (
    client_id     TEXT PRIMARY KEY,
    failures      INTEGER NOT NULL DEFAULT 0,
    first_failure REAL,
    locked_until  REAL
);
"""


@contextmanager
def _connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        # WAL lets the poller write while web workers read.
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with _connect() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------- latest state
def set_latest(payload: dict, fetched_at: float, error: str | None, failures: int):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO latest (id, payload, fetched_at, error, consecutive_failures)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                payload = excluded.payload,
                fetched_at = excluded.fetched_at,
                error = excluded.error,
                consecutive_failures = excluded.consecutive_failures
            """,
            (json.dumps(payload), fetched_at, error, failures),
        )


def record_failure(error: str, failures: int):
    """Mark the poll as failed without discarding the last good payload."""
    with _connect() as conn:
        updated = conn.execute(
            "UPDATE latest SET error = ?, consecutive_failures = ? WHERE id = 1",
            (error, failures),
        ).rowcount
        if not updated:
            # Nothing has ever been fetched, so there is no payload to preserve.
            conn.execute(
                """INSERT INTO latest (id, payload, fetched_at, error, consecutive_failures)
                   VALUES (1, ?, 0, ?, ?)""",
                (json.dumps({}), error, failures),
            )


def get_latest() -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM latest WHERE id = 1").fetchone()
    if row is None:
        return None
    return {
        "payload": json.loads(row["payload"]),
        "fetched_at": row["fetched_at"],
        "error": row["error"],
        "consecutive_failures": row["consecutive_failures"],
    }


# ------------------------------------------------------------------- snapshots
def add_snapshot(ts: float, health: dict, summary: dict):
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO snapshots
                (ts, score, grade, total_devices, reachable, unreachable,
                 bfd_up, bfd_down, alarms_critical, alarms_major, alarms_minor)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                ts,
                health.get("score"),
                health.get("grade"),
                summary.get("total_devices"),
                summary.get("reachable"),
                summary.get("unreachable"),
                summary.get("bfd_up"),
                summary.get("bfd_down"),
                summary.get("alarms_critical"),
                summary.get("alarms_major"),
                summary.get("alarms_minor"),
            ),
        )


def add_device_samples(ts: float, devices: list[dict]):
    rows = [
        (ts, d.get("system_ip"), d.get("hostname"), d.get("reachability"),
         d.get("cpu"), d.get("memory"))
        for d in devices
        if d.get("system_ip")
    ]
    if not rows:
        return
    with _connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO device_samples
               (ts, system_ip, hostname, reachability, cpu, memory)
               VALUES (?,?,?,?,?,?)""",
            rows,
        )


def get_history(hours: int = 24, limit: int = 500) -> list[dict]:
    cutoff = time.time() - hours * 3600
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM snapshots WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
    # Query descending so the limit keeps the newest rows, then flip for plotting.
    return [dict(r) for r in reversed(rows)]


def get_device_history(system_ip: str, hours: int = 24, limit: int = 500) -> list[dict]:
    cutoff = time.time() - hours * 3600
    with _connect() as conn:
        rows = conn.execute(
            """SELECT ts, reachability, cpu, memory FROM device_samples
               WHERE system_ip = ? AND ts >= ? ORDER BY ts DESC LIMIT ?""",
            (system_ip, cutoff, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def prune(retention_days: int | None = None):
    days = retention_days if retention_days is not None else config.HISTORY_RETENTION_DAYS
    cutoff = time.time() - days * 86400
    with _connect() as conn:
        conn.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))
        conn.execute("DELETE FROM device_samples WHERE ts < ?", (cutoff,))


# ----------------------------------------------------------------- alert state
def get_alert_state() -> dict[str, dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM alert_state").fetchall()
    return {r["key"]: dict(r) for r in rows}


def upsert_alert(key: str, severity: str, message: str, first_seen: float, last_notified: float):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO alert_state (key, severity, message, first_seen, last_notified)
               VALUES (?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                   severity = excluded.severity,
                   message = excluded.message,
                   last_notified = excluded.last_notified""",
            (key, severity, message, first_seen, last_notified),
        )


def clear_alerts(keys: list[str]):
    if not keys:
        return
    with _connect() as conn:
        conn.executemany("DELETE FROM alert_state WHERE key = ?", [(k,) for k in keys])


# -------------------------------------------------------------- login attempts
def get_login_attempt(client_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM login_attempts WHERE client_id = ?", (client_id,)
        ).fetchone()
    return dict(row) if row else None


def record_login_failure(client_id: str, now: float, window: float,
                         max_attempts: int, lockout: float) -> dict:
    """Count a failed attempt and lock the client out once it crosses the limit."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM login_attempts WHERE client_id = ?", (client_id,)
        ).fetchone()

        # Old failures expire, so occasional typos never accumulate into a lockout.
        if row is None or (now - (row["first_failure"] or 0)) > window:
            failures, first_failure = 1, now
        else:
            failures, first_failure = row["failures"] + 1, row["first_failure"]

        locked_until = now + lockout if failures >= max_attempts else None

        conn.execute(
            """INSERT INTO login_attempts (client_id, failures, first_failure, locked_until)
               VALUES (?,?,?,?)
               ON CONFLICT(client_id) DO UPDATE SET
                   failures = excluded.failures,
                   first_failure = excluded.first_failure,
                   locked_until = excluded.locked_until""",
            (client_id, failures, first_failure, locked_until),
        )

    return {"failures": failures, "locked_until": locked_until}


def clear_login_attempts(client_id: str):
    with _connect() as conn:
        conn.execute("DELETE FROM login_attempts WHERE client_id = ?", (client_id,))


def prune_login_attempts(before: float):
    """Drop records whose lockout has expired and whose window has closed."""
    with _connect() as conn:
        conn.execute(
            """DELETE FROM login_attempts
               WHERE COALESCE(locked_until, 0) < ? AND COALESCE(first_failure, 0) < ?""",
            (before, before),
        )
