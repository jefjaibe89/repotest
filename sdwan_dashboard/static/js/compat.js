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

  document.getElementById("compat-version").textContent =
    d.controller.platform_version || t("compat.unknown_version");
  document.getElementById("compat-tenancy").textContent =
    d.controller.tenancy_mode || "—";

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

startAutoRefresh([loadCompat]);
