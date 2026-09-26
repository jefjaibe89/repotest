"""Tests for alert rules, deduplication and delivery."""

import time

import pytest

import alerts
import config
import store


@pytest.fixture(autouse=True)
def clean_alert_state():
    store.clear_alerts(list(store.get_alert_state()))
    yield
    store.clear_alerts(list(store.get_alert_state()))


@pytest.fixture
def sent(monkeypatch):
    """Capture deliveries instead of posting them."""
    captured = []

    def fake_send(alert, resolved=False):
        captured.append({**alert, "resolved": resolved})
        return True

    monkeypatch.setattr(alerts, "send", fake_send)
    monkeypatch.setattr(config, "ALERTS_ENABLED", True)
    return captured


HEALTHY = ({"score": 100, "grade": "healthy"}, {"alarms_critical": 0}, [], [])


def test_healthy_fabric_fires_nothing(sent):
    result = alerts.process(*HEALTHY)
    assert result["firing"] == 0
    assert sent == []


def test_unreachable_device_fires_critical(sent):
    alerts.process(
        {"score": 100, "grade": "healthy"}, {"alarms_critical": 0},
        [{"system_ip": "10.0.0.1", "hostname": "edge1", "reachability": "unreachable"}], [],
    )
    assert len(sent) == 1
    assert sent[0]["severity"] == "Critical"
    assert "edge1" in sent[0]["message"]


def test_low_score_fires(sent, monkeypatch):
    monkeypatch.setattr(config, "ALERT_SCORE_THRESHOLD", 90)
    alerts.process({"score": 60, "grade": "degraded"}, {"alarms_critical": 0}, [], [])
    assert any("score is 60" in a["message"] for a in sent)


def test_repeat_poll_does_not_resend_within_cooldown(sent, monkeypatch):
    monkeypatch.setattr(config, "ALERT_COOLDOWN_SECONDS", 3600)
    devices = [{"system_ip": "10.0.0.1", "hostname": "edge1", "reachability": "unreachable"}]
    args = ({"score": 100, "grade": "healthy"}, {"alarms_critical": 0}, devices, [])

    alerts.process(*args)
    sent.clear()
    alerts.process(*args)  # same condition, still firing

    assert sent == [], "an ongoing problem must not be announced twice"


def test_alert_is_resent_after_cooldown_expires(sent, monkeypatch):
    monkeypatch.setattr(config, "ALERT_COOLDOWN_SECONDS", 0)
    devices = [{"system_ip": "10.0.0.1", "hostname": "edge1", "reachability": "unreachable"}]
    args = ({"score": 100, "grade": "healthy"}, {"alarms_critical": 0}, devices, [])

    alerts.process(*args)
    sent.clear()
    alerts.process(*args)

    assert len(sent) == 1


def test_recovery_sends_resolved_and_clears_state(sent):
    devices = [{"system_ip": "10.0.0.1", "hostname": "edge1", "reachability": "unreachable"}]
    alerts.process({"score": 100, "grade": "healthy"}, {"alarms_critical": 0}, devices, [])
    sent.clear()

    result = alerts.process(*HEALTHY)

    assert result["resolved"] == 1
    assert sent[0]["resolved"] is True
    assert "Recovered" in sent[0]["message"]
    assert store.get_alert_state() == {}


def test_first_seen_is_preserved_across_polls(sent, monkeypatch):
    monkeypatch.setattr(config, "ALERT_COOLDOWN_SECONDS", 0)
    devices = [{"system_ip": "10.0.0.1", "hostname": "edge1", "reachability": "unreachable"}]
    args = ({"score": 100, "grade": "healthy"}, {"alarms_critical": 0}, devices, [])

    alerts.process(*args)
    first = store.get_alert_state()["device:unreachable:10.0.0.1"]["first_seen"]
    time.sleep(0.01)
    alerts.process(*args)
    second = store.get_alert_state()["device:unreachable:10.0.0.1"]["first_seen"]

    assert first == second, "first_seen records when the problem started, not the last poll"


def test_disabled_alerting_is_a_no_op(monkeypatch, sent):
    monkeypatch.setattr(config, "ALERTS_ENABLED", False)
    devices = [{"system_ip": "10.0.0.1", "hostname": "e", "reachability": "unreachable"}]
    result = alerts.process({"score": 10, "grade": "critical"}, {"alarms_critical": 5}, devices, [])
    assert result == {"firing": 0, "notified": 0, "resolved": 0}
    assert sent == []


def test_webhook_failure_does_not_raise(monkeypatch):
    """A dead webhook must never take the poller down with it."""
    import requests

    monkeypatch.setattr(config, "ALERT_WEBHOOK_URL", "http://127.0.0.1:1/nope")
    monkeypatch.setattr(config, "ALERTS_ENABLED", True)

    def boom(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", boom)
    assert alerts.send({"severity": "Critical", "message": "x"}) is False


@pytest.mark.parametrize("fmt", ["slack", "teams", "generic"])
def test_every_format_carries_the_message(fmt):
    alert = {"severity": "Critical", "message": "edge1 is unreachable"}
    payload = alerts.FORMATTERS[fmt](alert, False)
    assert "edge1 is unreachable" in str(payload)
