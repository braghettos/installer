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
| `clickstack-agent` | `specialistAgents` | `[kagent]` only | **Fuzzy boundary** — gated `specialistAgents`, but only *useful* with the observability data layer; the toggles can't express "works better with" vs "requires". (Resolved differently from the rows above: agents are an intentional decoupled addon that degrades gracefully — §5 — so this is by-design, not a dep to add.) |

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
| `portal` (data + UI plane) | `portal` | authn, snowplow, frontend, portal (+crds) **+ the bell/events pipeline** (§5.2): `krateo-sse-proxy` → `krateo-events` (ClickHouse/Keeper) ← `otel-collector-{deployment,daemonset}`, + `clickhouse-operator`. **"clickhouse" is part of `portal`.** No agents. |
| `oasgen-provider` | `oasgen-provider` | oasgen-provider-crd, oasgen-provider |
| `observability` (product) | `krateo-clickstack`, `hyperdx-provider` | krateo-clickstack (HyperDX + Mongo), mongodb-operator, hyperdx-provider, **+ shared `krateo-events` + clickhouse-operator** (already up if `portal` is on). **No MCP servers** — those are agent tooling. |
| **`agents`** (addon core — decoupled) | kagent + orchestrator + its MCP tool | kagent-crds, kagent, **fetch-mcp-server** (the autopilot's fetch/doc-grounding tool), krateo-autopilot. **No component deps, and the autopilot does NOT hard-dep installer-agent** — deployable on any data plane or alone. This is the **`autopilot`** profile. |
| **`agents-specialist`** (addon) | installer-agent + the 5 platform specialist agents + their MCP tool | **krateo-installer-agent**, authn-agent, snowplow-agent, frontend-agent, clickstack-agent, core-provider-agent, **clickhouse-mcp-server** (the clickstack-agent's telemetry tool); deps `agents` (for kagent + the orchestrator) |
| **`agents-codegen`** (addon) | the 4 codegen agents | krateo-code-analysis-agent, krateo-ansible-to-operator-agent, krateo-tf-provider-to-operator-agent, krateo-tf-to-helm-agent; deps `agents` |
| `githubMcp` | (the GitHub RemoteMCPServer config) | thin; no chart component today |

Notes:
- **Agents are an overlay, not folded into the data capabilities.** Enabling `portal` does
  *not* install the portal agents; enabling `agents` does not install the portal. They are
  orthogonal (this resolves §8 Q2 = *separate / addon*).
- **`krateo-events` (the decomposed ClickHouse data layer, §5.2) is a shared substrate**:
  `portal` pulls it in for the bell, `observability` pulls it in for HyperDX/the tool. Whoever
  is enabled first brings it up; the other attaches. Assumes the ClickStack decomposition
  (§5.2 / RFC 0002); pre-decomposition `krateo-clickstack` is the monolith and `portal` would
  have to pull the whole thing.

### 4.3a Profiles — `open` (platform) and `enterprise` (platform + agents)

The **`open` profile is the platform itself: the data/UI planes, without the agent fleet.**
Agents are a decoupled addon (§5) layered on top — so `open` deliberately leaves them off;
**`enterprise`** = `open` + the **autopilot** (`agents` core) + the specialist/codegen agent fleet.

```yaml
spec:
  profile: open        # = portal + oasgen-provider + observability (NO agents)
  # equivalent explicit form:
  # capabilities: { portal: true, oasgen-provider: true, observability: true }
  # add agents on top:  agents: true, agents-specialist: true, agents-codegen: true
```

| Group | In `open`? | Components |
|---|---|---|
| **portal** | ✅ | authn-crd, snowplow-crd, frontend-crd, authn, snowplow, frontend, portal, krateo-sse-proxy, krateo-events, clickhouse-operator, otel-collector-deployment, otel-collector-daemonset |
| **oasgen-provider** | ✅ | oasgen-provider-crd, oasgen-provider |
| **observability** | ✅ | krateo-clickstack (HyperDX+Mongo), mongodb-operator, hyperdx-provider (+ shared krateo-events). **No MCP servers.** |
| **agents** (core) | ❌ addon | kagent-crds, kagent, fetch-mcp-server, krateo-autopilot |
| **agents-specialist** | ❌ addon | **krateo-installer-agent**, authn/snowplow/frontend/clickstack/core-provider agents, clickhouse-mcp-server |
| **agents-codegen** | ❌ addon | krateo-code-analysis / ansible-to-operator / tf-provider-to-operator / tf-to-helm agents |
| **githubMcp** | ❌ opt-in | GitHub RemoteMCPServer (needs a PAT) |

Profiles are just capability sets:
- **open** = platform, no agents — `{ portal, oasgen-provider, observability }`. The platform
  is **atomic**: there is **no `portal-only`** (nor observability-only / oasgen-only) — the data
  planes come as a unit.
- **enterprise** = open + **autopilot** + the agent fleet — `{ …open, agents, agents-specialist, agents-codegen }` (the `agents` core *is* the autopilot + kagent, then the specialist + codegen tiers on top)
- **autopilot** = the lean bootstrap — **krateo-autopilot + kagent + fetch-mcp** (its
  doc-grounding tool). No data plane, **no installer-agent** (that's `agents-specialist` /
  enterprise); you talk to the autopilot, and provisioning the platform means going
  `enterprise`. *(Formerly "agent-only".)*

**Excluded from `open`:** the **agent fleet** (all three agent tiers — an addon), the **MCP
servers** (`fetch-mcp-server`, `clickhouse-mcp-server` — agent tooling), and **`githubMcp`**
(opt-in, credential-gated). Orthogonal config/mode dimensions are not part of
any profile: LLM backend (`vertexAI` vs `localModel`/Ollama), `exposure.type`, `hitlApproval`,
`bootstrap.*`/`cert-manager`, `registryAuth`, and required secrets.

### 4.4 CR API

New, primary:

```yaml
spec:
  # profile: open        # optional preset — platform only: portal+oasgen-provider+observability, NO agents (§4.3a). enterprise = open + autopilot + agent fleet
  capabilities:
    portal: true             # data + UI plane (incl. clickhouse/events for the bell)
    oasgen-provider: false
    observability: false     # HyperDX/Mongo product over the shared ClickHouse
    agents: true             # addon core: kagent + autopilot + fetch-mcp (the `autopilot` profile)
    agents-specialist: false # the 5 platform specialist agents
    agents-codegen: false    # the 4 codegen agents
    githubMcp: false
```

`spec.features` is **retained as a deprecated compatibility shim**, mapped at render time:

| legacy `features.*` | → capability |
|---|---|
| `composableportal` | `portal` |
| `composableportalstarter` | `portal` (merged) |
| `composableoperations` | *(dropped — engine marker, no-op)* |
| `observabilityAgents` | `agents` (core) **+ `agents-specialist`** (legacy bundled `installer-agent`, now in the specialist tier) |
| `specialistAgents` | `agents-specialist` + `agents-codegen` |
| `observability` | `observability` |
| `oasgenprovider` | `oasgen-provider` |
| `podRestartAlert` | `observability` (the HyperDX/podRestartAlert pipeline) |
| `githubMcp` | `githubMcp` |

If `spec.capabilities` (or `spec.profile`) is set it wins; otherwise the shim derives
capabilities from `spec.features`. Because agents are a **decoupled addon** (§5), legacy
combinations that ran agents without their data plane — `specialistAgents: true` with
`observability: false` — map cleanly to `agents` without backing and behave exactly as
before. So unlike a hard-require model, the addon model **preserves render-parity** for these
combinations; there are no "intentionally broken" legacy combos to exempt (the one genuine
behavior change is the autopilot roster shrinking on lean installs — §5.1).

## 5. Correctness prerequisites

Closure is only correct if the dep graph is **complete**. A migration pre-step must audit
and fix incomplete deps.

**Decision (2026-06-19, supersedes the earlier "hard-require"): agents are a decoupled
addon — they are NOT tied to their component.** This matches the installer as-built: every
agent deps only on `[kagent]`; none deps on the component it speaks for. (The autopilot's
current `krateo-installer-agent` dep is **dropped** — installer-agent is `agents-specialist`,
and the autopilot routes to it when present rather than requiring it.) An agent runs fine with its component absent — it simply has
nothing to manage and says so. So we do **not** add agent→component edges; the agent fleet
is its own overlay capability that can be deployed on top of any data plane, or alone.

(The earlier "hard-require" answer assumed agents were bound to their component; they aren't,
so that direction is dropped.)

**MCP servers are agent tooling, never platform.** `fetch-mcp-server` (the autopilot's fetch
tool) lives in the `agents` core; `clickhouse-mcp-server` (the clickstack-agent's telemetry
tool) lives in `agents-specialist`. So **`open` installs neither agents nor MCP servers** —
both are part of the agents addon (`enterprise`). The clickhouse MCP server reads the ClickHouse
data layer (`krateo-events`) at runtime when present — but in `enterprise` (= `open` + agents)
the `open` platform already provides it, and absent it the tool degrades like the agent it
serves. So the agent+MCP addon is **fully decoupled** — no component-coupled exceptions.

The genuine dep-graph fixes Phase 0 still owns are the **data-plane** edges, not agent edges:
the `frontend → krateo-sse-proxy → krateo-events` bell path (§5.2) and any other real
data/control dependency currently missing.

### 5.1 Orchestrator roster (autopilot) — derived `extraAgents`

The autopilot orchestrates the rest of the fleet but **does not hard-dep its roster** — it
keeps deps `[kagent]` only (consistent with the agents-as-addon model, §5; the
`installer-agent` dep is dropped — it's `agents-specialist`). It routes to whatever sub-agents are present and degrades gracefully when one is
absent (the intent-classification + verify-before-assert prompt already lets it answer "that
agent isn't installed"). So which specialists exist is a property of the **enabled set**, not
of the autopilot's deps — which is exactly why the roster must be *derived*, not hand-listed.

**The roster is itself a third drift source.** Today the autopilot's sub-agent list is a
hand-maintained static array — `componentValues.krateo-autopilot.extraAgents` — that
advertises **all 10 sub-agents regardless of which are installed**. On an `autopilot`-profile
install it still claims it can route to authn/snowplow/clickstack/etc. agents that are not
present: the same `feature:`-style drift, one layer up.

**Decision (2026-06-19): derive `extraAgents` from the enabled closure.** The installer
projects the autopilot roster as exactly the installer/specialist agents present in
`enabled` (§4.2). The routing table then has a single source of truth — the dep graph —
advertises precisely what is installed, and shrinks automatically on lean installs. This
applies the RFC's "single source of truth" principle to orchestration and ties off the
`autopilot-agent-orchestration-boundary` loose end (the static roster was that piece).

This interacts with render-parity (§6.1): on the **enterprise** profile the derived roster equals
today's static list (all 10), so output is unchanged; on the **`autopilot`** profile the
roster correctly shrinks to **`[]`** (no sub-agents present — installer-agent and the
specialists are `agents-specialist`/enterprise) — an intended behavior change (the old all-10
list was the over-advertisement bug), so `autopilot` is parity-exempt for the autopilot's
`extraAgents` value.

### 5.2 ClickStack decomposition (prerequisite — RFC 0002)

A second dep-graph completeness gap, of a different kind: the **portal's events bell**
(`frontend` → browser → `krateo-sse-proxy` → ClickHouse ← `otel-collector-*`) is not in the
graph at all. `frontend` deps `[frontend-crd, authn, snowplow]` — *not* `krateo-sse-proxy` —
even though `sse-proxy` is a browser-facing component (it's in the `exposure` list, port
8080, configKeys `EVENTS_API_BASE_URL` / `EVENTS_PUSH_API_BASE_URL`) that the bell calls. So
a platform install with the events pipeline absent comes up **Ready with a dead bell**
(verified on krateo-bell: portal Ready, `observability: false`, no sse-proxy backend).

The blocker to fixing it cleanly: `krateo-clickstack` is a **monolith** — one chart
(appVersion 3.0.0) bundling **ClickHouse + Keeper + OTel gateway + HyperDX + MongoDB**,
deps on *both* operators. So "the portal needs ClickHouse" today means "the portal needs the
entire observability product."

**Decision (2026-06-19): decompose `krateo-clickstack`** so ClickHouse becomes a shared data
substrate, separate from the HyperDX/Mongo product:

| New component | Contains | Role | deps |
|---|---|---|---|
| `krateo-events` *(new)* | ClickHouse + Keeper + OTel gateway | the events/telemetry **data layer** | `clickhouse-operator` |
| `krateo-clickstack` *(slimmed)* | HyperDX + MongoDB | the observability **product** (dashboards) | `mongodb-operator`, `krateo-events` |

New / corrected **install** edges: `frontend` → `krateo-sse-proxy`; `krateo-sse-proxy` →
`krateo-events`; `otel-collector-*` → `krateo-events`; `krateo-clickstack` (product) →
`krateo-events`. (`clickhouse-mcp-server` *reads* `krateo-events` at runtime but does **not**
hard-dep it — it's decoupled agent tooling that degrades when ClickHouse is absent, §5; so
`agents-specialist` never drags the platform in.) `krateo-events` is pulled in by **either**
`portal` (bell) **or** `observability` (HyperDX/the product) via closure, brought up once and
shared. The portal gets a working bell without HyperDX/Mongo; observability layers
the product on top without re-provisioning ClickHouse.

This is a chart-level refactor in `braghettos/krateo-clickstack-chart` (split + a shared
ClickHouse connection config) plus installer rewiring, and is a **prerequisite to Phase 0**.
It is specified separately in **RFC 0002**; RFC 0001's map (§4.3) reflects the
post-decomposition target.

## 6. Churn safety

The installer reconciles a stateful platform; a gating refactor must not perturb running
components. Required guarantees:

1. **Render parity.** For every legacy `features` combination in use (at minimum `autopilot`,
   `open`, `enterprise`), `helm template` output under the new model must be
   **byte-identical** (modulo the removed `feature:` field, the dead `composableoperations`,
   and the autopilot `extraAgents` roster which is now derived — §5.1, the `autopilot`-profile
   roster shrinks by design) to the current output. A golden-file test enforces this. Because agents
   are a decoupled addon (§5), the legacy agents-without-backing combinations map cleanly and
   are preserved — there are no intentionally-broken combos to exempt (cf. §4.4).
2. **No-op on no change.** Same inputs → same `CompositionDefinition` / `Composition`
   manifests → cdc observes no diff → no restart. Tie into #56.
3. **Deterministic ordering.** Closure output is sorted by the existing
   `tier`/`deps` topological order so component array indices stay stable (surgical
   `spec.components[i].version` patches and JSON-patch live-rolls must keep working).

## 7. Rollout plan

0. **Phase −1 — ClickStack decomposition** (RFC 0002, prerequisite): split `krateo-clickstack`
   into `krateo-events` (ClickHouse data layer) + the slimmed HyperDX/Mongo product, and add
   the `frontend → sse-proxy → krateo-events` edges (§5.2). Chart work in
   `krateo-clickstack-chart` + installer rewiring. Must land before Phase 0 so the dep graph
   the closure walks is complete.
1. **Phase 0 — dep-graph completeness audit** (small, independent): fix `clickstack-agent`
   and any other incomplete deps; ship as a normal installer patch. De-risks everything.
2. **Phase 1 — closure engine, behind the shim:** add `capabilities` + `closure()` helper;
   `feature:` still present but unused; `spec.features` maps through the shim; **project
   `componentValues.krateo-autopilot.extraAgents` from the enabled closure** (§5.1) instead
   of the static list. Gated by the render-parity golden test (`enterprise` unchanged;
   `autopilot`-profile roster shrinks per §5.1). No behavior change on valid profiles.
3. **Phase 2 — drop `feature:`** from `components[]` and delete `composableoperations`.
   Pure cleanup once Phase 1 proves parity.
4. **Phase 3 — surface `capabilities`** in `values.schema.json`, docs (`INSTALL-WORKFLOW.md`,
   `llms.txt`), and the installer-agent/autopilot prompts (they currently patch
   `spec.features.*`; teach them `spec.capabilities.*`/`spec.profile`, keep features as
   fallback). **Also reframe the autopilot prompt's install routing:** the lean `autopilot`
   profile has no installer-agent, so first-time provisioning is *selecting the `open`/
   `enterprise` profile*, not delegating to installer-agent; the installer-agent (present in
   `enterprise`) is for *evolving* an installed platform. On the `autopilot` profile the
   autopilot should say so rather than route an install to an absent agent.

Each phase is independently shippable and reversible.

## 8. Open questions

1. ~~Agent ↔ backing-capability coupling~~ — **RESOLVED (2026-06-19): agents are a decoupled
   addon**, not tied to their component (matches the installer — every agent deps only on
   `kagent`). No agent→component edges; the agent fleet is its own overlay capability (§5).
   *(Supersedes the earlier "hard-require" answer, which assumed a coupling that doesn't exist.)*
1a. ~~Orchestrator roster~~ — **RESOLVED (2026-06-19):** the autopilot's `extraAgents` roster
   is **derived from the enabled closure** rather than hand-listed (§5.1).
2. ~~Fold vs separate / granularity of `specialists`~~ — **RESOLVED (2026-06-19): separate —
   agents are an addon.** Not folded into the data capabilities; deployable on any data plane
   or alone (§4.3). The addon is graduated into core/specialist/codegen tiers (§8 Q5), but
   every tier is orthogonal to the data planes.
3. ~~Profiles on top?~~ — **ADOPTED (2026-06-19):** `spec.profile` with **`open`** = the
   platform (portal + oasgen-provider + observability, **no agents**), **`enterprise`** = open
   + autopilot + the agent fleet, plus the lean **`autopilot`** bootstrap (autopilot + kagent). No `portal-only`
   — the platform is atomic. See §4.3a.
4. ~~`composableoperations`~~ — **RESOLVED (2026-06-19): drop.** It gates no component
   (core-provider is always-on via bootstrap), so it is not a capability. Removed from the
   toggle/capability surface; if an "engine present" signal is ever needed it is exposed as
   read-only **status**, not a settable feature. (The autopilot prompt's "base = composableportal
   + composableoperations + composableportalstarter" line collapses to the `portal` capability.)
5. ~~Agent-addon sub-tiers~~ — **RESOLVED (2026-06-19): graduated.** `agents` (core =
   kagent + autopilot + fetch-mcp = the `autopilot` profile),
   `agents-specialist` (installer-agent + the 5 platform agents + clickhouse-mcp),
   `agents-codegen` (the 4 codegen agents);
   the two specialist tiers dep `agents` core, never a data component (§4.3). `enterprise`
   installs all three tiers; `open` installs none; `autopilot` installs only the core.

## 9. Alternatives considered

- **B — keep toggles + closure/validation pass.** Auto-enable (or reject) deps whose
  feature is off. Removes the bug class with no API break, but preserves two sources of
  truth. Good interim; not the end state.
- **C — profiles only.** Replace the boolean map with one `profile` enum. Simplest UX,
  fewest invalid states, but loses à-la-carte control and still needs a component→profile
  mapping.
- **D — hybrid (capabilities + profiles).** Best UX + correctness — **this is what the RFC
  adopts**: closure over capabilities (§4.2) with `open`/`enterprise`/`autopilot` profiles as
  presets (§4.3a). (RFC 0002 is a different concern — the ClickStack chart decomposition, §5.2.)
