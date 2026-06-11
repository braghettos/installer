# Krateo PlatformOps Installer — Quickstart

Install the full Krateo PlatformOps platform from **one `helm install`**. The umbrella chart
self-bootstraps the composition engine, registers itself as an `Installer` composition, and then
drives a readiness-gated rollout of every component (authn, snowplow, frontend, portal,
oasgen-provider, observability, agents) — wiring exposure + the portal config **by
reconciliation**, with **no prerequisite scripts, no manual RBAC, and no post-install patching**.

> New here? Read **[README.md](./README.md)** for the architecture (the two render modes, the
> self-reconcile loop, the teardown hooks, and a state-machine diagram).

- **Umbrella source:** https://github.com/braghettos/installer (published to `oci://ghcr.io/braghettos/charts/installer`)
- **Charts (OCI):** `oci://ghcr.io/braghettos/charts/*`, `oci://ghcr.io/braghettos/krateo/*`, `oci://ghcr.io/braghettos/portal`.

| chart | version |
|---|---|
| **installer (umbrella)** | **`0.2.53`** |
| core-provider / -crd (bootstrap subchart) | `0.35.4` |
| cert-manager / clickhouse-operator / mongodb community-operator (bootstrap subcharts) | `v1.20.2` / `0.0.5` / `0.13.0` |
| authn / -crd | `0.22.2` |
| snowplow / -crd | `0.30.259` / `0.20.6` |
| frontend / -crd | `1.0.12` / `1.0.25` |
| portal | `1.2.2` |
| oasgen-provider / -crd | `0.9.0` |
| hyperdx-provider | `0.1.1` |
| krateo-clickstack (now a composition, app ClickStack `3.0.0`) | `0.1.2` |
| krateo-sse-proxy / otel-collector-{deployment,daemonset} | `0.1.1` |
| clickhouse-mcp-server | `0.1.7` |
| kagent-crds / kagent (now compositions) | `0.9.9` |
| krateo-autopilot | `0.1.7` |

> Each installer version pins a tested set of component versions (GVRs) and types `componentValues`
> against their schemas. Changing a component version means a **new installer version**, not an
> in-place edit. See **[Changing component versions](#changing-component-versions)**.

## Prerequisites

- `kubectl` and `helm` ≥ 3.16
- A cluster: a `kind` cluster, or a managed GKE cluster (any managed K8s works; GKE shown).
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
helm install installer oci://ghcr.io/braghettos/charts/installer --version 0.2.53 \
  -n krateo-system --create-namespace \
  --set exposure.type=LoadBalancer \
  --wait
```

```bash
# kind / local — NodePort Services pinned to the host-mapped nodePorts (step 0):
helm install installer oci://ghcr.io/braghettos/charts/installer --version 0.2.53 \
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
helm install installer oci://ghcr.io/braghettos/charts/installer --version 0.2.53 \
  -n krateo-system --create-namespace \
  --set exposure.type=NodePort \
  --set componentValues.frontend.service.nodePort=30080 \
  --set componentValues.snowplow.service.nodePort=30081 \
  --set componentValues.authn.service.nodePort=30082 \
  --set features.observability=false \
  --set features.observabilityAgents=false \
  --set bootstrap.clickhouseOperator.enabled=false \
  --set bootstrap.mongodbOperator.enabled=false \
  --set bootstrap.kagent.enabled=false \
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

### Watch it converge

```bash
watch kubectl -n krateo-system get compositiondefinition
# browser-facing Services — type LoadBalancer + external IP on GKE, or NodePort on kind:
kubectl -n krateo-system get svc
```

`helm install --wait` returns once the bootstrap layer is up; the component layer then rolls out
on core-provider's reconcile loop (a few minutes). All 19 CompositionDefinitions reaching
`READY=True` and a `demo-system` namespace appearing means the platform is up.

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
4. `helm upgrade installer oci://ghcr.io/braghettos/charts/installer --version <new> -n krateo-system`.

> **Do not edit `components[].version` in the live `Installer` CR.** It would move the Composition
> to a GVR/schema the installer's `values.schema.json` doesn't type (your `componentValues` would
> be validated against the old schema), and the stored Composition CR would have to convert across
> GVRs. Version changes belong to a new installer version. (Likewise, don't patch a single
> `CompositionDefinition.spec.chart.version` directly — that leaves a stale render and the wrong
> bootstrap RBAC.)

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
helm install installer oci://ghcr.io/braghettos/charts/installer --version 0.2.53 \
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
- **snowplow is the BFF for all portal content** (navmenus/routes/pages). If the portal shows
  `404 / widget does not exist` after login, check that snowplow is `Running` and its URL in
  `config.json` resolves.
