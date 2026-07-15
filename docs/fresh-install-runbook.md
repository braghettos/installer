# Fresh GKE install runbook — zero to working Krateo portal

From-zero runbook: new GKE cluster → fully working Krateo portal. Written for an automated
session or a new operator; every command is copy-pasteable, every `kubectl`/`helm` call passes an
**explicit** `--kubeconfig` and `--context` (`--kube-context` for helm) — never ambient config.

Scope: **first install only.** Day-2 component bumps are a different runbook —
[updating-component-versions.md](./updating-component-versions.md). Concepts/architecture:
[INSTALL-WORKFLOW.md](./INSTALL-WORKFLOW.md), profiles/kind/local: [../QUICKSTART.md](../QUICKSTART.md).

Placeholders used throughout (replace all of them):

| placeholder | meaning | example |
|---|---|---|
| `<GCP_PROJECT>` | GCP project hosting the cluster (and Vertex AI) | `operations-dev-krateo-io` |
| `<REGION>` | GKE region/zone | `europe-west4` |
| `<CLUSTER>` | cluster name | `krateo-prod` |
| `<INSTALLER_VERSION>` | published installer chart version | `0.2.227` |

```bash
# Set once; used by every command below.
export KCFG="$HOME/.kube/krateo/<CLUSTER>.kubeconfig"
export KCTX="gke_<GCP_PROJECT>_<REGION>_<CLUSTER>"
```

---

## 1. Prerequisites

- `gcloud`, `kubectl`, `helm` ≥ 3.16.
- **Kubernetes ≥ 1.36** — core-provider 2.x is de-webhooked and relies on the GA
  `MutatingAdmissionPolicy` (`admissionregistration.k8s.io/v1`). GKE **Standard** (not Autopilot).
- Outbound access to `ghcr.io` from both your workstation and the cluster nodes
  (`oci://ghcr.io/braghettos/krateo`, anonymous pull).
- **External-IP quota**: a full install creates **6 LoadBalancer Services** (authn, snowplow,
  frontend, kagent-ui, hyperdx/clickstack-app, sse-proxy). Ensure ≥ 6 free `IN_USE_ADDRESSES`
  in `<REGION>` or the Services sit `<pending>` forever.
- **Vertex AI** (agents, default-on): node SA needs an IAM role covering `aiplatform.*`
  (`roles/aiplatform.user`) and the `cloud-platform` OAuth scope. No API key, no SA key file
  (ADC via the metadata server). To skip agents entirely: `features.coreAgents=false`,
  `features.specialistAgents=false`.

**Pick `<INSTALLER_VERSION>` and verify it is PUBLISHED** — a git tag on the repo is *not* proof
the OCI chart exists. Query ghcr's tag list, **with pagination**: the default page size truncates
(~100 tags) and silently *omits recent versions* (verified: without `?n=1000` the list stops at
`0.2.156` while `0.2.228` exists):

```bash
TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:braghettos/krateo/installer:pull" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['token'])")
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://ghcr.io/v2/braghettos/krateo/installer/tags/list?n=1000" \
  | python3 -c "import json,sys;t=json.load(sys.stdin)['tags'];print('<INSTALLER_VERSION>' in t)"
# must print: True
```

---

## 2. Cluster creation (brief)

```bash
gcloud container clusters create <CLUSTER> \
  --project <GCP_PROJECT> --region <REGION> \
  --machine-type e2-standard-8 --num-nodes 1 \
  --scopes=cloud-platform

# Write a DEDICATED kubeconfig (never merge into ambient ~/.kube/config):
mkdir -p "$(dirname "$KCFG")"
KUBECONFIG="$KCFG" gcloud container clusters get-credentials <CLUSTER> \
  --project <GCP_PROJECT> --region <REGION>

kubectl --kubeconfig "$KCFG" --context "$KCTX" version | grep Server   # expect >= v1.36
kubectl --kubeconfig "$KCFG" --context "$KCTX" get nodes
```

Sizing: the reference release cluster runs the full platform (portal + observability stack +
agents) on **3 × e2-standard-8** (regional, 1 node per zone). `--num-nodes` is per-zone; adjust
for a zonal cluster. ClickHouse/MongoDB PVCs use the default StorageClass — nothing to pre-create.

