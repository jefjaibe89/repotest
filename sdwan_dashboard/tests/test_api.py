"""Endpoint contract tests. All run against the built-in mock client."""

import pytest


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_index_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Catalyst SD-WAN" in resp.data


@pytest.mark.parametrize(
    "path",
    [
        "/api/summary",
        "/api/health",
        "/api/devices",
        "/api/alarms",
        "/api/bfd",
        "/api/omp",
        "/api/interfaces",
        "/api/control",
        "/api/tunnels",
        "/api/history",
        "/api/status",
    ],
)
def test_endpoint_returns_json(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.is_json


def test_panels_report_data_age(client):
    """The UI needs to know how old the polled data is, not just that it arrived."""
    resp = client.get("/api/summary")
    assert "X-Data-Age" in resp.headers
    assert resp.headers["X-Data-Stale"] in ("0", "1")


def test_status_describes_the_poller(client):
    status = client.get("/api/status").get_json()
    assert status["has_data"] is True
    assert status["stale"] is False
    assert status["poll_interval"] > 0


def test_tunnels_have_the_expected_shape(client):
    tunnels = client.get("/api/tunnels").get_json()
    assert tunnels
    for t in tunnels:
        assert {"system-ip", "remote-system-ip", "state", "local-color"} <= set(t)


def test_history_window_is_clamped(client):
    """An absurd window must not turn into an unbounded scan."""
    assert client.get("/api/history?hours=99999").status_code == 200
    assert client.get("/api/history?hours=-5").status_code == 200


def test_device_detail_returns_every_section(client):
    detail = client.get("/api/device/10.0.1.1").get_json()
    assert set(detail) >= {
        "device", "interfaces", "tunnels", "control_connections",
        "omp_routes", "alarms", "history",
    }
    assert detail["device"]["system_ip"] == "10.0.1.1"


def test_device_detail_tunnels_belong_to_that_device(client):
    detail = client.get("/api/device/10.0.1.1").get_json()
    assert detail["tunnels"]
    assert all(t["system-ip"] == "10.0.1.1" for t in detail["tunnels"])


def test_unknown_device_returns_404(client):
    resp = client.get("/api/device/203.0.113.99")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"


def test_summary_counts_are_consistent(client, mock_data):
    data = client.get("/api/summary").get_json()
    assert data["total_devices"] == len(mock_data.get_device_list())
    assert data["reachable"] + data["unreachable"] == data["total_devices"]
    assert data["omp_up"] <= data["omp_total"]


def test_summary_counts_only_unacknowledged_alarms(client, mock_data):
    """An acknowledged alarm must not inflate the active-alarm badges."""
    data = client.get("/api/summary").get_json()
    expected = sum(
        1
        for a in mock_data.get_alarms()
        if a["severity"] == "Minor" and not a.get("acknowledged")
    )
    assert data["alarms_minor"] == expected


def test_devices_are_normalized(client):
    devices = client.get("/api/devices").get_json()
    assert devices, "mock client should return devices"
    for d in devices:
        # The API contract is snake_case; vManage's hyphenated keys must not leak.
        assert "system_ip" in d and "system-ip" not in d
        assert set(d) >= {"hostname", "device_type", "reachability", "cpu", "memory"}


def test_offline_devices_report_no_resource_metrics(client):
    devices = client.get("/api/devices").get_json()
    offline = [d for d in devices if d["reachability"] == "unreachable"]
    assert offline, "mock data should include an unreachable device"
    for d in offline:
        assert d["cpu"] is None and d["memory"] is None


def test_export_devices_csv(client):
    resp = client.get("/api/export/devices.csv")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert "attachment" in resp.headers["Content-Disposition"]

    lines = resp.get_data(as_text=True).strip().splitlines()
    assert lines[0].startswith("hostname,system_ip")
    assert len(lines) == len(client.get("/api/devices").get_json()) + 1
