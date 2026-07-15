# Krateo installer

One Helm install self-bootstraps the **entire Krateo platform** — the composition engine
(core-provider + chart-inspector), the portal (frontend + snowplow content API + authn),
the observability stack (ClickHouse + MongoDB + OTel + HyperDX), and the agent fleet
(kagent + autopilot + specialist agents) — driven by a single umbrella `Installer` composition.

- **Registry:** `oci://ghcr.io/braghettos/krateo/installer`
- **You run one release** (`installer`, bootstrap mode); the engine creates and manages a second
  release (`krateo`, the composition layer) **for you**. Don't install `krateo` yourself.

## Prerequisites

- `kubectl`, `helm` ≥ 3.16, and (for GKE) `gcloud`.
- **Kubernetes ≥ 1.36** on **GKE Standard** (not Autopilot) — core-provider 2.x relies on the GA
  `MutatingAdmissionPolicy`. A 3× `e2-standard-8` regional cluster comfortably runs the full platform.
- Outbound access to `ghcr.io` from your workstation **and** the cluster nodes (anonymous pull).
- **6 free LoadBalancer IPs** in the region (authn, snowplow, frontend, kagent-ui, hyperdx, sse-proxy),
  and enough **SSD quota** for the nodes + observability PVCs (~300–500 GB; it's a per-region quota).
- **Agents (default-on) use Vertex AI via ADC** — give the node SA `roles/aiplatform.user` and the
  `cloud-platform` OAuth scope. To skip them: `features.coreAgents=false`, `features.specialistAgents=false`.

## Install (the essentials)

Pick a **published** `<VERSION>` (verify with pagination — the tag list truncates without `?n=1000`):

```bash
TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:braghettos/krateo/installer:pull" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['token'])")
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://ghcr.io/v2/braghettos/krateo/installer/tags/list?n=1000" \
  | python3 -c "import json,sys;print(sorted(json.load(sys.stdin)['tags'])[-1])"   # newest published
```

Then one bootstrap install:

```bash
cat > /tmp/krateo-values.yaml <<'EOF'
bootstrap:
  coreProvider:
    enabled: true            # REQUIRED on the bootstrap install (chart default is false)
exposure:
  type: LoadBalancer         # GKE external IPs; the installer wires the browser-facing URLs from them
vertexAI:
  enabled: true
  projectID: <GCP_PROJECT>   # REQUIRED when agents are enabled
  location: global
krateo-core-provider:
  resources:
    limits:
      memory: 4Gi            # the 2Gi chart default can OOMKill the engine mid-CRD-compile on large installs
EOF

helm install installer oci://ghcr.io/braghettos/krateo/installer --version <VERSION> \
  -n krateo-system --create-namespace -f /tmp/krateo-values.yaml --wait --timeout 10m
```

`--wait` returns when the bootstrap layer is up; the full platform (portal + observability + agents)
finishes rolling out on the engine's reconcile loop over the next **~15–25 minutes**. The `krateo`
release appears a few minutes in.

## Verify

Umbrella Ready, all CompositionDefinitions Ready, and the portal reachable:

```bash
kubectl -n krateo-system get installers.composition.krateo.io installer \
  -o custom-columns='READY:.status.conditions[?(@.type=="Ready")].status,SYNCED:.status.conditions[?(@.type=="Synced")].status'
kubectl -n krateo-system get compositiondefinitions
kubectl -n krateo-system get svc frontend-krateo-frontend \
  -o jsonpath='http://{.status.loadBalancer.ingress[0].ip}:8080{"\n"}'   # the portal
```

## Documentation

- **[docs/fresh-install-runbook.md](docs/fresh-install-runbook.md)** — the full from-zero runbook:
  cluster creation, the exact values, the two-release model, the 6-point verification, and the
  common failure modes + recovery (engine OOM, ownership-strip wedge, stuck LoadBalancers, …).
- **[docs/updating-component-versions.md](docs/updating-component-versions.md)** — day-2: how to
  bump a component (or the engine/cdc) and ship it as a new installer release. First-install ≠ how
  you change it.

## How it works (in brief)

`helm install installer … bootstrap.coreProvider.enabled=true` renders the chart in **bootstrap
mode** (RBAC, SAs, the engine + its `core.krateo.io` CRDs as helm-owned subcharts, and a
post-install Job that applies the umbrella `Installer` CR). The core-provider then reconciles that
CR by re-rendering the **same chart in composition mode** under the stable release name `krateo`,
rolling out every component CompositionDefinition in dependency order. The component version set is
**chart content** (`chart/files/component-pins.yaml`), not values — so a given installer chart
version always deploys its tested-together set.
