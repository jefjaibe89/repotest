"""
Turns raw counters into the judgements each specialised view needs.

vManage reports totals; an operator needs ratios and verdicts. Drop counts mean
nothing without the packet count beside them, a 184 Mbps circuit means nothing
without knowing it was sold as 200, and a 78 ms tunnel is fine or broken purely
depending on which SLA class is bound to it.

Computed once per poll so every worker serves the same numbers.
"""

import config
import sdwan_client

# A queue is called out once its drop ratio crosses these.
QOS_WARN_RATIO = 0.001   # 0.1 %
QOS_CRIT_RATIO = 0.01    # 1 %

# Circuit utilisation thresholds.
LINK_WARN_PCT = 70
LINK_CRIT_PCT = 90

# Voice and other real-time classes are expected never to drop, so they are
# judged harder than bulk traffic.
REALTIME_CLASSES = {"voice", "voip", "real-time", "llq"}


def _ratio(part: int, whole: int) -> float:
    return 0.0 if whole <= 0 else part / whole


def _pct(part: float, whole: float) -> float:
    return 0.0 if whole <= 0 else round(part / whole * 100, 1)


# ------------------------------------------------------------------------ QoS
def analyse_qos(rows: list[dict]) -> dict:
    """Per-queue drop ratios, plus a per-interface roll-up."""
    queues = []
    for row in rows:
        tx = row.get("tx-packets", 0) or 0
        drops = row.get("drop-packets", 0) or 0
        offered = tx + drops          # what the queue was asked to send
        ratio = _ratio(drops, offered)
        cls = (row.get("class-name") or "").lower()
        realtime = cls in REALTIME_CLASSES

        if drops == 0:
            severity = "ok"
        elif realtime or ratio >= QOS_CRIT_RATIO:
            # Any drop in a real-time queue is worth a look, whatever the ratio.
            severity = "critical" if ratio >= QOS_CRIT_RATIO or realtime else "warning"
        elif ratio >= QOS_WARN_RATIO:
            severity = "warning"
        else:
            severity = "ok"

        queues.append({
            "hostname": row.get("host-name"),
            "interface": row.get("interface"),
            "policy": row.get("policy-name"),
            "queue": row.get("queue-id"),
            "klass": row.get("class-name"),
            "tx_packets": tx,
            "tx_bytes": row.get("tx-bytes", 0) or 0,
            "drops": drops,
            "drop_pct": round(ratio * 100, 3),
            "severity": severity,
            "realtime": realtime,
        })

    interfaces: dict[tuple, dict] = {}
    for q in queues:
        key = (q["hostname"], q["interface"])
        entry = interfaces.setdefault(key, {
            "hostname": q["hostname"], "interface": q["interface"],
            "policy": q["policy"], "tx_packets": 0, "drops": 0, "queues": 0,
            "worst_class": None, "worst_pct": 0.0,
        })
        entry["tx_packets"] += q["tx_packets"]
        entry["drops"] += q["drops"]
        entry["queues"] += 1
        if q["drop_pct"] > entry["worst_pct"]:
            entry["worst_pct"] = q["drop_pct"]
            entry["worst_class"] = q["klass"]

    for entry in interfaces.values():
        entry["drop_pct"] = round(
            _ratio(entry["drops"], entry["tx_packets"] + entry["drops"]) * 100, 3
        )

    queues.sort(key=lambda q: q["drop_pct"], reverse=True)

    return {
        "queues": queues,
        "interfaces": sorted(interfaces.values(), key=lambda i: i["drop_pct"], reverse=True),
        "totals": {
            "tx_packets": sum(q["tx_packets"] for q in queues),
            "drops": sum(q["drops"] for q in queues),
            "queues_dropping": sum(1 for q in queues if q["drops"] > 0),
            "realtime_drops": sum(q["drops"] for q in queues if q["realtime"]),
        },
    }


