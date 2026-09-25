# Cisco Catalyst SD-WAN — Health Check Dashboard

A single-page operations dashboard that answers one question at a glance: **is the SD-WAN fabric healthy right now?**

It pulls live state from the vManage REST API — device reachability, BFD and OMP sessions, control-plane status, WAN throughput and alarms — scores it, and renders the result with auto-refresh.

![Dashboard](docs/dashboard.png)

---

## Quick start

The dashboard ships with realistic demo data, so it runs with no controller attached:

```bash
cd sdwan_dashboard
pip install -r requirements.txt
python app.py
```

Open <http://localhost:5000>.

### Connecting to a real vManage

```bash
cp .env.example .env
# edit .env: set SDWAN_MODE=live plus VMANAGE_HOST / USER / PASS
python app.py
```

### Docker

```bash
docker compose up --build          # demo mode
SDWAN_MODE=live VMANAGE_HOST=… docker compose up --build   # live
```

---

## What it shows

**Overall Fabric Health** — a single 0–100 score with the weighted breakdown behind it and the top issues driving it down. Graded `healthy` (≥90), `degraded` (≥70) or `critical`.

| Category | Weight | What it measures |
|---|---|---|
| Reachability | 40 | Devices vManage can reach |
| BFD | 25 | Data-plane tunnel sessions up vs. down |
| Control | 15 | vSmart / vBond / vManage controllers online |
| Alarms | 12 | Unacknowledged alarms, weighted by severity |
| Resources | 8 | Devices running hot on CPU or memory |

Alongside it: KPI cards, reachability and BFD doughnuts, a WAN throughput chart, a searchable device inventory with CPU/memory bars, control-plane status and a severity-coloured alarm feed.

---

## API

Every endpoint returns JSON and is safe to scrape from another tool.

| Endpoint | Returns |
|---|---|
| `GET /api/health` | Overall score, per-category breakdown, findings |
| `GET /api/summary` | KPI counters |
| `GET /api/devices` | Normalized device inventory |
| `GET /api/alarms` | Alarms in the configured window |
| `GET /api/bfd` | BFD session summary per device |
| `GET /api/omp` | OMP peer state per device |
| `GET /api/interfaces` | WAN interface statistics |
| `GET /api/control` | Controller counts by type |
| `GET /api/export/devices.csv` | Inventory as a CSV download |
| `GET /healthz` | Liveness probe |

Failures against vManage come back as HTTP 502 with a readable reason:

```json
{"error": "connection", "message": "Cannot reach vManage at https://10.0.0.1:8443 — the controller did not respond in time"}
```

The UI surfaces that text in a banner and marks the data **Stale** rather than silently showing figures that are no longer true.

---

## Configuration

All settings are environment variables; see `.env.example` for the full list.

| Variable | Default | Notes |
|---|---|---|
| `SDWAN_MODE` | `mock` | `live` talks to vManage |
| `VMANAGE_HOST` / `_PORT` | — / `8443` | Controller address |
| `VMANAGE_USER` / `_PASS` | — | Credentials |
| `VMANAGE_VERIFY_SSL` | `false` | Enable once the certificate is trusted |
| `VMANAGE_TIMEOUT` | `20` | Per-request timeout, seconds |
| `SESSION_TTL_SECONDS` | `900` | How long a vManage login is reused |
| `REFRESH_INTERVAL_SECONDS` | `30` | Browser auto-refresh cadence |
| `CPU_WARN` / `CPU_CRIT` | `65` / `85` | Resource thresholds |
| `MEM_WARN` / `MEM_CRIT` | `65` / `85` | Resource thresholds |
| `ALARM_WINDOW_HOURS` | `24` | Alarm lookback window |

---

## Design notes

**Sessions are reused.** vManage authentication costs a round trip and the controller throttles logins, so one session is cached and shared across requests until `SESSION_TTL_SECONDS` elapses. A `401`, or the HTML login page vManage returns for an idled-out session, invalidates the cache and the next request re-authenticates.

**No CDN.** Chart.js is vendored under `static/vendor/`. SD-WAN management networks are routinely air-gapped, and a dashboard that renders blank without internet egress is useless in exactly the environment it is built for.

**One failing panel does not blank the page.** The browser refreshes all panels with `Promise.allSettled`, so a single failing endpoint leaves the rest on screen.

**Errors stay readable.** Transport exceptions are translated before they reach the operator — "the host is unreachable or refused the connection", not a urllib3 object address.

---

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

39 tests covering the API contract, the scoring rules (including edge cases like an empty fabric and offline devices reporting no telemetry) and the client's failure paths — bad credentials, expired sessions, timeouts and malformed responses.

---

## Layout

```
sdwan_dashboard/
├── app.py               Flask app, routes, session cache, error handling
├── sdwan_client.py      vManage REST client + mock data provider
├── health.py            Scoring rules
├── config.py            Environment configuration
├── templates/           Dashboard page
├── static/
│   ├── css/             Theme
│   ├── js/              Refresh loop and charts
│   └── vendor/          Chart.js (vendored, no CDN)
└── tests/               pytest suite
```
