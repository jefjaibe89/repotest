"""Compatibility reporting and graceful degradation.

vManage's monitoring API varies by release, licensing and tenancy. The
dashboard cannot assume an endpoint exists, so these pin down two things: one
missing source must not blank out the rest, and the UI must report what the
controller actually served rather than what we hoped it would.
"""

import pytest

import analysis
import compat
import poller
from sdwan_client import (
    MockSDWANClient,
    SDWANConnectionError,
    normalise_probe_class,
    normalise_sla_definition,
)


# --------------------------------------------------- policy list parsing
# Shapes verified against Cisco's catalystwan SDK models.
def test_probe_class_payload_lives_under_entries():
    out = normalise_probe_class({
        "name": "VOICE-PROBE", "listId": "uuid-1",
        "entries": [{"forwardingClass": "voice", "map": [{"color": "mpls", "dscp": 46}]}],
    })
    assert out["forwarding_class"] == "voice"
    assert out["dscp"] == 46
    assert out["dscp_map"] == [{"color": "mpls", "dscp": 46}]


def test_probe_class_keeps_per_colour_dscp():
    """DSCP is configured per TLOC colour, so one class can carry several."""
    out = normalise_probe_class({
        "name": "P", "listId": "u",
        "entries": [{"forwardingClass": "voice", "map": [
            {"color": "mpls", "dscp": 46},
            {"color": "biz-internet", "dscp": 34},
        ]}],
    })
    assert out["mixed_dscp"] is True
    assert out["dscp"] is None, "no single value when colours disagree"


def test_probe_class_accepts_a_flattened_row():
    """Some builds return a summary view; being strict would false-alarm."""
    out = normalise_probe_class({"name": "P", "forwardingClass": "voice", "dscp": 46})
    assert out["forwarding_class"] == "voice" and out["dscp"] == 46


def test_sla_thresholds_arrive_as_strings_and_are_converted():
    out = normalise_sla_definition({
        "name": "VOICE-SLA", "listId": "u",
        "entries": [{"latency": "50", "loss": "1", "jitter": "20", "appProbeClass": "p-uuid"}],
    })
    assert out["latency"] == 50 and isinstance(out["latency"], int)
    assert out["loss"] == 1
    assert out["app_probe_class"] == "p-uuid"


def test_unparseable_threshold_becomes_none_not_zero():
    """Zero would read as an impossibly strict budget every tunnel breaches."""
    out = normalise_sla_definition({"name": "S", "entries": [{"latency": "n/a"}]})
    assert out["latency"] is None


def test_sla_binds_to_the_probe_class_by_uuid():
    """It references the probe list's listId, not its name.

    Resolving by name instead would leave every SLA class reading as a broken
    binding on a correctly configured fabric.
    """
    probe = normalise_probe_class({
        "name": "VOICE-PROBE", "listId": "probe-uuid",
        "entries": [{"forwardingClass": "voice", "map": [{"color": "mpls", "dscp": 46}]}],
    })
    sla = normalise_sla_definition({
        "name": "VOICE-SLA", "listId": "sla-uuid",
        "entries": [{"latency": "50", "appProbeClass": "probe-uuid"}],
    })
    out = analysis.analyse_enhanced_aar(
        devices=[{"device-type": "vedge", "version": "17.12.3", "host-name": "e"}],
        sla_definitions=[sla], probe_classes=[probe],
    )
    assert out["classes"][0]["enhanced"] is True
    assert out["classes"][0]["dangling"] is False
    assert out["classes"][0]["probe_class"] == "VOICE-PROBE", "show the name, not a UUID"


def test_binding_to_an_unknown_uuid_is_still_dangling():
    probe = normalise_probe_class({"name": "P", "listId": "real-uuid", "entries": [{}]})
    sla = normalise_sla_definition({"name": "S", "entries": [{"appProbeClass": "ghost-uuid"}]})
    out = analysis.analyse_enhanced_aar([], [sla], [probe])
    assert out["classes"][0]["dangling"] is True


# ------------------------------------------------- graceful degradation
class _Partial(MockSDWANClient):
    """A controller whose release lacks the QoS and app-route endpoints."""

    def get_qos_stats(self):
        raise SDWANConnectionError("vManage returned HTTP 404 for /device/qos/scheduler")

    def get_app_route_stats(self):
        raise SDWANConnectionError("vManage returned HTTP 404 for /device/app-route/statistics")


def test_a_missing_optional_endpoint_does_not_fail_the_poll():
    payload = poller.collect(_Partial())
    assert payload["summary"]["total_devices"] > 0, "the rest of the fabric still renders"
    assert payload["devices"], "the inventory is unaffected"


def test_the_degraded_source_is_named_rather_than_silently_empty():
    degraded = poller.collect(_Partial())["compat"]["degraded"]
    assert set(degraded) == {"qos", "app_route"}
    assert "404" in degraded["qos"]


def test_panels_fed_by_a_missing_source_come_back_empty_not_wrong():
    payload = poller.collect(_Partial())
    assert payload["qos"]["queues"] == []
    assert payload["qos"]["totals"]["drops"] == 0
    # Unaffected views keep their data.
    assert payload["links"]["links"]


def test_losing_the_device_inventory_does_fail_the_poll():
    """Without it there is no dashboard, so this one is not optional."""
    class _NoDevices(MockSDWANClient):
        def get_device_list(self):
            raise SDWANConnectionError("unreachable")

    with pytest.raises(SDWANConnectionError):
        poller.collect(_NoDevices())


