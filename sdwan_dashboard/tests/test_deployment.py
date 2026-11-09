"""Standalone and cluster deployments, judged by their own rules.

A single Manager is a deliberate choice with no quorum to maintain. Two
Managers look like a cluster and cannot form a majority, which is a broken
cluster rather than a small one. The two scenarios need different checks.
"""

import pytest

import analysis

ALL_SERVICES = ("application-server", "configuration-db",
                "messaging-server", "statistics-db")


def _node(role, host, ip, reachable=True, version="20.12.1"):
    return {"device-type": role, "host-name": host, "system-ip": ip,
            "version": version,
            "reachability": "reachable" if reachable else "unreachable"}


def _services(*names, stopped=()):
    return [{"service": n, "enabled": n not in stopped,
             "status": "stopped" if n in stopped else "running"} for n in names]


def _fabric(managers, controllers=2, validators=2, **kw):
    devices = [_node("vmanage", f"m{i}", f"1.1.1.{i}", **kw) for i in range(1, managers + 1)]
    devices += [_node("vsmart", f"c{i}", f"2.2.2.{i}") for i in range(1, controllers + 1)]
    devices += [_node("vbond", f"v{i}", f"3.3.3.{i}") for i in range(1, validators + 1)]
    return devices


def _all_healthy(managers):
    return {f"1.1.1.{i}": _services(*ALL_SERVICES) for i in range(1, managers + 1)}


# ------------------------------------------------------ scenario detection
def test_one_manager_is_standalone():
    out = analysis.analyse_deployment(_fabric(1), {}, _all_healthy(1))
    assert out["mode"] == "standalone"
    assert out["observed_mode"] == "standalone"


def test_three_managers_is_a_cluster():
    out = analysis.analyse_deployment(_fabric(3), {}, _all_healthy(3))
    assert out["mode"] == "cluster"


def test_the_controller_declaration_is_preferred_over_the_count():
    """The API knows; the inventory is inferred from it."""
    out = analysis.analyse_deployment(
        _fabric(1), {"deploymentmode": "cluster"}, _all_healthy(1))
    assert out["declared_mode"] == "cluster"
    assert out["mode"] == "cluster"


def test_a_disagreement_between_declaration_and_inventory_is_reported():
    """A node removed from the cluster but left in the inventory looks like
    this, and picking one silently would hide it."""
    out = analysis.analyse_deployment(
        _fabric(3), {"deploymentmode": "standalone"}, _all_healthy(3))
    finding = next(f for f in out["findings"] if f["key"] == "deploy.finding.mode_mismatch")
    assert finding["params"]["declared"] == "standalone"
    assert finding["params"]["observed"] == "cluster"


def test_cluster_identity_is_carried_through():
    out = analysis.analyse_deployment(_fabric(3), {
        "deploymentmode": "cluster", "clusterid": "cluster-7",
        "mode": "SingleTenant", "domain": "corp.example.com",
    }, _all_healthy(3))
    assert out["cluster_id"] == "cluster-7"
    assert out["tenancy"] == "SingleTenant"
    assert out["domain"] == "corp.example.com"


# ------------------------------------------------------- standalone rules
def test_a_clean_standalone_is_healthy_not_degraded():
    """Running one node is a choice. Reporting it as degraded is crying wolf."""
    out = analysis.analyse_deployment(
        _fabric(1), {"deploymentmode": "standalone"}, _all_healthy(1))
    assert out["health"] == "healthy"


def test_standalone_still_states_the_consequence():
    out = analysis.analyse_deployment(
        _fabric(1), {"deploymentmode": "standalone"}, _all_healthy(1))
    note = next(f for f in out["findings"]
                if f["key"] == "deploy.finding.standalone_single_point")
    assert note["severity"] == "Info", "informational, so it does not degrade health"


def test_standalone_is_not_judged_by_cluster_service_rules():
    """configuration-db on one node is correct for a standalone."""
    out = analysis.analyse_deployment(
        _fabric(1), {"deploymentmode": "standalone"}, _all_healthy(1))
    assert not any(f["key"] == "deploy.finding.config_db_count" for f in out["findings"])


# ---------------------------------------------------------- cluster rules
def test_a_healthy_three_node_cluster_reports_nothing():
    out = analysis.analyse_deployment(
        _fabric(3), {"deploymentmode": "cluster"}, _all_healthy(3))
    assert out["health"] == "healthy"
    assert out["findings"] == []


def test_two_nodes_cannot_form_quorum():
    """The dangerous case: it looks like a cluster and losing either node
    loses the whole thing."""
    out = analysis.analyse_deployment(
        _fabric(2), {"deploymentmode": "cluster"}, _all_healthy(2))
    finding = next(f for f in out["findings"] if f["key"] == "deploy.finding.no_quorum")
    assert finding["severity"] == "Critical"
    assert out["health"] == "broken"


def test_an_even_cluster_is_flagged():
    out = analysis.analyse_deployment(
        _fabric(4), {"deploymentmode": "cluster"}, _all_healthy(4))
    finding = next(f for f in out["findings"] if f["key"] == "deploy.finding.even_nodes")
    assert finding["severity"] == "Major"


