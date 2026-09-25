"""Tests for the SQLite store."""

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
