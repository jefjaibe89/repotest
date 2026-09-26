"""Release consistency across every node in the fabric.

Enhanced AAR only looks at edges and the endpoint audit only sees the Manager
it connects to, so nothing was checking the Controllers, the Validators, the
other nodes of a Manager cluster, or whether the fabric agrees with itself.
"""

import pytest

import analysis


def _fabric(*specs):
    return [
        {"device-type": t, "host-name": h, "version": v,
         "system-ip": f"10.0.0.{i}", "site-id": "1", "reachability": "reachable"}
        for i, (t, h, v) in enumerate(specs, start=1)
    ]


ALIGNED = _fabric(
    ("vmanage", "mgr", "20.12.1"),
    ("vsmart", "ctrl", "20.12.1"),
    ("vbond", "val", "20.12.1"),
    ("vedge", "edge1", "17.12.3"),
)


# -------------------------------------------------------- train pairing
@pytest.mark.parametrize("version,release", [
    ("20.12.1", 12), ("17.12.3", 12),   # the pair Cisco ships together
    ("20.9.1", 9), ("17.9.1", 9),
    ("20.6", 6), ("17.6.5", 6),
])
def test_paired_trains_reduce_to_a_comparable_release(version, release):
    """20.x and 17.x are not comparable as numbers; the minor release is."""
    assert analysis.release_number(version) == release


@pytest.mark.parametrize("version", ["26.4.1", "19.2.1", "garbage", "", None, "20"])
def test_unpairable_versions_have_no_release(version):
    assert analysis.release_number(version) is None


# ------------------------------------------------------------- verdicts
def test_an_aligned_fabric_reports_nothing():
    out = analysis.analyse_fabric_versions(ALIGNED)
    assert out["state"] == "consistent"
    assert out["findings"] == []
    assert out["control_floor"] == 12


def test_an_edge_ahead_of_the_control_plane_is_critical():
    """Controllers are upgraded first, so this is the one combination the
    documented order rules out."""
    out = analysis.analyse_fabric_versions(_fabric(
        ("vmanage", "mgr", "20.9.1"),
        ("vsmart", "ctrl", "20.9.1"),
        ("vedge", "edge1", "17.12.3"),
    ))
    assert out["state"] == "unsupported"
    finding = next(f for f in out["findings"] if f["key"] == "fabric.finding.edge_ahead")
    assert finding["severity"] == "Critical"
    assert finding["params"]["host"] == "edge1"


def test_an_edge_behind_the_control_plane_is_allowed():
    """The supported direction; only a large gap is worth naming."""
    out = analysis.analyse_fabric_versions(_fabric(
        ("vmanage", "mgr", "20.12.1"),
        ("vedge", "edge1", "17.11.1"),
    ))
    assert out["state"] == "consistent"


def test_a_far_lagging_edge_is_named():
    out = analysis.analyse_fabric_versions(_fabric(
        ("vmanage", "mgr", "20.12.1"),
        ("vedge", "old", "17.6.5"),
    ))
    finding = next(f for f in out["findings"] if f["key"] == "fabric.finding.edge_lagging")
    assert finding["severity"] == "Minor"
    assert finding["params"]["gap"] == 6


def test_a_split_control_plane_is_reported():
    out = analysis.analyse_fabric_versions(_fabric(
        ("vmanage", "mgr", "20.12.1"),
        ("vsmart", "ctrl", "20.9.1"),
        ("vedge", "edge1", "17.9.1"),
    ))
    assert out["state"] == "skewed"
    assert any(f["key"] == "fabric.finding.control_plane_split" for f in out["findings"])


def test_the_lowest_control_release_is_the_ceiling_for_edges():
    """One lagging Controller constrains the whole fabric, not the average."""
    out = analysis.analyse_fabric_versions(_fabric(
        ("vmanage", "mgr", "20.12.1"),
        ("vsmart", "ctrl", "20.9.1"),
        ("vedge", "edge1", "17.12.1"),
    ))
    assert out["control_floor"] == 9
    assert any(f["key"] == "fabric.finding.edge_ahead" for f in out["findings"])


