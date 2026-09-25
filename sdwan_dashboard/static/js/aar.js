/* Application-aware routing: is every tunnel inside the SLA bound to it, and
   when did traffic last move because one was not?

   A latency figure means nothing on its own. 78 ms is fine under BULK-SLA and
   a breach under VOICE-SLA, so every measurement here is shown against the
   budget it is actually judged by, and a breach names which budget it blew. */
"use strict";

let chartSlaCompliance = null;

const REASON_COLORS = {
  loss: RED, latency: ORANGE, jitter: YELLOW, recovered: GREEN,
};

async function loadAar() {
  const data = await fetchJSON("/api/aar");
  renderAarTotals(data.totals);
  renderSlaClasses(data.classes);
  renderAarTunnels(data.tunnels);
  renderSwitchovers(data.events);
  renderComplianceChart(data.classes);
}

function renderAarTotals(totals) {
  document.getElementById("aar-tunnels").textContent = totals.tunnels;

  const compliance = document.getElementById("aar-compliance");
  compliance.textContent = totals.compliance_pct + "%";
  compliance.style.color =
    totals.compliance_pct >= 95 ? GREEN : totals.compliance_pct >= 80 ? ORANGE : RED;

  const violating = document.getElementById("aar-violating");
  violating.textContent = totals.violating;
  violating.style.color = totals.violating > 0 ? RED : GREEN;

  document.getElementById("aar-switchovers").textContent = totals.switchovers;

  // Why traffic moved is more actionable than how often: repeated loss
  // switchovers on one colour is a circuit problem, not a policy problem.
  const reasons = document.getElementById("aar-reasons");
  const entries = Object.entries(totals.by_reason || {});
  reasons.innerHTML = entries.length
    ? entries.map(([reason, count]) =>
        `<span class="reason-chip">
           <span class="reason-dot" style="background:${REASON_COLORS[reason] || DIM}"></span>
           ${esc(tReason(reason))} <b>${esc(count)}</b>
         </span>`).join("")
    : `<span class="small">${esc(t("aar.no_switchovers"))}</span>`;
}

function tReason(reason) {
  const key = "aar.reason." + reason;
  const out = t(key);
  return out === key ? reason : out;
}

function renderSlaClasses(classes) {
  const tbody = document.getElementById("sla-tbody");
  if (!classes.length) {
    tbody.innerHTML = emptyRow(7, t("aar.no_classes"));
    return;
  }
  tbody.innerHTML = classes.map(c => `<tr>
    <td style="font-weight:600">${esc(c.name)}</td>
    <td class="mono-cell">≤ ${esc(c.latency)} ms</td>
    <td class="mono-cell">≤ ${esc(c.loss)}%</td>
    <td class="mono-cell">≤ ${esc(c.jitter)} ms</td>
    <td class="mono-cell">${esc(c.tunnels)}</td>
    <td class="mono-cell" style="color:${c.violating ? RED : GREEN}">${esc(c.violating)}</td>
    <td>${meter(c.compliance_pct,
      c.compliance_pct >= 95 ? "ok" : c.compliance_pct >= 80 ? "warning" : "critical")}</td>
  </tr>`).join("");
}

function renderAarTunnels(tunnels) {
  const tbody = document.getElementById("aar-tbody");
  if (!tunnels.length) {
    tbody.innerHTML = emptyRow(8, t("aar.no_tunnels"));
    return;
  }

  // Each metric is shown against its own budget, and coloured only when it is
  // the one that breached.
  function metric(value, budget, unit, breached) {
    if (value === null || value === undefined) return "—";
    const color = breached ? RED : "";
    const shown = `<span class="metric-val" style="color:${color}">${esc(value)}${unit}</span>`;
    const limit = budget === null || budget === undefined
      ? "" : `<span class="metric-budget">/ ${esc(budget)}${unit}</span>`;
    return shown + limit;
  }

  tbody.innerHTML = tunnels.map(tn => {
    const breached = new Set(tn.breaches);
    return `<tr class="${tn.in_sla ? "" : "row-alert"}">
      <td style="font-weight:600">${esc(tn.hostname)}</td>
      <td><span class="chip">${esc(tn.local_color)}</span></td>
      <td class="mono-cell">${esc(tn.remote_ip)}</td>
      <td><span class="chip">${esc(tn.sla_class)}</span></td>
      <td class="mono-cell">${metric(tn.latency, tn.budget_latency, " ms", breached.has("latency"))}</td>
      <td class="mono-cell">${metric(tn.loss, tn.budget_loss, "%", breached.has("loss"))}</td>
      <td class="mono-cell">${metric(tn.jitter, tn.budget_jitter, " ms", breached.has("jitter"))}</td>
      <td>${tn.in_sla
        ? verdictPill("ok")
        : `<span class="pill pill-critical">${esc(t("aar.out_of_sla"))}</span>
           <span class="breach-list">${tn.breaches.map(b => esc(tReason(b))).join(", ")}</span>`}</td>
    </tr>`;
  }).join("");
}

function renderSwitchovers(events) {
  const wrap = document.getElementById("switchover-feed");
  if (!events.length) {
    wrap.innerHTML = `<p class="loading-cell">${esc(t("aar.no_switchovers"))}</p>`;
    return;
  }
  wrap.innerHTML = events.map(e => {
    const color = REASON_COLORS[e.reason] || DIM;
    const when = e.time ? new Date(e.time).toLocaleString(LOCALE) : "—";
    return `<div class="switch-item" style="border-left-color:${color}">
      <div class="switch-head">
        <span class="switch-reason" style="color:${color}">${esc(tReason(e.reason))}</span>
        <span class="switch-time">${esc(when)}</span>
      </div>
      <div class="switch-path">
        <strong>${esc(e.hostname)}</strong>
        <span class="chip">${esc(e.from_color)}</span>
        <span class="switch-arrow">→</span>
        <span class="chip">${esc(e.to_color)}</span>
      </div>
      <div class="switch-meta">${esc(e.policy)} · ${esc(e.sla_class)}</div>
    </div>`;
  }).join("");
}

function renderComplianceChart(classes) {
  const ctx = document.getElementById("chartSlaCompliance").getContext("2d");
  const data = {
    labels: classes.map(c => c.name),
    datasets: [
      {
        label: t("aar.compliant"),
        data: classes.map(c => c.compliant),
        backgroundColor: "rgba(0,214,143,.8)",
        borderRadius: 4,
        stack: "sla",
      },
      {
        label: t("aar.violating"),
        data: classes.map(c => c.violating),
        backgroundColor: "rgba(255,71,87,.8)",
        borderRadius: 4,
        stack: "sla",
      },
    ],
  };

  if (chartSlaCompliance) {
    chartSlaCompliance.data = data;
    chartSlaCompliance.update();
    return;
  }

  chartSlaCompliance = new Chart(ctx, {
    type: "bar",
    data,
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: "top", align: "end", labels: { boxWidth: 12, padding: 14 } } },
      scales: {
        x: { stacked: true, grid: { display: false } },
        y: { stacked: true, beginAtZero: true, grid: { color: "#253D57" }, ticks: { precision: 0 } },
      },
    },
  });
}

startAutoRefresh([loadAar]);
