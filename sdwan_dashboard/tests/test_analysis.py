"""Tests for the judgements behind the specialised views.

The point of analysis.py is that a raw counter is not an answer: drops matter
relative to packets sent, throughput relative to contracted bandwidth, and
latency relative to the SLA class bound to that tunnel. These tests pin those
relationships down.
"""

import re
from pathlib import Path

import pytest

import analysis

JS_DIR = Path(__file__).resolve().parent.parent / "static" / "js"


def test_view_scripts_do_not_redeclare_shared_identifiers():
    """Each page loads common.js plus one view script, in the same scope.

    A `const` declared in both makes the whole view script fail to parse, and
    the page then renders stuck on its loading state with no visible error.
    """
    declarations: dict[str, list[str]] = {}
    for path in sorted(JS_DIR.glob("*.js")):
        if path.name == "chart.umd.js":   # vendored, not ours
            continue
        for match in re.finditer(r"^(?:const|let|function|class)\s+(\w+)", path.read_text(), re.M):
            declarations.setdefault(match.group(1), []).append(path.name)

    clashes = {name: files for name, files in declarations.items() if len(files) > 1}
    assert not clashes, f"identifiers declared in more than one script: {clashes}"


# ------------------------------------------------------------------------ QoS
def _queue(cls="best-effort", tx=1000, drops=0, host="edge1", iface="Gi0/0/0"):
    return {
        "host-name": host, "interface": iface, "policy-name": "P",
        "queue-id": 0, "class-name": cls, "tx-packets": tx, "tx-bytes": tx * 100,
        "drop-packets": drops,
    }


def test_clean_queue_is_ok():
    out = analysis.analyse_qos([_queue(drops=0)])
    assert out["queues"][0]["severity"] == "ok"
    assert out["queues"][0]["drop_pct"] == 0


def test_drop_rate_is_relative_to_offered_traffic():
    """A drop count alone says nothing; the ratio is what an operator reads."""
    small = analysis.analyse_qos([_queue(tx=100, drops=100)])["queues"][0]
    large = analysis.analyse_qos([_queue(tx=1_000_000, drops=100)])["queues"][0]

    assert small["drop_pct"] == 50.0, "100 dropped of 200 offered is half"
    assert large["drop_pct"] < 0.02
    assert small["severity"] == "critical"
    assert large["severity"] == "ok"


def test_any_drop_in_a_realtime_queue_is_critical():
    """Shaping bulk traffic is normal; dropping voice is a fault at any rate."""
    voice = analysis.analyse_qos([_queue(cls="voice", tx=1_000_000, drops=1)])["queues"][0]
    bulk = analysis.analyse_qos([_queue(cls="best-effort", tx=1_000_000, drops=1)])["queues"][0]

    assert voice["realtime"] is True
    assert voice["severity"] == "critical"
    assert bulk["severity"] == "ok", "the same ratio in bulk traffic is fine"


@pytest.mark.parametrize("cls", ["voice", "VOIP", "Real-Time", "llq"])
def test_realtime_class_names_are_recognised_case_insensitively(cls):
    assert analysis.analyse_qos([_queue(cls=cls, drops=1)])["queues"][0]["realtime"]


def test_queues_are_sorted_worst_first():
    rows = [_queue(drops=0), _queue(drops=500), _queue(drops=50)]
    pcts = [q["drop_pct"] for q in analysis.analyse_qos(rows)["queues"]]
    assert pcts == sorted(pcts, reverse=True)


def test_interfaces_roll_up_their_queues():
    rows = [
        _queue(cls="voice", tx=1000, drops=0),
        _queue(cls="best-effort", tx=1000, drops=200),
    ]
    iface = analysis.analyse_qos(rows)["interfaces"][0]
    assert iface["queues"] == 2
    assert iface["drops"] == 200
    assert iface["worst_class"] == "best-effort"


def test_qos_totals_separate_realtime_drops():
    rows = [_queue(cls="voice", drops=3), _queue(cls="best-effort", drops=40)]
    totals = analysis.analyse_qos(rows)["totals"]
    assert totals["drops"] == 43
    assert totals["realtime_drops"] == 3, "voice drops are reported on their own"
    assert totals["queues_dropping"] == 2


def test_empty_qos_does_not_divide_by_zero():
    out = analysis.analyse_qos([])
    assert out["queues"] == [] and out["totals"]["drops"] == 0


