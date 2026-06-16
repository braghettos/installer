# Krateo Installer — Architecture

Deep dive into how the umbrella installs and runs the platform: the layers, the self-bootstrap +
self-reconcile machinery (with the hands-off auto-heal and the cdc resync), the per-component
CompositionDefinition lifecycle, the core-provider `2.0.x` engine internals, the agent runtime, and
the ModelConfig surface. For the install commands see **[QUICKSTART.md](./QUICKSTART.md)**; for the
two render modes + teardown see **[README.md](./README.md)**.

Current pins: installer `0.2.87` · core-provider **`2.0.2`** (de-webhooked — `MutatingAdmissionPolicy`,
requires **k8s ≥ 1.36**; chart `0.36.7`) · autopilot `0.1.12` · kagent app `0.9.7`.

---

## 1. Layers — compose of compositions

One `helm install` is the only privileged action. Everything below the Helm line is driven by the
trusted engine reacting to one declarative CR.

```mermaid
flowchart TB
    subgraph helm["Helm (one install, privileged — bootstrap mode)"]
        cp["core-provider 2.0.2 (de-webhooked)<br/>(+ chart-inspector, cdc image)"]
        ops["bootstrap operators<br/>cert-manager · ClickHouse · MongoDB"]
        sb["self-bootstrap Job<br/>(applies Installer CR + auto-heal)"]
        icd["installer CompositionDefinition"]
    end
    subgraph engine["core-provider — the composition engine (trusted, always on)"]
        cr["Installer CR<br/>(installers.composition.krateo.io)<br/>THE desired-state surface"]
    end
    subgraph comps["component compositions (gated, dependency-ordered)"]
        plat["platform: authn → snowplow → frontend → portal"]
        obs["observability: clickstack/otel/sse · oasgen → hyperdx"]
        agents["agents: kagent-crds → kagent → installer-agent + autopilot (+ specialists)"]
    end
    work["workloads: Deployments, Services, CRDs, kagent Agents, ModelConfigs"]

    helm --> cr
    cr -->|"Pass A + Pass B<br/>(re-render each resync)"| comps
    comps -->|"each Composition = a Helm release<br/>installed in-cluster by its cdc"| work
```

**The seam:** `helm install` renders **bootstrap mode** (`bootstrap.coreProvider.enabled: true`);
core-provider then re-renders the *same chart* in **composition mode** (`false`) on its reconcile
loop. That re-render is the self-reconcile — no `helm upgrade`, no `up.sh`.

---

## 2. Self-bootstrap & self-reconcile — sequence

How one command becomes a running platform, with **no manual `kubectl`** (validated end-to-end).

```mermaid
sequenceDiagram
    autonumber
    actor U as operator
    participant H as Helm
    participant CP as core-provider
    participant J as self-bootstrap Job
    participant IC as Installer CR
    participant CDC as installers-cdc<br/>(spawned per CD)
    participant K as component cdc + Helm

    U->>H: helm install installer … (bootstrap mode)
    H->>CP: install core-provider + chart-inspector + operators
    H->>CP: apply installer CompositionDefinition
    CP-->>CP: generate installers.composition.krateo.io CRD + spawn installers-cdc
    H->>J: post-install hook
    J->>IC: kubectl apply Installer CR (bootstrap OFF, picked values)
    loop auto-heal (≤5m, until Synced)
        J->>IC: strip stale external-create-{pending,failed} annotation
    end
    Note over IC,CDC: crossplane occasionally loses the first-reconcile<br/>create race ("cannot determine creation result") —<br/>a HARD stop that does not self-clear. The Job clears it.
    IC->>CDC: Synced=True → core-provider renders umbrella in COMPOSITION mode
    loop every resync (60s) — advance one dependency level
        CDC->>CP: Pass A — emit component CompositionDefinitions
        CP-->>CP: generate each component CRD + spawn its cdc
        CDC->>CDC: re-discover new CRDs (RESTMapper Reset on miss)
        CDC->>K: Pass B — emit component CR (gated: crdExists AND deps Ready=True)
        K->>K: helm install the component release in-cluster
    end
    K-->>U: all enabled components Ready (kagent operator up, agents Accepted + Running)
```

Two failure modes this design defeats, both **without operator intervention**:

