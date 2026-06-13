# Agent-driven provisioning — driving the platform from the Installer CR

A pattern (and procedure) for letting a kagent agent — the `krateo-installer-agent` (federated
from [`kagent/chart`](./chart)) — **provision and evolve the Krateo platform by editing the
`Installer` custom resource**, instead of running privileged installs itself. The
`krateo-autopilot` orchestrator routes "install Krateo" requests to it.

The `Installer` CR (`installers.composition.krateo.io`) is the platform's single **declarative
desired-state surface**. core-provider self-reconciles it: change `spec.features`, `components`,
or `componentValues` and the engine re-renders (Pass A → CompositionDefinitions; Pass B →
Compositions) and provisions the difference, in dependency order. An agent that can `patch` that
one resource can therefore drive the whole platform — while needing none of the cluster-wide
privileges an installer normally requires.

## Why this, and not "the agent installs everything"

Asking an agent to run the bootstrap `helm install` itself means its ServiceAccount must create
CRDs, ClusterRoles, webhooks, namespaces, and install core-provider — effectively **cluster-admin**.
That inverts the trust model and, in practice, fails on `forbidden` because the kagent tool-server
SA isn't granted that.

Driving the platform through the `Installer` CR splits privilege correctly:

| Step | Who | Privilege |
|---|---|---|
| Bootstrap: engine + the agent | a human / pipeline, once | privileged |
| Provision / evolve the platform | **the agent**, by editing one CR | **`patch` on `installers.composition.krateo.io`** |
| The actual heavy lifting | **core-provider** (already running, trusted) | privileged — but it's the engine |

The agent declares intent; the trusted engine executes it. The apiserver's strict schema
validation (below) keeps that intent valid. Every change is an ordinary, audited `kubectl patch`.

## Procedure

### 1. Bootstrap the engine + the agent (privileged, once)

A human or pipeline runs the one privileged `helm install` with the **agent-only profile**, which
brings up only the composition engine + kagent + the `krateo-installer-agent` + the
`krateo-autopilot` — no platform components yet:

```bash
curl -sO https://raw.githubusercontent.com/braghettos/krateo-installer/main/chart/values-agent-only.yaml
helm install installer oci://ghcr.io/braghettos/krateo/installer --version 0.2.60 \
  -n krateo-system --create-namespace -f values-agent-only.yaml \
  --set vertexAI.enabled=true --set vertexAI.projectID=<PROJECT>
```

After this the `Installer` CR exists and the agent is running. The `krateo-installer-agent` is a
federated kagent `Agent` (shipped from `kagent/chart`, installed as the `krateo-installer-agent`
composition) — its chart already provisions its `ModelConfig` and the narrow RBAC below, so no
standalone `kubectl apply` is needed. This is the only step that needs broad privilege.

### 2. Grant the agent narrow RBAC

> For `krateo-installer-agent` this is **already done by its chart** (`kagent/chart/templates/rbac.yaml`).
> The Role below is the template to replicate for any *other* agent you want to drive the Installer.

The agent only needs to read/patch the `Installer` CR and observe the rollout — **not**
cluster-admin. Bind this to whatever ServiceAccount the kagent tool-server runs as:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: installer-driver
  namespace: krateo-system
rules:
  - apiGroups:
      - composition.krateo.io
    resources:
      - installers
    verbs:
      - get
      - list
      - watch
      - patch
  - apiGroups:
      - core.krateo.io
    resources:
      - compositiondefinitions
    verbs:
      - get
      - list
      - watch
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: installer-driver
  namespace: krateo-system
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: installer-driver
subjects:
  - kind: ServiceAccount
    name: <kagent-tool-server-serviceaccount>
    namespace: krateo-system
```

(Add `get/list/watch` on the component Composition kinds too if you want the agent to inspect
their status while it reasons.)

### 3. Drive provisioning — the agent edits the Installer CR

To enable a feature, the agent flips one field on the `Installer` CR (its `k8s_patch_resource`
tool does exactly this):

```bash
kubectl patch installers.composition.krateo.io installer -n krateo-system --type=merge \
  -p '{"spec":{"features":{"oasgenprovider":true}}}'
```

The same surface drives the rest of the configuration model:

- **`spec.features.<flag>`** — enable/disable a capability group (observability, oasgenprovider,
  podRestartAlert, observabilityAgents, …). Additive and clean.
- **`spec.componentValues.<name>`** — override a component's Composition spec (strictly typed).
- **`spec.registryAuth`** — point components at a private registry.
- **`spec.components`** — advanced; changing a component **version** is a GVR change (prefer a new
  installer version — see the QUICKSTART).

## What core-provider does (observed)

On a kind cluster, toggling `features.oasgenprovider` from `false` → `true` with the patch above
drove the full chain unattended:

| t (after the patch) | What happened |
|---|---|
| ~20s | **Pass A** — `oasgen-provider-crd` + `oasgen-provider` CompositionDefinitions emitted |
| ~30s | both **Ready** (generated CRDs, cdc controllers Running) |
| ~120s | **Pass B** — `oasgen-provider-crd` Composition emitted + Ready |
| ~165s | `oasgen-provider` Composition emitted + Ready — **after its dependency** |
| | Helm releases `oasgen-provider-crd-0.9.0` + `oasgen-provider-0.9.0` deployed |

A single boolean flip on one namespaced CR → definitions → generated CRDs → Compositions → running
workloads, **in dependency order**. The patcher needed nothing but `patch` on the `Installer` CR.

## Safety

- **Strict schema validation.** The `installers` CRD is generated from the installer's
  `values.schema.json`, so the **apiserver validates every patch** the agent makes. Unknown fields
  are rejected (`componentValues.snowplow.bogusFieldXYZ` → *"unknown field"*). The agent literally
  cannot write invalid platform config; its blast radius is bounded to *valid installer settings*.
- **Narrow, audited authority.** The agent holds `patch` on exactly one resource type in one
  namespace. Every change is a normal `kubectl patch`, fully recorded.
- **Confirmation on destructive actions.** The agent's system prompt requires it to describe and
  confirm anything destructive (disabling a feature tears down its components; a `helm uninstall`
  removes the platform) before acting.

## Caveats

- **Feature dependencies.** Capabilities aren't independent — e.g. `observabilityAgents` pulls in
  the autopilot, which depends on `clickhouse-mcp-server` → `krateo-clickstack` (observability). The
  expert agent knows the dependency graph and enables features in a valid order.
- **Operator prerequisites.** Some features need a bootstrap operator that the lean install skipped
  (enabling `observability` needs the ClickHouse + MongoDB operators). Either include those operators
  in the bootstrap, or have the agent confirm they're present first.
- **Model availability.** The agent needs its `ModelConfig` working (Vertex ADC) from the start.
- **The bootstrap is still privileged.** A human/pipeline performs the one `helm install`. After
  that, the platform is **agent-drivable** with minimal rights — which is the whole point.

## Teardown

Disabling a feature on the `Installer` CR (`features.<flag>: false`) tears down that capability's
components via the engine's reconcile. Tearing the whole platform down is still
`helm uninstall installer` (the three teardown hooks run; see the
[README](../README.md#why-the-teardown-is-split-across-three-hooks)).
