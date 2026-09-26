/* Compatibility: what this controller served, against what we assume.

   Only the last column is a fact about the controller in front of us. The
   evidence column is a claim about the API in general, and says how well
   founded that claim is. */
"use strict";

const EVIDENCE_PILL = {
  catalogued: "ok",
  variant: "warning",
  unverified: "down",
};

async function loadCompat() {
  const d = await fetchJSON("/api/compat");

  const rel = d.controller.release || {};
  document.getElementById("compat-version").textContent =
    d.controller.platform_version || t("compat.unknown_version");

  // A release newer than the audit is information, not a fault: the source
  // table below is what says whether anything actually broke.
  const RELEASE_COLOR = {
    within_audit: GREEN, newer_than_verified: CISCO_BLUE,
    below_minimum: ORANGE, unknown: DIM,
  };
  const verdict = document.getElementById("compat-tenancy");
  verdict.textContent =
    t("compat.rel." + (rel.status || "unknown")) +
    (d.controller.tenancy_mode ? " · " + d.controller.tenancy_mode : "");
  verdict.style.color = RELEASE_COLOR[rel.status] || DIM;

  const note = document.getElementById("compat-range");
  if (rel.status === "newer_than_verified") {
    note.textContent = t("compat.rel.newer_note", rel);
  } else if (rel.status === "below_minimum") {
    note.textContent = t("compat.rel.old_note", rel);
  } else {
    note.textContent = t("compat.rel.range", rel);
  }

  const served = document.getElementById("compat-served");
  served.textContent = `${d.counts.served}/${d.counts.total}`;
  served.style.color = d.counts.served === d.counts.total ? GREEN : ORANGE;

  document.getElementById("compat-catalogued").textContent = d.counts.catalogued;
  document.getElementById("compat-variant").textContent = d.counts.variant;
  document.getElementById("compat-unverified").textContent = d.counts.unverified;

  const caps = d.controller.capabilities || [];
  document.getElementById("compat-caps").textContent = caps.length
    ? t("compat.capabilities") + ": " + caps.join(", ")
    : "";

  renderSources(d.sources);
}

function renderSources(sources) {
  const tbody = document.getElementById("compat-tbody");
  if (!sources.length) {
    tbody.innerHTML = emptyRow(4, t("common.no_data"));
    return;
  }

  tbody.innerHTML = sources.map(s => {
    const note = s.note ? `<span class="breach-list">${esc(t(s.note))}</span>` : "";
    return `<tr class="${s.served ? "" : "row-alert"}">
      <td>
        <span class="mono-cell"><b>${esc(s.method)}</b> ${esc(s.path)}</span>
        ${s.required ? `<span class="tag-rt">${esc(t("compat.required"))}</span>` : ""}
      </td>
      <td>${esc(t(s.feeds))}</td>
      <td>
        <span class="pill pill-${EVIDENCE_PILL[s.evidence] || "down"}">${esc(t("compat.ev." + s.evidence))}</span>
        ${note}
      </td>
      <td>${s.served
        ? `<span class="pill pill-ok">${esc(t("compat.served_yes"))}</span>`
        : `<span class="pill pill-critical">${esc(t("compat.served_no"))}</span>
           <span class="breach-list">${esc(s.reason || "")}</span>`}</td>
    </tr>`;
  }).join("");
}

// ------------------------------------------------- release consistency
const FABRIC_COLORS = {
  consistent: GREEN, skewed: ORANGE, unsupported: RED, unknown: DIM,
};

async function loadFabricVersions() {
  const d = await fetchJSON("/api/fabric-versions");
  const color = FABRIC_COLORS[d.state] || DIM;

  const state = document.getElementById("fabric-state");
  state.textContent = t("fabric.state." + d.state);
  state.style.color = color;
  document.getElementById("fabric-card").style.borderLeftColor = color;
  document.getElementById("fabric-desc").textContent = t("fabric.desc." + d.state);

  document.getElementById("fabric-control").textContent = d.totals.control_plane;
  document.getElementById("fabric-edges").textContent = d.totals.edges;
  document.getElementById("fabric-versions").textContent = d.totals.distinct_versions;

  renderRoleRows(d.by_role);
  renderNodeRows(d.nodes);
  renderFabricFindings(d.findings);
}

