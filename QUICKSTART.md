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
| **installer (umbrella)** | **`0.2.47`** |
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

> The component versions are pinned in the installer chart's `values.yaml`; you select a version
> set by choosing an **installer chart version**. See **[Changing component versions](#changing-component-versions)**.

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

### kind (local — gets real LoadBalancer IPs via MetalLB)

```bash
kind create cluster --name krateo-installer
# MetalLB so LoadBalancer Services get an external IP from the docker network:
helm repo add metallb https://metallb.github.io/metallb && helm repo update
helm install metallb metallb/metallb -n metallb-system --create-namespace --wait
# Pick a small pool INSIDE the kind docker subnet (adjust the .255.x range to your subnet):
SUBNET=$(docker network inspect kind -f '{{(index .IPAM.Config 0).Subnet}}')   # e.g. 172.19.0.0/16
BASE=$(echo "$SUBNET" | cut -d. -f1-2)
kubectl apply -f - <<EOF
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata: { name: krateo-pool, namespace: metallb-system }
spec: { addresses: ["${BASE}.255.200-${BASE}.255.250"] }
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata: { name: krateo-l2, namespace: metallb-system }
spec: { ipAddressPools: [krateo-pool] }
EOF
```

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

```bash
helm install installer oci://ghcr.io/braghettos/charts/installer --version 0.2.47 \
  -n krateo-system --create-namespace \
  --set exposure.type=LoadBalancer \
  --wait
```

That's it. The install:

1. pre-installs the engine CRDs, installs the engine + operator subcharts (core-provider,
   cert-manager, ClickHouse operator, MongoDB community-operator);
2. registers the `installer` CompositionDefinition; a **post-install hook** waits for
   core-provider to generate the `Installer` CRD, then applies the `Installer` CR;
3. core-provider reconciles that CR and rolls out every component by dependency order.

**Light install (portal + events only — good for constrained kind):**

```bash
helm install installer oci://ghcr.io/braghettos/charts/installer --version 0.2.47 \
  -n krateo-system --create-namespace \
  --set exposure.type=LoadBalancer \
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
# all browser-facing services LoadBalancer + IPs:
kubectl -n krateo-system get svc | grep LoadBalancer
```

`helm install --wait` returns once the bootstrap layer is up; the component layer then rolls out
on core-provider's reconcile loop (a few minutes). All 19 CompositionDefinitions reaching
`READY=True` and a `demo-system` namespace appearing means the platform is up.

## 2. Access the portal

```bash
# Portal external IP (frontend Service):
kubectl -n krateo-system get svc -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.type}{" "}{.status.loadBalancer.ingress[0].ip}{"\n"}{end}' | grep -i 'frontend ' | grep LoadBalancer
# -> open  http://<FRONTEND_IP>:8080

# Admin password (NOTE: rotates on every reconcile by design — read it immediately before login):
kubectl -n krateo-system get secret admin-password -o jsonpath='{.data.password}' | base64 -d; echo
# user: admin
```

If a LoadBalancer IP is unavailable (e.g. GKE IP quota), reach the portal via port-forward:

```bash
FE=$(kubectl -n krateo-system get svc -o name | grep -i '/frontend-' | head -1)
kubectl -n krateo-system port-forward "$FE" 18080:8080   # then http://localhost:18080
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
| `namespaces.krateo` / `.clickhouse` | `krateo-system` | everything runs in one namespace |
| `bootstrap.<engine\|certManager\|clickhouseOperator\|mongodbOperator\|kagent>.enabled` | `true` | install that prerequisite as a subchart; set `false` if already present |

## Changing component versions

The version of each component (portal, frontend, snowplow, …) is **pinned in the installer
chart's `values.yaml`** under `components[].version`; it is intentionally **not** exposed
per-component in the self-applied `Installer` CR (baking versions into the CR would freeze them
and shadow chart upgrades). So you pick a coherent, tested version *set* by choosing an installer
chart version.

**Supported — bump the portal (or any component) version:**

1. edit `components[].version` for `portal` in `chart/values.yaml`,
2. release a new installer chart version (push a semver tag),
3. `helm upgrade installer oci://ghcr.io/braghettos/charts/installer --version <new> -n krateo-system`.

The `installer` CompositionDefinition tracks `.Chart.Version`, so the upgrade makes core-provider
re-pull the new installer chart and the new component versions propagate through the reconcile.

**Escape hatch (discouraged) — override `components` directly:** `components` *is* a valid
top-level spec field (`values.schema.json`), so you can override the whole list via
`-f my-components.yaml` at install time or by editing the live `Installer` CR. Caveats: Helm
**replaces lists wholesale** (you must supply the *entire* `components` array, not just the
portal entry), and an override **freezes versions** — a later installer-chart upgrade won't move
them because the CR override shadows the chart's `values.yaml`. Do **not** instead patch a single
component's `CompositionDefinition` `spec.chart.version` directly: that leaves a stale render and
a controller with the wrong bootstrap RBAC.

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
