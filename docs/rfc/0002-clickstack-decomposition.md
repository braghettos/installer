# RFC 0002 — Decompose ClickStack (events data layer vs observability product)

- **Status:** Draft / Proposed
- **Author:** Diego Braga
- **Date:** 2026-06-19
- **Affects:** `braghettos/krateo-clickstack-chart` (chart split), `krateo-installer` (`components[]`
  rewiring), the `frontend → sse-proxy` wiring
- **Prerequisite for:** RFC 0001 Phase 0 (the capability dep-closure needs a complete,
  correctly-shaped graph)
- **Related:** RFC 0001 §4.3/§5.2; teardown-completeness #93 (ClickStack PVC retention)

## 1. Summary

`krateo-clickstack` is a **monolith**: one chart (appVersion 3.0.0) bundling **ClickHouse +
Keeper + OTel gateway + HyperDX + MongoDB**. The portal's events **bell** needs ClickHouse
(via `krateo-sse-proxy`), but cannot get it without also pulling HyperDX + Mongo. And the
graph doesn't even record the coupling — `frontend` does not dep `krateo-sse-proxy`, so a
portal-only install comes up **Ready with a dead bell**.

This RFC splits the monolith into:

- **`krateo-events`** *(new)* — the **events data layer**: ClickHouse + Keeper + OTel gateway,
  plus the shared ClickHouse **connection contract** (Secret + ConfigMap) consumers read.
- **`krateo-clickstack`** *(slimmed)* — the **observability product**: HyperDX + MongoDB,
  reading the shared ClickHouse.

and adds the missing `frontend → krateo-sse-proxy → krateo-events` edges. `krateo-events`
becomes a shared substrate that either `portal` (bell) or `observability` (HyperDX/agent)
pulls in via closure — brought up once, attached by both.

## 2. Motivation

(Grounded in RFC 0001 §5.2.)

- **Dead bell on portal-only.** Verified on krateo-bell: `portal` Ready, `observability: false`,
  no `sse-proxy` → the events bell has no backend. `frontend` deps `[frontend-crd, authn,
  snowplow]`; `sse-proxy` is browser-facing (in `exposure`, port 8080, configKeys
  `EVENTS_API_BASE_URL` / `EVENTS_PUSH_API_BASE_URL`) but nothing records `frontend → sse-proxy`.
- **Welded layers.** ClickHouse (needed by the bell) is inseparable from HyperDX/Mongo
  (the product) because they ship in one chart. So "give the portal a bell" today means
  "install the entire observability product."
- **No clean lean portal.** You cannot have portal + working bell without HyperDX/Mongo.

## 3. Goals / Non-goals

**Goals**
- ClickHouse becomes an independently-installable shared data layer.
- The portal bell works without HyperDX/Mongo.
- The dep graph records the real `frontend → sse-proxy → krateo-events` path.
- **No ClickHouse data loss** across the split (PVCs preserved).

