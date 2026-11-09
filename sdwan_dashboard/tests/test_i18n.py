"""Tests for translation and language selection.

The completeness tests are the ones that matter over time: they fail the moment
a string is added to English without being added everywhere else, which is how
a partially translated UI normally ships.
"""

import json
import re
from pathlib import Path

import pytest

import i18n

DASHBOARD = Path(__file__).resolve().parent.parent
LOCALES = sorted(p.stem for p in (DASHBOARD / "i18n" / "locales").glob("*.json"))


# ------------------------------------------------------------ catalogues
def test_more_than_one_language_is_installed():
    assert len(LOCALES) >= 2
    assert "en" in LOCALES and "es" in LOCALES


@pytest.mark.parametrize("locale", LOCALES)
def test_catalogue_is_valid_json_with_metadata(locale):
    data = json.loads((DASHBOARD / "i18n" / "locales" / f"{locale}.json").read_text("utf-8"))
    assert "_meta" in data, f"{locale}.json has no _meta block"
    assert data["_meta"].get("native"), "the switcher shows the native name"
    assert data["_meta"].get("name")


@pytest.mark.parametrize("locale", [loc for loc in LOCALES if loc != "en"])
def test_translation_is_complete(locale):
    """Every key English defines must exist in every other language."""
    missing = i18n.missing_keys(locale)
    assert not missing, (
        f"{locale}.json is missing {len(missing)} key(s): {missing[:10]}"
    )


@pytest.mark.parametrize("locale", [loc for loc in LOCALES if loc != "en"])
def test_no_keys_that_english_does_not_define(locale):
    """A key only a translation has is dead weight, or a typo."""
    english = set(i18n._catalogs["en"]) - {"_meta"}
    other = set(i18n._catalogs[locale]) - {"_meta"}
    assert not (other - english), f"{locale}.json defines unknown keys: {sorted(other - english)}"


@pytest.mark.parametrize("locale", [loc for loc in LOCALES if loc != "en"])
def test_placeholders_match_english(locale):
    """A translation that drops or renames {a_placeholder} renders wrong."""
    english = i18n._catalogs["en"]
    mismatches = []
    for key, source in english.items():
        if key == "_meta" or key not in i18n._catalogs[locale]:
            continue
        expected = set(re.findall(r"\{(\w+)\}", source))
        actual = set(re.findall(r"\{(\w+)\}", i18n._catalogs[locale][key]))
        if expected != actual:
            mismatches.append((key, sorted(expected), sorted(actual)))
    assert not mismatches, f"placeholder mismatch in {locale}: {mismatches}"


@pytest.mark.parametrize("locale", [loc for loc in LOCALES if loc != "en"])
def test_translation_is_not_a_copy_of_english(locale):
    """Catch a catalogue that was duplicated but never actually translated."""
    english = i18n._catalogs["en"]
    other = i18n._catalogs[locale]
    shared = [k for k in english if k != "_meta" and k in other]
    identical = [k for k in shared if english[k] == other[k]]
    # Some values legitimately match: units, protocol names, product names.
    assert len(identical) / len(shared) < 0.5, (
        f"{locale}.json is {len(identical)}/{len(shared)} identical to English"
    )


# ------------------------------------------------------------ translate()
def test_translate_returns_the_requested_language():
    assert i18n.translate("nav.sign_out", "en") == "Sign out"
    assert i18n.translate("nav.sign_out", "es") == "Salir"


def test_translate_fills_placeholders():
    out = i18n.translate("tunnels.summary", "en", up=6, down=1, total=7)
    assert "6" in out and "1" in out and "7" in out
    assert "{up}" not in out


def test_unknown_key_returns_the_key_rather_than_blank():
    assert i18n.translate("nope.not.here", "en") == "nope.not.here"


def test_unknown_locale_falls_back_to_english():
    assert i18n.translate("nav.sign_out", "zz") == "Sign out"


def test_missing_placeholder_does_not_blank_the_string():
    """A bad call must degrade to the untouched template, not to nothing."""
    out = i18n.translate("tunnels.summary", "en")
    assert out


def test_catalog_merges_english_under_a_partial_translation():
    merged = i18n.catalog("es")
    english = i18n.catalog("en")
    assert set(merged) == set(english), "no key may disappear when switching language"
    assert "_meta" not in merged