---

## 3. Install the `installer` release (bootstrap)

One `helm install`, one release: **`installer`**, in `krateo-system`, with
`bootstrap.coreProvider.enabled=true`. This is the **bootstrap-mode** render: RBAC, SAs, the
engine (core-provider + chart-inspector) and its `core.krateo.io` CRDs as helm-owned subcharts,
plus a post-install Job that applies the umbrella `Installer` CR. Everything else follows
automatically (section 4).

Values file — this is the shape the reference release cluster actually runs
(`helm get values installer`), plus the Vertex project:

```bash
cat > /tmp/krateo-installer-values.yaml <<'EOF'
bootstrap:
  coreProvider:
    enabled: true            # REQUIRED on the bootstrap install; chart default is false
exposure:
  type: LoadBalancer         # GKE: external IPs; the installer wires browser-facing URLs from them
vertexAI:
  enabled: true
  projectID: <GCP_PROJECT>   # REQUIRED when agents are enabled; no default
  location: global
# REQUIRED override: the chart-default 2Gi limit OOMKills the engine mid-CRD-compile
# on clusters with many CompositionDefinitions (see failure mode 6.2).
krateo-core-provider:
  resources:
    limits:
      memory: 4Gi
EOF

helm --kubeconfig "$KCFG" --kube-context "$KCTX" install installer \
  oci://ghcr.io/braghettos/krateo/installer --version <INSTALLER_VERSION> \
  -n krateo-system --create-namespace \
  -f /tmp/krateo-installer-values.yaml \
  --wait --timeout 10m
```

Notes:

- **Component versions are NOT values.** The tested-together set is chart content —
  `chart/files/component-pins.yaml`, loaded via `.Files.Get` — so it always ships with the chart
  version you install. Don't try to pin components from the values file.
- **Schema defaults are what actually deploys.** The values become the `Installer` CR spec, and
  that CR is validated/defaulted by the CRD generated from `chart/values.schema.json` — any key
  you don't set gets the **schema** default at reconcile time (e.g. `exposure.type: NodePort`,
  `features.*: true`). `values.yaml` is documentation of those defaults, not the mechanism.
  Durable config goes in this values file (or later, in the `Installer` CR spec) — never as a
  `kubectl` patch on a component CR, which Pass B reverts on the next reconcile.
- **`--wait` returns when the bootstrap layer is up**, not the platform. The component rollout
  continues on the engine's reconcile loop (~60s per dependency layer). Full platform on GKE:
  expect **~15–25 minutes** total (verified: layer-0 CRD compositions at T+~7m, last
  observability component at T+~20m on the reference cluster).
- Portal component pin must be **≥ 1.3.5** (older ships a broken login-only portal). Current
  installer versions pin far above this (e.g. `portal 1.5.15` in 0.2.227+) — only relevant if you
  deliberately install an old `<INSTALLER_VERSION>`.

Watch it converge:

```bash
watch kubectl --kubeconfig "$KCFG" --context "$KCTX" -n krateo-system get compositiondefinitions
kubectl --kubeconfig "$KCFG" --context "$KCTX" -n krateo-system get svc
```

---

## 4. The `krateo` release (composition layer) — created FOR you

The end state has **two Helm releases of the same chart** in `krateo-system`:

| release | mode | contents | managed by |
|---|---|---|---|
| `installer` | bootstrap (`bootstrap.coreProvider.enabled=true`) | RBAC, SAs, engine CRDs, core-provider, chart-inspector, self-bootstrap hook | **you** (`helm install`/`upgrade`) |
| `krateo` | composition (`enabled=false`) | every component CompositionDefinition + component CRs | **the `installers-vX-controller`** (cdc) |

**Do not `helm install krateo` yourself.** The post-install hook applies the `Installer` CR
(bootstrap flags OFF in its spec, auto-healing any stuck `krateo.io/external-create-pending`);
core-provider reconciles that CR by re-rendering the same chart in composition mode under the
stable release name `krateo`, and rolls components out in dependency order (Pass A definitions →
Pass B gated compositions). The `krateo` release simply *appears* a few minutes after step 3.