def test_a_healthy_controller_degrades_nothing():
    assert poller.collect(MockSDWANClient())["compat"]["degraded"] == {}


# ------------------------------------------------------- the registry
def test_every_source_declares_its_evidence():
    valid = {compat.CATALOGUED, compat.VARIANT, compat.UNVERIFIED}
    for source in compat.SOURCES:
        assert source["evidence"] in valid, source["key"]
        assert source["method"] in ("GET", "POST")
        assert source["path"].startswith("/")
        assert source["feeds"], "a source nothing renders should not be collected"


def test_source_keys_are_unique():
    keys = [s["key"] for s in compat.SOURCES]
    assert len(keys) == len(set(keys))


def test_registry_covers_every_source_the_poller_can_degrade():
    """A source the poller can report as degraded must be declared here."""
    degradable = set(poller.collect(_Partial())["compat"]["degraded"])
    assert degradable <= set(compat.BY_KEY)


def test_only_the_device_inventory_is_required():
    required = [s["key"] for s in compat.SOURCES if s["required"]]
    assert required == ["devices"]


def test_variant_sources_explain_why():
    for source in compat.SOURCES:
        if source["evidence"] == compat.VARIANT:
            assert source.get("note"), f"{source['key']} should say what differs"


def test_summary_marks_degraded_sources_as_not_served():
    out = compat.summarise({"qos": "HTTP 404"})
    qos = next(r for r in out["sources"] if r["key"] == "qos")
    assert qos["served"] is False and qos["reason"] == "HTTP 404"
    assert out["counts"]["served"] == out["counts"]["total"] - 1


# ------------------------------------------------------------ the view
def test_compat_page_renders(client):
    assert client.get("/compat").status_code == 200


def test_compat_endpoint_reports_the_controller(client):
    data = client.get("/api/compat").get_json()
    assert data["controller"]["platform_version"]
    assert {"sources", "counts", "controller"} <= set(data)


def test_compat_is_translated(client):
    body = client.get("/compat?lang=es").get_data(as_text=True)
    assert "Compatibilidad con vManage" in body


# ----------------------------------------------- forward compatibility
# A version table is written at a point in time and the software keeps
# shipping. Failing closed on an unrecognised train reports a modern fabric as
# incapable of something its software has had for years.
@pytest.mark.parametrize("version", [
    "21.1.1", "22.4.1", "24.3.1", "26.1.1", "26.12.0", "27.2.0", "30.1.1",
])
def test_releases_newer_than_the_table_are_taken_as_capable(version):
    supported, _ = analysis.supports_enhanced_aar(version)
    assert supported is True, f"{version} must not read as incapable"
    assert analysis.version_status(version) == "assumed"


@pytest.mark.parametrize("version,expected", [
    ("17.12.3", "checked"),    # known train, above its floor
    ("17.6.5", "too_old"),     # known train, below its floor
    ("20.12.1", "checked"),
    ("20.6.1", "too_old"),
    ("26.1.1", "assumed"),     # newer than any train with a floor
    ("19.2.2", "too_old"),     # vEdge software predating the renumbering
    ("18.4.1", "too_old"),
    ("garbage", "unknown"),
    (None, "unknown"),
])
def test_version_verdict_says_how_it_was_reached(version, expected):
    assert analysis.version_status(version) == expected


def test_an_assumed_release_is_not_reported_as_a_blocker():
    out = analysis.analyse_enhanced_aar(
        devices=[{"device-type": "vedge", "version": "26.1.1", "host-name": "modern"}],
        sla_definitions=[_sla_def()], probe_classes=[_probe_def()],
    )
    assert out["totals"]["devices_blocking"] == 0
    assert out["state"] == "enabled"
    assert out["devices"][0]["basis"] == "assumed"


def _probe_def():
    return normalise_probe_class({
        "name": "P", "listId": "p-uuid",
        "entries": [{"forwardingClass": "voice", "map": [{"color": "mpls", "dscp": 46}]}],
    })


def _sla_def():
    return normalise_sla_definition({
        "name": "S", "listId": "s-uuid",
        "entries": [{"latency": "50", "appProbeClass": "p-uuid"}],
    })


# ------------------------------------------------ controller release range
@pytest.mark.parametrize("version,status", [
    ("20.3.0", "within_audit"),
    ("20.12.1", "within_audit"),
    ("20.16.1", "within_audit"),
    ("21.1.1", "newer_than_verified"),
    ("26.1.1", "newer_than_verified"),
    ("28.4.0", "newer_than_verified"),
    ("20.1.0", "below_minimum"),
    ("19.2.1", "below_minimum"),
    (None, "unknown"),
])
def test_controller_release_is_placed_against_the_audit(version, status):
    assert compat.check_controller(version)["status"] == status


def test_a_newer_controller_is_information_not_a_failure():
    """Refusing a release that postdates the table would be the worse bug."""
    result = compat.check_controller("26.1.1")
    assert result["status"] == "newer_than_verified"
    assert result["verified_to"] == "20.16"
    assert result["minimum"] == "20.3"


def test_controller_check_survives_odd_version_strings():
    for raw in ("", "20", "20.x", "v20.12", "20.12.1a"):
        assert compat.check_controller(raw)["status"] in (
            "unknown", "within_audit", "below_minimum", "newer_than_verified"
        )


def test_api_reports_the_release_verdict(client):
    release = client.get("/api/compat").get_json()["controller"]["release"]
    assert release["status"] in ("within_audit", "newer_than_verified",
                                "below_minimum", "unknown")
    assert release["verified_to"]