**Non-goals**
- Changing ClickHouse/HyperDX/Mongo versions or their runtime behavior.
- The capability/enablement model (that's RFC 0001).

## 4. Design

### 4.1 Chart split (in `krateo-clickstack-chart`)

| Chart | Contains | Emits | deps |
|---|---|---|---|
| **`krateo-events`** *(new)* | ClickHouse cluster + Keeper + OTel gateway | the ClickHouse **connection contract**: `otel-clickhouse-credentials` Secret + ClickHouse http-handlers ConfigMap (today emitted by the monolith) | `clickhouse-operator` |
| **`krateo-clickstack`** *(slimmed)* | HyperDX + MongoDB | (product UI) | `mongodb-operator`, `krateo-events` (reads the contract) |

The **connection contract** is the seam: `krateo-events` owns and publishes the ClickHouse
endpoint + credentials; every consumer (HyperDX, `krateo-sse-proxy`, the otel-collectors,
`clickhouse-mcp-server`) reads them by name instead of assuming co-location. This is what lets
the layers live in separate charts/compositions.

### 4.2 Corrected / new edges (installer `components[]`)

```
frontend            → krateo-sse-proxy            (NEW — the bell path)
krateo-sse-proxy    → krateo-events               (was: krateo-clickstack)
otel-collector-*    → krateo-events               (was: krateo-clickstack)
clickhouse-mcp-server → krateo-events             (was: krateo-clickstack)
krateo-clickstack   → krateo-events, mongodb-operator   (product reads the shared CH)
krateo-events       → clickhouse-operator
```

Resulting closures (RFC 0001 §4.3): `portal` pulls `… → sse-proxy → krateo-events →
clickhouse-operator` (+ collectors); `observability` pulls `krateo-clickstack (HyperDX/Mongo)
→ krateo-events`. `krateo-events` is shared — first enabler brings it up.

### 4.3 Frontend wiring

The bell coupling is partly **config**, not just ordering: the frontend must know the
sse-proxy URL. The installer already resolves browser-facing Service endpoints (the
`exposure` block); `frontend` gains its events endpoint wired to the `sse-proxy` Service, and
the `frontend → sse-proxy` dep guarantees sse-proxy exists first.

## 5. Migration & churn safety (the hard part)

`krateo-events` carries the **ClickHouse StatefulSet + Keeper** — stateful, with PVCs. Moving
the StatefulSet from the `krateo-clickstack` chart to the `krateo-events` chart must **not**
recreate or orphan those PVCs (that would drop telemetry data and, with ZK-auth Keeper,
risk a wedged cluster — cf. installer-stateful-churn-safety).

Requirements:
1. **PVC continuity.** The ClickHouse/Keeper StatefulSet keeps the **same name, namespace,
   labels, and `volumeClaimTemplates`** in `krateo-events` as it had in the monolith, so the
   existing PVCs rebind. Verify with a dry-run + PVC-identity diff before any live roll.
2. **Helm ownership handoff.** The moved resources change Helm release ownership
   (`krateo-clickstack` → `krateo-events`). Plan adopt/annotate (or a documented
   delete-release-keep-resources step) so reconcile doesn't fight ownership — the same
   ownership-mismatch failure mode as the portal `demo-system` orphan (#98).
3. **No-op on no change** once migrated (RFC 0001 §6).
4. Land alongside **#93** (ClickHouse PVC retention policy) so teardown stays clean afterward.

On a **fresh** install none of this applies — it's only the in-place migration of existing
clusters (krateo-bell) that needs the careful handoff.

## 6. Versioning & publishing

- `krateo-events` — new chart, starts `0.1.0`, published from `krateo-clickstack-chart` CI to
  `oci://ghcr.io/braghettos/krateo/krateo-events` (canonical release-oci, like its siblings).
- `krateo-clickstack` — **major bump** (removing ClickHouse/Keeper is a breaking chart change);
  appVersion stays the ClickStack product version.
- Installer re-pins: replace the single `krateo-clickstack` component with `krateo-events` +
  the slimmed `krateo-clickstack`, rewire deps (§4.2), ship an installer release.

## 7. Rollout

1. **Build `krateo-events`** chart (ClickHouse + Keeper + OTel gateway + connection contract);
   publish `0.1.0`. No installer change yet.
2. **Slim `krateo-clickstack`** to HyperDX + Mongo, consuming the contract; major bump.
3. **Rewire installer** `components[]` (§4.2) + frontend events wiring; render-parity test
   (full-platform output equivalent modulo the split); ship installer release.
4. **Migrate krateo-bell** with the PVC-continuity + ownership-handoff plan (§5), observe-only,
   no forced clearing.

Each step is independently shippable; steps 1–2 are pure chart work with no live impact.

## 8. Open questions

1. **OTel gateway placement** — with `krateo-events` (data layer) as drawn, or its own thin
   component? (It's the write path into ClickHouse, so it sits naturally with the data layer.)
2. **Connection contract shape** — reuse the existing `otel-clickhouse-credentials` Secret +
   http-handlers ConfigMap names verbatim (minimizes consumer changes), or introduce a single
   typed `ClickHouseConnection` ConfigMap?
3. **`krateo-events` naming** — `krateo-events` vs `krateo-clickhouse` vs `clickstack-data`.
4. **Migration mechanism** — Helm resource adoption vs a one-time documented
   delete-release-keep-resources, given core-provider drives the release (#98-style ownership).