# ---------------------------------------------------------------------- links
def analyse_links(rows: list[dict]) -> dict:
    """Utilisation against each circuit's configured bandwidth."""
    links = []
    for row in rows:
        up = row.get("bandwidth-upstream", 0) or 0
        down = row.get("bandwidth-downstream", 0) or 0
        tx = row.get("tx-kbps", 0) or 0
        rx = row.get("rx-kbps", 0) or 0
        oper_up = str(row.get("if-oper-status", "")).lower() == "up"

        tx_pct = _pct(tx, up)
        rx_pct = _pct(rx, down)
        worst = max(tx_pct, rx_pct)

        if not oper_up:
            severity = "down"
        elif worst >= LINK_CRIT_PCT:
            severity = "critical"
        elif worst >= LINK_WARN_PCT:
            severity = "warning"
        else:
            severity = "ok"

        links.append({
            "hostname": row.get("host-name"),
            "interface": row.get("interface"),
            "color": row.get("color"),
            "bw_up": up,
            "bw_down": down,
            "tx_kbps": tx,
            "rx_kbps": rx,
            "tx_pct": tx_pct,
            "rx_pct": rx_pct,
            "worst_pct": worst,
            "oper_up": oper_up,
            "severity": severity,
        })

    # Busiest first: that is the order an operator reads this table in.
    links.sort(key=lambda link: (not link["oper_up"], -link["worst_pct"]))

    by_color: dict[str, dict] = {}
    for link in links:
        entry = by_color.setdefault(link["color"] or "—", {
            "color": link["color"] or "—", "count": 0,
            "capacity_kbps": 0, "tx_kbps": 0, "rx_kbps": 0,
        })
        entry["count"] += 1
        entry["capacity_kbps"] += link["bw_up"]
        entry["tx_kbps"] += link["tx_kbps"]
        entry["rx_kbps"] += link["rx_kbps"]

    for entry in by_color.values():
        entry["used_pct"] = _pct(entry["tx_kbps"], entry["capacity_kbps"])

    active = [link for link in links if link["oper_up"]]
    return {
        "links": links,
        "by_color": sorted(by_color.values(), key=lambda c: c["capacity_kbps"], reverse=True),
        "totals": {
            "count": len(links),
            "down": sum(1 for link in links if not link["oper_up"]),
            "saturated": sum(1 for link in active if link["severity"] == "critical"),
            "busy": sum(1 for link in active if link["severity"] == "warning"),
            "capacity_kbps": sum(link["bw_up"] for link in active),
            "tx_kbps": sum(link["tx_kbps"] for link in active),
            "rx_kbps": sum(link["rx_kbps"] for link in active),
        },
    }


# ------------------------------------------------------------------------ AAR
def analyse_aar(stats: list[dict], classes: list[dict], events: list[dict]) -> dict:
    """Judge every tunnel against the SLA class bound to it.

    A tunnel is out of SLA when it breaches any one of latency, loss or jitter,
    so the result names which, rather than only saying it failed.
    """
    budgets = {c["name"]: c for c in classes}

    tunnels = []
    for row in stats:
        sla_name = row.get("sla-class")
        budget = budgets.get(sla_name, {})
        latency = row.get("latency")
        loss = row.get("loss")
        jitter = row.get("jitter")

        breaches = []
        if budget:
            if latency is not None and latency > budget.get("latency", float("inf")):
                breaches.append("latency")
            if loss is not None and loss > budget.get("loss", float("inf")):
                breaches.append("loss")
            if jitter is not None and jitter > budget.get("jitter", float("inf")):
                breaches.append("jitter")

        tunnels.append({
            "hostname": row.get("host-name"),
            "local_color": row.get("local-color"),
            "remote_ip": row.get("remote-system-ip"),
            "remote_color": row.get("remote-color"),
            "sla_class": sla_name,
            "policy": row.get("app-route-policy"),
            "latency": latency,
            "loss": loss,
            "jitter": jitter,
            "budget_latency": budget.get("latency"),
            "budget_loss": budget.get("loss"),
            "budget_jitter": budget.get("jitter"),
            "breaches": breaches,
            "in_sla": not breaches,
            # No class bound means nothing to measure against, not a pass.
            "unmeasured": not budget,
        })

    # Violations first, then by how many budgets they blew.
    tunnels.sort(key=lambda t: (t["in_sla"], -len(t["breaches"])))

    per_class = []
    for definition in classes:
        members = [t for t in tunnels if t["sla_class"] == definition["name"]]
        compliant = sum(1 for t in members if t["in_sla"])
        per_class.append({
            **definition,
            "tunnels": len(members),
            "compliant": compliant,
            "violating": len(members) - compliant,
            "compliance_pct": _pct(compliant, len(members)),
        })

    normalised_events = sorted(
        (
            {
                "time": e.get("entry_time"),
                "hostname": e.get("host-name"),
                "policy": e.get("app-route-policy"),
                "sla_class": e.get("sla-class"),
                "from_color": e.get("from-color"),
                "to_color": e.get("to-color"),
                "reason": e.get("reason"),
            }
            for e in events
        ),
        key=lambda e: e["time"] or 0,
        reverse=True,
    )

    violating = [t for t in tunnels if not t["in_sla"]]
    return {
        "tunnels": tunnels,
        "classes": per_class,
        "events": normalised_events,
        "totals": {
            "tunnels": len(tunnels),
            "in_sla": sum(1 for t in tunnels if t["in_sla"]),
            "violating": len(violating),
            "compliance_pct": _pct(len(tunnels) - len(violating), len(tunnels)),
            "switchovers": len(normalised_events),
            "by_reason": _count_reasons(normalised_events),
        },
    }