# ---------------------------------------------------------------------- links
def _link(bw=100_000, tx=0, rx=0, status="Up", color="mpls"):
    return {
        "host-name": "edge1", "interface": "Gi0/0/0", "color": color,
        "bandwidth-upstream": bw, "bandwidth-downstream": bw,
        "tx-kbps": tx, "rx-kbps": rx, "if-oper-status": status,
    }


def test_utilisation_is_relative_to_contracted_bandwidth():
    """184 Mbps is comfortable on a gigabit and desperate on a 200 Mbps link."""
    roomy = analysis.analyse_links([_link(bw=1_000_000, tx=184_000)])["links"][0]
    tight = analysis.analyse_links([_link(bw=200_000, tx=184_000)])["links"][0]

    assert roomy["tx_pct"] == 18.4 and roomy["severity"] == "ok"
    assert tight["tx_pct"] == 92.0 and tight["severity"] == "critical"


@pytest.mark.parametrize("pct,expected", [(10, "ok"), (75, "warning"), (95, "critical")])
def test_link_severity_thresholds(pct, expected):
    link = analysis.analyse_links([_link(bw=100_000, tx=pct * 1000)])["links"][0]
    assert link["severity"] == expected


def test_worst_direction_drives_the_verdict():
    """A circuit saturated inbound is saturated, however quiet the upload is."""
    link = analysis.analyse_links([_link(bw=100_000, tx=1_000, rx=95_000)])["links"][0]
    assert link["worst_pct"] == 95.0
    assert link["severity"] == "critical"


def test_down_circuit_is_not_reported_as_idle():
    link = analysis.analyse_links([_link(tx=0, rx=0, status="Down")])["links"][0]
    assert link["severity"] == "down"
    assert link["oper_up"] is False


def test_down_circuits_are_excluded_from_capacity_totals():
    """A dead circuit contributes no usable capacity."""
    totals = analysis.analyse_links([
        _link(bw=100_000, tx=50_000),
        _link(bw=900_000, status="Down"),
    ])["totals"]
    assert totals["capacity_kbps"] == 100_000
    assert totals["down"] == 1


def test_links_are_sorted_busiest_first_with_down_last():
    rows = [_link(tx=10_000), _link(status="Down"), _link(tx=90_000)]
    out = analysis.analyse_links(rows)["links"]
    assert out[0]["tx_kbps"] == 90_000
    assert out[-1]["oper_up"] is False


def test_links_group_by_transport_colour():
    rows = [
        _link(color="mpls", bw=100_000, tx=50_000),
        _link(color="mpls", bw=100_000, tx=30_000),
        _link(color="biz-internet", bw=200_000, tx=20_000),
    ]
    by_color = {c["color"]: c for c in analysis.analyse_links(rows)["by_color"]}
    assert by_color["mpls"]["count"] == 2
    assert by_color["mpls"]["capacity_kbps"] == 200_000
    assert by_color["mpls"]["used_pct"] == 40.0


def test_zero_bandwidth_does_not_divide_by_zero():
    link = analysis.analyse_links([_link(bw=0, tx=5000)])["links"][0]
    assert link["tx_pct"] == 0.0


# ------------------------------------------------------------------------ AAR
CLASSES = [
    {"name": "VOICE-SLA", "latency": 50, "loss": 1.0, "jitter": 20},
    {"name": "BULK-SLA", "latency": 300, "loss": 5.0, "jitter": 100},
]


def _tunnel(sla="VOICE-SLA", latency=10, loss=0.0, jitter=2):
    return {
        "host-name": "edge1", "local-color": "mpls", "remote-system-ip": "10.0.0.1",
        "remote-color": "mpls", "sla-class": sla, "app-route-policy": "AAR",
        "latency": latency, "loss": loss, "jitter": jitter,
    }


def test_the_same_measurement_passes_or_fails_by_its_sla_class():
    """78 ms is a breach under VOICE-SLA and comfortable under BULK-SLA."""
    voice = analysis.analyse_aar([_tunnel("VOICE-SLA", latency=78)], CLASSES, [])
    bulk = analysis.analyse_aar([_tunnel("BULK-SLA", latency=78)], CLASSES, [])

    assert voice["tunnels"][0]["in_sla"] is False
    assert voice["tunnels"][0]["breaches"] == ["latency"]
    assert bulk["tunnels"][0]["in_sla"] is True


