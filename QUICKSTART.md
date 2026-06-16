# Krateo PlatformOps Installer — Quickstart

Install the full Krateo PlatformOps platform from **one `helm install`**. The umbrella chart
self-bootstraps the composition engine, registers itself as an `Installer` composition, and then
drives a readiness-gated rollout of every component (authn, snowplow, frontend, portal,
oasgen-provider, observability, agents) — wiring exposure + the portal config **by
reconciliation**, with **no prerequisite scripts, no manual RBAC, and no post-install patching**.

> New here? Read **[README.md](./README.md)** for the architecture (the two render modes, the
> self-reconcile loop, the teardown hooks, and a state-machine diagram), and
> **[ARCHITECTURE.md](./ARCHITECTURE.md)** for the deep dive — sequence diagrams, the per-component
> lifecycle FSM, the engine internals, the agent topology, and ModelConfig resolution.

- **Umbrella source:** https://github.com/braghettos/krateo-installer (published to `oci://ghcr.io/braghettos/krateo/installer`)
- **Charts (OCI):** all components publish to the consolidated `oci://ghcr.io/braghettos/krateo/*` registry.

| chart | version |
|---|---|
| **installer (umbrella)** | **`0.2.87`** |
| core-provider / -crd (bootstrap subchart; app core-provider **`2.0.2`**, **de-webhooked** — needs **k8s ≥ 1.36**) | `0.36.7` |
| cert-manager / clickhouse-operator / mongodb community-operator (bootstrap subcharts) | `v1.20.2` / `0.0.5` / `0.13.0` |
| authn / -crd | `0.22.6` / `0.22.4` |
| snowplow / -crd | `1.0.10` / `0.21.1` |
| frontend / -crd | `1.0.16` / `1.0.25` |
| portal | `1.2.3` |
| oasgen-provider / -crd | `0.9.4` / `0.9.5` |
| hyperdx-provider | `0.1.2` |
| krateo-clickstack (composition, app ClickStack `3.0.0`) | `0.1.5` |
| krateo-sse-proxy / otel-collector-deployment / otel-collector-daemonset | `0.1.3` / `0.2.0` / `0.1.1` |
| clickhouse-mcp-server | `0.1.7` |
| kagent-crds / kagent (compositions; app kagent `0.9.7`) | `0.1.0` |
| krateo-autopilot | `0.1.12` |
| krateo-installer-agent | `0.1.0` |
| 9 federated specialists (authn/snowplow/frontend/clickstack/core-provider agents `0.1.1`; code-analysis/ansible/tf-provider/tf-to-helm `0.1.0`) | `0.1.1` / `0.1.0` |

