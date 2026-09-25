"""Error-path tests for the live vManage client.

These exercise the failure modes that only show up against a real controller:
bad credentials, expired sessions, timeouts and malformed responses.
"""

import pytest
import requests

from sdwan_client import SDWANAuthError, SDWANClient, SDWANConnectionError, describe


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", content_type="application/json"):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.headers = {"Content-Type": content_type}

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


@pytest.fixture
def client():
    return SDWANClient("vmanage.test", 8443, "admin", "secret", timeout=5)


def test_login_success_sets_authenticated(client, monkeypatch):
    monkeypatch.setattr(client.session, "post", lambda *a, **k: FakeResponse(200))
    monkeypatch.setattr(client.session, "get", lambda *a, **k: FakeResponse(200, text="tok"))
    client.session.cookies.set("JSESSIONID", "abc")

    assert client.login() is True
    assert client.authenticated
    assert client.session.headers["X-XSRF-TOKEN"] == "tok"


def test_login_without_cookie_raises_auth_error(client, monkeypatch):
    """vManage answers bad credentials with 200 and an HTML page, not a 401."""
    monkeypatch.setattr(client.session, "post", lambda *a, **k: FakeResponse(200, text="<html>"))

    with pytest.raises(SDWANAuthError):
        client.login()
    assert not client.authenticated


def test_login_timeout_raises_connection_error(client, monkeypatch):
    def boom(*a, **k):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(client.session, "post", boom)
    with pytest.raises(SDWANConnectionError):
        client.login()


def test_missing_token_endpoint_is_tolerated(client, monkeypatch):
    """vManage <19.2 has no token endpoint; a 404 must not fail the login."""
    monkeypatch.setattr(client.session, "post", lambda *a, **k: FakeResponse(200))
    monkeypatch.setattr(client.session, "get", lambda *a, **k: FakeResponse(404))
    client.session.cookies.set("JSESSIONID", "abc")

    assert client.login() is True
    assert "X-XSRF-TOKEN" not in client.session.headers


def test_401_invalidates_session(client, monkeypatch):
    client._authenticated = True
    monkeypatch.setattr(client.session, "request", lambda *a, **k: FakeResponse(401))

    with pytest.raises(SDWANAuthError):
        client.get_device_list()
    assert not client.authenticated


def test_html_response_is_treated_as_expired_session(client, monkeypatch):
    """An idled-out session returns the login page with HTTP 200."""
    client._authenticated = True
    monkeypatch.setattr(
        client.session,
        "request",
        lambda *a, **k: FakeResponse(200, text="<html>login</html>", content_type="text/html"),
    )

    with pytest.raises(SDWANAuthError):
        client.get_device_list()
    assert not client.authenticated


def test_server_error_raises_connection_error(client, monkeypatch):
    monkeypatch.setattr(client.session, "request", lambda *a, **k: FakeResponse(503))
    with pytest.raises(SDWANConnectionError):
        client.get_device_list()


def test_device_list_unwraps_data_key(client, monkeypatch):
    monkeypatch.setattr(
        client.session,
        "request",
        lambda *a, **k: FakeResponse(200, json_data={"data": [{"host-name": "edge1"}]}),
    )
    assert client.get_device_list() == [{"host-name": "edge1"}]


def test_missing_data_key_returns_empty_list(client, monkeypatch):
    monkeypatch.setattr(client.session, "request", lambda *a, **k: FakeResponse(200, json_data={}))
    assert client.get_device_list() == []


@pytest.mark.parametrize(
    "exc, expected",
    [
        (requests.Timeout("x"), "did not respond in time"),
        (requests.exceptions.SSLError("x"), "TLS verification failed"),
        (requests.ConnectionError("x"), "unreachable or refused"),
    ],
)
def test_describe_returns_operator_readable_text(exc, expected):
    assert expected in describe(exc)


def test_error_message_hides_urllib3_internals(client, monkeypatch):
    """The banner must never show object addresses or library internals."""
    def boom(*a, **k):
        raise requests.ConnectionError(
            "HTTPSConnectionPool(host='x'): <urllib3.connection.HTTPSConnection "
            "object at 0x7f38773e0390>"
        )

    monkeypatch.setattr(client.session, "post", boom)
    with pytest.raises(SDWANConnectionError) as exc_info:
        client.login()

    message = str(exc_info.value)
    assert "urllib3" not in message
    assert "0x" not in message
    assert "unreachable" in message


def test_logout_swallows_transport_errors(client, monkeypatch):
    """Logout is best-effort — a dead controller must not raise on cleanup."""
    def boom(*a, **k):
        raise requests.ConnectionError("gone")

    client._authenticated = True
    monkeypatch.setattr(client.session, "get", boom)

    client.logout()
    assert not client.authenticated
