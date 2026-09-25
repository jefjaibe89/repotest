/* Cisco Catalyst SD-WAN — Health Dashboard JS */
"use strict";

// ---------------------------------------------------------------- Chart setup
Chart.defaults.color = "#7A9BBF";
Chart.defaults.borderColor = "#253D57";

let chartReachability = null;
let chartBfd = null;
let chartThroughput = null;
let chartHealthScore = null;
let chartTrend = null;
let trendHours = 1;

// Consecutive failed refreshes. The banner only appears once a refresh has
// actually failed, so a single blip during a controller failover is not
// reported as an outage.
let consecutiveFailures = 0;
let bannerDismissed = false;

const CISCO_BLUE  = "#00BCEB";
const GREEN       = "#00D68F";
const RED         = "#FF4757";
const ORANGE      = "#FF9A3C";
const YELLOW      = "#FFD600";
const SURFACE2    = "#1D2F44";

// ---------------------------------------------------------------- Fetch helpers
async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    // The API reports SD-WAN failures as {error, message}; surface that text
    // rather than a bare status code so the operator knows what to fix.
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && body.message) detail = body.message;
    } catch (_) { /* non-JSON error page — keep the status code */ }
    const err = new Error(detail);
    err.url = url;
    throw err;
  }
  return res.json();
}

// ---------------------------------------------------------------- Error banner
function showError(message) {
  if (bannerDismissed) return;
  const banner = document.getElementById("error-banner");
  document.getElementById("error-title").textContent =
    consecutiveFailures > 1
      ? `Cannot reach vManage (${consecutiveFailures} failed refreshes)`
      : "Connection problem";
  document.getElementById("error-detail").textContent = message;
  banner.hidden = false;
}

function hideError() {
  document.getElementById("error-banner").hidden = true;
  bannerDismissed = false;
}

document.getElementById("error-dismiss").addEventListener("click", () => {
  bannerDismissed = true;
  document.getElementById("error-banner").hidden = true;
});

// ---------------------------------------------------------------- KPI Summary
async function loadSummary() {
  const d = await fetchJSON("/api/summary");

  document.getElementById("kpi-total").textContent       = d.total_devices;
  document.getElementById("kpi-reachable").textContent   = d.reachable;
  document.getElementById("kpi-unreachable").textContent = d.unreachable;
  document.getElementById("kpi-bfd-up").textContent      = d.bfd_up;
  document.getElementById("kpi-bfd-up-lbl").textContent  = d.bfd_up;
  document.getElementById("kpi-bfd-down").textContent    = d.bfd_down;
  document.getElementById("kpi-omp").textContent         = d.omp_up;
  document.getElementById("kpi-omp-up").textContent      = d.omp_up;
  document.getElementById("kpi-omp-total").textContent   = d.omp_total;
  document.getElementById("kpi-critical").textContent    = d.alarms_critical;
  document.getElementById("kpi-major").textContent       = d.alarms_major;
  document.getElementById("kpi-minor").textContent       = d.alarms_minor;

  updateReachabilityChart(d.reachable, d.unreachable);
  updateBfdChart(d.bfd_up, d.bfd_down);
}

// ---------------------------------------------------------------- Health score
const GRADE_COLORS = { healthy: GREEN, degraded: ORANGE, critical: RED };
const SEV_COLORS   = { Critical: RED, Major: ORANGE, Minor: YELLOW, Info: "#7A9BBF" };
const SEVERITIES   = ["Critical", "Major", "Minor", "Info"];

async function loadHealth() {
  const h = await fetchJSON("/api/health");

  const color = GRADE_COLORS[h.grade] || GREEN;
  const valueEl = document.getElementById("health-score-value");
  valueEl.textContent = h.score;
  valueEl.style.color = color;

  const gradeEl = document.getElementById("health-grade");
  gradeEl.textContent = h.grade;
  gradeEl.className = `health-grade grade-${h.grade}`;

  updateHealthGauge(h.score, color);
  renderHealthBars(h.categories);
  renderFindings(h.findings);
}

