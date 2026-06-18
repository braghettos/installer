# RFC 0001 — Capability-driven enablement (replace per-component feature toggles)

- **Status:** Draft / Proposed
- **Author:** Diego Braga
- **Date:** 2026-06-19
- **Affects:** `krateo-installer` umbrella chart (`chart/values.yaml`, `chart/templates/{definitions,compositions}.yaml`, `_helpers.tpl`), the `Installer` CR API (`spec.features`)
- **Supersedes:** the `features.*` boolean map + per-component `feature:` string

## 1. Summary

Today a component is provisioned iff its hand-written `feature:` string maps to a `true`
entry in the `Installer` CR's `spec.features` map. Enablement therefore has **two
independent, hand-maintained sources of truth** — the `feature:` tag and the `deps:`
graph — and nothing keeps them consistent. When they disagree, a component is scheduled
whose dependency is absent: an unsatisfiable install that only surfaces at runtime.

This RFC proposes deriving enablement from a **single source of truth — the dependency
graph** — by selecting a small set of user-facing **capabilities**, each rooted at one or
more components, and provisioning the **transitive dependency closure** of the selected
roots. Per-component `feature:` tags are removed. Mis-gating becomes impossible by
construction.

## 2. Motivation

### 2.1 The bug class (observed)

Three real mismatches exist in the current chart, all the same shape — a component's
`feature` gate is independent of, and disagrees with, what its `deps` actually require:

| Component | `feature:` | `deps:` (and their feature) | Defect |
|---|---|---|---|
| `clickhouse-mcp-server` | ~~`specialistAgents`~~ → `observability` (fixed in 0.2.124) | `krateo-clickstack` (`observability`) | Enabling specialist agents without observability scheduled it with no ClickHouse backend. |
| `hyperdx-provider` | `podRestartAlert` | `oasgen-provider` (`oasgenprovider`) | Enabling `podRestartAlert` without `oasgenprovider` leaves its dep unsatisfiable. |
| `clickstack-agent` | `specialistAgents` | `[kagent]` only | Dep graph is **incomplete** — the agent is non-functional without the observability data layer / `clickhouse-mcp-server`, but nothing records that. |

The model even encodes feature→feature requirements **in prose**:
`specialistAgents # ... (need observabilityAgents)`. A comment is not a constraint.

### 2.2 Structural problems

- **Redundant third source of truth.** A toggle says *what the user wants*; `deps` says
  *what that needs*. The per-component `feature:` string is a third thing that restates a
  subset of the dep graph by hand, and drifts from it.
- **Combinatorial validity.** 9 booleans = 512 combinations; most are invalid
  (`specialistAgents` without `observability`, `podRestartAlert` without `oasgenprovider`).
  Invalid combinations are not rejected — they become stuck reconciles.
