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

## Versions

### Controller range

| | |
|---|---|
| Targeted from | 20.3 |
| Endpoint audit reaches | 20.16 |
| Above that | Runs, reported as newer than the audit |

The audit ceiling is 20.16 because that is the highest release Cisco's
catalystwan SDK declares a constraint for. A controller on a later train — 21,
24, 26, anything — is **not** refused. It is labelled *newer than the audit* in
the Compatibility view, and the source table there records what it actually
served. Refusing to run against a release that postdates this table would be a
worse failure than running unverified against it.

### Feature floors

| Feature | Floor | Basis |
|---|---|---|
| Enhanced AAR (IOS XE edges) | 17.9.1 | Cisco release notes; **not** verified here |
| Enhanced AAR (controller train) | 20.9.1 | Cisco release notes; **not** verified here |

None of the four catalogued endpoints carries a `@versions` constraint in the
SDK, so it gave no evidence either way for these floors. Confirm them against
your own release notes before relying on them.

### Deployment scenario

A Manager runs either standalone or clustered, and the two are judged by
different rules. The mode comes from `GET /clusterManagement/tenancy/mode`
(`deploymentmode`, `clusterid`) and per-node service state from
`GET /clusterManagement/vManage/details/{ip}` — both catalogued endpoints, and
both verified against Cisco's `TenancyMode` and `VManageDetails` models.

**Standalone** has no quorum to maintain, so none of the cluster rules apply.
The single point of failure is stated as information, not as a fault: running
one node is a choice, and reporting a correct deployment as degraded would be
crying wolf.

**Cluster** is checked against:

| Requirement | Reported as |
|---|---|
| At least three nodes | Critical — two cannot form a majority |
| An odd node count | Major — an even split leaves neither side with quorum |
| `configuration-db` on exactly three nodes | Critical when fewer, Major when more |
| `application-server` and `messaging-server` on every node | Major |
| Every Manager node reachable | Critical |

When the controller's declared mode and the inventory disagree — a node
removed from the cluster but still listed, for instance — that disagreement is
reported rather than one of them being picked silently.

Controller and Validator redundancy is checked in both scenarios: a single one
of either is a Major finding, since losing it takes that function with it.

### Consistency across the fabric

The feature floors above judge one node at a time. Separately, every node's
release is compared against the rest, because the fabric has to agree with
itself.

Cisco pairs the two trains by minor release — controller 20.12 ships with
IOS XE SD-WAN 17.12 — which is the only way to compare a Manager against an
edge, since 20.x and 17.x cannot be compared as numbers. That pairing comes
from the release numbering convention, not from a compatibility matrix, so it
is reported as guidance rather than as a support verdict.

| Situation | Reported as |
|---|---|
| An edge ahead of the control plane | Critical — controllers are upgraded first |
| Control-plane nodes split across releases | Major — expected mid-upgrade, a problem if settled |
| Nodes of one role disagreeing (a Manager cluster) | Major |
| An edge more than three releases behind | Minor |
| A node reporting no usable release | Minor |

The lowest control-plane release is the ceiling, not the average: one lagging
Controller constrains the whole fabric.

### Trains the floor table does not know

A version table is written once and the software keeps shipping, so an
unrecognised train must not be read as incapable. The rule:

| Train | Verdict | Basis reported |
|---|---|---|
| 17.x, 20.x | Compared to the floor above | `checked` |
| 18.x, 19.x | Never gained the feature | `too_old` |
| Newer than 20.x | Taken as capable | `assumed` |

An edge on 26.4.1 therefore reads as capable, and the AAR view says why: the
feature predates that release, rather than that release having been verified.
Before this rule existed, every device on a train past 20.x was reported as
unable to run enhanced AAR — a false alarm across an entire modern fabric.

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