# -------------------------------------------------------------- enhanced AAR
# Enhanced application-aware routing sends its probes marked with the DSCP of
# the traffic class they represent, instead of one default value. It matters
# because QoS treats DSCP values differently: without it a SLA class is judged
# by whatever queue the default probe lands in, which may be nothing like the
# queue its own traffic rides in — so the figures look fine while the traffic
# they claim to describe is being dropped.
#
# Release floors, by software train.
ENHANCED_AAR_MIN = {
    17: (17, 9, 1),    # Cisco IOS XE Catalyst SD-WAN
    20: (20, 9, 1),    # Catalyst SD-WAN controllers and vEdge
}

# The newest train with a floor recorded above. Anything on a higher train
# postdates the feature, so it is assumed to carry it rather than reported as
# incapable: this check must not mark a fabric as unable to do something its
# software has shipped with for years, just because this table was written
# before that release existed. Trains between the known ones (18.x, 19.x are
# vEdge software predating the 20.x renumbering) never gained it and still
# read as unsupported.
NEWEST_KNOWN_TRAIN = max(ENHANCED_AAR_MIN)


def parse_version(raw: str | None) -> tuple[int, ...] | None:
    """Turn "17.12.3" into (17, 12, 3). Returns None for anything unparseable."""
    if not raw:
        return None
    parts = []
    for piece in str(raw).split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else None


def supports_enhanced_aar(raw: str | None) -> tuple[bool, str | None]:
    """Whether this release can run enhanced AAR, and the floor it is judged by."""
    version = parse_version(raw)
    if version is None:
        return False, None

    major = version[0]
    floor = ENHANCED_AAR_MIN.get(major)

    if floor is None:
        # Newer than every train we have a floor for: the feature predates it.
        if major > NEWEST_KNOWN_TRAIN:
            return True, None
        # 18.x / 19.x and anything older never gained it.
        return False, None

    padded = version + (0,) * (len(floor) - len(version))
    return padded[:len(floor)] >= floor, ".".join(str(n) for n in floor)


# ---------------------------------------------------------- deployment
# A Manager runs either standalone or clustered, and the two are judged by
# different rules — a single node is a deliberate choice, whereas two nodes
# cannot form quorum and is a broken cluster rather than a small one.
#
# Cluster requirements encoded here:
#   - at least three nodes, because quorum needs a majority
#   - an odd count, so a partition always leaves one side with a majority
#   - configuration-db on exactly three nodes, which is where its quorum lives
#
# The services come from Cisco's VManageDetails model: application-server,
# configuration-db, messaging-server and statistics-db.
CLUSTER_MIN_NODES = 3
CONFIG_DB_NODES = 3
MANAGER_SERVICES = (
    "application-server",
    "configuration-db",
    "messaging-server",
    "statistics-db",
)
# Services expected on every node of a cluster; configuration-db is the
# exception, being limited to three by design.
SERVICES_ON_EVERY_NODE = ("application-server", "messaging-server")


