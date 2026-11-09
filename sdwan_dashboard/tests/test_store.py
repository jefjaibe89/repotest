"""Tests for the SQLite store."""

import sqlite3
import time

import store


def test_latest_roundtrip():
    store.set_latest({"summary": {"total_devices": 3}}, 1000.0, None, 0)
    latest = store.get_latest()
    assert latest["payload"]["summary"]["total_devices"] == 3
    assert latest["error"] is None


def test_failure_preserves_last_good_payload():
    """A failed poll must not wipe the data operators are still looking at."""
    store.set_latest({"summary": {"total_devices": 7}}, time.time(), None, 0)
    store.record_failure("vManage unreachable", 3)

    latest = store.get_latest()
    assert latest["payload"]["summary"]["total_devices"] == 7
    assert latest["error"] == "vManage unreachable"
    assert latest["consecutive_failures"] == 3


def test_history_is_returned_oldest_first():
    now = time.time()
    for i in range(5):
        store.add_snapshot(now - i, {"score": 90 + i, "grade": "healthy"}, {})

    rows = store.get_history(hours=1)
    timestamps = [r["ts"] for r in rows]
    assert timestamps == sorted(timestamps), "charts need chronological order"


def test_history_limit_keeps_newest_samples():
    now = time.time()
    for i in range(10):
        store.add_snapshot(now + i, {"score": i, "grade": "healthy"}, {})

    rows = store.get_history(hours=1, limit=3)
    assert len(rows) == 3
    assert [r["score"] for r in rows] == [7, 8, 9]


def test_history_excludes_samples_outside_the_window():
    now = time.time()
    store.add_snapshot(now - 7200, {"score": 10, "grade": "critical"}, {})
    store.add_snapshot(now, {"score": 99, "grade": "healthy"}, {})

    scores = [r["score"] for r in store.get_history(hours=1)]
    assert 10 not in scores
    assert 99 in scores


def test_prune_deletes_old_rows():
    old = time.time() - 30 * 86400
    store.add_snapshot(old, {"score": 1, "grade": "critical"}, {})
    store.add_device_samples(old, [{"system_ip": "9.9.9.9", "hostname": "old"}])

    store.prune(retention_days=7)

    assert all(r["ts"] > old for r in store.get_history(hours=24 * 365))
    assert store.get_device_history("9.9.9.9", hours=24 * 365) == []


def test_device_history_is_scoped_to_one_device():
    # IPs outside the mock fabric, so the seeding poll's samples cannot bleed in.
    now = time.time()
    store.add_device_samples(now, [
        {"system_ip": "198.51.100.1", "hostname": "a", "reachability": "reachable", "cpu": 10, "memory": 20},
        {"system_ip": "198.51.100.2", "hostname": "b", "reachability": "reachable", "cpu": 30, "memory": 40},
    ])

    rows = store.get_device_history("198.51.100.1", hours=1)
    assert rows and all(r["cpu"] == 10 for r in rows)


def test_device_samples_skips_rows_without_an_ip():
    """Rows keyed on a missing system IP would collide; they are dropped instead."""
    store.add_device_samples(time.time(), [{"hostname": "no-ip"}])  # must not raise


def test_alert_state_roundtrip_and_clear():
    now = time.time()
    store.upsert_alert("device:down:1.1.1.1", "Critical", "down", now, now)
    assert "device:down:1.1.1.1" in store.get_alert_state()

    store.clear_alerts(["device:down:1.1.1.1"])
    assert "device:down:1.1.1.1" not in store.get_alert_state()


# ------------------------------------------------- concurrent worker boot
# gunicorn starts its workers at the same instant and every one of them opens
# this database immediately. Without a busy timeout that raised "database is
# locked" on roughly one boot in ten — a worker dying at import for no lasting
# reason, which is what made the Docker CI job flaky.
def _boot(db_path, lock_path, queue):
    import os
    import sys
    import traceback

    os.environ.update(DB_PATH=str(db_path), POLLER_LOCK_PATH=str(lock_path),
                      SDWAN_MODE="mock")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        import app  # noqa: F401  (importing runs bootstrap)
        queue.put(None)
    except Exception:
        queue.put(traceback.format_exc())


def _boot_together(tmp_path, workers, seed=None):
    import multiprocessing as mp

    ctx = mp.get_context("spawn")
    db = tmp_path / "concurrent.db"
    lock = tmp_path / "concurrent.lock"
    if seed:
        seed(db)

    queue = ctx.Queue()
    procs = [ctx.Process(target=_boot, args=(db, lock, queue)) for _ in range(workers)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(90)
    return [queue.get() for _ in procs]


def test_workers_starting_together_do_not_crash(tmp_path):
    failures = [f for f in _boot_together(tmp_path, workers=4) if f]
    assert not failures, f"a worker died during startup:\n{failures[0]}"


def test_workers_can_migrate_an_older_database_together(tmp_path):
    """The upgrade path is the one that adds a column, so race it too."""
    def legacy(path):
        conn = sqlite3.connect(path)
        conn.execute(
            """CREATE TABLE latest (
                   id INTEGER PRIMARY KEY CHECK (id = 1),
                   payload TEXT NOT NULL, fetched_at REAL NOT NULL,
                   error TEXT, consecutive_failures INTEGER NOT NULL DEFAULT 0)"""
        )
        conn.execute("INSERT INTO latest VALUES (1, '{}', 0, NULL, 0)")
        conn.commit()
        conn.close()

    failures = [f for f in _boot_together(tmp_path, workers=4, seed=legacy) if f]
    assert not failures, f"a worker died migrating:\n{failures[0]}"


def test_migration_is_idempotent():
    """Running it twice must not raise on the second pass."""
    store.init()
    store.init()
    latest = store.get_latest()
    assert latest is None or "error_key" in latest
