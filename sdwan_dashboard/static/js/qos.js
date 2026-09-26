/* QoS view: where is the policy dropping traffic, and does it matter?

   Raw drop counts do not answer that — a thousand drops out of ten million in
   best-effort is healthy shaping, while one drop in a voice queue is a fault.
   The server does that judgement (analysis.py); this renders it. */
"use strict";

let chartQosClass = null;

async function loadQos() {
  const data = await fetchJSON("/api/qos");
  renderQosTotals(data.totals);
  renderQosInterfaces(data.interfaces);
  renderQosQueues(data.queues);
  renderQosChart(data.queues);
}

function renderQosTotals(totals) {
  document.getElementById("qos-drops").textContent = compact(totals.drops);
  document.getElementById("qos-packets").textContent = compact(totals.tx_packets);
  document.getElementById("qos-queues").textContent = totals.queues_dropping;

  // A real-time queue dropping anything at all is the headline, so it gets its
  // own figure rather than being averaged into the total.
  const rt = document.getElementById("qos-realtime");
  rt.textContent = compact(totals.realtime_drops);
  rt.style.color = totals.realtime_drops > 0 ? RED : GREEN;
  document.getElementById("qos-realtime-verdict").textContent =
    totals.realtime_drops > 0 ? t("qos.realtime_bad") : t("qos.realtime_ok");
}

function renderQosInterfaces(interfaces) {
  const tbody = document.getElementById("qos-iface-tbody");
  if (!interfaces.length) {
    tbody.innerHTML = emptyRow(6, t("qos.none"));
    return;
  }
  tbody.innerHTML = interfaces.map(i => `<tr>
    <td style="font-weight:600">${esc(i.hostname)}</td>
    <td>${esc(i.interface)}</td>
    <td><span class="chip">${esc(i.policy)}</span></td>
    <td>${esc(compact(i.tx_packets))}</td>
    <td>${esc(compact(i.drops))}</td>
    <td>${meter(i.drop_pct, i.drop_pct >= 1 ? "critical" : i.drop_pct > 0 ? "warning" : "ok")}</td>
  </tr>`).join("");
}

function renderQosQueues(queues) {
  const tbody = document.getElementById("qos-queue-tbody");
  if (!queues.length) {
    tbody.innerHTML = emptyRow(8, t("qos.none"));
    return;
  }
  tbody.innerHTML = queues.map(q => `<tr>
    <td style="font-weight:600">${esc(q.hostname)}</td>
    <td>${esc(q.interface)}</td>
    <td class="mono-cell">${esc(q.queue)}</td>
    <td>
      <span class="chip">${esc(q.klass)}</span>
      ${q.realtime ? `<span class="tag-rt">${esc(t("qos.realtime"))}</span>` : ""}
    </td>
    <td>${esc(compact(q.tx_packets))}</td>
    <td>${esc(compact(q.drops))}</td>
    <td class="mono-cell">${esc(q.drop_pct)}%</td>
    <td>${verdictPill(q.severity)}</td>
  </tr>`).join("");
}

function renderQosChart(queues) {
  // Aggregate by class: an operator thinks "is voice dropping?", not
  // "is queue 0 on Gi0/0/1 dropping?".
  const byClass = {};
  queues.forEach(q => {
    const entry = byClass[q.klass] || (byClass[q.klass] = { drops: 0, tx: 0 });
    entry.drops += q.drops;
    entry.tx += q.tx_packets;
  });

  const labels = Object.keys(byClass);
  const pct = labels.map(k => {
    const e = byClass[k];
    return e.tx + e.drops ? +(e.drops / (e.tx + e.drops) * 100).toFixed(3) : 0;
  });

  const ctx = document.getElementById("chartQosClass").getContext("2d");
  const data = {
    labels,
    datasets: [{
      label: t("qos.drop_pct"),
      data: pct,
      backgroundColor: pct.map(v => v >= 1 ? RED : v > 0.1 ? ORANGE : GREEN),
      borderRadius: 4,
    }],
  };

  if (chartQosClass) {
    chartQosClass.data = data;
    chartQosClass.update();
    return;
  }

  chartQosClass = new Chart(ctx, {
    type: "bar",
    data,
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: c => ` ${c.raw}%` } },
      },
      scales: {
        x: { grid: { display: false } },
        y: { beginAtZero: true, grid: { color: "#253D57" }, ticks: { callback: v => v + "%" } },
      },
    },
  });
}

startAutoRefresh([loadQos]);
