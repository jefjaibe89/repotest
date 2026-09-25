"""Tests for the optional dashboard login."""

import time

import pytest

import auth
import config
import store


@pytest.fixture
def secured(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "s3cret")
    monkeypatch.setattr(config, "DASHBOARD_USER", "admin")
    # Tests share one database, so start each from a clean throttle state.
    store.prune_login_attempts(time.time() + 10_000)
    return True


def _fail_login(client, times, headers=None):
    for _ in range(times):
        client.post(
            "/login",
            data={"username": "admin", "password": "wrong"},
            headers=headers or {},
        )


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


# --------------------------------------------------------------- throttling
def test_login_locks_out_after_repeated_failures(client, secured, monkeypatch):
    monkeypatch.setattr(config, "LOGIN_MAX_ATTEMPTS", 3)
    _fail_login(client, 3)

    resp = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert resp.status_code == 429


def test_lockout_rejects_even_the_correct_password(client, secured, monkeypatch):
    """Once locked, the right password must not open the door either."""
    monkeypatch.setattr(config, "LOGIN_MAX_ATTEMPTS", 3)
    _fail_login(client, 3)

    resp = client.post("/login", data={"username": "admin", "password": "s3cret"})
    assert resp.status_code == 429
    assert client.get("/").status_code == 302


def test_attempts_below_the_limit_are_not_locked(client, secured, monkeypatch):
    monkeypatch.setattr(config, "LOGIN_MAX_ATTEMPTS", 5)
    _fail_login(client, 2)

    resp = client.post("/login", data={"username": "admin", "password": "s3cret"})
    assert resp.status_code == 302, "a couple of typos must not lock a user out"


def test_successful_login_clears_the_failure_count(client, secured, monkeypatch):
    monkeypatch.setattr(config, "LOGIN_MAX_ATTEMPTS", 3)
    _fail_login(client, 2)
    client.post("/login", data={"username": "admin", "password": "s3cret"})

    assert auth.lockout_remaining("127.0.0.1") == 0
    _fail_login(client, 2)  # counter restarted, so this must not lock
    resp = client.post("/login", data={"username": "admin", "password": "s3cret"})
    assert resp.status_code == 302


def test_old_failures_expire_out_of_the_window(monkeypatch):
    now = time.time()
    store.clear_login_attempts("expiry-test")

    store.record_login_failure("expiry-test", now=now - 10_000, window=900,
                               max_attempts=3, lockout=300)
    store.record_login_failure("expiry-test", now=now - 9_000, window=900,
                               max_attempts=3, lockout=300)
    # Far outside the window, so the counter restarts rather than accumulating.
    result = store.record_login_failure("expiry-test", now=now, window=900,
                                        max_attempts=3, lockout=300)

    assert result["failures"] == 1
    assert result["locked_until"] is None


def test_lockout_expires_after_its_duration():
    now = time.time()
    store.clear_login_attempts("expired-lock")
    store.record_login_failure("expired-lock", now=now - 1000, window=900,
                               max_attempts=1, lockout=300)

    record = store.get_login_attempt("expired-lock")
    assert record["locked_until"] < now, "a 300s lockout set 1000s ago must have elapsed"


def test_forged_forwarded_header_cannot_evade_the_lockout(client, secured, monkeypatch):
    """Spoofing X-Forwarded-For must not hand an attacker a fresh quota."""
    monkeypatch.setattr(config, "TRUST_PROXY_HEADERS", False)
    monkeypatch.setattr(config, "LOGIN_MAX_ATTEMPTS", 3)

    for i in range(3):
        client.post("/login", data={"username": "admin", "password": "wrong"},
                    headers={"X-Forwarded-For": f"10.0.0.{i}"})

    resp = client.post("/login", data={"username": "admin", "password": "wrong"},
                       headers={"X-Forwarded-For": "10.0.0.99"})
    assert resp.status_code == 429


def test_forwarded_header_is_used_when_proxy_is_trusted(client, secured, monkeypatch):
    """Behind a trusted proxy, one client's lockout must not block everyone."""
    monkeypatch.setattr(config, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(config, "LOGIN_MAX_ATTEMPTS", 3)

    _fail_login(client, 3, headers={"X-Forwarded-For": "198.51.100.7"})
    assert client.post("/login", data={"username": "admin", "password": "wrong"},
                       headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 429

    other = client.post("/login", data={"username": "admin", "password": "s3cret"},
                        headers={"X-Forwarded-For": "198.51.100.8"})
    assert other.status_code == 302


def test_proxy_chain_uses_the_leftmost_client(client, secured, monkeypatch):
    monkeypatch.setattr(config, "TRUST_PROXY_HEADERS", True)
    with client.application.test_request_context(
        headers={"X-Forwarded-For": "203.0.113.5, 10.0.0.1, 10.0.0.2"}
    ):
        assert auth.client_id() == "203.0.113.5"