function updateHealthGauge(score, color) {
  const ctx = document.getElementById("chartHealthScore").getContext("2d");
  const data = {
    datasets: [{
      data: [score, 100 - score],
      backgroundColor: [color, SURFACE2],
      borderWidth: 0,
      circumference: 360,
    }],
  };
  if (chartHealthScore) {
    chartHealthScore.data = data;
    chartHealthScore.update();
    return;
  }
  chartHealthScore = new Chart(ctx, {
    type: "doughnut",
    data,
    options: {
      cutout: "78%",
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
    },
  });
}

function renderHealthBars(categories) {
  const wrap = document.getElementById("health-bars");
  wrap.innerHTML = Object.entries(categories).map(([name, c]) => {
    const color = c.score >= 90 ? GREEN : c.score >= 70 ? ORANGE : RED;
    return `<div class="health-bar-row">
      <span class="health-bar-name">${esc(name)}</span>
      <div class="bar-bg"><div class="bar-fill" style="width:${c.score}%;background:${color}"></div></div>
      <span class="health-bar-val">${c.score}%</span>
    </div>`;
  }).join("");
}

function renderFindings(findings) {
  const wrap = document.getElementById("health-findings-list");
  if (!findings.length) {
    wrap.innerHTML = `<div class="finding-all-clear">✓ No issues detected</div>`;
    return;
  }
  wrap.innerHTML = findings.slice(0, 8).map(f => {
    const color = SEV_COLORS[f.severity] || SEV_COLORS.Info;
    return `<div class="finding-item">
      <span class="finding-dot" style="background:${color}"></span>
      <span>${esc(f.message)}</span>
    </div>`;
  }).join("");
}

// ---------------------------------------------------------------- Health trend
async function loadTrend() {
  const rows = await fetchJSON(`/api/history?hours=${trendHours}`);
  const empty = document.getElementById("trend-empty");

  // A single sample cannot be drawn as a line; say so rather than show a blank box.
  if (rows.length < 2) {
    empty.hidden = false;
    document.getElementById("chartTrend").style.opacity = "0.15";
    return;
  }
  empty.hidden = true;
  document.getElementById("chartTrend").style.opacity = "1";

  // Over a multi-day window a bare clock time cannot distinguish Monday 09:00
  // from Thursday 09:00, so longer ranges carry the date too.
  const labels = rows.map(r => {
    const d = new Date(r.ts * 1000);
    if (trendHours > 24) {
      return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit" });
    }
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  });

  const ctx = document.getElementById("chartTrend").getContext("2d");
  const data = {
    labels,
    datasets: [
      {
        label: "Health score",
        data: rows.map(r => r.score),
        borderColor: CISCO_BLUE,
        backgroundColor: "rgba(0,188,235,.12)",
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        borderWidth: 2,
        yAxisID: "y",
      },
      {
        label: "Devices down",
        data: rows.map(r => r.unreachable),
        borderColor: RED,
        backgroundColor: "transparent",
        tension: 0.3,
        pointRadius: 0,
        borderWidth: 1.5,
        borderDash: [4, 3],
        yAxisID: "y1",
      },
    ],
  };

  if (chartTrend) {
    chartTrend.data = data;
    chartTrend.update();
    return;
  }

  chartTrend = new Chart(ctx, {
    type: "line",
    data,
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "top", align: "end", labels: { boxWidth: 12, padding: 14 } } },
      scales: {
        x: { grid: { color: "#253D57" }, ticks: { maxTicksLimit: 10, font: { size: 10 } } },
        y: { min: 0, max: 100, grid: { color: "#253D57" }, title: { display: true, text: "Score" } },
        y1: {
          position: "right",
          beginAtZero: true,
          grid: { drawOnChartArea: false },
          ticks: { precision: 0 },
          title: { display: true, text: "Down" },
        },
      },
    },
  });
}