**The auto-cascade (day-2, know it now):** after the first install, any `helm upgrade` of the
`installer` release bumps the installer CompositionDefinition version, and the *running*
`installers-vX-controller` helm-upgrades the `krateo` release itself (~30s later). Never race it
with a concurrent manual upgrade of `krateo` — the loser fails with `release: already exists`
(failure mode 6.4). The controller owns `krateo`; you own `installer`.

Its values are machine-generated (the `Installer` CR spec plus cdc-injected `global.composition*`
identity and the expanded `components` pin list) — inspect, never hand-edit:

```bash
helm --kubeconfig "$KCFG" --kube-context "$KCTX" -n krateo-system get values krateo -o yaml | head -30
```

---

## 5. Post-install verification

Run all of these; every check must pass before calling the install done.

**5.1 Umbrella `Installer` CR is Ready and Synced:**

```bash
kubectl --kubeconfig "$KCFG" --context "$KCTX" -n krateo-system \
  get installers.composition.krateo.io installer \
  -o custom-columns='NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status,SYNCED:.status.conditions[?(@.type=="Synced")].status'
# READY=True  SYNCED=True
```

**5.2 All CompositionDefinitions Ready, versions == chart pins:**

```bash
kubectl --kubeconfig "$KCFG" --context "$KCTX" -n krateo-system get compositiondefinitions \
  -o custom-columns='NAME:.metadata.name,VER:.spec.chart.version,READY:.status.conditions[?(@.type=="Ready")].status'
# every row READY=True; each VER must equal the pin in chart/files/component-pins.yaml
# of the installed chart version (full profile: ~27 CDs incl. the umbrella `installer` one).
```

Cross-check the pins of the exact version you installed:

```bash
helm --kubeconfig "$KCFG" --kube-context "$KCTX" pull \
  oci://ghcr.io/braghettos/krateo/installer --version <INSTALLER_VERSION> -d /tmp --untar
grep -E 'name:|version:' /tmp/installer/files/component-pins.yaml
```

**5.3 Helm revisions FLAT over ~2 minutes** (no reconcile churn — a climbing `krateo` revision
means the engine is fighting something):

```bash
helm --kubeconfig "$KCFG" --kube-context "$KCTX" -n krateo-system list -a
sleep 120
helm --kubeconfig "$KCFG" --kube-context "$KCTX" -n krateo-system list -a
# `installer` and `krateo` REVISION unchanged between the two runs
```

**5.4 Zero unowned composition children** (exactly ONE non-Helm-owned object is expected — the
`Installer` CR itself, which the bootstrap Job `kubectl apply`s):

```bash
for r in $(kubectl --kubeconfig "$KCFG" --context "$KCTX" api-resources \
    --api-group=composition.krateo.io -o name); do
  kubectl --kubeconfig "$KCFG" --context "$KCTX" -n krateo-system get "$r" -o json 2>/dev/null \
  | python3 -c "
import json,sys
for i in json.load(sys.stdin).get('items',[]):
    if (i['metadata'].get('labels') or {}).get('app.kubernetes.io/managed-by') != 'Helm':
        print('UNOWNED', i['kind'], i['metadata']['name'])"
done
# expected output, exactly one line:  UNOWNED Installer installer
```

**5.5 Browser-facing config matches the real LoadBalancer endpoints.** With
`exposure.type=LoadBalancer` the installer *computes* `AUTHN_API_BASE_URL` /
`SNOWPLOW_API_BASE_URL` / `EVENTS_*` from the peer Services' LB IPs via a reconcile-time Helm
`lookup` — you never set them by hand, but they **must** match, and on the first reconcile
pass (before IPs are assigned) they can transiently read `http://localhost:...` until the next
resync fills them in:

```bash
kubectl --kubeconfig "$KCFG" --context "$KCTX" -n krateo-system get svc \
  -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.metadata.name}{"\t"}{.status.loadBalancer.ingress[0].ip}{"\n"}{end}'
kubectl --kubeconfig "$KCFG" --context "$KCTX" -n krateo-system \
  get krateofrontends.composition.krateo.io frontend -o jsonpath='{.spec.config}' | python3 -m json.tool
# AUTHN_API_BASE_URL == http://<authn LB IP>:8082, SNOWPLOW_API_BASE_URL == http://<snowplow LB IP>:8081,
# EVENTS_*_API_BASE_URL == http://<sse-proxy LB IP>:8080 — no localhost left.
```

