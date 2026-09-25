# vManage compatibility

## What was actually verified, and how

Cisco's own Python SDK, [`catalystwan`](https://pypi.org/project/catalystwan/)
(v0.41.6), ships a catalogue of 604 vManage endpoints and Pydantic models for
their payloads. That catalogue was used as the authority here, because it is
Cisco's, it is machine-readable, and it can be re-checked.

**No live vManage of any version was available.** Nothing below was tested
against a running controller. The dashboard therefore reports its own
compatibility at runtime instead of relying on this document — see
**Compatibility** in the navigation, which shows what the controller in front
of you actually served on the last poll.

## Result of the audit

Four of the sixteen data sources match Cisco's catalogue exactly:

| Endpoint | Feeds |
|---|---|
| `GET /device` | Device inventory (the only required source) |
| `GET /client/server` | Controller version, tenancy, capabilities |
| `GET /template/policy/list/appprobe` | Enhanced AAR validation |
| `GET /template/policy/list/sla` | Enhanced AAR validation |

Three exist in the catalogue **only with a `deviceId` parameter**:

| Endpoint | Catalogue form |
|---|---|
| `GET /device/bfd/summary` | `/device/bfd/sessions?deviceId=…` |
| `GET /device/omp/summary` | `/device/omp/summary?deviceId=…` |
| `GET /device/interface` | `/device/interface/synced?deviceId=…` |

This is the audit's most significant finding. vManage's real-time `/device/*`
endpoints are generally **per-device**, queried live through the controller.
The fabric-wide calls this dashboard makes may not be supported in that form,
in which case the correct implementation is either an aggregate under
`/statistics/*` or iteration over the device inventory. That has not been
resolved, because resolving it by guessing a different path would repeat the
mistake.

The remaining nine are absent from the catalogue. That is **not** proof they do
not exist: `catalystwan` implements mostly configuration and template
management, and covers little of the real-time monitoring surface. It does mean
there is no verification for them.

## What the audit corrected

The policy-list models were verified in detail, and the implementation was
wrong in three ways. On a real controller the enhanced AAR panel would have
reported a correctly configured fabric as broken.

**Payload nesting.** A policy list carries its content in `entries`, not as
flat fields:

```json
{ "name": "VOICE-PROBE", "listId": "…", "type": "appProbe",
  "entries": [ { "forwardingClass": "voice",
                 "map": [ {"color": "mpls", "dscp": 46} ] } ] }
```

**The SLA→probe binding is a UUID.** `SLAClassListEntry.app_probe_class` is the
probe list's `listId`, not its name. Resolving it by name — which the first
implementation did — never matches, so every SLA class would have been reported
as a broken binding.

**Thresholds are strings.** `latency`, `loss` and `jitter` come back as `"50"`,
not `50`, and were being compared numerically. DSCP is also configured **per
TLOC colour** under `entries[].map[]`, so one probe class can mark differently
on different transports; a single value is only shown when the colours agree.

Both readers also accept a flattened row, because some builds return summary
views of these lists and being strict would produce the same false alarm from
the other direction.

## Version floors

| Feature | Floor | Basis |
|---|---|---|
| Enhanced AAR (IOS XE edges) | 17.9.1 | Cisco release notes; **not** verified here |
| Enhanced AAR (controller train) | 20.9.1 | Cisco release notes; **not** verified here |

The SDK carries `@versions` constraints on some endpoints (`>=20.4`, `>=20.6`,
`>=20.9`, `>=20.13`, `>=20.16`), but none on the four endpoints this dashboard
uses, so it gave no evidence either way for these floors. They are encoded in
`analysis.ENHANCED_AAR_MIN` and should be confirmed against your own release
notes before being relied on.

## How the dashboard behaves when a source is missing

Only `GET /device` is required. Every other source degrades on its own: the
panel it feeds comes back empty, the reason is recorded, and the Compatibility
view names it. One endpoint a controller does not have cannot blank out the
rest of the dashboard.

This is what makes the unverified endpoints tolerable rather than reckless — a
controller that rejects nine of them still renders the inventory, the health
score and everything else it does serve.

## Re-running the audit

```bash
pip download catalystwan --no-deps -d /tmp/cw
cd /tmp && unzip -q cw/*.whl -d cwx
grep -E '^(GET|POST|PUT|DELETE) ' cwx/catalystwan/ENDPOINTS.md
```

Compare against the paths declared in `sdwan_dashboard/compat.py`, which is the
single place the dashboard states what it depends on.

## What would actually settle this

One hour against a lab vManage, with `SDWAN_MODE=live`, reading the
Compatibility view. It reports exactly which of the sixteen sources that
controller served, which is the fact this document cannot supply.