document.getElementById("range-picker").addEventListener("click", e => {
  const btn = e.target.closest(".range-btn");
  if (!btn) return;
  document.querySelectorAll(".range-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  trendHours = Number(btn.dataset.hours);
  loadTrend().catch(err => console.error("Trend load failed:", err));
});

// ---------------------------------------------------------------- Tunnels
async function loadTunnels() {
  const tunnels = await fetchJSON("/api/tunnels");
  const tbody = document.getElementById("tunnel-tbody");

  const up = tunnels.filter(t => t.state === "up").length;
  document.getElementById("tunnel-summary").textContent =
    `${up} up · ${tunnels.length - up} down · ${tunnels.length} total`;

  if (!tunnels.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="loading-cell">No tunnels reported.</td></tr>`;
    return;
  }

  tbody.innerHTML = tunnels.map(t => {
    const stateCls = t.state === "up" ? "state-up" : "state-down";
    const loss = t["loss-percentage"];
    const lossColor = loss === null || loss === undefined ? ""
                    : loss >= 5 ? RED : loss >= 1 ? ORANGE : GREEN;
    return `<tr>
      <td style="font-weight:600">${esc(t["host-name"])}</td>
      <td><span class="color-chip">${esc(t["local-color"])}</span></td>
      <td style="font-family:monospace;color:#7A9BBF">${esc(t["remote-system-ip"])}</td>
      <td><span class="color-chip">${esc(t["remote-color"])}</span></td>
      <td><span class="${stateCls}">● ${esc(t.state)}</span></td>
      <td>${num(t.latency, " ms")}</td>
      <td style="color:${lossColor}">${num(loss, "%")}</td>
      <td>${num(t.jitter, " ms")}</td>
    </tr>`;
  }).join("");
}

function num(value, suffix = "") {
  if (value === null || value === undefined) return `<span style="color:#7A9BBF">—</span>`;
  return `${value}${suffix}`;
}

// ---------------------------------------------------------------- Device Table
async function loadDevices() {
  const devices = await fetchJSON("/api/devices");
  renderDeviceTable(devices);
}

function renderDeviceTable(devices) {
  const tbody = document.getElementById("device-tbody");
  if (!devices.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="loading-cell">No devices found.</td></tr>`;
    return;
  }

  tbody.innerHTML = devices.map(d => {
    const statusClass = d.reachability === "reachable" ? "status-reachable"
                      : d.reachability === "unreachable" ? "status-unreachable"
                      : "status-warning";
    const statusDot = d.reachability === "reachable" ? "●" : "●";

    const typeClass = {
      vmanage: "type-vmanage",
      vsmart:  "type-vsmart",
      vbond:   "type-vbond",
      vedge:   "type-vedge",
    }[d.device_type] || "type-vedge";

    const cpuHtml  = barHtml(d.cpu);
    const memHtml  = barHtml(d.memory);

    return `<tr class="clickable-row"
                data-search="${esc((d.hostname + d.system_ip).toLowerCase())}"
                data-system-ip="${esc(d.system_ip)}"
                data-hostname="${esc(d.hostname)}">
      <td style="font-weight:600">${esc(d.hostname)}</td>
      <td style="font-family:monospace;color:#7A9BBF">${esc(d.system_ip)}</td>
      <td><span class="type-chip ${typeClass}">${esc(d.device_type)}</span></td>
      <td style="color:#7A9BBF">${esc(d.model)}</td>
      <td style="color:#7A9BBF">${esc(d.version)}</td>
      <td>${esc(d.site_id)}</td>
      <td><span class="status-badge ${statusClass}">${statusDot} ${esc(d.reachability)}</span></td>
      <td>${cpuHtml}</td>
      <td>${memHtml}</td>
    </tr>`;
  }).join("");
}

function barHtml(value) {
  if (value === null || value === undefined) return `<span style="color:#7A9BBF">—</span>`;
  const pct = Math.min(100, Math.max(0, value));
  const color = pct >= 85 ? RED : pct >= 65 ? ORANGE : GREEN;
  return `<div class="bar-wrap">
    <div class="bar-bg"><div class="bar-fill" style="width:${pct}%;background:${color}"></div></div>
    <span class="bar-label">${pct}%</span>
  </div>`;
}

// ---------------------------------------------------------------- Alarms
async function loadAlarms() {
  const alarms = await fetchJSON("/api/alarms");
  const feed = document.getElementById("alarms-feed");

  if (!alarms.length) {
    feed.innerHTML = `<p class="loading-cell">No alarms.</p>`;
    return;
  }

  feed.innerHTML = alarms.map(a => {
    // Map through the known severities rather than trusting the field: it
    // reaches a class attribute, where a stray quote would break out of it.
    const sev = SEVERITIES.includes(a.severity) ? a.severity : "Info";
    const cls = `alarm-${sev.toLowerCase()}`;
    const time = a.entry_time ? new Date(a.entry_time).toLocaleString() : "—";
    const ackHtml = a.acknowledged ? `<div class="alarm-ack">✓ Acknowledged</div>` : "";
    return `<div class="alarm-item ${cls}">
      <div class="alarm-header">
        <span class="alarm-sev sev-${sev}">${sev}</span>
        <span class="alarm-time">${time}</span>
      </div>
      <div class="alarm-msg">${esc(a.message || a.type || "—")}</div>
      ${ackHtml}
    </div>`;
  }).join("");
}

// ---------------------------------------------------------------- Control Plane
async function loadControl() {
  const data = await fetchJSON("/api/control");
  const list = document.getElementById("control-list");

  if (!data.length) {
    list.innerHTML = `<p class="loading-cell">No data.</p>`;
    return;
  }

  const labelMap = { vmanage: "vManage", vsmart: "vSmart", vbond: "vBond" };

  list.innerHTML = data.map(item => {
    const name = labelMap[item["device-type"]] || esc(item["device-type"]);
    const up   = item.up   ?? item.count ?? 0;
    const down = item.down ?? 0;
    const total = item.count ?? (up + down);
    const allUp = down === 0;
    const statusCls = allUp ? "status-all-up" : down < total ? "status-partial" : "status-all-down";
    const statusTxt = allUp ? "All Healthy" : `${esc(down)} Down`;
    return `<div class="control-item">
      <div>
        <div class="control-item-name">${name}</div>
        <div class="control-item-count">${esc(up)} / ${esc(total)} online</div>
      </div>
      <div class="control-item-status ${statusCls}">${statusTxt}</div>
    </div>`;
  }).join("");
}

// ---------------------------------------------------------------- Interfaces / Throughput
async function loadInterfaces() {
  const data = await fetchJSON("/api/interfaces");
  updateThroughputChart(data);
}

// ---------------------------------------------------------------- Charts
function updateReachabilityChart(reachable, unreachable) {
  const ctx = document.getElementById("chartReachability").getContext("2d");
  const data = {
    labels: ["Reachable", "Unreachable"],
    datasets: [{
      data: [reachable, unreachable],
      backgroundColor: [GREEN, RED],
      borderWidth: 0,
      hoverOffset: 8,
    }],
  };
  if (chartReachability) {
    chartReachability.data = data;
    chartReachability.update();
    return;
  }
  chartReachability = new Chart(ctx, {
    type: "doughnut",
    data,
    options: {
      cutout: "65%",
      plugins: {
        legend: { position: "bottom", labels: { padding: 16, boxWidth: 12 } },
        tooltip: { callbacks: { label: c => ` ${c.label}: ${c.raw}` } },
      },
    },
  });
}

function updateBfdChart(up, down) {
  const ctx = document.getElementById("chartBfd").getContext("2d");
  const data = {
    labels: ["Up", "Down"],
    datasets: [{
      data: [up, down],
      backgroundColor: [CISCO_BLUE, RED],
      borderWidth: 0,
      hoverOffset: 8,
    }],
  };
  if (chartBfd) {
    chartBfd.data = data;
    chartBfd.update();
    return;
  }
  chartBfd = new Chart(ctx, {
    type: "doughnut",
    data,
    options: {
      cutout: "65%",
      plugins: {
        legend: { position: "bottom", labels: { padding: 16, boxWidth: 12 } },
        tooltip: { callbacks: { label: c => ` ${c.label}: ${c.raw}` } },
      },
    },
  });
}

function updateThroughputChart(interfaces) {
  const upLinks = interfaces.filter(i => i["if-oper-status"] === "Up").slice(0, 6);
  const labels  = upLinks.map(i => `${i["host-name"]} — ${i.interface.replace("GigabitEthernet", "Gi")}`);
  const txData  = upLinks.map(i => Math.round((i["tx-kbps"] || 0) / 1000));
  const rxData  = upLinks.map(i => Math.round((i["rx-kbps"] || 0) / 1000));

  const ctx = document.getElementById("chartThroughput").getContext("2d");
  const data = {
    labels,
    datasets: [
      {
        label: "TX (Mbps)",
        data: txData,
        backgroundColor: "rgba(0,188,235,.7)",
        borderRadius: 4,
      },
      {
        label: "RX (Mbps)",
        data: rxData,
        backgroundColor: "rgba(0,214,143,.6)",
        borderRadius: 4,
      },
    ],
  };

  if (chartThroughput) {
    chartThroughput.data = data;
    chartThroughput.update();
    return;
  }

  chartThroughput = new Chart(ctx, {
    type: "bar",
    data,
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "top", align: "end", labels: { boxWidth: 12, padding: 14 } },
        tooltip: { callbacks: { label: c => ` ${c.dataset.label}: ${c.raw} Mbps` } },
      },
      scales: {
        x: {
          grid: { color: "#253D57" },
          ticks: { maxRotation: 25, font: { size: 11 } },
        },
        y: {
          grid: { color: "#253D57" },
          ticks: { callback: v => `${v}M` },
          beginAtZero: true,
        },
      },
    },
  });
}

