"""Prometheus exposition and the security headers around it."""

import re

import pytest

import config
import metrics
import poller
from sdwan_client import MockSDWANClient


@pytest.fixture
def scrape(client, monkeypatch):
    monkeypatch.setattr(config, "METRICS_ENABLED", True)
    monkeypatch.setattr(config, "METRICS_TOKEN", "")
    return lambda **kw: client.get("/metrics", **kw)


# ----------------------------------------------------------- exposition
def test_metrics_are_off_by_default(client, monkeypatch):
    """They carry the same inventory the dashboard shows, so exposing them
    has to be a deliberate choice."""
    monkeypatch.setattr(config, "METRICS_ENABLED", False)
    assert client.get("/metrics").status_code == 404


def test_content_type_is_the_prometheus_one(scrape):
    resp = scrape()
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "text/plain; version=0.0.4; charset=utf-8"
    assert resp.headers["Content-Type"].count("charset") == 1, "no duplicated charset"


def test_every_family_declares_help_and_type(scrape):
    body = scrape().get_data(as_text=True)
    helps = {l.split()[2] for l in body.splitlines() if l.startswith("# HELP")}
    types = {l.split()[2] for l in body.splitlines() if l.startswith("# TYPE")}
    assert helps == types, "HELP and TYPE must be declared for the same families"
    assert len(helps) > 20


def test_sample_names_match_their_declared_families(scrape):
    body = scrape().get_data(as_text=True)
    declared = {l.split()[2] for l in body.splitlines() if l.startswith("# TYPE")}
    for line in body.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        name = re.match(r"([a-zA-Z_:][a-zA-Z0-9_:]*)", line).group(1)
        assert name in declared, f"{name} has no TYPE declaration"


def test_names_follow_prometheus_conventions(scrape):
    body = scrape().get_data(as_text=True)
    for line in body.splitlines():
        if not line.startswith("# TYPE"):
            continue
        name = line.split()[2]
        assert name.startswith("sdwan_"), name
        assert re.fullmatch(r"[a-z_]+", name), f"{name} should be lower snake_case"


def test_counters_are_named_total(scrape):
    for line in scrape().get_data(as_text=True).splitlines():
        if line.startswith("# TYPE") and line.split()[3] == "counter":
            assert line.split()[2].endswith("_total")


# -------------------------------------------------------------- content
def test_collection_health_is_reported_separately_from_fabric_health(scrape):
    """A scraper must be able to tell a healthy fabric from a stopped poller."""
    body = scrape().get_data(as_text=True)
    assert re.search(r"^sdwan_up 1$", body, re.M)
    assert re.search(r"^sdwan_poll_age_seconds ", body, re.M)
    assert re.search(r"^sdwan_poll_consecutive_failures 0$", body, re.M)


def test_fabric_score_and_categories_are_exposed(scrape):
    body = scrape().get_data(as_text=True)
    assert re.search(r"^sdwan_fabric_health_score \d+", body, re.M)
    assert 'sdwan_fabric_health_category_score{category="reachability"}' in body


@pytest.mark.parametrize("name", [
    "sdwan_devices_total", "sdwan_bfd_sessions", "sdwan_alarms_active",
    "sdwan_device_cpu_percent", "sdwan_qos_dropped_packets_total",
    "sdwan_link_utilisation_percent", "sdwan_aar_tunnel_latency_ms",
    "sdwan_aar_compliance_percent", "sdwan_enhanced_aar_sla_classes",
])
def test_each_view_contributes_metrics(scrape, name):
    assert name in scrape().get_data(as_text=True)


def test_missing_readings_are_omitted_not_zeroed():
    """An unreachable device reports no CPU; 0% would look like an idle one."""
    payload = poller.collect(MockSDWANClient())
    body = metrics.render(payload, {"age_seconds": 1, "error": None,
                                    "consecutive_failures": 0})

    offline = [d for d in payload["devices"] if d["cpu"] is None]
    assert offline, "the mock fabric has an unreachable device"
    for device in offline:
        assert f'sdwan_device_cpu_percent{{hostname="{device["hostname"]}"' not in body


def test_a_stopped_poller_still_reports_a_series(client, monkeypatch):
    """A gap is something a scraper has to interpret; sdwan_up 0 is not."""
    monkeypatch.setattr(config, "METRICS_ENABLED", True)
    monkeypatch.setattr(config, "METRICS_TOKEN", "")
    import store
    monkeypatch.setattr(store, "get_latest", lambda: None)

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "sdwan_up 0" in resp.get_data(as_text=True)


