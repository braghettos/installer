# Krateo PlatformOps Installer — Quickstart

Install the full Krateo PlatformOps platform as a **compose-of-compositions blueprint**:
one `Installer` Composition drives a readiness-gated rollout of every component
(authn, snowplow, frontend, portal, oasgen-provider, observability) and wires their
exposure + the portal config **by reconciliation** — no post-install patching.

- **Umbrella source:** https://github.com/braghettos/installer (published to `oci://ghcr.io/braghettos/charts/installer`)
- **Component forks (org `braghettos`):** `authn-chart`, `snowplow-chart`, `core-provider-chart`,
  `oasgen-provider-chart`, `clickstack-chart`, and `krateo-installer-charts`
  (frontend, frontend-crd, krateo-sse-proxy, krateo-autopilot, otel-collector-{deployment,daemonset}).
- **Charts (OCI):** `oci://ghcr.io/braghettos/charts/*`, `oci://ghcr.io/braghettos/krateo/*`,
  `oci://ghcr.io/braghettos/portal`.

| chart | version |
|---|---|
| installer (umbrella) | `0.2.2` |
| core-provider / -crd | `0.35.3` |
| authn / -crd | `0.22.2` |
| snowplow / -crd | `0.30.249` |
| oasgen-provider / -crd | `0.9.1` |
| frontend / -crd | `1.0.10` |
| portal | `1.2.2` |
| krateo-sse-proxy, krateo-autopilot, otel-collector-{deployment,daemonset} | `0.1.0` |
| clickstack (helm-installed) | `3.0.3` |

## Prerequisites

- `kubectl` and `helm` ≥ 3.16
- A cluster: a `kind` cluster, or a managed GKE cluster (any managed K8s works; GKE shown).
- Outbound access to `ghcr.io`.

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
> resource-constrained kind, deploy with `observability`/`observabilityAgents` set to `false`
> (the portal, login, and events bell still work — the bell's sse-proxy serves Kubernetes
> events directly). Enable them only if the node has the headroom.

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

## 1. Bootstrap prerequisites (both clusters)

The engine (core-provider) can't bootstrap itself, and the observability operators are cluster
infra — install them once up front.

```bash
kubectl create ns krateo-system
kubectl create ns clickhouse-system

# Operators (skip cert-manager/clickhouse/mongodb/kagent if deploying without observability)
helm repo add jetstack https://charts.jetstack.io
helm repo add mongodb https://mongodb.github.io/helm-charts
helm repo update
helm install cert-manager jetstack/cert-manager --version v1.20.2 \
  -n cert-manager --create-namespace --set crds.enabled=true --wait
helm install clickhouse-operator oci://ghcr.io/clickhouse/clickhouse-operator-helm --version 0.0.5 \
  -n clickhouse-operator-system --create-namespace --wait
helm install mongodb-operator mongodb/community-operator --version 0.13.0 \
  -n mongodb --create-namespace --set operator.watchNamespace='*' --wait
helm install kagent-crds oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds --version 0.9.6 \
  -n kagent --create-namespace --wait
helm install kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent --version 0.9.6 -n kagent --wait

# Composition engine
helm install core-provider-crd oci://ghcr.io/braghettos/charts/core-provider-crd --version 0.35.3 -n krateo-system --wait
helm install core-provider     oci://ghcr.io/braghettos/charts/core-provider     --version 0.35.3 -n krateo-system --wait

# Placeholder secret for the autopilot agent (replace with a real Gemini key to use it)
kubectl -n krateo-system create secret generic gemini-api-key --from-literal=apiKey=PLACEHOLDER

# RBAC: the umbrella resolves browser-facing LoadBalancer IPs via `lookup "Service"` at
# reconcile time. The installer cdc ServiceAccount needs read access to Services (it cannot
# self-grant — Kubernetes escalation prevention). SA name follows the umbrella version.
INSTALLER_VER=0.2.2
SA="installers-v$(echo "$INSTALLER_VER" | tr '.' '-')"      # -> installers-v0-2-2
kubectl apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: { name: installer-cdc-service-reader, namespace: krateo-system }
rules: [{ apiGroups: [""], resources: ["services"], verbs: ["get","list","watch"] }]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: { name: installer-cdc-service-reader, namespace: krateo-system }
roleRef: { apiGroup: rbac.authorization.k8s.io, kind: Role, name: installer-cdc-service-reader }
subjects: [{ kind: ServiceAccount, name: ${SA}, namespace: krateo-system }]
EOF
```