// ---------------------------------------------------------------- Device modal
const modal = document.getElementById("device-modal");

function openModal(systemIp, hostname) {
  document.getElementById("modal-title").textContent = hostname || systemIp;
  document.getElementById("modal-subtitle").textContent = systemIp;
  document.getElementById("modal-body").innerHTML = `<p class="loading-cell">Loading…</p>`;
  modal.hidden = false;
  document.body.style.overflow = "hidden";

  fetchJSON(`/api/device/${encodeURIComponent(systemIp)}`)
    .then(renderModal)
    .catch(err => {
      document.getElementById("modal-body").innerHTML =
        `<div class="login-error">${esc(err.message)}</div>`;
    });
}

function closeModal() {
  modal.hidden = true;
  document.body.style.overflow = "";
}

document.getElementById("modal-close").addEventListener("click", closeModal);
modal.addEventListener("click", e => { if (e.target === modal) closeModal(); });
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && !modal.hidden) closeModal();
});

function renderModal(d) {
  const dev = d.device;
  const fields = [
    ["Type", dev.device_type], ["Model", dev.model], ["Version", dev.version],
    ["Site", dev.site_id], ["Serial", dev.serial], ["Status", dev.reachability],
    ["CPU", dev.cpu === null ? "—" : dev.cpu + "%"],
    ["Memory", dev.memory === null ? "—" : dev.memory + "%"],
  ];

  document.getElementById("modal-body").innerHTML = `
    <div class="modal-section">
      <p class="modal-section-title">Overview</p>
      <div class="modal-grid">
        ${fields.map(([k, v]) => `<div>
          <div class="modal-field-label">${esc(k)}</div>
          <div class="modal-field-value">${esc(v)}</div>
        </div>`).join("")}
      </div>
    </div>

    ${section("Interfaces", d.interfaces,
      ["Interface", "Admin", "Oper", "IP Address", "VPN", "Speed"],
      i => [
        i.ifname,
        i["if-admin-status"],
        stateSpan(i["if-oper-status"]),
        i["ip-address"],
        i["vpn-id"],
        i["speed-mbps"] ? i["speed-mbps"] + " Mbps" : "—",
      ])}

    ${section("IPsec Tunnels", d.tunnels,
      ["Remote", "Local Color", "Remote Color", "State", "Latency", "Loss"],
      t => [
        t["remote-system-ip"],
        t["local-color"],
        t["remote-color"],
        stateSpan(t.state),
        t.latency === null ? "—" : t.latency + " ms",
        t["loss-percentage"] === null ? "—" : t["loss-percentage"] + "%",
      ])}

    ${section("Control Connections", d.control_connections,
      ["Peer Type", "System IP", "Color", "Protocol", "State", "Uptime"],
      c => [
        c["peer-type"], c["system-ip"], c["local-color"],
        c.protocol, stateSpan(c.state), c.uptime,
      ])}

    ${section("OMP Routes Received", d.omp_routes,
      ["VPN", "Prefix", "From Peer", "Status"],
      r => [r["vpn-id"], r.prefix, r["from-peer"], r.status])}
  `;
}