| Symptom | Cause | Fix (where) |
|---|---|---|
| Installer CR stuck `Synced=False`, "cannot determine creation result" | crossplane create-pending race on first reconcile | self-bootstrap Job auto-heal loop (`self-bootstrap.yaml`) |
| Rollout stalls — next dependency level never emitted | cdc's RESTMapper cached, doesn't see freshly-generated CRDs | cdc `1.0.2` `Reset()`s on a cache miss + `COMPOSITION_CONTROLLER_RESYNC_INTERVAL=60s` (core-provider chart) |

---

## 3. Per-component CompositionDefinition lifecycle — FSM

Every component (e.g. `kagent-crds`, `authn`, `krateo-autopilot`) walks this independently. Pass A
and Pass B are gated; the resync loop re-evaluates the gates until the whole DAG is `Ready`.

```mermaid
stateDiagram-v2
    direction TB
    [*] --> Defined: Pass A emits the component CompositionDefinition
    Defined --> CRDGen: core-provider reconciles the CD
    CRDGen --> ControllerUp: CRD generated (composition.krateo.io/v<chartver>/<Kind>) + cdc Deployment spawned

    state "external-create race?" as race
    ControllerUp --> race
    race --> AutoHeal: pending annotation stuck (first-reconcile)
    AutoHeal --> CDReady: annotation cleared → Synced=True
    race --> CDReady: clean create

    CDReady --> Gated: CD Ready=True; Pass B may emit the CR
    Gated --> Waiting: deps NOT all Ready  (or CRD not yet discoverable)
    Waiting --> Gated: next resync re-evaluates (cdc re-discovers CRDs)
    Gated --> CREmitted: crdExists=true AND depsReady=true → Pass B emits the component CR
    CREmitted --> Installing: the component's cdc helm-installs the release
    Installing --> Ready: workload up, status Ready=True
    Ready --> [*]
```

- **Pass A gate:** the feature flag (`features.<flag>`) is enabled.
- **Pass B gates** (`_helpers.tpl`): `inst.crdExists` (the generated CRD is served) **and**
  `inst.depsReady` (every `deps` entry reports `Ready=True`). Both are live `lookup`s — which is why
  advancement depends on the cdc re-discovering CRDs each resync.

---

## 4. Engine internals — core-provider `2.0.x` (de-webhooked) + same-version disambiguation

**De-webhooked (2.0.x).** Unlike the 1.x line (which ran a `/mutate` admission webhook + a
conversion webhook backed by a CSR-issued serving cert), core-provider **2.0.x** uses a
`MutatingAdmissionPolicy` (`admissionregistration.k8s.io/v1`, **GA in Kubernetes 1.36**) and
`NoneConverter` CRDs — no webhook server, no CSR, no cert-manager dependency for the engine itself.
**This is why the installer requires k8s ≥ 1.36.** (It also means GKE Autopilot's Warden, which
blocks the `system:node` CSR the 1.x webhook needed, is no longer a blocker for the engine — though
the full platform still wants Standard for other reasons.)

The agent layer ships **many components at the same chart version `0.1.0`** (kagent-crds, kagent,
installer-agent, and 9 specialists). Each generates a CRD under the **same** apiVersion
`composition.krateo.io/v0-1-0` but a **different Kind** (`KagentCrds`, `Kagent`,
`KrateoInstallerAgent`, …). The old engine (`0.26.1`) couldn't tell them apart and stalled
("too many definitions"); `1.0.x` resolves a CR → its CompositionDefinition by **version *and*
kind** (carried forward into 2.0.x), and names each spawned controller by **resource + version**:

```mermaid
flowchart LR
    subgraph v010["apiVersion composition.krateo.io/v0-1-0 (shared)"]
        a["KagentCrds CR"] --> ca["kagentcrds-v0-1-0-controller"]
        b["Kagent CR"] --> cb["kagents-v0-1-0-controller"]
        c["KrateoInstallerAgent CR"] --> cc["krateoinstalleragents-v0-1-0-controller"]
    end
    ca & cb & cc -->|"match CD by status.apiVersion + kind"| reg["core-provider CD registry"]
```