function renderRoleRows(byRole) {
  const tbody = document.getElementById("fabric-role-tbody");
  const rows = Object.values(byRole).filter(r => r.count > 0);
  if (!rows.length) {
    tbody.innerHTML = emptyRow(4, t("common.no_data"));
    return;
  }
  tbody.innerHTML = rows.map(r => `<tr class="${r.consistent ? "" : "row-alert"}">
    <td style="font-weight:600">${esc(tRole(r.role))}</td>
    <td class="mono-cell">${esc(r.count)}</td>
    <td class="mono-cell">${esc(r.versions.join(", ") || "—")}</td>
    <td>${r.consistent
      ? `<span class="pill pill-ok">${esc(t("fabric.yes"))}</span>`
      : `<span class="pill pill-warning">${esc(t("fabric.no"))}</span>`}</td>
  </tr>`).join("");
}

function renderNodeRows(nodes) {
  const tbody = document.getElementById("fabric-node-tbody");
  if (!nodes.length) {
    tbody.innerHTML = emptyRow(6, t("common.no_data"));
    return;
  }
  tbody.innerHTML = nodes.map(n => `<tr>
    <td style="font-weight:600">${esc(n.hostname)}</td>
    <td><span class="type-chip type-${esc(n.role || "unknown")}"
             title="${esc(roleLegacy(n.role))}">${esc(tRole(n.role, true))}</span></td>
    <td class="mono-cell">${esc(n.system_ip)}</td>
    <td class="mono-cell">${esc(n.site_id)}</td>
    <td class="mono-cell">${esc(n.version || "—")}</td>
    <td>${n.reachable
      ? `<span class="pill pill-ok">${esc(t("devices.reachable"))}</span>`
      : `<span class="pill pill-critical">${esc(t("devices.unreachable"))}</span>`}</td>
  </tr>`).join("");
}

function renderFabricFindings(findings) {
  const wrap = document.getElementById("fabric-findings");
  if (!findings.length) {
    wrap.innerHTML = `<div class="finding-all-clear">✓ ${esc(t("fabric.desc.consistent"))}</div>`;
    return;
  }
  wrap.innerHTML = findings.map(f => {
    const color = SEV_COLORS[f.severity] || DIM;
    return `<div class="finding-item">
      <span class="finding-dot" style="background:${color}"></span>
      <span>${esc(t(f.key, localiseParams(f.params)))}</span>
    </div>`;
  }).join("");
}


// ------------------------------------------------- deployment scenario
const DEPLOY_COLORS = {
  healthy: GREEN, degraded: ORANGE, broken: RED, unknown: DIM,
};

// Services expected on every node, against configuration-db which is capped
// at three by design.
const SERVICE_EXPECTATION = {
  "configuration-db": { exactly: 3 },
};

async function loadDeployment() {
  const d = await fetchJSON("/api/deployment");
  const color = DEPLOY_COLORS[d.health] || DIM;

  const mode = document.getElementById("deploy-mode");
  mode.textContent = t("deploy.mode." + d.mode);
  mode.style.color = color;
  document.getElementById("deploy-card").style.borderLeftColor = color;
  document.getElementById("deploy-desc").textContent = t("deploy.desc." + d.mode);
  document.getElementById("deploy-health").textContent = t("deploy.health." + d.health);

  // The scenario's own facts, shown only when the controller reported them.
  const stats = [
    [d.totals.managers, t("deploy.managers")],
    [d.totals.controllers, tRole("controller")],
    [d.totals.validators, tRole("validator")],
  ];
  document.getElementById("deploy-stats").innerHTML = stats.map(
    ([n, label]) => `<span class="eaar-stat"><strong>${esc(n)}</strong> ${esc(label)}</span>`
  ).join("");

  const ident = [];
  if (d.cluster_id) ident.push(`${t("deploy.cluster_id")}: ${d.cluster_id}`);
  if (d.tenancy) ident.push(`${t("deploy.tenancy")}: ${d.tenancy}`);
  if (d.domain) ident.push(`${t("deploy.domain")}: ${d.domain}`);
  document.getElementById("deploy-cluster-id").textContent = ident.join(" · ");

  renderServiceRows(d);
  renderRedundancy(d.roles);
  renderManagerNodes(d);
  renderDeployFindings(d.findings);
}

