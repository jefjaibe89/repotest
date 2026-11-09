"""Unit tests for the health scoring logic."""

import health


def _devices(*reachability):
    return [
        {"host-name": f"dev{i}", "reachability": r, "cpu-load": 10, "mem-util": 10}
        for i, r in enumerate(reachability)
    ]


def test_perfect_fabric_scores_100():
    report = health.compute(
        devices=_devices("reachable", "reachable"),
        bfd=[{"host-name": "dev0", "bfd-sessions-up": 4, "bfd-sessions-down": 0}],
        control=[{"device-type": "vsmart", "count": 2, "up": 2, "down": 0}],
        alarms=[],
    )
    assert report["score"] == 100
    assert report["grade"] == "healthy"
    assert report["findings"] == []


def test_empty_fabric_does_not_crash_or_penalize():
    """No data must not be scored as a failure — it divides by zero otherwise."""
    report = health.compute(devices=[], bfd=[], control=[], alarms=[])
    assert report["score"] == 100


def test_unreachable_device_lowers_score_and_is_reported():
    report = health.compute(
        devices=_devices("reachable", "unreachable"),
        bfd=[],
        control=[],
        alarms=[],
    )
    assert report["score"] < 100
    assert any("dev1" in f["message"] for f in report["findings"])
    assert report["categories"]["reachability"]["score"] == 50


def test_acknowledged_alarms_do_not_reduce_score():
    kwargs = dict(devices=_devices("reachable"), bfd=[], control=[])
    acked = health.compute(
        alarms=[{"severity": "Critical", "message": "x", "acknowledged": True}], **kwargs
    )
    assert acked["score"] == 100

    live = health.compute(
        alarms=[{"severity": "Critical", "message": "x", "acknowledged": False}], **kwargs
    )
    assert live["score"] < 100


def test_findings_are_sorted_by_severity():
    report = health.compute(
        devices=_devices("unreachable"),
        bfd=[{"host-name": "dev0", "bfd-sessions-up": 1, "bfd-sessions-down": 1}],
        control=[],
        alarms=[],
    )
    order = [health.SEVERITY_ORDER[f["severity"]] for f in report["findings"]]
    assert order == sorted(order)


def test_duplicate_findings_are_collapsed():
    """The same issue raised by two checks must appear once."""
    report = health.compute(
        devices=_devices("unreachable"),
        bfd=[],
        control=[],
        alarms=[{"severity": "Critical", "message": "dev0 is unreachable", "acknowledged": False}],
    )
    messages = [f["message"].lower() for f in report["findings"]]
    assert messages.count("dev0 is unreachable") == 1


def test_offline_devices_are_excluded_from_resource_scoring():
    """A device with no CPU/mem telemetry must not count as resource-stressed."""
    report = health.compute(
        devices=[{"host-name": "down", "reachability": "unreachable",
                  "cpu-load": None, "mem-util": None}],
        bfd=[], control=[], alarms=[],
    )
    assert report["categories"]["resources"]["score"] == 100


def test_hot_cpu_is_flagged_as_major():
    report = health.compute(
        devices=[{"host-name": "hot", "reachability": "reachable",
                  "cpu-load": 95, "mem-util": 20}],
        bfd=[], control=[], alarms=[],
    )
    resource_findings = [f for f in report["findings"] if f["category"] == "resources"]
    assert resource_findings[0]["severity"] == "Major"
    assert "95%" in resource_findings[0]["message"]


def test_grade_thresholds():
    assert health.grade(100) == "healthy"
    assert health.grade(90) == "healthy"
    assert health.grade(89) == "degraded"
    assert health.grade(70) == "degraded"
    assert health.grade(69) == "critical"


def test_weights_sum_to_100():
    assert sum(health.WEIGHTS.values()) == 100