def test_a_breach_names_every_budget_it_blew():
    out = analysis.analyse_aar(
        [_tunnel("VOICE-SLA", latency=78, loss=3.2, jitter=27)], CLASSES, []
    )
    assert set(out["tunnels"][0]["breaches"]) == {"latency", "loss", "jitter"}


def test_a_tunnel_exactly_on_budget_still_complies():
    out = analysis.analyse_aar(
        [_tunnel("VOICE-SLA", latency=50, loss=1.0, jitter=20)], CLASSES, []
    )
    assert out["tunnels"][0]["in_sla"] is True, "the budget is inclusive"


def test_tunnel_with_no_known_class_is_flagged_not_passed():
    """An unmeasurable tunnel must not be counted as healthy."""
    out = analysis.analyse_aar([_tunnel("UNKNOWN-SLA", latency=9999)], CLASSES, [])
    assert out["tunnels"][0]["unmeasured"] is True


def test_violations_are_listed_first():
    rows = [_tunnel(), _tunnel(latency=78, loss=9), _tunnel()]
    out = analysis.analyse_aar(rows, CLASSES, [])["tunnels"]
    assert out[0]["in_sla"] is False
    assert all(t["in_sla"] for t in out[1:])


def test_compliance_is_computed_per_class():
    rows = [_tunnel("VOICE-SLA"), _tunnel("VOICE-SLA", latency=78), _tunnel("BULK-SLA")]
    by_name = {c["name"]: c for c in analysis.analyse_aar(rows, CLASSES, [])["classes"]}
    assert by_name["VOICE-SLA"]["tunnels"] == 2
    assert by_name["VOICE-SLA"]["violating"] == 1
    assert by_name["VOICE-SLA"]["compliance_pct"] == 50.0
    assert by_name["BULK-SLA"]["compliance_pct"] == 100.0


def test_class_with_no_tunnels_does_not_divide_by_zero():
    out = analysis.analyse_aar([], CLASSES, [])
    assert all(c["compliance_pct"] == 0.0 for c in out["classes"])
    assert out["totals"]["compliance_pct"] == 0.0


def test_switchovers_are_newest_first():
    events = [
        {"entry_time": 100, "reason": "loss", "host-name": "a"},
        {"entry_time": 300, "reason": "latency", "host-name": "b"},
        {"entry_time": 200, "reason": "loss", "host-name": "c"},
    ]
    times = [e["time"] for e in analysis.analyse_aar([], CLASSES, events)["events"]]
    assert times == [300, 200, 100]


def test_switchovers_are_counted_by_reason():
    """Repeated loss switchovers on one colour is a circuit problem, not policy."""
    events = [
        {"entry_time": 1, "reason": "loss"},
        {"entry_time": 2, "reason": "loss"},
        {"entry_time": 3, "reason": "jitter"},
    ]
    totals = analysis.analyse_aar([], CLASSES, events)["totals"]
    assert totals["by_reason"] == {"loss": 2, "jitter": 1}
    assert totals["switchovers"] == 3


def test_event_without_a_reason_is_still_counted():
    totals = analysis.analyse_aar([], CLASSES, [{"entry_time": 1}])["totals"]
    assert totals["by_reason"] == {"unknown": 1}


# --------------------------------------------------------------- the API layer
def test_view_pages_render(client):
    for path in ("/qos", "/links", "/aar"):
        assert client.get(path).status_code == 200


def test_view_pages_are_translated(client):
    body = client.get("/qos?lang=es").get_data(as_text=True)
    assert "QoS y descartes por cola" in body


def test_specialised_endpoints_serve_analysed_data(client):
    qos = client.get("/api/qos").get_json()
    assert {"queues", "interfaces", "totals"} <= set(qos)
    assert qos["queues"], "the mock fabric has QoS counters"

    links = client.get("/api/links").get_json()
    assert {"links", "by_color", "totals"} <= set(links)

    aar = client.get("/api/aar").get_json()
    assert {"tunnels", "classes", "events", "totals"} <= set(aar)


def test_specialised_views_require_login_when_enabled(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "x")
    for path in ("/qos", "/links", "/aar"):
        assert client.get(path).status_code == 302
    for path in ("/api/qos", "/api/links", "/api/aar"):
        assert client.get(path).status_code == 401


def test_nav_lists_every_view(client):
    body = client.get("/").get_data(as_text=True)
    for path in ("/qos", "/links", "/aar"):
        assert f'href="{path}"' in body