- **Dead surface.** `composableoperations` gates no component (its own comment: "gates no
  component here"); it is an engine-present marker only.
- **Fuzzy boundaries.** `observability` / `observabilityAgents` / `specialistAgents`
  overlap — `clickhouse-mcp-server` is an agent tool that needs the data layer, so it
  legitimately belongs to two of them.

## 3. Goals / Non-goals

**Goals**
- One source of truth for enablement (the dep graph).
- Make "enabled but dependency disabled" unrepresentable.
- A small, meaningful user-facing surface (capabilities, not 9 overlapping booleans).
- Backward-compatible CR API (`spec.features` keeps working via a shim).
- Provably **no-op on no change** (the installer heavy-reconciles a stateful platform — see #56).

**Non-goals**
- Changing what the components *are* or their chart versions.
- Changing `tier` / dependency-ordering semantics (Pass A definitions, Pass B `depsReady`).
- Re-architecting core-provider / cdc.

## 4. Proposed design

### 4.1 Concepts

- **Component** (internal): an entry in `components[]` — `name`, `kind`, `version`,
  `tier`, `deps`. **The `feature:` field is removed.**
- **Capability** (user-facing): a named deliverable the user selects. Each capability
  declares one or more **root** components. A capability is "the smallest set of things a
  user would turn on or off as a unit."

### 4.2 Enablement = transitive dep-closure of selected roots

```
selected   = { capability c : spec.capabilities[c] == true }
roots      = ⋃ { capabilities[c].roots : c ∈ selected }
enabled    = closure(roots)          # roots ∪ all transitive deps
provision iff component ∈ enabled
```

`closure()` walks `deps` to a fixpoint. Because enablement *is* the closure, a component
can never be enabled while one of its deps is disabled — the entire 2.1 bug class is
eliminated. Roots must be declared explicitly (not inferred by reverse-deps) because leaf
components that nothing depends on — `fetch-mcp-server`, `clickhouse-mcp-server`,
`krateo-sse-proxy`, every agent — would otherwise never be pulled in.

### 4.3 Proposed capability map (derived from the current graph)

| Capability | Roots | Closure (what gets provisioned) |
|---|---|---|
| `portal` | `portal`, + the portal agents `authn-agent`/`snowplow-agent`/`frontend-agent` | portal, frontend, authn, snowplow, + their `-crd`s, + the portal agents (which hard-dep those components) |
| `agents` | `krateo-autopilot`, `fetch-mcp-server` | kagent-crds, kagent, krateo-installer-agent, krateo-autopilot, fetch-mcp-server |
| `codegen` | the 4 backing-less codegen agents + `core-provider-agent` | + kagent (already via closure); no other backing |
| `observability` | `otel-collector-daemonset`, `krateo-sse-proxy`, `clickhouse-mcp-server`, `clickstack-agent` | clickhouse-operator, mongodb-operator, krateo-clickstack, otel-collector-deployment, otel-collector-daemonset, krateo-sse-proxy, clickhouse-mcp-server, clickstack-agent |
| `oasgen` | `oasgen-provider` | oasgen-provider-crd, oasgen-provider |
| `podRestartAlert` | `hyperdx-provider` | hyperdx-provider **+ oasgen-provider (+crd)** ← closure auto-fixes the 2.1 mismatch |
| `githubMcp` | (the GitHub RemoteMCPServer config) | thin; no chart component today |

Note how `podRestartAlert`'s closure pulls in `oasgen-provider` automatically — the latent
mismatch in 2.1 simply cannot occur under this model.

### 4.4 CR API

New, primary:

```yaml
spec:
  capabilities:
    portal: true
    agents: true
    observability: false
    specialists: false
    oasgen: false
    podRestartAlert: false
```

`spec.features` is **retained as a deprecated compatibility shim**, mapped at render time:

| legacy `features.*` | → capability |
|---|---|
| `composableportal` | `portal` |
| `composableportalstarter` | `portal` (merged) |
| `composableoperations` | *(dropped — engine marker, no-op)* |
| `observabilityAgents` | `agents` |
| `specialistAgents` | `codegen` (+ portal agents follow `portal`, `clickstack-agent` follows `observability`) |
| `observability` | `observability` |
| `oasgenprovider` | `oasgen` |
| `podRestartAlert` | `podRestartAlert` |
| `githubMcp` | `githubMcp` |

If `spec.capabilities` is set it wins; otherwise the shim derives capabilities from
`spec.features`. This lets existing CRs and the autopilot/installer-agent keep working
unchanged **for the valid combinations**.

**Intentional non-parity for invalid combos.** Because the portal/observability specialist
agents now hard-require their backing (§5), a legacy CR with `specialistAgents: true` but
`observability: false` / `composableportal: false` — a combination that was *already
broken* (agents with absent deps) — does **not** round-trip to the old render. The new
model instead pulls in the backing the agents require. This is the bug class being fixed,
not a regression; render-parity (§6.1) is therefore asserted only over the **valid**
profiles (agent-only, portal, full-platform), and the migration notes call out that
the now-corrected combos change behavior on next reconcile.

## 5. Correctness prerequisites

Closure is only correct if the dep graph is **complete**. A migration pre-step must audit
and fix incomplete deps.

**Decision (2026-06-19): specialist agents hard-require their backing component.** An agent
that manages a component cannot exist without it — so each agent declares a hard `dep` on
what it manages, and closure pulls that backing stack in. This makes "a specialist agent
running with nothing to manage" unrepresentable (the same principle as the rest of the
RFC), at the cost that selecting an agent transitively provisions its backing capability.

Today every agent deps only on `[kagent]`; Phase 0 adds the backing deps:

| Agent | Add dep → | Backing pulled in by closure |
|---|---|---|
| `authn-agent` | `authn` | portal-auth stack |
| `snowplow-agent` | `snowplow` | snowplow stack |
| `frontend-agent` | `frontend` | frontend (+ authn, snowplow) |
| `clickstack-agent` | `clickhouse-mcp-server` | the **entire `observability`** closure |
| `core-provider-agent` | *(none)* | core-provider is always-on via bootstrap — no gate |
| `krateo-code-analysis-agent` | *(none)* | codegen: no backing component (reads GitHub) |
| `krateo-ansible-to-operator-agent` | *(none)* | codegen |
| `krateo-tf-provider-to-operator-agent` | *(none)* | codegen |
| `krateo-tf-to-helm-agent` | *(none)* | codegen |

**Consequence to weigh (feeds §8 Q2):** under hard-require, enabling the `specialists`
capability drags in `portal` **and** `observability` via `clickstack-agent`/the portal
agents. That is logically correct but coarse. It strengthens the case for either (a) moving
`clickstack-agent` into the `observability` capability (it requires the full obs stack
anyway), and binding each portal agent to `portal`, rather than (b) a monolithic
`specialists` capability that silently implies the whole platform. The codegen agents,
having no backing, remain freely selectable and are the natural members of a standalone
`codegen`/`specialists` capability.

## 6. Churn safety

The installer reconciles a stateful platform; a gating refactor must not perturb running
components. Required guarantees:

1. **Render parity (valid profiles only).** For every **valid** legacy `features`
   combination in use (at minimum agent-only, portal, full-platform), `helm template`
   output under the new model must be **byte-identical** (modulo the removed `feature:`
   field and dead `composableoperations`) to the current output. A golden-file test
   enforces this. Previously-**invalid** combinations (e.g. `specialistAgents` without the
   backing capability) are intentionally *not* preserved — see §4.4; the closure corrects
   them by pulling in the required backing.
2. **No-op on no change.** Same inputs → same `CompositionDefinition` / `Composition`
   manifests → cdc observes no diff → no restart. Tie into #56.
3. **Deterministic ordering.** Closure output is sorted by the existing
   `tier`/`deps` topological order so component array indices stay stable (surgical
   `spec.components[i].version` patches and JSON-patch live-rolls must keep working).

## 7. Rollout plan

1. **Phase 0 — dep-graph completeness audit** (small, independent): fix `clickstack-agent`
   and any other incomplete deps; ship as a normal installer patch. De-risks everything.
2. **Phase 1 — closure engine, behind the shim:** add `capabilities` + `closure()` helper;
   `feature:` still present but unused; `spec.features` maps through the shim. Gated by the
   render-parity golden test. No behavior change.
3. **Phase 2 — drop `feature:`** from `components[]` and delete `composableoperations`.
   Pure cleanup once Phase 1 proves parity.
4. **Phase 3 — surface `capabilities`** in `values.schema.json`, docs (`INSTALL-WORKFLOW.md`,
   `llms.txt`), and the installer-agent/autopilot prompts (they currently patch
   `spec.features.*`; teach them `spec.capabilities.*`, keep features as fallback).

Each phase is independently shippable and reversible.

## 8. Open questions

1. ~~Agent ↔ backing-capability coupling~~ — **RESOLVED (2026-06-19): hard require.** Each
   specialist agent declares a hard `dep` on its backing component; closure pulls the
   backing stack in (§5).
2. **Granularity of `specialists`** (now sharper given Q1=hard-require): a monolithic
   `specialists` capability would, via the agents' hard deps, silently pull in `portal` +
   `observability`. Recommend instead binding each agent to the capability it backs —
   `clickstack-agent` → `observability`; portal agents (`authn`/`snowplow`/`frontend`) →
   `portal`; `core-provider-agent` → always-on; and a standalone `codegen` capability for
   the four backing-less codegen agents. Confirm this split.
3. **Profiles on top?** A future `spec.profile: agent-only | portal | full` could expand
   to a capability set (RFC 0002). Out of scope here.
4. **`composableoperations`**: confirmed safe to drop, or keep as a no-op marker some
   external consumer reads?

## 9. Alternatives considered

- **B — keep toggles + closure/validation pass.** Auto-enable (or reject) deps whose
  feature is off. Removes the bug class with no API break, but preserves two sources of
  truth. Good interim; not the end state.
- **C — profiles only.** Replace the boolean map with one `profile` enum. Simplest UX,
  fewest invalid states, but loses à-la-carte control and still needs a component→profile
  mapping.
- **D — hybrid (capabilities + profiles).** Best UX + correctness, most work. Natural
  follow-on (RFC 0002) once A lands.