def analyse_deployment(
    devices: list[dict],
    tenancy: dict | None = None,
    manager_services: dict[str, list[dict]] | None = None,
) -> dict:
    """Work out which deployment this is, then judge it by that scenario's rules."""
    tenancy = tenancy or {}
    manager_services = manager_services or {}

    def of_role(role):
        return [d for d in devices if sdwan_client.device_role(d.get("device-type")) == role]

    managers = of_role("manager")
    controllers = of_role("controller")
    validators = of_role("validator")

    # What the controller says, and what the inventory shows. They can disagree
    # — a node removed from the cluster but still in the inventory, say — and
    # the disagreement is worth reporting rather than silently picking one.
    declared = (tenancy.get("deploymentmode") or "").strip().lower() or None
    cluster_id = tenancy.get("clusterid") or None
    observed = _observed_mode(len(managers))
    mode = declared if declared in ("standalone", "cluster") else observed

    nodes = []
    for d in managers:
        ip = d.get("system-ip")
        services = manager_services.get(ip) or []
        nodes.append({
            "hostname": d.get("host-name"),
            "system_ip": ip,
            "version": d.get("version"),
            "reachable": d.get("reachability") == "reachable",
            "services": [
                {
                    "service": s.get("service"),
                    "enabled": bool(s.get("enabled")),
                    "status": s.get("status"),
                    "healthy": bool(s.get("enabled"))
                    and str(s.get("status", "")).lower() in ("running", "up", "active"),
                }
                for s in services
            ],
        })

    service_counts = {name: 0 for name in MANAGER_SERVICES}
    for node in nodes:
        for svc in node["services"]:
            if svc["service"] in service_counts and svc["healthy"]:
                service_counts[svc["service"]] += 1

    roles = {
        "manager": _role_state("manager", managers, redundant_at=CLUSTER_MIN_NODES),
        "controller": _role_state("controller", controllers, redundant_at=2),
        "validator": _role_state("validator", validators, redundant_at=2),
    }

    findings = _deployment_findings(
        mode, declared, observed, managers, nodes, service_counts, roles
    )

    actionable = [f for f in findings if f["severity"] != "Info"]
    if not managers:
        # Nothing to judge: without a Manager there is no deployment, and
        # saying "degraded" about whatever else is missing would be a verdict
        # on a scenario that was never observed.
        health = "unknown"
    elif any(f["severity"] == "Critical" for f in actionable):
        health = "broken"
    elif actionable:
        health = "degraded"
    else:
        health = "healthy"

    return {
        "mode": mode or "unknown",
        "declared_mode": declared,
        "observed_mode": observed,
        "health": health,
        "cluster_id": cluster_id,
        "tenancy": tenancy.get("mode"),
        "domain": tenancy.get("domain"),
        "nodes": nodes,
        "service_counts": service_counts,
        "roles": roles,
        "findings": findings,
        "totals": {
            "managers": len(managers),
            "controllers": len(controllers),
            "validators": len(validators),
            "managers_reachable": sum(1 for n in nodes if n["reachable"]),
            "services_reported": sum(len(n["services"]) for n in nodes),
        },
    }


def _observed_mode(manager_count: int) -> str | None:
    if manager_count == 0:
        return None
    if manager_count == 1:
        return "standalone"
    return "cluster"


def _role_state(role: str, members: list[dict], redundant_at: int) -> dict:
    reachable = sum(1 for d in members if d.get("reachability") == "reachable")
    return {
        "role": role,
        "count": len(members),
        "reachable": reachable,
        "redundant": len(members) >= redundant_at,
        "redundant_at": redundant_at,
    }