**5.6 Portal reachable + login works.** The admin password **rotates on every reconcile** — read
it immediately before logging in:

```bash
FRONTEND_IP=$(kubectl --kubeconfig "$KCFG" --context "$KCTX" -n krateo-system \
  get svc frontend-krateo-frontend -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
kubectl --kubeconfig "$KCFG" --context "$KCTX" -n krateo-system \
  get secret admin-password -o jsonpath='{.data.password}' | base64 -d; echo
# open http://$FRONTEND_IP:8080  ->  user `admin` + that password; expect the full
# portal app shell (sidebar + dashboard), NOT a bare login-only page.
curl -s -o /dev/null -w '%{http_code}\n' "http://$FRONTEND_IP:8080"   # 200
```

Note the portal is served over **plain HTTP** (not a secure context): that is expected and
supported; only clipboard-copy conveniences degrade.

---

## 6. Common failure modes & recovery

### 6.1 Chart pull fails / "not found" at install time
- **Symptom:** `helm install` fails pulling `oci://ghcr.io/braghettos/krateo/installer:<ver>`.
- **Cause:** the version was never published — a git tag on the source repo does not publish the
  OCI chart; or you checked `tags/list` without pagination and trusted a truncated answer.
- **Fix:** re-run the tag check from section 1 **with `?n=1000`**; pick the newest published `0.2.N`.

### 6.2 core-provider OOMKill — CD bumps freeze mid-migration
- **Symptom chain:** a CompositionDefinition version bump never completes: the generated CRD
  **never gains the new served version**; the composition CR is **deleted but not recreated**
  while the component pods stay up; the CD sits `Ready=False`
  (`crd for 'composition.krateo.io/vX-Y-Z' does not exists yet` /
  `error generating CRD: exit status 1`). `kubectl describe` on the `installer-core-provider`
  pod shows `OOMKilled` — the kill lands mid in-process Go compile of the CRD.
- **Cause:** chart-default 2Gi memory limit is too small once the cluster has many CDs.
- **Fix:** the `krateo-core-provider.resources.limits.memory: 4Gi` override from section 3.
  If you missed it, add it to the values file and
  `helm --kubeconfig "$KCFG" --kube-context "$KCTX" upgrade installer oci://ghcr.io/braghettos/krateo/installer --version <INSTALLER_VERSION> -n krateo-system -f /tmp/krateo-installer-values.yaml --timeout 10m`.
- **NEVER `--reuse-values`** on any upgrade of the `installer` release: it freezes the
  cdc/chart-inspector image tags at the old subchart defaults. Always `-f <the live values>`
  (recover them with `helm --kubeconfig "$KCFG" --kube-context "$KCTX" -n krateo-system get values installer -o yaml`).

### 6.3 Ownership-strip wedge — `invalid ownership metadata`
- **Symptom:** umbrella or a composition goes `Ready=False`/`Synced=False` with
  `... exists and cannot be imported into the current release: invalid ownership metadata; ... must be set to "Helm"`.
- **Cause:** a composition child came up unowned (a cdc full-PUT strips helm labels on CRD-version
  migration); helm's import gate blocks the reconcile before the engine can adopt it.
- **Fix:** stamp helm ownership on **ALL** unowned composition children **in one pass** — helm
  only reports them one at a time (whack-a-mole), so scan the whole group first with the loop from
  check 5.4, then for each hit:

  ```bash
  kubectl --kubeconfig "$KCFG" --context "$KCTX" -n krateo-system \
    label <resource> <name> app.kubernetes.io/managed-by=Helm --overwrite
  kubectl --kubeconfig "$KCFG" --context "$KCTX" -n krateo-system \
    annotate <resource> <name> \
    meta.helm.sh/release-name=krateo \
    meta.helm.sh/release-namespace=krateo-system --overwrite
  ```

  Release-name mapping: `composition.krateo.io/*` children → **`krateo`**; portal widget CRs
  (`widgets.templates.krateo.io/*`, `restactions.templates.krateo.io/*`) → **`portal`**.
  Re-run the 5.4 scan until only `UNOWNED Installer installer` remains.

