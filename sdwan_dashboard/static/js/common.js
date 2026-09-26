/* Shared by every dashboard view: translation, escaping, fetching, the error
   banner and the staleness indicator. Loaded before each view's own script. */
"use strict";

Chart.defaults.color = "#7A9BBF";
Chart.defaults.borderColor = "#253D57";

const CISCO_BLUE = "#00BCEB";
const GREEN      = "#00D68F";
const RED        = "#FF4757";
const ORANGE     = "#FF9A3C";
const YELLOW     = "#FFD600";
const SURFACE2   = "#1D2F44";
const DIM        = "#7A9BBF";

const SEV_COLORS = { Critical: RED, Major: ORANGE, Minor: YELLOW, Info: DIM };
const SEVERITIES = ["Critical", "Major", "Minor", "Info"];

// Mirrors DEVICE_ROLES in sdwan_client.py, for the few payloads that carry a
// raw device-type rather than a normalised role.
const ROLE_OF = {
  vmanage: "manager", manager: "manager",
  vsmart: "controller", controller: "controller",
  vbond: "validator", validator: "validator",
  vedge: "edge", cedge: "edge", edge: "edge",
};

// Verdicts produced by analysis.py, mapped to the colour each one is shown in.
const VERDICT_COLORS = {
  ok: GREEN, warning: ORANGE, critical: RED, down: DIM,
};

// Consecutive failed refreshes. The banner only appears once a refresh has
// actually failed, so a single blip during a controller failover is not
// reported as an outage.
let consecutiveFailures = 0;
let bannerDismissed = false;

// ---------------------------------------------------------------- i18n
// The catalog is inlined by the server so the dashboard renders with no egress.
function t(key, params) {
  var text = (typeof I18N === "object" && I18N[key]) || key;
  if (!params) return text;
  return text.replace(/\{(\w+)\}/g, function (match, name) {
    return name in params ? params[name] : match;
  });
}

// Values that arrive from vManage are translated for display only, falling
// back to the raw value for anything the catalogue does not know.
function tSeverity(sev) { return t("alarms.sev." + sev); }

function tReach(state) {
  var key = "devices." + String(state).toLowerCase();
  var out = t(key);
  return out === key ? state : out;
}

function tVerdict(verdict) { return t("verdict." + verdict); }

// Cisco's current product names, shown from the canonical role rather than
// the wire value. The legacy name goes in the title attribute, because
// operators still say "vManage" and need to recognise the row.
function tRole(role, short) {
  if (!role) return t("role.unknown");
  const key = (short ? "role.short." : "role.") + role;
  const out = t(key);
  return out === key ? role : out;
}

// Findings are computed server-side, where the viewer's language is not
// known, so a role name arrives as its canonical key. Translate it before it
// is interpolated, or a Spanish sentence ends up containing "validator".
function localiseParams(params) {
  if (!params || !("role" in params)) return params;
  return { ...params, role: tRole(params.role) };
}

function roleLegacy(role) {
  const key = "role.legacy." + role;
  const out = t(key);
  return out === key ? "" : t("role.also_known_as", { legacy: out });
}

// ---------------------------------------------------------------- escaping
function esc(str) {
  if (str === null || str === undefined) return "—";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function num(value, suffix = "") {
  if (value === null || value === undefined) return `<span style="color:${DIM}">—</span>`;
  return `${esc(value)}${suffix}`;
}

// Large packet counts are unreadable as raw digits in a table.
function compact(value) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat(LOCALE, { notation: "compact", maximumFractionDigits: 1 })
    .format(value);
}

// vManage reports bandwidth in kbps; operators read Mbps and Gbps.
function bitrate(kbps) {
  if (kbps === null || kbps === undefined) return "—";
  if (kbps >= 1000000) return (kbps / 1000000).toFixed(1) + " Gbps";
  if (kbps >= 1000) return Math.round(kbps / 1000) + " Mbps";
  return Math.round(kbps) + " kbps";
}

// ---------------------------------------------------------------- fetching
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