> Requirement, not optional: the installer **must** run core-provider **`1.0.x` or newer** (it pins
> `2.0.2`). On `0.26.1` any namespace with >1 component at one chart version deadlocks ("too many
> definitions"). cdc controllers are named `{resource}-{apiVersion}-controller` via the spawn-template
> ConfigMap (`assets/cdc/configmap.yaml`).

---

## 5. Agent runtime topology

`observabilityAgents` brings up the minimal layer; `specialistAgents` adds the 9 component experts.
`krateo-autopilot` is the single orchestrator — every other agent is an **A2A sub-agent**, and tools
come from **RemoteMCPServers**. The kagent operator reconciles each `Agent` CR into a Deployment.

```mermaid
flowchart TB
    subgraph operator["kagent operator (from the kagent composition, fullnameOverride=kagent)"]
        ctrl["kagent-controller<br/>(reconciles Agent CRs → Deployments)"]
        tools["kagent-tools<br/>RemoteMCPServer: kagent-tool-server"]
        pg["kagent-postgresql"]
        ui["kagent-ui"]
    end
    subgraph models["ModelConfigs (kagent.dev/v1alpha2)"]
        flash["gemini-flash (GeminiVertexAI)"]
        pro["gemini-pro (GeminiVertexAI)"]
    end
    subgraph agentlayer["agents"]
        ap["krateo-autopilot<br/>ORCHESTRATOR"]
        ia["krateo-installer-agent<br/>(patches the Installer CR)"]
        k8s["k8s-agent"]
        helm["helm-agent"]
        spec["9 specialists<br/>(specialistAgents)"]
    end
    extmcp["RemoteMCPServers<br/>github-mcp-server · clickhouse-mcp-server"]

    ctrl --> ap & ia & k8s & helm & spec
    ap -. A2A .-> ia
    ap -. A2A .-> k8s
    ap -. A2A .-> helm
    ap -. A2A .-> spec
    ap --> tools
    ia --> tools
    spec --> extmcp
    ap --> flash
    ap --> pro
    ia --> models
    spec --> models
```

Notes that bite if wrong:
- **Tool-server name.** Agents reference `RemoteMCPServer/kagent-tool-server` by the kagent
  default-install convention. The kagent composition sets `kagentapp.fullnameOverride=kagent` so the
  server is named `kagent-tool-server` (not `kagent-<release-hash>-tool-server`).
- **extraAgents gating.** The autopilot's A2A list is filtered to the agents whose component feature
  is enabled (`compositions.yaml`); in agent-only mode it references only `krateo-installer-agent`,
  so kagent doesn't fail to compile it ("Agent … not found").
- **k8s-agent prompt.** The autopilot chart ships `prompts/k8s_agent` (added `0.1.11`).
- **kagent-ui exposure.** The UI Service is nested at `kagentapp.ui.service` (not the chart
  top-level `service`), so the umbrella exposes it via the kagent component's `serviceValuesPath:
  kagentapp.ui.service` — when `exposure.type=LoadBalancer`/`NodePort` the exposure layer flips that
  Service while the kagent chart keeps its ClusterIP default. Plain-HTTP is safe on kagent ≥ 0.9.7.
- **Adding an agent / dynamic spawn.** The autopilot only routes to agents in its `type: Agent` tool
  list (installer-built from enabled components). A bare `kubectl apply` of an `Agent` CR spawns a
  running, directly-A2A-reachable agent but does **not** make it orchestrated — see
  **[ADDING-AN-AGENT.md](./kagent/ADDING-AN-AGENT.md)**.

---

## 6. ModelConfig resolution

Each agent's model is configured through `componentValues.<agent>.modelConfig` — independent of the
global `vertexAI` defaults. See *Give an agent a different model* in the QUICKSTART.

**Ownership topology (the seam for any model change):** the **autopilot OWNS** the two shared
ModelConfigs `gemini-flash` + `gemini-pro`; the installer-agent and all 9 specialists reference them
**by name** with `create:false`. So flipping those two flips the whole fleet — which is exactly how
the opt-in **`localModel`** path works (default off; `vertexAI` stays the default):

```mermaid
flowchart LR
    ap["krateo-autopilot<br/>models: flash + pro"] -->|creates| mcf["ModelConfig gemini-flash"]
    ap -->|creates| mcp["ModelConfig gemini-pro"]
    sp["specialist / installer-agent<br/>modelConfig.create=false (default)"] -.->|references by name| mcf
    ded["any agent<br/>modelConfig.create=true"] -->|provisions its own| own["dedicated ModelConfig<br/>(Vertex / Gemini / Anthropic / OpenAI)"]
    lm["localModel.enabled=true<br/>(umbrella opt-in)"] -.->|"autopilot (modelOwner) renders flash+pro as provider:Ollama @ host;<br/>every other agent pointed at refName (gemini-flash)"| mcf
```

> **Local LLM in one flag.** `--set localModel.enabled=true --set localModel.host=<ollama-url>
> --set localModel.model=qwen3.6` makes `compositions.yaml` inject `localModel` into the autopilot
> (the `modelOwner`) so it renders `gemini-flash`/`gemini-pro` as `provider: Ollama`, and point every
> other agent at `refName` (`create:false`) — the entire fleet runs on the local model, no
> per-agent-chart change, `vertexAI` injection suppressed. Use a **tool-calling-capable** model
> (qwen3.6 recommended; gemma ≤3 cannot tool-call). See
> [QUICKSTART — local model](./QUICKSTART.md#run-the-agent-layer-on-a-local-model-ollama).

`modelConfig` fields (strictly typed in `componentValues`; `additionalProperties:false`):

| field | meaning |
|---|---|
| `name` | the ModelConfig the agent's `Agent` CR references |
| `create` | `false` = reference an existing one by `name`; `true` = provision a dedicated one |
| `provider` | **enum** — `Anthropic · OpenAI · AzureOpenAI · Ollama · Gemini · GeminiVertexAI · AnthropicVertexAI · Bedrock · SAPAICore` (the kagent CRD list; a typo is rejected at `helm install`) |
| `model` | model id |
| `vertexAI.{projectID,location}` | used when `provider: GeminiVertexAI` |
| `apiKeySecret` / `apiKeySecretKey` | Secret + key, for key-based providers |

The `provider` enum is enforced both in each agent chart's `values.schema.json` and centrally by
`hack/gen-componentvalues-schema.py` (it injects the enum while regenerating, so the installer
enforces it even for an agent whose OCI chart predates the enum).

---

## 7. "The autopilot installs Krateo" — sequence

The agent-only profile's whole point: a human bootstraps the agent layer once; the **agent** then
provisions the platform by editing one CR (it never needs cluster-admin). See
**[kagent/AGENT-DRIVEN-PROVISIONING.md](./kagent/AGENT-DRIVEN-PROVISIONING.md)**.

```mermaid
sequenceDiagram
    autonumber
    actor U as user
    participant AP as krateo-autopilot
    participant IA as krateo-installer-agent
    participant IC as Installer CR
    participant CP as core-provider

    U->>AP: "install Krateo"
    AP->>IA: A2A delegate (install/evolve the platform)
    Note over IA: holds patch on installers.composition.krateo.io ONLY<br/>(its chart ships the narrow RBAC)
    IA->>IC: kubectl patch spec.features.composableportal=true (etc.)
    IC->>CP: reconcile the changed desired state
    loop self-reconcile (60s)
        CP->>CP: Pass A + Pass B — provision the new components in dependency order
    end
    CP-->>AP: components Ready
    AP-->>U: report status (full result, not "I forwarded it")
```

The apiserver **validates every patch** against the `Installer` CRD (generated from
`values.schema.json`), so the agent literally cannot write invalid platform config — its blast
radius is *valid installer settings only*.

---

## 8. Teardown — three-hook FSM

`helm uninstall` gives no ordering guarantee that a finalizing controller outlives what it
finalizes. The fix uses the Helm rule that **all `pre-delete` hooks complete before any normal
resource is deleted** (controllers still alive), plus a `post-delete` sweep. Full detail in the
README; the order:

```mermaid
stateDiagram-v2
    direction LR
    [*] --> Draining: helm uninstall
    Draining --> Sweeping: footprint drained
    Sweeping --> [*]: bare

    note right of Draining
        controllers ALIVE
        HOOK 1 ordered-teardown (pre-delete, composition release):
          delete component Compositions in REVERSE dep order
        HOOK 2 bootstrap-teardown (pre-delete, bootstrap release):
          delete installer CD; block until whole footprint drains
    end note
    note right of Sweeping
        controllers GONE
        HOOK 3 post-delete-cleanup: remove non-helm-owned leftovers
          (core-provider MutatingWebhookConfiguration, generated *.hyperdx CRDs)
    end note
```
