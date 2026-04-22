/* Cisco Catalyst SD-WAN — Health Dashboard JS */
"use strict";

// ---------------------------------------------------------------- Chart setup
Chart.defaults.color = "#7A9BBF";
Chart.defaults.borderColor = "#253D57";

let chartReachability = null;
let chartBfd = null;
let chartThroughput = null;

const CISCO_BLUE  = "#00BCEB";
const GREEN       = "#00D68F";
const RED         = "#FF4757";
const ORANGE      = "#FF9A3C";
const YELLOW      = "#FFD600";
const SURFACE2    = "#1D2F44";

// ---------------------------------------------------------------- Fetch helpers
async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status} — ${url}`);
  return res.json();
}

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

    return `<tr data-search="${(d.hostname + d.system_ip).toLowerCase()}">
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
    const sev = a.severity || "Info";
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
    const name = labelMap[item["device-type"]] || item["device-type"];
    const up   = item.up   ?? item.count ?? 0;
    const down = item.down ?? 0;
    const total = item.count ?? (up + down);
    const allUp = down === 0;
    const statusCls = allUp ? "status-all-up" : down < total ? "status-partial" : "status-all-down";
    const statusTxt = allUp ? "All Healthy" : `${down} Down`;
    return `<div class="control-item">
      <div>
        <div class="control-item-name">${name}</div>
        <div class="control-item-count">${up} / ${total} online</div>
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

// ---------------------------------------------------------------- Search filter
document.getElementById("device-search").addEventListener("input", function () {
  const q = this.value.toLowerCase();
  document.querySelectorAll("#device-tbody tr[data-search]").forEach(row => {
    row.style.display = row.dataset.search.includes(q) ? "" : "none";
  });
});

// ---------------------------------------------------------------- Timestamp
function setLastUpdate() {
  document.getElementById("last-update").textContent =
    "Updated: " + new Date().toLocaleTimeString();
}

// ---------------------------------------------------------------- HTML escape
function esc(str) {
  if (str === null || str === undefined) return "—";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------------------------------------------------------------- Full refresh
async function refreshAll() {
  try {
    await Promise.all([
      loadSummary(),
      loadDevices(),
      loadAlarms(),
      loadControl(),
      loadInterfaces(),
    ]);
    setLastUpdate();
  } catch (err) {
    console.error("Dashboard refresh error:", err);
  }
}

// ---------------------------------------------------------------- Boot
refreshAll();
setInterval(refreshAll, REFRESH_INTERVAL);
