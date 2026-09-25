"""Regression tests for the findings raised in the security review.

Each of these fails if the corresponding fix is reverted.
"""

import re
from pathlib import Path

import pytest

import config
from sdwan_client import SDWANClient, describe

JS_DIR = Path(__file__).resolve().parent.parent / "static" / "js"

# Every first-party script, so a new view cannot introduce an unescaped sink
# without these checks seeing it. The vendored library is not ours to audit.
JS_FILES = sorted(p for p in JS_DIR.glob("*.js"))
JS = "\n".join(p.read_text() for p in JS_FILES)


# ------------------------------------------------- escaping in the front end
def test_cell_escaping_is_decided_by_provenance_not_by_value():
    """Sniffing the value let any string starting with '<span' through raw."""
    assert 'startsWith("<span")' not in JS
    assert "value instanceof SafeMarkup" in JS


def test_state_span_is_the_only_source_of_trusted_markup():
    assert "class SafeMarkup" in JS
    # Exactly one place mints trusted markup.
    assert JS.count("new SafeMarkup(") == 1


def test_data_search_attribute_is_escaped():
    """A hostname with a quote would otherwise break out of the attribute."""
    match = re.search(r'data-search="\$\{([^}]+)\}"', JS)
    assert match, "data-search attribute not found"
    assert match.group(1).startswith("esc("), f"unescaped: {match.group(1)}"


def test_alarm_severity_is_allow_listed():
    """severity reaches a class attribute, so it must not be free text."""
    assert "SEVERITIES.includes(a.severity)" in JS


def test_esc_covers_every_html_metacharacter():
    body = JS[JS.index("function esc(str)"):]
    body = body[:body.index("\n}")]
    for char in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert char in body, f"esc() does not produce {char}"


# ---------------------------------------------------------- redirect handling
@pytest.mark.parametrize(
    "target",
    ["//evil.example.com", "/\\evil.example.com", "https://evil.example.com",
     "https:/evil.example.com", "javascript:alert(1)", "", None],
)
def test_unsafe_redirect_targets_are_rejected(target):
    import app
    assert app._safe_next(target) is None


@pytest.mark.parametrize("target", ["/", "/api/status", "/devices?site=101"])
def test_same_site_paths_are_kept(target):
    import app
    assert app._safe_next(target) == target


# --------------------------------------------------------------- TLS handling
def test_tls_verification_defaults_to_on(monkeypatch):
    monkeypatch.delenv("VMANAGE_VERIFY_SSL", raising=False)
    assert config._verify_setting("true") is True
    # The module-level default is what ships when nothing is configured.
    import importlib
    reloaded = importlib.reload(config)
    assert reloaded.VMANAGE_VERIFY_SSL is True


def test_ca_bundle_path_is_passed_through_not_coerced_to_false():
    """A bundle path must enable verification, not silently disable it."""
    result = config._verify_setting("/etc/ssl/certs/vmanage-ca.pem")
    assert result == "/etc/ssl/certs/vmanage-ca.pem"
    assert result is not False


@pytest.mark.parametrize("raw", ["ture", "yes please", "1.pem", "TRUE_"])
def test_unrecognised_values_do_not_disable_verification(raw):
    """A typo must never fail open into an unverified connection."""
    assert config._verify_setting(raw) is not False


@pytest.mark.parametrize("raw,expected", [("false", False), ("0", False), ("no", False),
                                          ("true", True), ("on", True), ("1", True)])
def test_explicit_booleans_still_work(raw, expected):
    assert config._verify_setting(raw) is expected


def test_client_defaults_to_verifying():
    client = SDWANClient("vmanage.test", 8443, "u", "p")
    assert client.session.verify is True


def test_client_accepts_a_ca_bundle():
    client = SDWANClient("vmanage.test", 8443, "u", "p", verify_ssl="/tmp/ca.pem")
    assert client.session.verify == "/tmp/ca.pem"


def test_tls_error_never_advises_disabling_verification():
    """That advice would tell operators to switch off the control mid-attack."""
    import requests

    message = describe(requests.exceptions.SSLError("x"))
    assert "VMANAGE_VERIFY_SSL=false" not in message
    assert "CA bundle" in message