def test_configuration_db_must_run_on_exactly_three():
    services = _all_healthy(3)
    services["1.1.1.3"] = _services(*ALL_SERVICES, stopped=("configuration-db",))

    out = analysis.analyse_deployment(_fabric(3), {"deploymentmode": "cluster"}, services)
    finding = next(f for f in out["findings"] if f["key"] == "deploy.finding.config_db_count")
    assert finding["severity"] == "Critical"
    assert finding["params"] == {"running": 2, "expected": 3}


def test_too_many_config_db_nodes_is_reported_less_severely():
    """More than three is wrong but does not threaten quorum the same way."""
    services = _all_healthy(5)
    out = analysis.analyse_deployment(_fabric(5), {"deploymentmode": "cluster"}, services)
    finding = next(f for f in out["findings"] if f["key"] == "deploy.finding.config_db_count")
    assert finding["severity"] == "Major"
    assert finding["params"]["running"] == 5


def test_a_service_missing_from_one_node_is_named():
    services = _all_healthy(3)
    services["1.1.1.2"] = _services(*ALL_SERVICES, stopped=("messaging-server",))

    out = analysis.analyse_deployment(_fabric(3), {"deploymentmode": "cluster"}, services)
    node = next(f for f in out["findings"] if f["key"] == "deploy.finding.node_service_down")
    assert node["params"]["host"] == "m2"
    assert "messaging-server" in node["params"]["services"]


def test_an_unreachable_manager_breaks_the_cluster():
    devices = _fabric(3)
    devices[2]["reachability"] = "unreachable"

    out = analysis.analyse_deployment(devices, {"deploymentmode": "cluster"}, _all_healthy(3))
    assert out["health"] == "broken"
    assert any(f["key"] == "deploy.finding.manager_unreachable" for f in out["findings"])


def test_missing_service_data_does_not_invent_a_verdict():
    """A node the controller said nothing about contributes nothing."""
    out = analysis.analyse_deployment(_fabric(3), {"deploymentmode": "cluster"}, {})
    assert out["totals"]["services_reported"] == 0
    assert all(n["services"] == [] for n in out["nodes"])


# ------------------------------------------------------------ redundancy
def test_a_single_controller_is_flagged():
    out = analysis.analyse_deployment(
        _fabric(3, controllers=1), {"deploymentmode": "cluster"}, _all_healthy(3))
    finding = next(f for f in out["findings"]
                   if f["key"] == "deploy.finding.no_redundancy")
    assert finding["params"]["role"] == "controller"


def test_a_missing_role_is_flagged():
    out = analysis.analyse_deployment(
        _fabric(3, validators=0), {"deploymentmode": "cluster"}, _all_healthy(3))
    finding = next(f for f in out["findings"] if f["key"] == "deploy.finding.role_absent")
    assert finding["params"]["role"] == "validator"


def test_redundancy_is_reported_per_role():
    out = analysis.analyse_deployment(
        _fabric(3, controllers=2, validators=1), {"deploymentmode": "cluster"},
        _all_healthy(3))
    assert out["roles"]["controller"]["redundant"] is True
    assert out["roles"]["validator"]["redundant"] is False


# ------------------------------------------------------------- edge cases
def test_no_manager_at_all():
    out = analysis.analyse_deployment(
        [_node("vedge", "e1", "10.0.0.1")], {}, {})
    assert out["mode"] == "unknown"
    assert out["health"] == "unknown"


def test_empty_fabric_does_not_raise():
    out = analysis.analyse_deployment([], {}, {})
    assert out["totals"]["managers"] == 0


def test_findings_are_ordered_by_severity():
    services = _all_healthy(2)
    out = analysis.analyse_deployment(
        _fabric(2, controllers=1), {"deploymentmode": "cluster"}, services)
    order = {"Critical": 0, "Major": 1, "Minor": 2, "Info": 3}
    ranks = [order[f["severity"]] for f in out["findings"]]
    assert ranks == sorted(ranks)


# ---------------------------------------------------------------- the view
def test_endpoint_serves_the_scenario(client):
    data = client.get("/api/deployment").get_json()
    assert {"mode", "health", "nodes", "roles", "service_counts",
            "findings", "totals"} <= set(data)


def test_the_demo_fabric_is_a_cluster_with_a_real_finding(client):
    """The mock runs three Managers with configuration-db on two of them, so
    the view shows the check earning its keep rather than a clean result."""
    data = client.get("/api/deployment").get_json()
    assert data["mode"] == "cluster"
    assert data["totals"]["managers"] == 3
    assert data["service_counts"]["configuration-db"] == 2
    assert any(f["key"] == "deploy.finding.config_db_count" for f in data["findings"])


def test_findings_are_translatable(client):
    for finding in client.get("/api/deployment").get_json()["findings"]:
        assert finding["key"].startswith("deploy.finding.")
        assert "params" in finding


def test_the_scenario_appears_on_the_compatibility_page(client):
    body = client.get("/compat").get_data(as_text=True)
    assert 'id="deploy-card"' in body
    assert 'id="deploy-node-tbody"' in body