## 2. ClickStack + snowplow prerequisites (only when observability is enabled)

ClickStack is helm-installed directly (its operator-CRD values are too complex for
core-provider CRD generation). Snowplow expects an external cache-warmup ConfigMap.

```bash
helm upgrade --install krateo-clickstack oci://ghcr.io/braghettos/charts/clickstack --version 3.0.3 \
  -n clickhouse-system --create-namespace \
  --set namespaces.clickhouse=clickhouse-system --set namespaces.krateo=krateo-system

# Snowplow mounts this; absent -> pod stuck ContainerCreating (FailedMount). Empty is fine.
kubectl -n krateo-system create configmap snowplow-cache-warmup --from-literal=cache-warmup.yaml='{}'
```

## 3. Deploy the installer

```bash
# Register the umbrella CompositionDefinition (generates the Installer CRD)
kubectl apply -f - <<EOF
apiVersion: core.krateo.io/v1alpha1
kind: CompositionDefinition
metadata: { name: installer, namespace: krateo-system }
spec:
  chart: { url: oci://ghcr.io/braghettos/charts/installer, version: "0.2.2" }
EOF
kubectl -n krateo-system wait --for=condition=Ready compositiondefinition/installer --timeout=180s

# Create the Installer Composition. exposure.type=LoadBalancer; flip observability flags off
# for a light (portal+events) install.
kubectl apply -f - <<EOF
apiVersion: composition.krateo.io/v0-2-2
kind: Installer
metadata:
  name: krateo
  namespace: krateo-system
  labels: { app.kubernetes.io/name: installer }   # required: admission policy reads labels
spec:
  installDefinitions: true
  ociRepo: oci://ghcr.io/braghettos/charts
  namespaces: { krateo: krateo-system, clickhouse: clickhouse-system }
  bootstrap:                                       # operators installed in step 1 -> all false
    coreProvider: { enabled: false }
    certManager: { enabled: false }
    clickhouseOperator: { enabled: false }
    mongodbOperator: { enabled: false }
    kagent: { enabled: false }
  exposure: { type: LoadBalancer }                 # NodePort | LoadBalancer | Ingress
  features:
    composableportal: true
    composableportalstarter: true
    composableoperations: true
    oasgenprovider: true
    observability: true                            # set false on constrained kind
    observabilityAgents: true                      # set false on constrained kind
    githubMcp: false
    podRestartAlert: false
EOF
```

The platform now rolls out by reconciliation: CompositionDefinitions register, then each
Composition emits once its dependencies report `Ready=True` and its CRD exists. As the cloud
assigns LoadBalancer IPs, the umbrella's `lookup` fills the frontend `config.json`
(`AUTHN`/`SNOWPLOW`/`EVENTS`/`EVENTS_PUSH` URLs) automatically.

Watch it converge:

```bash
watch kubectl -n krateo-system get compositiondefinition
# all browser-facing services LoadBalancer + IPs:
kubectl -n krateo-system get svc | grep LoadBalancer
```

## 4. Access the portal

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

## Known caveats

- **admin-password rotates** every reconcile (owned by the portal release, by design). Read it
  immediately before logging in.
- **Events bell** consumes events from sse-proxy (`/events`, `/notifications`). The bell icon
  populating is the working state; the bell's *click-through events page* is not seeded by the
  portal-starter (no EventList page widget) and currently errors — tracked separately.
- **snowplow is the BFF for all portal content** (navmenus/routes/pages). If the portal shows
  `404 / widget does not exist` after login, check that snowplow is `Running` and its URL in
  `config.json` resolves.

## Teardown

```bash
kubectl -n krateo-system delete installers.composition.krateo.io krateo --wait
kubectl -n krateo-system delete compositiondefinition installer
# kind:  kind delete cluster --name krateo-installer
# GKE:   gcloud container clusters delete krateo --zone us-central1-a --quiet
```