def _deployment_findings(mode, declared, observed, managers, nodes, service_counts, roles):
    findings = []
    count = len(managers)

    if declared and observed and declared != observed:
        findings.append({
            "severity": "Major",
            "key": "deploy.finding.mode_mismatch",
            "params": {"declared": declared, "observed": observed, "count": count},
        })

    if mode == "cluster":
        # Two nodes is the dangerous case: it looks like a cluster and cannot
        # form a majority, so losing either one loses the whole thing.
        if count < CLUSTER_MIN_NODES:
            findings.append({
                "severity": "Critical",
                "key": "deploy.finding.no_quorum",
                "params": {"count": count, "minimum": CLUSTER_MIN_NODES},
            })
        elif count % 2 == 0:
            findings.append({
                "severity": "Major",
                "key": "deploy.finding.even_nodes",
                "params": {"count": count},
            })

        db_nodes = service_counts.get("configuration-db", 0)
        if count >= CLUSTER_MIN_NODES and db_nodes != CONFIG_DB_NODES:
            findings.append({
                "severity": "Critical" if db_nodes < CONFIG_DB_NODES else "Major",
                "key": "deploy.finding.config_db_count",
                "params": {"running": db_nodes, "expected": CONFIG_DB_NODES},
            })

        for service in SERVICES_ON_EVERY_NODE:
            running = service_counts.get(service, 0)
            if nodes and running < count:
                findings.append({
                    "severity": "Major",
                    "key": "deploy.finding.service_missing",
                    "params": {"service": service, "running": running, "nodes": count},
                })

    elif mode == "standalone":
        # Not a fault — a deliberate choice — but the consequence is worth
        # stating, because it is invisible until the node is gone.
        # Informational, not a fault: running one node is a choice, and the
        # consequence is only worth stating because it is invisible until the
        # node is gone. It must not make a correct deployment read as degraded.
        findings.append({
            "severity": "Info",
            "key": "deploy.finding.standalone_single_point",
            "params": {},
        })

    for node in nodes:
        if not node["reachable"]:
            findings.append({
                "severity": "Critical",
                "key": "deploy.finding.manager_unreachable",
                "params": {"host": node["hostname"]},
            })
        unhealthy = [s["service"] for s in node["services"] if not s["healthy"]]
        if unhealthy:
            findings.append({
                "severity": "Major",
                "key": "deploy.finding.node_service_down",
                "params": {"host": node["hostname"], "services": ", ".join(unhealthy)},
            })

    for role in ("controller", "validator"):
        state = roles[role]
        if state["count"] and not state["redundant"]:
            findings.append({
                "severity": "Major",
                "key": "deploy.finding.no_redundancy",
                "params": {"role": role, "count": state["count"]},
            })
        elif state["count"] == 0:
            findings.append({
                "severity": "Major",
                "key": "deploy.finding.role_absent",
                "params": {"role": role},
            })

    order = {"Critical": 0, "Major": 1, "Minor": 2, "Info": 3}
    findings.sort(key=lambda f: order.get(f["severity"], 9))
    return findings


# ------------------------------------------------------- fabric versions
# Cisco pairs the two trains by minor release: controller 20.12 goes with
# IOS XE SD-WAN 17.12, 20.9 with 17.9, and so on. That pairing is the only way
# to compare a Manager against an edge, since 20.x and 17.x are not comparable
# as numbers.
#
# Taken from the release numbering convention rather than verified against a
# compatibility matrix, so it is reported as guidance and not as a verdict on
# whether a combination is supported.
PAIRED_TRAINS = {20: "control", 17: "edge"}

# The control plane is expected to be at or ahead of the edges. Edges running
# further behind than this are worth naming: interoperability windows are
# finite and an edge left far back stops receiving features the controllers
# already expect.
EDGE_LAG_WARN = 3


def release_number(raw: str | None) -> int | None:
    """The minor release that lets a 20.x controller be compared to a 17.x edge."""
    version = parse_version(raw)
    if version is None or len(version) < 2:
        return None
    return version[1] if version[0] in PAIRED_TRAINS else None


def analyse_fabric_versions(devices: list[dict]) -> dict:
    """Check every node's release, not just the ones a feature happens to need.

    Enhanced AAR only cares about edges and the endpoint audit only sees the
    Manager it connects to, so nothing was looking at the controllers, the
    validators, the other nodes of a Manager cluster, or whether the fabric
    agrees with itself about which release it is on.
    """
    nodes = []
    for d in devices:
        role = sdwan_client.device_role(d.get("device-type"))
        raw = d.get("version")
        nodes.append({
            "hostname": d.get("host-name"),
            "system_ip": d.get("system-ip"),
            "site_id": d.get("site-id"),
            "role": role,
            "version": raw,
            "release": release_number(raw),
            "reachable": d.get("reachability") == "reachable",
        })

    control_roles = ("manager", "controller", "validator")
    control = [n for n in nodes if n["role"] in control_roles]
    edges = [n for n in nodes if n["role"] == "edge"]

    by_role = {}
    for role in ("manager", "controller", "validator", "edge"):
        members = [n for n in nodes if n["role"] == role]
        versions = sorted({n["version"] for n in members if n["version"]})
        by_role[role] = {
            "role": role,
            "count": len(members),
            "versions": versions,
            "consistent": len(versions) <= 1,
            "nodes": members,
        }

    control_releases = sorted({n["release"] for n in control if n["release"] is not None})
    # The lowest control-plane release is what an edge has to stay at or under:
    # one lagging controller constrains the whole fabric.
    control_floor = control_releases[0] if control_releases else None

    findings = _fabric_version_findings(by_role, control_releases, control_floor, edges, nodes)

    if any(f["severity"] == "Critical" for f in findings):
        state = "unsupported"
    elif findings:
        state = "skewed"
    elif not control:
        state = "unknown"
    else:
        state = "consistent"

    return {
        "state": state,
        "nodes": sorted(nodes, key=lambda n: (n["role"] or "zz", n["hostname"] or "")),
        "by_role": by_role,
        "control_releases": control_releases,
        "control_floor": control_floor,
        "findings": findings,
        "totals": {
            "nodes": len(nodes),
            "control_plane": len(control),
            "edges": len(edges),
            "distinct_versions": len({n["version"] for n in nodes if n["version"]}),
            "unreadable": sum(1 for n in nodes if not n["release"]),
        },
    }