> Each installer version pins a tested set of component versions (GVRs) and types `componentValues`
> against their schemas. Changing a component version means a **new installer version**, not an
> in-place edit. See **[Changing component versions](#changing-component-versions)**.

## Prerequisites

- `kubectl` and `helm` ≥ 3.16
- A cluster running **Kubernetes ≥ 1.36** — core-provider `2.0.x` is de-webhooked and uses a
  `MutatingAdmissionPolicy` (GA in 1.36). A `kind` cluster (≥ node image `v1.36`) or a managed GKE
  cluster (any managed K8s ≥ 1.36 works; GKE shown). For the *full* platform GKE **Standard** is
  recommended over Autopilot (Warden constraints on some platform components).
- Outbound access to `ghcr.io`.

Everything else — the composition engine (core-provider) and the observability operators
(cert-manager, ClickHouse operator, MongoDB community-operator) — is installed **for you** as
Helm subchart dependencies during the one `helm install` (the `bootstrap.*` flags, all on by
default). Disable any operator already present on the cluster with `--set bootstrap.<op>.enabled=false`.

---

## 0. Pick your cluster

### kind (local)

kind has no cloud load balancer, so the browser-facing components are exposed as **NodePort** and
reached directly through kind `extraPortMappings` — no `port-forward`. Create the cluster with the
host ports the portal expects: the frontend's `config.json` defaults point the browser at
`localhost:8081` / `localhost:8082` for snowplow / authn, so map those plus `8080` for the portal:

```bash
cat > kind-krateo.yaml <<'EOF'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 30080   # frontend nodePort -> portal UI
        hostPort: 8080
      - containerPort: 30081   # snowplow nodePort
        hostPort: 8081
      - containerPort: 30082   # authn nodePort
        hostPort: 8082
EOF
kind create cluster --name krateo-installer --config kind-krateo.yaml
```

Then install (step 1) with the kind variant, which pins those three Services to the mapped nodePorts.

> The full observability backends (ClickHouse + HyperDX + MongoDB + kagent) are heavy. On a
> resource-constrained kind, install with `observability`/`observabilityAgents` off (the portal,
> login, and events bell still work — the bell's sse-proxy serves Kubernetes events directly).
> See the light-install command below.

### GKE (managed)

```bash
gcloud container clusters create krateo \
  --zone us-central1-a --machine-type e2-standard-8 --num-nodes 1
gcloud container clusters get-credentials krateo --zone us-central1-a
```

> Shared-project gotcha: LoadBalancer Services need free `IN_USE_ADDRESSES` quota. The portal,
> authn, snowplow, and sse-proxy each take one external IP. If the quota is exhausted, the
> Services stay `<pending>` (the Service is still type LoadBalancer — it just can't get an IP).

---

## 1. Install — one command

Pick `exposure.type` for your cluster: `LoadBalancer` on a cloud cluster (GKE, external IPs), or
`NodePort` on kind / any cluster without a cloud load balancer.

```bash
# GKE / cloud — external LoadBalancer IPs:
helm install installer oci://ghcr.io/braghettos/krateo/installer --version 0.2.87 \
  -n krateo-system --create-namespace \
  --set exposure.type=LoadBalancer \
  --wait
```

```bash
# kind / local — NodePort Services pinned to the host-mapped nodePorts (step 0):
helm install installer oci://ghcr.io/braghettos/krateo/installer --version 0.2.87 \
  -n krateo-system --create-namespace \
  --set exposure.type=NodePort \
  --set componentValues.frontend.service.nodePort=30080 \
  --set componentValues.snowplow.service.nodePort=30081 \
  --set componentValues.authn.service.nodePort=30082 \
  --wait
```

That's it. The install:

1. pre-installs the engine CRDs, installs the engine + operator subcharts (core-provider,
   cert-manager, ClickHouse operator, MongoDB community-operator);
2. registers the `installer` CompositionDefinition; a **post-install hook** waits for
   core-provider to generate the `Installer` CRD, then applies the `Installer` CR;
3. core-provider reconciles that CR and rolls out every component by dependency order.

**Light install (portal + login only — good for constrained kind, NodePort):**

```bash
helm install installer oci://ghcr.io/braghettos/krateo/installer --version 0.2.87 \
  -n krateo-system --create-namespace \
  --set exposure.type=NodePort \
  --set componentValues.frontend.service.nodePort=30080 \
  --set componentValues.snowplow.service.nodePort=30081 \
  --set componentValues.authn.service.nodePort=30082 \
  --set features.observability=false \
  --set features.observabilityAgents=false \
  --set features.specialistAgents=false \
  --set bootstrap.clickhouseOperator.enabled=false \
  --set bootstrap.mongodbOperator.enabled=false \
  --wait
```

**Agents (`observabilityAgents=true`)** need either Vertex AI ADC or a Gemini key:

```bash
# Vertex AI (default): point it at your project, the autopilot uses workload-identity ADC
--set vertexAI.enabled=true --set vertexAI.projectID=<YOUR_GCP_PROJECT> --set vertexAI.location=us-central1
# …or a Gemini API key instead of Vertex:
--set vertexAI.enabled=false
kubectl -n krateo-system create secret generic gemini-api-key --from-literal=apiKey=<KEY>
```

> **GKE Vertex ADC:** the node SA needs an IAM role covering `aiplatform.*` (`roles/aiplatform.user`
> or `roles/editor`) and the `cloud-platform` OAuth scope; the default Compute SA + a cluster created
> with `--scopes=cloud-platform` and `--workload-metadata=GCE_METADATA` works with no key, no SA file.

### Agent-only install — let the autopilot install Krateo

Bring up **only** kagent + the installer-agent + the autopilot, then drive the rest of the
platform *through the agent* by patching the `Installer` CR (see
**[kagent/AGENT-DRIVEN-PROVISIONING.md](./kagent/AGENT-DRIVEN-PROVISIONING.md)**):

```bash
curl -sO https://raw.githubusercontent.com/braghettos/krateo-installer/main/chart/values-agent-only.yaml
helm install installer oci://ghcr.io/braghettos/krateo/installer --version 0.2.87 \
  -n krateo-system --create-namespace -f values-agent-only.yaml \
  --set vertexAI.enabled=true --set vertexAI.projectID=<YOUR_GCP_PROJECT>
```

This renders just the `observabilityAgents` layer (kagent-crds → kagent → krateo-installer-agent
+ krateo-autopilot) — 4 compositions, no platform components, no ClickHouse/MongoDB operators.
Once the autopilot pod is `Running`, ask it to **“install Krateo”**: it routes to
`krateo-installer-agent`, which patches the `Installer` CR (`features.composableportal=true`, …),
and core-provider provisions the full platform in dependency order — hands-off.

> **kagent UI** (`observabilityAgents`) is exposed through the same exposure layer — the kagent
> component carries `serviceValuesPath: kagentapp.ui.service`, so `exposure.type=LoadBalancer`/`NodePort`
> flips the nested UI Service while the kagent chart keeps its ClusterIP default. Plain-HTTP is safe
> on kagent ≥ 0.9.7. **Adding your own autopilot-orchestrated agent:**
> **[kagent/ADDING-AN-AGENT.md](./kagent/ADDING-AN-AGENT.md)**.

### Watch it converge

```bash
watch kubectl -n krateo-system get compositiondefinition
# browser-facing Services — type LoadBalancer + external IP on GKE, or NodePort on kind:
kubectl -n krateo-system get svc
```

`helm install --wait` returns once the bootstrap layer is up; the component layer then rolls out
on core-provider's reconcile loop (~60s per dependency level). When every enabled component's
CompositionDefinition reports `READY=True` (28 for the full profile; 4 for agent-only) and a
`demo-system` namespace appears, the platform is up — with no manual `kubectl` at any point.

## 2. Access the portal

Admin password (NOTE: rotates on every reconcile by design — read it immediately before login):

```bash
kubectl -n krateo-system get secret admin-password -o jsonpath='{.data.password}' | base64 -d; echo
# user: admin
```

**kind / NodePort — open the portal directly (ports mapped at cluster creation):**

Open **http://localhost:8080** and log in as `admin`. The browser reaches snowplow/authn at the
mapped `localhost:8081` / `localhost:8082` that the frontend's `config.json` already points to —
no `port-forward`.

> The events bell's `sse-proxy` has no pinnable nodePort, so on a **full** install the bell is
> reachable only on a LoadBalancer/cloud install — or port-forward it on demand:
> `kubectl -n krateo-system port-forward svc/$(kubectl -n krateo-system get svc -o name | grep -i sse-proxy | head -1 | cut -d/ -f2) 8083:8080`.

**GKE / LoadBalancer — the frontend's external IP:**

```bash
kubectl -n krateo-system get svc -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.type}{" "}{.status.loadBalancer.ingress[0].ip}{"\n"}{end}' | grep -i 'frontend ' | grep LoadBalancer
# -> open  http://<FRONTEND_IP>:8080
```

## 3. Configuration reference

All knobs are Helm values (set with `--set`/`-f`); they become the `Installer` CR spec the
umbrella self-applies. Schema: `chart/values.schema.json`.

| key | default | meaning |
|---|---|---|
| `exposure.type` | `NodePort` | `NodePort` \| `LoadBalancer` \| `Ingress` (browser-facing Services) |
| `exposure.ingress.domain` | `""` | with `type: Ingress`, host base — `authn.<domain>`, `frontend.<domain>`, … |
| `features.composableportal*` / `composableoperations` | `true` | **core** — install regardless (not gated) |
| `features.oasgenprovider` | `true` | oasgen-provider (OpenAPI → CRD generator) |
| `features.observability` | `true` | ClickStack + OTel collectors + sse-proxy |
| `features.observabilityAgents` | `true` | kagent operator + krateo-autopilot + MCP tools |
| `features.podRestartAlert` | `false` | hyperdx-provider pod-restart alert pipeline |
| `features.githubMcp` | `false` | hosted GitHub MCP RemoteMCPServer |
| `vertexAI.enabled` / `projectID` / `location` | `true` / — | autopilot LLM via Vertex AI ADC |
| `secrets.geminiApiKey` | `gemini-api-key` | secret name used when `vertexAI.enabled=false` |
| `localModel.enabled` / `host` / `model` | `false` / — / `qwen3.6` | opt-in: run the whole agent fleet on a local Ollama model (see [Run the agent layer on a local model](#run-the-agent-layer-on-a-local-model-ollama)) |
| `hitlApproval` | `true` | human-in-the-loop approval for the autopilot |
| `componentValues.<name>` | unset | per-component spec overrides, deep-merged (see [Customizing a component's spec](#customizing-a-components-spec)) |
| `registryAuth.*` (`enabled`, `username`, `passwordRef`, `insecureSkipVerifyTLS`) | `false` | private-registry credentials + TLS-skip for in-cluster component-chart pulls (see [Private registries](#private-authenticated-registries)) |
| `namespaces.krateo` / `.clickhouse` | `krateo-system` | everything runs in one namespace |
| `bootstrap.<engine\|certManager\|clickhouseOperator\|mongodbOperator\|kagent>.enabled` | `true` | install that prerequisite as a subchart; set `false` if already present |

## Changing component versions

**The installer version is the unit that manages component versions.** A component's version
determines its Composition's **served apiVersion** (its GVR — e.g. portal `1.2.2` →
`composition.krateo.io/v1-2-2`) and its **schema**. Because the installer's `values.schema.json`
is regenerated per release to **type `componentValues` against those exact schemas**, a component
version change is a **GVR + schema change that ships as a new installer version** — not an in-place
edit of a running one.

To bump a component (e.g. portal):

1. edit `components[].version` for `portal` in `chart/values.yaml`,
2. re-run `python3 hack/gen-componentvalues-schema.py chart` to regenerate the typed schema for the
   new component GVRs,
3. release a new installer chart version (push a semver tag),
4. `helm upgrade installer oci://ghcr.io/braghettos/krateo/installer --version <new> -n krateo-system`.

> **You *can* edit `components[].version` in the live `Installer` CR — it works** (core-provider
> adds the new served CRD version, migrates the stored object via a neutral `vacuum` storage
> version, recreates the Composition cleanly, and rolls the component; verified, no stranding). But
> two downsides make the **new-installer-version path preferred**: (1) `componentValues` for that
> component stays typed against the *installer-pinned* schema, not the version you bumped to — so
> the typing guarantee is misaligned; (2) each distinct version you bump to stays a **served** CRD
> version, so repeated in-place churn accumulates versions (the root of the `invalid group/version`
> corruption seen after many churn cycles). A new installer version regenerates the schema (typing
> stays accurate) and ships one coherent GVR set (no accumulation). Either way, don't patch a
> `CompositionDefinition.spec.chart.version` directly — that leaves a stale render + wrong RBAC.

## Customizing a component's spec

The component Composition CRs (`portals…/portal`, `authn`, `snowplow`, …) are **managed by the
Installer composition** — Pass B re-renders them every reconcile, so a direct
`kubectl edit portals… portal` is **reverted** on the next reconcile. To durably customize a
component's spec, use the **`componentValues`** map on the Installer composition (a sibling of
`components`, also editable in place):

```bash
kubectl edit installers.composition.krateo.io installer -n krateo-system
```
```yaml
spec:
  componentValues:
    snowplow:
      replicaCount: 2
    frontend:
      service:
        annotations:
          external-dns.alpha.kubernetes.io/hostname: portal.example.com
```

Each entry is **deep-merged** into that component's rendered spec. The installer-computed wiring
(`service.type` from `exposure`, the frontend `config` URLs, `vertexAI`, `hitlApproval`) stays
**authoritative** — it wins on any leaf conflict — so you can add `service.annotations` next to the
installer's `service.type`, set `resources`/`replicaCount`, etc., without breaking exposure or the
portal's URL wiring. (Same merge semantics as `--set`: you cannot override the four wired fields,
only extend around them.)

`componentValues` is **strictly typed**: `values.schema.json` embeds each pinned component's own
chart schema under `componentValues.<name>`, so `helm` validates your overrides against the
component's **real** Composition schema and rejects typos and unknown components
(`componentValues.snowplow.replicaCont` → `additional property not allowed`). That typing is
**version-bound** — it's regenerated per installer release by
[`hack/gen-componentvalues-schema.py`](./hack/gen-componentvalues-schema.py), which pulls each
pinned component chart's `values.schema.json` and embeds it. This is why a component's **GVR/schema
change ships as a new installer version** (with a regenerated schema), not as an in-place edit of a
running one — see [Changing component versions](#changing-component-versions).

### Give an agent a different model (ModelConfig)

The agents don't have to share one model. Each agent's model is set through `componentValues`
(install-time `values.yaml`/`--set`, or an in-place edit of the Installer CR — `modelConfig` is
**not** one of the four installer-authoritative fields, so your override flows through cleanly).

By default the **autopilot** provisions two shared `ModelConfig`s — `gemini-flash` and `gemini-pro`
(Vertex-backed) — and every specialist + the installer-agent **reference `gemini-flash`**. The two
agent shapes:

- **`krateo-autopilot`** uses a `models` map (`models.flash`, `models.pro`) — the two ModelConfigs
  it creates and routes between.
- **`krateo-installer-agent`** and every **`krateo-<component>-agent`** use a single `modelConfig`
  block:

  | field | meaning |
  |---|---|
  | `name` | the `ModelConfig` the agent's `Agent` CR references |
  | `create` | `false` = reference an existing ModelConfig by `name`; `true` = provision a dedicated one |
  | `provider` | `GeminiVertexAI` (ADC, no key) · `Gemini` · `Anthropic` · `OpenAI` |
  | `model` | the model id |
  | `vertexAI.{projectID,location}` | used when `provider: GeminiVertexAI` |
  | `apiKeySecret` / `apiKeySecretKey` | Secret holding the key, when the provider needs one |

**1. Point an agent at a different existing ModelConfig** (e.g. give the snowplow agent the larger
`gemini-pro` the autopilot already created):

```yaml
spec:                              # (or top-level for `helm install -f`)
  componentValues:
    krateo-snowplow-agent:
      modelConfig:
        name: gemini-pro           # create stays false — just reference it
```

**2. Give an agent its own dedicated ModelConfig** (e.g. run the code-analysis agent on Anthropic
Claude instead of Vertex):

```bash
kubectl -n krateo-system create secret generic anthropic-api-key --from-literal=apiKey=<KEY>
```
```yaml
  componentValues:
    krateo-code-analysis-agent:
      modelConfig:
        name: claude-sonnet
        create: true               # provision a new ModelConfig for THIS agent
        provider: Anthropic
        model: claude-sonnet-4-5
        apiKeySecret: anthropic-api-key
        apiKeySecretKey: apiKey
```

**3. Change the autopilot's own models** (it has two slots):

```yaml
  componentValues:
    krateo-autopilot:
      models:
        pro:
          model: gemini-2.5-pro
          provider: GeminiVertexAI
```

Apply any of these at install (`-f values.yaml` / `--set componentValues.krateo-snowplow-agent.modelConfig.name=gemini-pro`)
or at runtime via `kubectl edit installers.composition.krateo.io installer` — core-provider
re-renders the agent's Composition and it picks up the new model on the next reconcile. For a
dedicated `GeminiVertexAI` ModelConfig (`create: true`), set `modelConfig.vertexAI.projectID`
explicitly — the global `--set vertexAI.projectID` only flows into the autopilot's shared ModelConfigs.

### Run the agent layer on a local model (Ollama)

Instead of Gemini/Vertex, you can run the **entire** agent fleet — the autopilot, the
installer-agent, and every specialist — on **one local LLM** served by
[Ollama](https://ollama.com), with no cloud model credentials. This is **opt-in**: the
`vertexAI` default is unchanged; you turn it on with `localModel.enabled=true`. It pairs
naturally with the [agent-only install](#agent-only-install--let-the-autopilot-install-krateo)
("the kagent approach") — bring up just the agents, talk to them locally, no cloud at all.

**How it wires up (one knob, whole fleet):** the **autopilot owns** the two shared
`ModelConfig`s (`gemini-flash` / `gemini-pro`) that every other agent references by name
(`create: false`). With `localModel.enabled`, the installer injects `localModel` into the
autopilot so it renders *those two* as `provider: Ollama` pointing at your endpoint, and points
every other agent at `gemini-flash` — so flipping one flag moves the whole fleet to the local
model, **no per-agent configuration**.

**Pick a tool-calling-capable model — this matters.** These agents are *tool-heavy* (the
installer-agent fires `k8s_patch_resource` / `helm_*`; the autopilot routes multi-tool A2A
loops). The model **must** support function/tool calling or the agents will chat but never
*act*:

| model (`--set localModel.model=`) | notes |
|---|---|
| **`qwen3.6`** (default; 35B‑A3B MoE, ~24 GB Q4) | **Recommended.** Most reliable local tool-caller; native tool calling in Ollama, no template hacks. MoE (~3B active) → fast. |
| `qwen3:14b` (~10 GB) / `qwen3.5:9b` (~6 GB) | Lighter fallbacks for smaller GPUs — same Qwen3 tool-calling reliability, less headroom. |
| `gemma4` (26B‑A4B, ~24 GB) | Works, but dense and **fragile**: needs the `--jinja --chat-template-kwargs '{"enable_thinking":false}'` server config or tool calls return empty content. |
| ~~`gemma:2b/7b`, gemma2, gemma3~~ | **Avoid** — gemma ≤ 3 cannot tool-call in Ollama; the agents won't be able to take actions. |

**1. Run Ollama in-cluster and pull the model** (any reachable Ollama works — in-cluster shown):

```bash
kubectl create namespace ollama
kubectl -n ollama create deployment ollama --image=ollama/ollama --port=11434
kubectl -n ollama expose deployment ollama --port=11434
# pull a tool-calling model into the running pod (GPU node strongly recommended):
kubectl -n ollama exec deploy/ollama -- ollama pull qwen3.6
# -> reachable in-cluster at  http://ollama.ollama.svc.cluster.local:11434
```

> A GPU node pool makes the agents usable; on CPU-only the model loads but tool-call latency is
> high. For a quick laptop test you can instead point `host` at a host-run Ollama
> (`http://host.docker.internal:11434` on Docker Desktop / kind).

**2. Install (or upgrade) with the local-model flags** — here on the agent-only profile:

```bash
helm install installer oci://ghcr.io/braghettos/krateo/installer --version <ver> \
  -n krateo-system --create-namespace -f values-agent-only.yaml \
  --set vertexAI.enabled=false \
  --set localModel.enabled=true \
  --set localModel.host=http://ollama.ollama.svc.cluster.local:11434 \
  --set localModel.model=qwen3.6
```

`localModel` knobs (all under the top-level `localModel:`):

| key | default | meaning |
|---|---|---|
| `localModel.enabled` | `false` | opt in to the local Ollama provider (takes precedence over `vertexAI`) |
| `localModel.host` | `""` | Ollama endpoint, e.g. `http://ollama.ollama.svc.cluster.local:11434` |
| `localModel.model` | `qwen3.6` | model id pulled in Ollama (must support tool calling) |
| `localModel.refName` | `gemini-flash` | the autopilot-owned `ModelConfig` the other agents reference |

No Gemini key, no Vertex ADC, no `gemini-api-key` Secret needed. Switch back to cloud any time
by setting `localModel.enabled=false` (and `vertexAI.enabled=true` or providing a Gemini key).

> **Swapping the model later:** `--set localModel.model=<other>` (or edit the live `Installer`
> CR) re-renders the autopilot's ModelConfigs and the fleet picks up the new model on the next
> reconcile. To use `gemma4`, configure your Ollama/llama-server with the jinja chat-template
> kwarg noted above, then `--set localModel.model=gemma4`.

## Private (authenticated) registries

If the component charts live in a **private** OCI registry, core-provider needs credentials to pull
them **in-cluster** (it pulls each component chart, and the self-reconcile pulls this installer
chart). Wire that with `registryAuth` → `CompositionDefinition.spec.chart.credentials`. The
bootstrap `helm install` itself authenticates with your **local** `helm registry login`; one
`ghcr.io` token covers all `braghettos/*` repos.

```bash
# 1. Namespace + a Secret holding the registry token (e.g. a ghcr.io PAT with read:packages):
kubectl create namespace krateo-system
kubectl -n krateo-system create secret generic ghcr-pat --from-literal=token=<TOKEN>

# 2. Local login so the bootstrap install can pull the umbrella + subcharts:
helm registry login ghcr.io -u <USER> -p <TOKEN>

# 3. Install, pointing registryAuth at that Secret (namespace omitted -> defaults to krateo-system):
helm install installer oci://ghcr.io/braghettos/krateo/installer --version 0.2.87 \
  -n krateo-system \
  --set exposure.type=LoadBalancer \
  --set registryAuth.enabled=true \
  --set registryAuth.username=<USER> \
  --set registryAuth.passwordRef.name=ghcr-pat \
  --set registryAuth.passwordRef.key=token \
  --wait
```

Every component CompositionDefinition (and the installer's own) is then emitted with
`spec.chart.credentials` referencing that Secret, so core-provider authenticates each pull. For a
self-signed registry, also pass `--set registryAuth.insecureSkipVerifyTLS=true` (maps to
`spec.chart.insecureSkipVerifyTLS`). A component sourced from a classic (non-OCI) helm repo can set
its `chartRepo` (the helm repo name → `spec.chart.repo`) in the `components` list.

## Teardown

One command. It is ordered and finalizer-safe — three Helm hooks tear the whole composition tree
down in reverse-dependency order while the controllers are still alive, then sweep the runtime
leftovers (see [README — teardown hooks](./README.md#why-the-teardown-is-split-across-three-hooks)).
No manual finalizer-clearing or webhook deletion needed.

```bash
helm uninstall installer -n krateo-system
# kind:  kind delete cluster --name krateo-installer
# GKE:   gcloud container clusters delete krateo --zone us-central1-a --quiet
```

After uninstall the only residue is **inherent Helm behavior** (not Krateo defects): the
`krateo-system` namespace (`--create-namespace` namespaces are never deleted on uninstall),
StatefulSet PVCs (ClickHouse/MongoDB data), and `crds/`-directory CRDs (the engine + operator
CRDs that must pre-install for the bootstrap). Delete those with a one-liner if you want bare:

```bash
kubectl delete ns krateo-system
kubectl get crd | grep -E 'krateo.io|kagent.dev' | awk '{print $1}' | xargs -r kubectl delete crd
```

## Known caveats

- **admin-password rotates** every reconcile (owned by the portal release, by design). Read it
  immediately before logging in.
- **Events bell** consumes events from sse-proxy (`/events`, `/notifications`). The bell icon
  populating is the working state; the bell's *click-through events page* is not seeded by the
  portal-starter (no EventList page widget) and currently errors — tracked separately.
- **snowplow serves all portal content** (the SPA fetches navmenus/routes/pages/widgets from
  `SNOWPLOW_API_BASE_URL` `/call?resource=…`). If the portal shows
  `404 / widget does not exist` after login, check that snowplow is `Running` and its URL in
  `config.json` resolves.
