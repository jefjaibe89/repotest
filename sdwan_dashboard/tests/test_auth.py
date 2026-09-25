"""Tests for the optional dashboard login."""

import pytest

import auth
import config


@pytest.fixture
def secured(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "s3cret")
    monkeypatch.setattr(config, "DASHBOARD_USER", "admin")
    return True


def test_auth_is_off_when_no_password_is_set(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "")
    assert auth.enabled() is False


def test_auth_is_on_once_a_password_is_set(secured):
    assert auth.enabled() is True


def test_credentials_must_both_match(secured):
    assert auth.check_credentials("admin", "s3cret") is True
    assert auth.check_credentials("admin", "wrong") is False
    assert auth.check_credentials("root", "s3cret") is False
    assert auth.check_credentials(None, None) is False


def test_dashboard_is_open_when_auth_is_disabled(client, monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "")
    assert client.get("/").status_code == 200
    assert client.get("/api/summary").status_code == 200


def test_browser_is_redirected_to_login(client, secured):
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_api_returns_401_rather_than_a_redirect(client, secured):
    """XHR callers need a status they can act on, not an HTML login page."""
    resp = client.get("/api/summary")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "auth_required"


def test_healthz_stays_open_for_container_probes(client, secured):
    assert client.get("/healthz").status_code == 200


def test_successful_login_grants_access(client, secured):
    client.post("/login", data={"username": "admin", "password": "s3cret"})
    assert client.get("/").status_code == 200
    assert client.get("/api/summary").status_code == 200


def test_failed_login_denies_access(client, secured):
    client.post("/login", data={"username": "admin", "password": "nope"})
    assert client.get("/").status_code == 302


def test_logout_revokes_access(client, secured):
    client.post("/login", data={"username": "admin", "password": "s3cret"})
    client.get("/logout")
    assert client.get("/").status_code == 302


def test_next_parameter_cannot_redirect_off_site(client, secured):
    """An absolute 'next' would turn the login into an open redirect."""
    resp = client.post(
        "/login?next=https://evil.example.com/steal",
        data={"username": "admin", "password": "s3cret"},
    )
    assert "evil.example.com" not in resp.headers["Location"]


def test_next_parameter_keeps_relative_paths(client, secured):
    resp = client.post(
        "/login?next=/api/status", data={"username": "admin", "password": "s3cret"}
    )
    assert resp.headers["Location"].endswith("/api/status")
