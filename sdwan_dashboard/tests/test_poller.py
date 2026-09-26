"""Tests for the background poller."""

import poller
import store
from sdwan_client import MockSDWANClient, SDWANConnectionError


def test_collect_returns_every_panel():
    payload = poller.collect(MockSDWANClient())
    assert set(payload) >= {
        "summary", "health", "devices", "alarms", "bfd", "omp",
        "control", "interfaces", "tunnels",
    }


def test_collect_normalizes_devices():
    payload = poller.collect(MockSDWANClient())
    for d in payload["devices"]:
        assert "system_ip" in d and "system-ip" not in d


def test_poll_once_persists_snapshot_and_samples():
    assert poller.poll_once(MockSDWANClient) is True

    latest = store.get_latest()
    assert latest["error"] is None
    assert latest["payload"]["summary"]["total_devices"] > 0
    assert store.get_history(hours=1)


def test_failed_poll_records_error_without_losing_data():
    poller.poll_once(MockSDWANClient)          # establish good data
    good = store.get_latest()["payload"]["summary"]["total_devices"]

    def broken():
        raise SDWANConnectionError("controller down")

    assert poller.poll_once(broken) is False

    latest = store.get_latest()
    assert latest["error"] == "controller down"
    assert latest["consecutive_failures"] >= 1
    assert latest["payload"]["summary"]["total_devices"] == good


def test_unexpected_exception_does_not_propagate():
    """The poller thread must survive any bug in collection."""
    def exploding():
        raise ValueError("boom")

    assert poller.poll_once(exploding) is False
    assert "boom" in store.get_latest()["error"]


def test_consecutive_failures_reset_on_success():
    def broken():
        raise SDWANConnectionError("down")

    poller.poll_once(broken)
    poller.poll_once(broken)
    assert store.get_latest()["consecutive_failures"] >= 2

    poller.poll_once(MockSDWANClient)
    assert store.get_latest()["consecutive_failures"] == 0


def test_alert_failure_does_not_fail_the_poll(monkeypatch):
    """Data collection must not depend on the notifier working."""
    import alerts

    def boom(*a, **k):
        raise RuntimeError("notifier exploded")

    monkeypatch.setattr(alerts, "process", boom)
    assert poller.poll_once(MockSDWANClient) is True


def test_lock_is_exclusive(tmp_path, monkeypatch):
    """Only one process may poll, or gunicorn workers would duplicate writes."""
    import config

    monkeypatch.setattr(config, "POLLER_LOCK_PATH", str(tmp_path / "p.lock"))
    monkeypatch.setattr(poller, "_lock_handle", None)

    assert poller.acquire_lock() is True

    import subprocess
    import sys
    code = (
        "import fcntl,sys\n"
        f"h=open({str(tmp_path / 'p.lock')!r},'w')\n"
        "try:\n"
        "    fcntl.flock(h, fcntl.LOCK_EX | fcntl.LOCK_NB); print('acquired')\n"
        "except OSError:\n"
        "    print('blocked')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.stdout.strip() == "blocked"