# --------------------------------------------------------- label safety
@pytest.mark.parametrize("hostname", ['edge"1', "edge\\1", "edge\n1", 'a"b\\c'])
def test_label_values_from_vmanage_cannot_break_the_format(hostname):
    """Hostnames come from the controller, exactly like the XSS sinks did.

    The format is line-based and quote-delimited, so a quote, a backslash or a
    newline in a label value has to be escaped rather than removed — and the
    sample has to stay on one parseable line either way.
    """
    payload = {"devices": [{"hostname": hostname, "system_ip": "1.1.1.1",
                            "site_id": "1", "device_type": "vedge",
                            "reachability": "reachable", "cpu": 10, "memory": 20}]}
    body = metrics.render(payload, {"age_seconds": 0, "error": None,
                                    "consecutive_failures": 0})

    lines = [l for l in body.splitlines() if l.startswith("sdwan_device_cpu_percent{")]
    assert len(lines) == 1, "the value must not split the sample across lines"
    line = lines[0]

    value = re.search(r'hostname="((?:[^"\\]|\\.)*)"', line)
    assert value, f"the label is not properly quoted: {line}"
    # Every quote and backslash inside the value carries an escape.
    assert not re.search(r'(?<!\\)"', value.group(1))
    assert re.fullmatch(r'[a-z_]+\{.*\} [\d.eE+-]+', line), line


def test_a_newline_in_a_label_cannot_forge_a_metric():
    payload = {"devices": [{"hostname": "x\nsdwan_fake_metric 999",
                            "system_ip": "1.1.1.1", "site_id": "1",
                            "device_type": "vedge", "reachability": "reachable",
                            "cpu": 10, "memory": 20}]}
    body = metrics.render(payload, {"age_seconds": 0, "error": None,
                                    "consecutive_failures": 0})
    assert not re.search(r"^sdwan_fake_metric", body, re.M)


# --------------------------------------------------------------- access
def test_no_token_means_open(client, monkeypatch):
    monkeypatch.setattr(config, "METRICS_ENABLED", True)
    monkeypatch.setattr(config, "METRICS_TOKEN", "")
    assert client.get("/metrics").status_code == 200


def test_token_is_required_when_configured(client, monkeypatch):
    monkeypatch.setattr(config, "METRICS_ENABLED", True)
    monkeypatch.setattr(config, "METRICS_TOKEN", "s3cret")

    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Basic s3cret"}).status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_metrics_do_not_sit_behind_the_session_login(client, monkeypatch):
    """A scraper has no session, so the dashboard login cannot guard this."""
    monkeypatch.setattr(config, "METRICS_ENABLED", True)
    monkeypatch.setattr(config, "METRICS_TOKEN", "")
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "login-required")

    assert client.get("/").status_code == 302, "the dashboard is gated"
    assert client.get("/metrics").status_code == 200, "the scrape endpoint is not"


# ------------------------------------------------------ security headers
def test_csp_is_sent_with_a_nonce(client):
    resp = client.get("/")
    csp = resp.headers["Content-Security-Policy"]
    nonce = re.search(r"'nonce-([\w-]+)'", csp).group(1)
    assert f'<script nonce="{nonce}"' in resp.get_data(as_text=True)


def test_the_nonce_changes_every_response(client):
    first = re.search(r"'nonce-([\w-]+)'", client.get("/").headers["Content-Security-Policy"])
    second = re.search(r"'nonce-([\w-]+)'", client.get("/").headers["Content-Security-Policy"])
    assert first.group(1) != second.group(1)


@pytest.mark.parametrize("directive", [
    "default-src 'self'", "object-src 'none'", "base-uri 'self'",
    "frame-ancestors 'none'", "form-action 'self'", "connect-src 'self'",
])
def test_csp_locks_down_the_dangerous_directives(client, directive):
    assert directive in client.get("/").headers["Content-Security-Policy"]


def test_inline_script_is_not_blanket_allowed(client):
    """'unsafe-inline' in script-src would defeat the whole point."""
    csp = client.get("/").headers["Content-Security-Policy"]
    script_src = next(d for d in csp.split("; ") if d.startswith("script-src"))
    assert "'unsafe-inline'" not in script_src


@pytest.mark.parametrize("header,value", [
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "same-origin"),
    ("X-Frame-Options", "DENY"),
])
def test_other_security_headers(client, header, value):
    assert client.get("/").headers[header] == value


def test_csp_can_be_turned_off_for_debugging(client, monkeypatch):
    monkeypatch.setattr(config, "CSP_ENABLED", False)
    assert "Content-Security-Policy" not in client.get("/").headers