function renderServiceRows(d) {
  const tbody = document.getElementById("deploy-service-tbody");
  if (d.mode !== "cluster") {
    tbody.innerHTML = emptyRow(4, t("deploy.not_clustered"));
    return;
  }

  const nodes = d.totals.managers;
  tbody.innerHTML = Object.entries(d.service_counts).map(([service, running]) => {
    const rule = SERVICE_EXPECTATION[service];
    const expected = rule ? rule.exactly : nodes;
    const label = rule
      ? t("deploy.exactly", { count: rule.exactly })
      : t("deploy.every_node");
    const ok = running === expected;
    return `<tr class="${ok ? "" : "row-alert"}">
      <td class="mono-cell" style="font-weight:600">${esc(service)}</td>
      <td class="mono-cell">${esc(running)} ${esc(t("deploy.of_nodes", { count: nodes }))}</td>
      <td>${esc(label)}</td>
      <td>${ok ? verdictPill("ok") : verdictPill("critical")}</td>
    </tr>`;
  }).join("");
}

function renderRedundancy(roles) {
  const wrap = document.getElementById("deploy-redundancy");
  wrap.innerHTML = Object.values(roles).map(r => `<div class="eaar-device">
    <span class="eaar-device-name">${esc(tRole(r.role))}</span>
    <span class="eaar-device-ver">
      <b>${esc(r.count)}</b> ·
      ${r.redundant
        ? `<span style="color:${GREEN}">${esc(t("deploy.redundant_yes"))}</span>`
        : `<span style="color:${ORANGE}">${esc(t("deploy.redundant_no"))}</span>`}
    </span>
  </div>`).join("");
}

function renderManagerNodes(d) {
  const tbody = document.getElementById("deploy-node-tbody");
  if (!d.nodes.length) {
    tbody.innerHTML = emptyRow(5, t("common.no_data"));
    return;
  }
  tbody.innerHTML = d.nodes.map(n => {
    const services = n.services.length
      ? n.services.map(s => `<span class="chip" style="color:${s.healthy ? GREEN : RED};
           border-color:${s.healthy ? GREEN : RED}" title="${esc(s.status)}">${esc(s.service)}</span>`).join(" ")
      : `<span class="small">${esc(t("deploy.no_service_data"))}</span>`;
    return `<tr class="${n.reachable ? "" : "row-alert"}">
      <td style="font-weight:600">${esc(n.hostname)}</td>
      <td class="mono-cell">${esc(n.system_ip)}</td>
      <td class="mono-cell">${esc(n.version || "—")}</td>
      <td>${services}</td>
      <td>${n.reachable
        ? `<span class="pill pill-ok">${esc(t("devices.reachable"))}</span>`
        : `<span class="pill pill-critical">${esc(t("devices.unreachable"))}</span>`}</td>
    </tr>`;
  }).join("");
}

function renderDeployFindings(findings) {
  const wrap = document.getElementById("deploy-findings");
  if (!findings.length) {
    wrap.innerHTML = `<div class="finding-all-clear">✓ ${esc(t("deploy.health.healthy"))}</div>`;
    return;
  }
  wrap.innerHTML = findings.map(f => {
    const color = SEV_COLORS[f.severity] || DIM;
    return `<div class="finding-item">
      <span class="finding-dot" style="background:${color}"></span>
      <span>${esc(t(f.key, localiseParams(f.params)))}</span>
    </div>`;
  }).join("");
}

startAutoRefresh([loadCompat, loadDeployment, loadFabricVersions]);
