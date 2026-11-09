"""Serving the dashboard under a path prefix.

Behind a proxy that mounts it on a subpath, absolute URLs from the root point
outside the mount: every asset 404s and the page renders as an unstyled
skeleton. That failure is silent — no error anyone can read, just a blank
page — so these pin the mount point down.
"""

import importlib

import pytest

import config


@pytest.fixture
def mounted(monkeypatch):
    """Serve the app as if a proxy mounted it on /dash."""
    monkeypatch.setattr(config, "APPLICATION_ROOT", "/dash")
    return "/dash"


def test_at_the_root_nothing_is_prefixed(client):
    body = client.get("/").get_data(as_text=True)
    assert 'href="/static/css/style.css"' in body
    assert 'const BASE = ""' in body


def test_a_mount_point_reaches_every_generated_url(client, mounted):
    body = client.get(f"{mounted}/").get_data(as_text=True)
    assert f'href="{mounted}/static/css/style.css"' in body
    assert f'src="{mounted}/static/js/common.js"' in body
    assert f'href="{mounted}/qos"' in body


def test_the_browser_is_told_where_the_app_is_mounted(client, mounted):
    """Panels fetch from JavaScript, so the mount point has to reach them."""
    body = client.get(f"{mounted}/").get_data(as_text=True)
    assert f'const BASE = "{mounted}"' in body


def test_the_csv_link_is_mounted_too(client, mounted):
    """It is markup rather than a fetch, so it needs its own handling."""
    body = client.get(f"{mounted}/").get_data(as_text=True)
    assert f'href="{mounted}/api/export/devices.csv"' in body


def test_views_and_api_answer_under_the_mount(client, mounted):
    for path in ("/", "/qos", "/links", "/aar", "/compat",
                 "/api/health", "/api/summary", "/healthz"):
        assert client.get(f"{mounted}{path}").status_code == 200, path


def test_the_login_redirect_stays_inside_the_mount(client, mounted, monkeypatch):
    """A redirect to /login would leave the mount and 404 on the proxy."""
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "x")
    location = client.get(f"{mounted}/").headers["Location"]
    assert location.startswith(f"{mounted}/login"), location


def test_a_trusted_proxy_can_declare_the_prefix(client, monkeypatch):
    """nginx and traefik send X-Forwarded-Prefix rather than being configured
    twice; honoured only when the proxy is trusted."""
    monkeypatch.setattr(config, "APPLICATION_ROOT", "")
    monkeypatch.setattr(config, "TRUST_PROXY_HEADERS", True)

    body = client.get("/", headers={"X-Forwarded-Prefix": "/sdwan"}).get_data(as_text=True)
    assert 'href="/sdwan/static/css/style.css"' in body


def test_an_untrusted_proxy_header_is_ignored(client, monkeypatch):
    """Otherwise any client could move every link on the page."""
    monkeypatch.setattr(config, "APPLICATION_ROOT", "")
    monkeypatch.setattr(config, "TRUST_PROXY_HEADERS", False)

    body = client.get("/", headers={"X-Forwarded-Prefix": "/evil"}).get_data(as_text=True)
    assert "/evil/" not in body
    assert 'href="/static/css/style.css"' in body


def test_configuration_wins_over_the_header(client, monkeypatch):
    monkeypatch.setattr(config, "APPLICATION_ROOT", "/dash")
    monkeypatch.setattr(config, "TRUST_PROXY_HEADERS", True)

    body = client.get("/dash/", headers={"X-Forwarded-Prefix": "/other"}).get_data(as_text=True)
    assert "/dash/static/css/style.css" in body
    assert "/other/" not in body


@pytest.mark.parametrize("configured,expected", [
    ("/dash", "/dash"), ("/dash/", "/dash"), ("", ""),
])
def test_a_trailing_slash_does_not_double_up(monkeypatch, configured, expected):
    monkeypatch.setenv("APPLICATION_ROOT", configured)
    assert importlib.reload(config).APPLICATION_ROOT == expected
    monkeypatch.delenv("APPLICATION_ROOT")
    importlib.reload(config)
