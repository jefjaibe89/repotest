"""
Translations.

One JSON file per language in `locales/`, discovered at import. Adding a
language means adding a file — no registration list to update, no compilation
step, and nothing to rebuild when a string changes.

English is the source language: `en.json` defines the full set of keys, and
every other catalog falls back to it for anything it is missing, so a partial
translation degrades to English rather than showing a raw key.
"""

import json
import logging
import re
from pathlib import Path

log = logging.getLogger("sdwan-dashboard.i18n")

LOCALES_DIR = Path(__file__).parent / "locales"
DEFAULT_LOCALE = "en"

_catalogs: dict[str, dict] = {}


def _load_all():
    for path in sorted(LOCALES_DIR.glob("*.json")):
        try:
            _catalogs[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.exception("Could not load locale %s", path.name)

    if DEFAULT_LOCALE not in _catalogs:
        raise RuntimeError(f"Missing the source catalog {DEFAULT_LOCALE}.json")


_load_all()


def available() -> list[dict]:
    """Every installed language, for the switcher. Ordered with English first."""
    langs = []
    for code, catalog in _catalogs.items():
        meta = catalog.get("_meta", {})
        langs.append({
            "code": code,
            "name": meta.get("name", code),
            "native": meta.get("native", code),
        })
    langs.sort(key=lambda entry: (entry["code"] != DEFAULT_LOCALE, entry["native"]))
    return langs


def is_supported(code: str | None) -> bool:
    return bool(code) and code in _catalogs


def negotiate(requested: str | None, cookie: str | None, header: str | None) -> str:
    """Pick a locale: explicit choice, then saved choice, then browser, then English."""
    if is_supported(requested):
        return requested
    if is_supported(cookie):
        return cookie
    for code in _parse_accept_language(header):
        if is_supported(code):
            return code
        # "es-AR" should still find "es".
        base = code.split("-")[0]
        if is_supported(base):
            return base
    return DEFAULT_LOCALE


def _parse_accept_language(header: str | None) -> list[str]:
    """Return the header's languages, highest quality first."""
    if not header:
        return []
    entries = []
    for part in header.split(","):
        piece = part.strip()
        if not piece:
            continue
        code, _, params = piece.partition(";")
        quality = 1.0
        match = re.search(r"q=([0-9.]+)", params)
        if match:
            try:
                quality = float(match.group(1))
            except ValueError:
                quality = 0.0
        entries.append((quality, code.strip().lower()))
    entries.sort(key=lambda entry: entry[0], reverse=True)
    return [code for _, code in entries]


def catalog(locale: str) -> dict:
    """The full key/value map for a locale, with English filling any gaps."""
    base = dict(_catalogs[DEFAULT_LOCALE])
    if locale != DEFAULT_LOCALE and locale in _catalogs:
        base.update(_catalogs[locale])
    base.pop("_meta", None)
    return base


def translate(key: str, locale: str = DEFAULT_LOCALE, **params) -> str:
    """Look up `key`, falling back to English and then to the key itself."""
    catalogs = [_catalogs.get(locale, {}), _catalogs[DEFAULT_LOCALE]]
    for source in catalogs:
        if key in source:
            text = source[key]
            break
    else:
        log.warning("Missing translation key: %s", key)
        return key

    if not params:
        return text
    try:
        return text.format(**params)
    except (KeyError, IndexError):
        # A malformed placeholder must not blank out the UI.
        log.warning("Bad placeholders for key %s in locale %s", key, locale)
        return text


def missing_keys(locale: str) -> list[str]:
    """Keys present in English but absent from `locale`. Used by the tests."""
    source = set(_catalogs[DEFAULT_LOCALE]) - {"_meta"}
    target = set(_catalogs.get(locale, {})) - {"_meta"}
    return sorted(source - target)