def _fabric_version_findings(by_role, control_releases, control_floor, edges, nodes):
    findings = []

    # An edge ahead of the control plane is the one combination Cisco's
    # upgrade order rules out: controllers are meant to go first.
    if control_floor is not None:
        for edge in edges:
            if edge["release"] is not None and edge["release"] > control_floor:
                findings.append({
                    "severity": "Critical",
                    "key": "fabric.finding.edge_ahead",
                    "params": {
                        "host": edge["hostname"],
                        "version": edge["version"],
                        "control": control_floor,
                    },
                })

    # Mixed releases across the control plane: normal mid-upgrade, a problem
    # if it is where the fabric has settled.
    if len(control_releases) > 1:
        findings.append({
            "severity": "Major",
            "key": "fabric.finding.control_plane_split",
            "params": {"releases": ", ".join(f"20.{r}" for r in control_releases)},
        })

    for role in ("manager", "controller", "validator"):
        entry = by_role[role]
        if entry["count"] > 1 and not entry["consistent"]:
            findings.append({
                "severity": "Major",
                "key": "fabric.finding.role_mixed",
                "params": {"role": role, "versions": ", ".join(entry["versions"])},
            })

    if control_floor is not None:
        for edge in edges:
            if edge["release"] is None:
                continue
            gap = control_floor - edge["release"]
            if gap >= EDGE_LAG_WARN:
                findings.append({
                    "severity": "Minor",
                    "key": "fabric.finding.edge_lagging",
                    "params": {
                        "host": edge["hostname"],
                        "version": edge["version"],
                        "gap": gap,
                    },
                })

    for node in nodes:
        if node["release"] is None:
            findings.append({
                "severity": "Minor",
                "key": "fabric.finding.version_unreadable",
                "params": {
                    "host": node["hostname"] or "—",
                    "version": node["version"] or "—",
                },
            })

    order = {"Critical": 0, "Major": 1, "Minor": 2}
    findings.sort(key=lambda f: order.get(f["severity"], 9))
    return findings


def version_status(raw: str | None) -> str:
    """How the verdict on this release was reached, for honest reporting.

    "assumed" matters: the dashboard says the release is capable because it
    postdates the feature, not because anyone checked that release.
    """
    version = parse_version(raw)
    if version is None:
        return "unknown"

    major = version[0]
    if major in ENHANCED_AAR_MIN:
        return "checked" if supports_enhanced_aar(raw)[0] else "too_old"
    return "assumed" if major > NEWEST_KNOWN_TRAIN else "too_old"


