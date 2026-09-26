"""
Health scoring for the SD-WAN fabric.

Turns raw vManage data into a single 0-100 score plus the list of findings that
explain it, so the dashboard can answer "is the network OK?" at a glance
instead of making the operator read five separate panels.
"""

import config
import i18n


def _finding(severity: str, category: str, key: str, **params) -> dict:
    """Build a finding as a key plus its parameters.

    The dashboard renders it in the viewer's language; `message` is the English
    rendering, kept because deduplication compares messages and because any
    consumer of the API that ignores the key still gets readable text.
    """
    return {
        "severity": severity,
        "category": category,
        "key": key,
        "params": params,
        "message": i18n.translate(key, i18n.DEFAULT_LOCALE, **params),
    }


def _host(device: dict) -> str:
    return device.get("host-name") or i18n.translate("finding.unknown_host")

# Each category contributes a share of the final score.
WEIGHTS = {
    "reachability": 40,
    "bfd": 25,
    "control": 15,
    "alarms": 12,
    "resources": 8,
}

SEVERITY_ORDER = {"Critical": 0, "Major": 1, "Minor": 2, "Info": 3}


def _pct(part: int, whole: int) -> float:
    """Share of `part` in `whole`, as 0.0-1.0. An empty set counts as healthy."""
    return 1.0 if whole <= 0 else part / whole


def score_reachability(devices: list[dict]) -> tuple[float, list[dict]]:
    total = len(devices)
    down = [d for d in devices if d.get("reachability") != "reachable"]
    ratio = _pct(total - len(down), total)
    findings = [
        _finding("Critical", "reachability", "finding.unreachable", host=_host(d))
        for d in down
    ]
    return ratio, findings


def score_bfd(bfd: list[dict]) -> tuple[float, list[dict]]:
    up = sum(d.get("bfd-sessions-up", 0) for d in bfd)
    down = sum(d.get("bfd-sessions-down", 0) for d in bfd)
    ratio = _pct(up, up + down)
    findings = [
        _finding("Major", "bfd", "finding.bfd_down",
                 host=_host(d), count=d.get("bfd-sessions-down", 0))
        for d in bfd
        if d.get("bfd-sessions-down", 0) > 0
    ]
    return ratio, findings


def score_control(control: list[dict]) -> tuple[float, list[dict]]:
    total = sum(c.get("count", 0) for c in control)
    up = sum(c.get("up", 0) for c in control)
    ratio = _pct(up, total)
    findings = [
        _finding("Critical", "control", "finding.control_down",
                 type=c.get("device-type") or i18n.translate("finding.controller"),
                 count=c.get("down", 0))
        for c in control
        if c.get("down", 0) > 0
    ]
    return ratio, findings


def score_alarms(alarms: list[dict]) -> tuple[float, list[dict]]:
    """Unacknowledged alarms erode the score; each severity costs a fixed amount."""
    cost_per_severity = {"Critical": 0.25, "Major": 0.10, "Minor": 0.03, "Info": 0.0}
    open_alarms = [a for a in alarms if not a.get("acknowledged")]
    penalty = sum(cost_per_severity.get(a.get("severity", "Info"), 0.0) for a in open_alarms)
    ratio = max(0.0, 1.0 - penalty)

    # An alarm's text comes from the controller, so there is no key to
    # translate — it is passed through as vManage worded it.
    findings = [
        {
            "severity": a.get("severity", "Info"),
            "category": "alarms",
            "key": None,
            "params": {},
            "message": (a.get("message") or a.get("type")
                        or i18n.translate("finding.unnamed_alarm")),
        }
        for a in open_alarms
        if a.get("severity") in ("Critical", "Major")
    ]
    return ratio, findings


def score_resources(devices: list[dict]) -> tuple[float, list[dict]]:
    """Penalise devices running hot on CPU or memory."""
    findings = []
    measured = 0
    stressed = 0

    for d in devices:
        cpu = d.get("cpu-load")
        mem = d.get("mem-util")
        if cpu is None and mem is None:
            continue  # Offline devices report nothing; reachability already caught them.
        measured += 1

        if cpu is not None and cpu >= config.CPU_CRIT:
            stressed += 1
            findings.append(
                _finding("Major", "resources", "finding.cpu_high", host=_host(d), pct=cpu)
            )
        elif mem is not None and mem >= config.MEM_CRIT:
            stressed += 1
            findings.append(
                _finding("Major", "resources", "finding.memory_high", host=_host(d), pct=mem)
            )
        elif (cpu is not None and cpu >= config.CPU_WARN) or (
            mem is not None and mem >= config.MEM_WARN
        ):
            findings.append(
                _finding("Minor", "resources", "finding.resources_warn", host=_host(d))
            )

    return _pct(measured - stressed, measured), findings


def _dedupe(findings: list[dict]) -> list[dict]:
    """Drop repeats of the same message.

    A device that is down is reported both by the reachability check and by the
    alarm vManage raised for it. Showing the operator the same line twice is
    noise, so the first occurrence wins — and because the list is already sorted
    by severity, that is the most severe framing of the issue.
    """
    seen: set[str] = set()
    unique = []
    for f in findings:
        key = f["message"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    return unique


def grade(score: int) -> str:
    if score >= 90:
        return "healthy"
    if score >= 70:
        return "degraded"
    return "critical"


def compute(
    devices: list[dict],
    bfd: list[dict],
    control: list[dict],
    alarms: list[dict],
) -> dict:
    """Combine every category into the overall fabric health report."""
    ratios = {}
    findings: list[dict] = []

    for name, (ratio, found) in {
        "reachability": score_reachability(devices),
        "bfd": score_bfd(bfd),
        "control": score_control(control),
        "alarms": score_alarms(alarms),
        "resources": score_resources(devices),
    }.items():
        ratios[name] = ratio
        findings.extend(found)

    total = sum(WEIGHTS[name] * ratio for name, ratio in ratios.items())
    score = int(round(total))

    findings.sort(key=lambda f: SEVERITY_ORDER.get(f["severity"], 9))
    findings = _dedupe(findings)

    return {
        "score": score,
        "grade": grade(score),
        "categories": {
            name: {
                "score": int(round(ratio * 100)),
                "weight": WEIGHTS[name],
            }
            for name, ratio in ratios.items()
        },
        "findings": findings,
    }