def test_a_mixed_manager_cluster_is_reported():
    """A Manager cluster is several nodes; they should agree."""
    out = analysis.analyse_fabric_versions(_fabric(
        ("vmanage", "m1", "20.12.1"),
        ("vmanage", "m2", "20.12.4"),
        ("vedge", "edge1", "17.12.1"),
    ))
    finding = next(f for f in out["findings"] if f["key"] == "fabric.finding.role_mixed")
    assert finding["params"]["role"] == "manager"
    assert out["by_role"]["manager"]["consistent"] is False


def test_an_unreadable_version_is_named_not_ignored():
    out = analysis.analyse_fabric_versions(_fabric(
        ("vmanage", "mgr", "20.12.1"),
        ("vedge", "mystery", None),
    ))
    finding = next(f for f in out["findings"] if f["key"] == "fabric.finding.version_unreadable")
    assert finding["params"]["host"] == "mystery"


def test_no_control_plane_leaves_nothing_to_compare_against():
    out = analysis.analyse_fabric_versions(_fabric(("vedge", "edge1", "17.12.1")))
    assert out["state"] == "unknown"
    assert out["control_floor"] is None


def test_an_empty_fabric_does_not_raise():
    out = analysis.analyse_fabric_versions([])
    assert out["state"] == "unknown"
    assert out["totals"]["nodes"] == 0


# ---------------------------------------------------------- every node
def test_every_role_is_counted_not_just_edges():
    out = analysis.analyse_fabric_versions(ALIGNED)
    assert out["totals"]["control_plane"] == 3, "manager, controller and validator"
    assert out["totals"]["edges"] == 1
    assert out["totals"]["nodes"] == 4


def test_nodes_are_listed_with_their_role_and_release():
    out = analysis.analyse_fabric_versions(ALIGNED)
    roles = {n["hostname"]: n["role"] for n in out["nodes"]}
    assert roles == {"mgr": "manager", "ctrl": "controller",
                     "val": "validator", "edge1": "edge"}


def test_roles_are_recognised_under_the_current_naming_too():
    out = analysis.analyse_fabric_versions(_fabric(
        ("manager", "mgr", "20.12.1"),
        ("controller", "ctrl", "20.12.1"),
        ("validator", "val", "20.12.1"),
        ("edge", "edge1", "17.12.1"),
    ))
    assert out["totals"]["control_plane"] == 3
    assert out["totals"]["edges"] == 1


def test_findings_are_ordered_by_severity():
    out = analysis.analyse_fabric_versions(_fabric(
        ("vmanage", "m1", "20.9.1"),
        ("vmanage", "m2", "20.12.1"),
        ("vedge", "ahead", "17.12.3"),
        ("vedge", "mystery", None),
    ))
    order = {"Critical": 0, "Major": 1, "Minor": 2}
    ranks = [order[f["severity"]] for f in out["findings"]]
    assert ranks == sorted(ranks)


# ------------------------------------------------------------ the view
def test_endpoint_serves_the_report(client):
    data = client.get("/api/fabric-versions").get_json()
    assert {"state", "nodes", "by_role", "findings", "totals"} <= set(data)


def test_findings_are_translatable(client):
    for finding in client.get("/api/fabric-versions").get_json()["findings"]:
        assert finding["key"].startswith("fabric.finding.")
        assert "params" in finding


def test_the_report_appears_on_the_compatibility_page(client):
    body = client.get("/compat").get_data(as_text=True)
    assert 'id="fabric-card"' in body
    assert 'id="fabric-node-tbody"' in body


def test_the_demo_fabric_reports_its_lagging_edge(client):
    """The mock fabric has one edge six releases behind, so the view has
    something real to show rather than a uniformly clean result."""
    data = client.get("/api/fabric-versions").get_json()
    assert data["state"] == "skewed"
    assert any(f["key"] == "fabric.finding.edge_lagging" for f in data["findings"])