// Markup is trusted because of where it came from, never because of how it
// looks. Only this module can mint a SafeMarkup, so a data value that happens
// to start with "<span" can no longer pass itself off as our own output.
class SafeMarkup {
  constructor(html) { this.html = html; }
}

function stateSpan(state) {
  const isUp = String(state).toLowerCase() === "up";
  return new SafeMarkup(
    `<span class="${isUp ? "state-up" : "state-down"}">● ${esc(state)}</span>`
  );
}

function section(title, rows, headers, mapRow) {
  const body = !rows || !rows.length
    ? `<p class="modal-empty">Nothing reported.</p>`
    : `<table class="mini-table">
         <thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead>
         <tbody>${rows.map(r => `<tr>${mapRow(r).map(cellHtml).join("")}</tr>`).join("")}</tbody>
       </table>`;
  return `<div class="modal-section">
    <p class="modal-section-title">${esc(title)}</p>${body}
  </div>`;
}

function cellHtml(value) {
  return `<td>${value instanceof SafeMarkup ? value.html : esc(value)}</td>`;
}

document.getElementById("device-tbody").addEventListener("click", e => {
  const row = e.target.closest("tr[data-system-ip]");
  if (row) openModal(row.dataset.systemIp, row.dataset.hostname);
});

// ---------------------------------------------------------------- Search filter
document.getElementById("device-search").addEventListener("input", function () {
  const q = this.value.toLowerCase();
  document.querySelectorAll("#device-tbody tr[data-search]").forEach(row => {
    row.style.display = row.dataset.search.includes(q) ? "" : "none";
  });
});

