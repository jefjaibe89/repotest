/* Link utilisation: which circuits are running out of room?

   Throughput alone cannot answer that — 184 Mbps is comfortable on a gigabit
   circuit and desperate on a 200 Mbps one. Everything here is a ratio against
   the bandwidth each TLOC was configured with. */
"use strict";

let chartLinkColor = null;

async function loadLinks() {
  const data = await fetchJSON("/api/links");
  renderLinkTotals(data.totals);
  renderLinkTable(data.links);
  renderColorChart(data.by_color);
  renderColorTable(data.by_color);
}

function renderLinkTotals(totals) {
  document.getElementById("link-count").textContent = totals.count;
  document.getElementById("link-capacity").textContent = bitrate(totals.capacity_kbps);
  document.getElementById("link-tx").textContent = bitrate(totals.tx_kbps);
  document.getElementById("link-rx").textContent = bitrate(totals.rx_kbps);

  const sat = document.getElementById("link-saturated");
  sat.textContent = totals.saturated;
  sat.style.color = totals.saturated > 0 ? RED : GREEN;

  const busy = document.getElementById("link-busy");
  busy.textContent = totals.busy;
  busy.style.color = totals.busy > 0 ? ORANGE : DIM;

  const down = document.getElementById("link-down");
  down.textContent = totals.down;
  down.style.color = totals.down > 0 ? RED : DIM;
}

function renderLinkTable(links) {
  const tbody = document.getElementById("link-tbody");
  if (!links.length) {
    tbody.innerHTML = emptyRow(8, t("links.none"));
    return;
  }
  tbody.innerHTML = links.map(l => `<tr>
    <td style="font-weight:600">${esc(l.hostname)}</td>
    <td>${esc(l.interface)}</td>
    <td><span class="chip">${esc(l.color)}</span></td>
    <td class="mono-cell">${esc(bitrate(l.bw_up))}</td>
    <td>${l.oper_up ? meter(l.tx_pct, l.severity) : "—"}</td>
    <td>${l.oper_up ? meter(l.rx_pct, l.severity) : "—"}</td>
    <td class="mono-cell">${esc(bitrate(l.tx_kbps))} / ${esc(bitrate(l.rx_kbps))}</td>
    <td>${verdictPill(l.severity)}</td>
  </tr>`).join("");
}

function renderColorTable(colors) {
  const tbody = document.getElementById("color-tbody");
  if (!colors.length) {
    tbody.innerHTML = emptyRow(5, t("links.none"));
    return;
  }
  tbody.innerHTML = colors.map(c => `<tr>
    <td><span class="chip">${esc(c.color)}</span></td>
    <td class="mono-cell">${esc(c.count)}</td>
    <td class="mono-cell">${esc(bitrate(c.capacity_kbps))}</td>
    <td class="mono-cell">${esc(bitrate(c.tx_kbps))}</td>
    <td>${meter(c.used_pct, c.used_pct >= 90 ? "critical" : c.used_pct >= 70 ? "warning" : "ok")}</td>
  </tr>`).join("");
}

function renderColorChart(colors) {
  // Capacity versus what is actually being used, per transport colour: the
  // shape that tells you whether a circuit type is worth what it costs.
  const labels = colors.map(c => c.color);
  const ctx = document.getElementById("chartLinkColor").getContext("2d");
  const data = {
    labels,
    datasets: [
      {
        label: t("links.capacity"),
        data: colors.map(c => Math.round(c.capacity_kbps / 1000)),
        backgroundColor: SURFACE2,
        borderColor: "#253D57",
        borderWidth: 1,
        borderRadius: 4,
      },
      {
        label: t("links.in_use"),
        data: colors.map(c => Math.round(c.tx_kbps / 1000)),
        backgroundColor: "rgba(0,188,235,.8)",
        borderRadius: 4,
      },
    ],
  };

  if (chartLinkColor) {
    chartLinkColor.data = data;
    chartLinkColor.update();
    return;
  }

  chartLinkColor = new Chart(ctx, {
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
        x: { grid: { display: false } },
        y: { beginAtZero: true, grid: { color: "#253D57" }, ticks: { callback: v => v + "M" } },
      },
    },
  });
}

startAutoRefresh([loadLinks]);