# ------------------------------------------------------------ negotiation
def test_default_is_english():
    assert i18n.negotiate(None, None, None) == "en"


def test_explicit_choice_beats_everything():
    assert i18n.negotiate("es", "en", "en-GB") == "es"


def test_saved_choice_beats_the_browser():
    assert i18n.negotiate(None, "es", "en-GB,en;q=0.9") == "es"


def test_browser_preference_is_used_when_nothing_is_saved():
    assert i18n.negotiate(None, None, "es-ES,es;q=0.9,en;q=0.8") == "es"


def test_regional_variant_matches_its_base_language():
    assert i18n.negotiate(None, None, "es-AR") == "es"


def test_unsupported_language_falls_back_to_english():
    """Derive the unsupported code, so adding a language never breaks this."""
    absent = next(code for code in ("zz", "qq", "xx") if code not in LOCALES)
    assert i18n.negotiate(None, None, f"{absent}-XX,{absent};q=0.9") == "en"
    assert i18n.negotiate("klingon", None, None) == "en"


def test_quality_values_are_respected():
    """The highest q wins even when it is not listed first."""
    assert i18n.negotiate(None, None, "fr;q=0.9,es;q=1.0") == "es"


def test_malformed_accept_language_does_not_raise():
    for header in ("", ";;;", "es;q=bogus", ",,,", "q=1"):
        assert i18n.negotiate(None, None, header) in LOCALES


def test_available_lists_every_locale_with_english_first():
    langs = i18n.available()
    assert [entry["code"] for entry in langs][0] == "en"
    assert {entry["code"] for entry in langs} == set(LOCALES)
    assert all(entry["native"] for entry in langs)


# ------------------------------------------------------------ the web layer
def test_dashboard_defaults_to_english(client):
    body = client.get("/").get_data(as_text=True)
    assert 'lang="en"' in body
    assert "Health Dashboard" in body


def test_dashboard_honours_accept_language(client):
    body = client.get("/", headers={"Accept-Language": "es-ES,es;q=0.9"}).get_data(as_text=True)
    assert 'lang="es"' in body
    assert "Panel de salud" in body


def test_query_parameter_switches_language(client):
    body = client.get("/?lang=es").get_data(as_text=True)
    assert "Panel de salud" in body


def test_switcher_sets_a_cookie_and_returns_the_viewer_back(client):
    resp = client.get("/lang/es?next=/")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")
    assert "dashboard_lang=es" in resp.headers.get("Set-Cookie", "")

    assert "Panel de salud" in client.get("/").get_data(as_text=True)


def test_switcher_ignores_an_unknown_language(client):
    resp = client.get("/lang/zz")
    assert "dashboard_lang" not in resp.headers.get("Set-Cookie", "")


def test_switcher_cannot_be_used_as_an_open_redirect(client):
    """It takes a `next` too, so it needs the same guard as the login."""
    for target in ("//evil.example.com", "https://evil.example.com", "/\\evil.example.com"):
        resp = client.get(f"/lang/es?next={target}")
        assert "evil.example.com" not in resp.headers["Location"]


def test_page_ships_the_catalogue_to_the_browser(client):
    """The panels are built client-side, so the strings must travel with the page."""
    body = client.get("/?lang=es").get_data(as_text=True)
    assert "const I18N =" in body
    assert "Degradado" in body, "JS-rendered strings must be in the inlined catalogue"


def test_login_page_is_translated(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "x")
    body = client.get("/login?lang=es").get_data(as_text=True)
    assert "Contraseña" in body
    assert "Entrar" in body


def test_api_stays_language_neutral(client):
    """Machine-readable endpoints must not change shape with the language."""
    en = client.get("/api/summary?lang=en").get_json()
    es = client.get("/api/summary?lang=es").get_json()
    assert en == es


def test_findings_carry_a_key_so_the_browser_can_translate_them(client):
    report = client.get("/api/health").get_json()
    keyed = [f for f in report["findings"] if f.get("key")]
    assert keyed, "computed findings should be translatable"
    for finding in keyed:
        assert "params" in finding
        assert finding["message"], "English text is kept as the fallback"