// ---------------------------------------------------------------- Timestamp
function setLastUpdate(fetchedAt) {
  // Show when the data was collected, not when the browser drew it.
  const when = fetchedAt ? new Date(fetchedAt * 1000) : new Date();
  const el = document.getElementById("last-update");
  el.textContent = "Updated: " + when.toLocaleTimeString();
  el.style.color = "";
}

// ---------------------------------------------------------------- HTML escape
function esc(str) {
  if (str === null || str === undefined) return "—";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ---------------------------------------------------------------- Full refresh
async function refreshAll() {
  // allSettled, not all: one dead panel must not blank out the others.
  const results = await Promise.allSettled([
    loadHealth(),
    loadSummary(),
    loadDevices(),
    loadAlarms(),
    loadControl(),
    loadInterfaces(),
    loadTunnels(),
    loadTrend(),
  ]);

  const failures = results.filter(r => r.status === "rejected");

  if (failures.length === 0) {
    consecutiveFailures = 0;
    // Data now comes from the poller, so a successful fetch only proves the web
    // tier is alive. The poller can be failing behind it, serving the last good
    // payload — /api/status is what says whether the figures are current.
    await reportPollerHealth();
    return;
  }

  consecutiveFailures += 1;
  failures.forEach(f => console.error("Dashboard refresh error:", f.reason));
  showError(failures[0].reason?.message || "Unknown error");

  // Some panels may have loaded; say when the data on screen was last good.
  if (failures.length < results.length) setLastUpdate();
  else markStale();
}

async function reportPollerHealth() {
  let status;
  try {
    status = await fetchJSON("/api/status");
  } catch (_) {
    setLastUpdate();  // Can't tell; the panels loaded, so don't cry wolf.
    return;
  }

  if (status.stale || status.error) {
    const when = status.fetched_at
      ? new Date(status.fetched_at * 1000).toLocaleTimeString()
      : "never";
    document.getElementById("last-update").textContent = `Stale — last good ${when}`;
    document.getElementById("last-update").style.color = "#FF9A3C";
    showError(
      status.error ||
      `Poller has not refreshed in ${Math.round(status.age_seconds)}s`
    );
  } else {
    hideError();
    setLastUpdate(status.fetched_at);
  }
}

function markStale() {
  const el = document.getElementById("last-update");
  if (!el.textContent.startsWith("Stale")) {
    // On the very first refresh there is no prior good timestamp to point back to.
    const previous = el.textContent.startsWith("Updated: ")
      ? el.textContent.replace(/^Updated: /, "last good ")
      : "no data yet";
    el.textContent = `Stale — ${previous}`;
  }
  el.style.color = "#FF9A3C";
}

// ---------------------------------------------------------------- Boot
refreshAll();
setInterval(refreshAll, REFRESH_INTERVAL);