### 6.4 `release: already exists` on an upgrade
- **Symptom:** a manual helm upgrade of `krateo` fails with `release: already exists`.
- **Cause:** you raced the auto-cascade — the `installers-vX-controller` upgrades `krateo` itself
  ~30s after any `installer` release upgrade.
- **Fix:** don't touch `krateo`. Upgrade only `installer`, wait, then verify per section 5.

### 6.5 Portal is login-only / missing the app shell
- **Symptom:** login works but the portal shows no sidebar/dashboard (a fraction of widgets).
- **Cause:** portal component < **1.3.5** (broken portal chart).
- **Fix:** install a current `<INSTALLER_VERSION>` (pins portal ≥ 1.5.x). If you must run a
  custom pin, keep portal ≥ 1.3.5 — bumps go through the installer version, see
  [updating-component-versions.md](./updating-component-versions.md).

### 6.6 Blank portal / "Failed to fetch" in the browser
- **Symptom:** the portal page loads blank; devtools shows fetches to a dead IP or `localhost`.
- **Cause:** frontend config vs live LB endpoints drift — either the first-reconcile transient
  (IPs not yet assigned when the config was computed) or an LB Service was recreated and got a
  **new ephemeral IP**.
- **Fix:** the reconcile-time lookup re-resolves on the next resync (~60s) — re-run check 5.5;
  hard-refresh the browser. If a stale `localhost` persists, nudge the engine by waiting one full
  resync, then re-check the `KrateoFrontend` CR `spec.config`.

### 6.7 Frontend `ImagePullBackOff` after an image override
- **Symptom:** `frontend-krateo-frontend` pod in `ImagePullBackOff` pulling
  `ghcr.io/krateoplatformops/frontend:<tag>`.
- **Cause:** the frontend deploys via the `KrateoFrontend` CR image; overriding
  `componentValues.frontend.image.tag` **alone** lets the schema default repository
  (`ghcr.io/krateoplatformops/frontend`) reassert — the wrong registry.
- **Fix:** always set **BOTH** fields together:
  `componentValues.frontend.image.repository=ghcr.io/braghettos/krateo-frontend` **and**
  `componentValues.frontend.image.tag=<version>`. (A fresh install needs neither — the pinned
  chart's defaults are correct.)

### 6.8 LoadBalancer Services stuck `<pending>`
- **Symptom:** browser-facing Services never get an `EXTERNAL-IP`.
- **Cause:** exhausted `IN_USE_ADDRESSES` quota in `<REGION>` (6 LBs on a full install).
- **Fix:** free/raise the quota; the Services acquire IPs without further action, and the next
  reconcile wires the config (check 5.5).

### 6.9 `Installer` CR parked on `external-create-pending`
- **Symptom:** right after install, the `Installer` CR never goes `Synced`; it carries a
  `krateo.io/external-create-pending` or `-failed` annotation.
- **Cause:** core-provider lost the create race ("cannot determine creation result") — a hard
  stop that does not self-clear.
- **Fix:** normally none — the `installer-self-bootstrap` post-install Job auto-heals this until
  `Synced`. If the Job has already been reaped, strip the annotations manually:

  ```bash
  kubectl --kubeconfig "$KCFG" --context "$KCTX" -n krateo-system \
    annotate installers.composition.krateo.io installer \
    krateo.io/external-create-pending- krateo.io/external-create-failed-
  ```

---

## 7. Day-2: bumping component versions

First install done ≠ how you change it. Bumps of any component (snowplow, portal, frontend,
agents, …) or of the engine/cdc ship as a **new installer chart version** (pin edit → PR → tag →
`helm upgrade installer` → the auto-cascade updates `krateo`). Follow
[updating-component-versions.md](./updating-component-versions.md) verbatim — including its
gotchas (forward-only pins, published-tag verification, running-cdc image check, and the same
ownership-stamp recovery as 6.3).