// ---------------------------------------------------------------- error banner
function showError(message) {
  if (bannerDismissed) return;
  const banner = document.getElementById("error-banner");
  if (!banner) return;
  document.getElementById("error-title").textContent =
    consecutiveFailures > 1
      ? t("error.repeated", { count: consecutiveFailures })
      : t("error.title");
  document.getElementById("error-detail").textContent = message;
  banner.hidden = false;
}

function hideError() {
  const banner = document.getElementById("error-banner");
  if (!banner) return;
  banner.hidden = true;
  bannerDismissed = false;
}

(function wireDismiss() {
  const btn = document.getElementById("error-dismiss");
  if (!btn) return;
  btn.addEventListener("click", () => {
    bannerDismissed = true;
    document.getElementById("error-banner").hidden = true;
  });
})();

// ---------------------------------------------------------------- freshness
function setLastUpdate(fetchedAt) {
  // Show when the data was collected, not when the browser drew it.
  const when = fetchedAt ? new Date(fetchedAt * 1000) : new Date();
  const el = document.getElementById("last-update");
  if (!el) return;
  el.textContent = t("status.updated", { time: when.toLocaleTimeString(LOCALE) });
  el.style.color = "";
}

async function reportPollerHealth() {
  let status;
  try {
    status = await fetchJSON("/api/status");
  } catch (_) {
    setLastUpdate();  // Can't tell; the panels loaded, so don't cry wolf.
    return;
  }

  const el = document.getElementById("last-update");
  if (status.stale || status.error) {
    const when = status.fetched_at
      ? new Date(status.fetched_at * 1000).toLocaleTimeString(LOCALE)
      : t("status.never");
    if (el) {
      el.textContent = t("status.stale", { time: when });
      el.style.color = ORANGE;
    }
    // error_key lets a failure raised by the poller render in this viewer's
    // language; status.error is the English text kept for logs.
    showError(
      (status.error_key && t(status.error_key)) ||
      status.error ||
      t("error.no_refresh", { seconds: Math.round(status.age_seconds) })
    );
  } else {
    hideError();
    setLastUpdate(status.fetched_at);
  }
}

function markStale() {
  const el = document.getElementById("last-update");
  if (!el || el.textContent.startsWith(t("status.stale", { time: "" }).slice(0, 6))) return;
  el.textContent = t("status.stale", { time: t("status.never") });
  el.style.color = ORANGE;
}

// ---------------------------------------------------------------- refresh loop
// Every view refreshes the same way: run its loaders, keep whatever succeeded,
// and report the poller's freshness separately from the web tier's.
function startAutoRefresh(loaders) {
  async function refreshAll() {
    // allSettled, not all: one dead panel must not blank out the others.
    const results = await Promise.allSettled(loaders.map(fn => fn()));
    const failures = results.filter(r => r.status === "rejected");

    if (failures.length === 0) {
      consecutiveFailures = 0;
      await reportPollerHealth();
      return;
    }

    consecutiveFailures += 1;
    failures.forEach(f => console.error("Dashboard refresh error:", f.reason));
    showError(failures[0].reason?.message || t("error.unknown"));

    // Some panels may have loaded; say when the data on screen was last good.
    if (failures.length < results.length) setLastUpdate();
    else markStale();
  }

  refreshAll();
  setInterval(refreshAll, REFRESH_INTERVAL);
}

// ---------------------------------------------------------------- table bits
// A severity pill, used by every specialised view.
function verdictPill(verdict) {
  return `<span class="pill pill-${esc(verdict)}">${esc(tVerdict(verdict))}</span>`;
}

// A proportion bar with its own colour, for utilisation and drop ratios.
function meter(pct, severity) {
  const width = Math.min(100, Math.max(0, pct));
  const color = VERDICT_COLORS[severity] || GREEN;
  return `<div class="bar-wrap">
    <div class="bar-bg"><div class="bar-fill" style="width:${width}%;background:${color}"></div></div>
    <span class="bar-label">${esc(pct)}%</span>
  </div>`;
}

function emptyRow(columns, message) {
  return `<tr><td colspan="${columns}" class="loading-cell">${esc(message)}</td></tr>`;
}
