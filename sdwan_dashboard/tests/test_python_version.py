"""The version guard at the top of app.py.

The codebase uses `X | None` annotations: 3.9 parses them and raises TypeError
when they are evaluated, so without a guard the app dies inside whichever
module is imported first, naming neither Python nor the version needed.
macOS ships 3.9 as `python3`, so this is met by anyone following the README
on a Mac.
"""

import collections
import re
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app.py"
SOURCE = APP.read_text()


def _guard_source():
    match = re.search(r"^import sys$.*?^    raise SystemExit\(1\)$", SOURCE, re.M | re.S)
    assert match, "the version guard is no longer where this test expects it"
    return match.group(0)


def test_the_guard_runs_before_any_project_import():
    """A guard after `import config` never runs: config raises first."""
    guard_at = SOURCE.index("if sys.version_info <")
    for module in ("import config", "import auth", "import csv", "import metrics"):
        assert SOURCE.index(module) > guard_at, f"{module} is imported before the guard"


@pytest.mark.parametrize("version", [(3, 8, 10), (3, 9, 6), (3, 9, 20)])
def test_an_old_interpreter_is_refused_with_a_usable_message(version, monkeypatch, capsys):
    fake = collections.namedtuple("v", "major minor micro releaselevel serial")
    monkeypatch.setattr(sys, "version_info", fake(*version, "final", 0))
    monkeypatch.setattr(sys, "version", f"{'.'.join(map(str, version))} (fake)")

    with pytest.raises(SystemExit) as exit_info:
        exec(_guard_source(), {"__name__": "guard"})

    assert exit_info.value.code == 1
    message = capsys.readouterr().err
    assert "3.10" in message, "it must say which version is needed"
    assert ".".join(map(str, version)) in message, "and which one is running"
    assert "python3.12" in message, "and how to fix it"


@pytest.mark.parametrize("version", [(3, 10, 0), (3, 12, 1), (3, 13, 0)])
def test_a_supported_interpreter_passes_through(version, monkeypatch):
    fake = collections.namedtuple("v", "major minor micro releaselevel serial")
    monkeypatch.setattr(sys, "version_info", fake(*version, "final", 0))
    exec(_guard_source(), {"__name__": "guard"})   # must not raise


def test_the_guard_itself_parses_on_old_python():
    """It is useless if the file cannot be read by the interpreter it warns.

    Only syntax 3.9 understands: no match statements, no `X | Y` annotations,
    no walrus in a comprehension — f-strings and sys.version_info are fine.
    """
    guard = _guard_source()
    assert "|" not in guard.replace("||", ""), "an annotation union would defeat the guard"
    assert not re.search(r"^\s*match .*:$", guard, re.M)


def test_the_floor_matches_what_ci_and_the_readme_declare():
    readme = (APP.parent / "README.md").read_text()
    workflow = (APP.parent.parent / ".github" / "workflows" / "tests.yml").read_text()

    assert "(3, 10)" in SOURCE
    assert '"3.10"' in workflow, "CI should test the floor the guard enforces"
    assert "3.10" in readme
