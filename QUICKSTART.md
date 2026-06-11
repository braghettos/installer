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
| **installer (umbrella)** | **`0.2.50`** |
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

> Each installer version ships a pinned, tested version set; after install the full list lives in
> the `Installer` composition spec and a component version can be changed in place. See
> **[Changing component versions](#changing-component-versions)**.

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

```bash
kind create cluster --name krateo-installer
```

kind has no cloud load balancer, so browser-facing components use **NodePort** Services
(`exposure.type=NodePort`, the chart default) and you reach the portal via `kubectl port-forward`.
Install in step 1 with `--set exposure.type=NodePort` (see the kind variant there), then jump to
[Access the portal](#2-access-the-portal).

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
helm install installer oci://ghcr.io/braghettos/charts/installer --version 0.2.50 \
  -n krateo-system --create-namespace \
  --set exposure.type=LoadBalancer \
  --wait
```

```bash
# kind / local — NodePort Services (reach the portal via port-forward, step 2):
helm install installer oci://ghcr.io/braghettos/charts/installer --version 0.2.50 \
  -n krateo-system --create-namespace \
  --set exposure.type=NodePort \
  --wait
```

That's it. The install:

1. pre-installs the engine CRDs, installs the engine + operator subcharts (core-provider,
   cert-manager, ClickHouse operator, MongoDB community-operator);
2. registers the `installer` CompositionDefinition; a **post-install hook** waits for
   core-provider to generate the `Installer` CRD, then applies the `Installer` CR;
3. core-provider reconciles that CR and rolls out every component by dependency order.

**Light install (portal + events only — good for constrained kind, NodePort):**

```bash
helm install installer oci://ghcr.io/braghettos/charts/installer --version 0.2.50 \
  -n krateo-system --create-namespace \
  --set exposure.type=NodePort \
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

**kind / NodePort — reach the portal via port-forward:**

```bash
FE=$(kubectl -n krateo-system get svc -o name | grep -i '/frontend-' | head -1)
kubectl -n krateo-system port-forward "$FE" 18080:8080   # then open http://localhost:18080
```

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
| `namespaces.krateo` / `.clickhouse` | `krateo-system` | everything runs in one namespace |
| `bootstrap.<engine\|certManager\|clickhouseOperator\|mongodbOperator\|kagent>.enabled` | `true` | install that prerequisite as a subchart; set `false` if already present |

## Changing component versions

The live `Installer` composition **ships with the full `components` list in its spec** (each
component's pinned `version`, `repo`, and `deps`). It is the **source of truth** for the component
set and versions, so you change a version by editing **one entry in place**:

```bash
kubectl edit installers.composition.krateo.io installer -n krateo-system
# find the portal entry under spec.components and bump its version:
#   - name: portal
#     version: "1.2.3"     # was 1.2.2
```

On save, core-provider re-renders: Pass A updates the `portal` CompositionDefinition's
`chart.version`, regenerates the CRD if needed, and the portal cdc rolls the new chart. No other
component is touched.

> **Why edit the whole-list-in-the-CR and not a one-line override?** Helm **replaces list
> overrides wholesale** (it deep-merges maps but never merges arrays element-by-element), so a
> partial `components:` would prune every component you didn't include. Shipping the complete list
> in the CR is what makes a single in-place edit safe.

**Caveat — the CR shadows the chart.** Because the component list lives in the CR, a
`helm upgrade installer --version <new>` **does not** auto-change component versions or add/remove
components (the baked list shadows the new chart's `values.yaml`). To adopt a newer chart's
component set, re-apply the self-bootstrap CR from that chart version (or reinstall). And do
**not** patch a single component's `CompositionDefinition` `spec.chart.version` directly — that
leaves a stale render and a controller with the wrong bootstrap RBAC; edit the `Installer`
composition instead.

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