def analyse_enhanced_aar(
    devices: list[dict],
    sla_definitions: list[dict],
    probe_classes: list[dict],
) -> dict:
    """Check whether enhanced AAR is actually in effect, not merely available.

    Three things have to line up: the software has to support it, an
    app-probe-class has to exist, and each SLA class has to reference one.
    Any of the three missing leaves some traffic measured by default probes.
    """
    # An SLA class references its app-probe-class by the probe list's listId
    # UUID. Names are indexed too, because some vManage builds return the name
    # in that field and resolving only by UUID would report a correctly
    # configured fabric as broken.
    probes_by_id = {str(p["list_id"]): p for p in probe_classes if p.get("list_id")}
    probes_by_name = {p["name"]: p for p in probe_classes if p.get("name")}

    # --- per SLA class ---
    classes = []
    for definition in sla_definitions:
        reference = definition.get("app_probe_class")
        probe = None
        if reference is not None:
            key = str(reference)
            probe = probes_by_id.get(key) or probes_by_name.get(key)

        classes.append({
            "name": definition.get("name"),
            # Show the operator the probe class's name, never a raw UUID.
            "probe_class": (probe or {}).get("name") or (reference if probe else None),
            "enhanced": bool(probe),
            # A binding pointing at a class that does not exist is worse than
            # no binding: it looks configured and measures nothing special.
            "dangling": reference is not None and probe is None,
            "dscp": (probe or {}).get("dscp"),
            "dscp_map": (probe or {}).get("dscp_map") or [],
            "mixed_dscp": bool((probe or {}).get("mixed_dscp")),
            "forwarding_class": (probe or {}).get("forwarding_class"),
            "latency": definition.get("latency"),
            "loss": definition.get("loss"),
            "jitter": definition.get("jitter"),
        })

    # --- per device ---
    # Matched by role: "vedge" today, but a rename must not empty this list.
    edges = [d for d in devices if sdwan_client.device_role(d.get("device-type")) == "edge"]
    device_rows = []
    for d in edges:
        ok, floor = supports_enhanced_aar(d.get("version"))
        device_rows.append({
            "hostname": d.get("host-name"),
            "system_ip": d.get("system-ip"),
            "site_id": d.get("site-id"),
            "version": d.get("version"),
            "supported": ok,
            # "assumed" when the release is newer than anything in the table:
            # capable by date rather than by verification.
            "basis": version_status(d.get("version")),
            "required": floor,
            "reachable": d.get("reachability") == "reachable",
        })
    # Blockers first: those are the rows an operator has to act on.
    device_rows.sort(key=lambda r: (r["supported"], r["hostname"] or ""))

    enhanced = [c for c in classes if c["enhanced"]]
    blocking = [d for d in device_rows if not d["supported"]]
    bound = {c["probe_class"] for c in classes if c["enhanced"]}
    unused = [p["name"] for p in probe_classes
              if p.get("name") and p["name"] not in bound]

    if not probe_classes:
        state = "disabled"
    elif not enhanced:
        state = "disabled"
    elif len(enhanced) < len(classes) or blocking:
        state = "partial"
    else:
        state = "enabled"

    findings = _enhanced_aar_findings(classes, blocking, unused, probe_classes)

    return {
        "state": state,
        "classes": classes,
        "devices": device_rows,
        "probe_classes": [
            {
                "name": p.get("name"),
                "dscp": p.get("dscp"),
                "forwarding_class": p.get("forwarding_class"),
                "dscp_map": p.get("dscp_map") or [],
                "used_by": [c["name"] for c in classes if c["probe_class"] == p.get("name")],
            }
            for p in probe_classes
        ],
        "findings": findings,
        "totals": {
            "sla_total": len(classes),
            "sla_enhanced": len(enhanced),
            "sla_default": len(classes) - len(enhanced),
            "coverage_pct": _pct(len(enhanced), len(classes)),
            "probe_classes": len(probe_classes),
            "devices_total": len(device_rows),
            "devices_supported": sum(1 for d in device_rows if d["supported"]),
            "devices_blocking": len(blocking),
        },
    }


def _enhanced_aar_findings(classes, blocking, unused, probes) -> list[dict]:
    """What to actually do about it, most severe first."""
    findings = []

    if not probes:
        findings.append({
            "severity": "Major", "key": "eaar.finding.no_probe_classes", "params": {},
        })

    for c in classes:
        if c["dangling"]:
            findings.append({
                "severity": "Critical",
                "key": "eaar.finding.dangling_probe",
                "params": {"sla": c["name"], "probe": c["probe_class"]},
            })
        elif not c["enhanced"]:
            findings.append({
                "severity": "Major",
                "key": "eaar.finding.default_probing",
                "params": {"sla": c["name"]},
            })

    for d in blocking:
        findings.append({
            "severity": "Major",
            "key": "eaar.finding.version_too_old",
            "params": {
                "host": d["hostname"],
                "version": d["version"] or "—",
                "required": d["required"] or "17.9.1",
            },
        })

    for name in unused:
        findings.append({
            "severity": "Minor",
            "key": "eaar.finding.probe_unused",
            "params": {"probe": name},
        })

    order = {"Critical": 0, "Major": 1, "Minor": 2}
    findings.sort(key=lambda f: order.get(f["severity"], 9))
    return findings


def _count_reasons(events: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        reason = event.get("reason") or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))
