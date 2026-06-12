# kagent Agent: `krateo-installer-expert`

A [kagent](https://kagent.dev) Agent CRD that turns an LLM into a subject-matter expert on
**this installer** — the compose-of-compositions umbrella that self-bootstraps and
self-reconciles the whole Krateo PlatformOps platform from one `helm install`, and tears it
down cleanly via three ordered Helm hooks.

The system prompt embeds the non-obvious architecture: the two render modes
(`bootstrap.coreProvider.enabled`), the runtime-generated `installers` CRD + post-install
hook, the Pass A / Pass B self-reconcile gated on `crdExists` + `depsReady`, the three
teardown hooks (`ordered-teardown` → `bootstrap-teardown` → `post-delete-cleanup`) and why
finalizer-based cleanup needs live controllers, the strictly-typed `componentValues` /
`registryAuth` knobs, the installer-version GVR model + the `vacuum` migration, and the
operational gotchas (demo-system, rotating admin-password, snowplow-as-BFF, kind NodePort).

The agent wires in the built-in `kagent-tool-server` (`k8s_*` + `helm_*` tools), so it can
inspect CompositionDefinitions, Compositions, the Installer CR, controller logs, and Helm
releases on the live cluster and reason about what it finds.

> **Beyond Q&A:** the agent can also **provision and evolve the platform** by editing the
> `Installer` CR (no cluster-admin needed) — see
> **[AGENT-DRIVEN-PROVISIONING.md](./AGENT-DRIVEN-PROVISIONING.md)**.

## Prerequisites

### 1. kagent on the cluster

If you installed the platform with this umbrella and `features.observabilityAgents=true`,
**kagent is already running** in `namespaces.krateo` (default `krateo-system`) — skip ahead.

Otherwise install kagent standalone (verified on v0.9.9; no `helm repo add` needed):

```bash
kubectl create ns krateo-system --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install kagent-crds \
  oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds \
  --version 0.9.9 --namespace krateo-system --wait --timeout 5m

helm upgrade --install kagent \
  oci://ghcr.io/kagent-dev/kagent/helm/kagent \
  --version 0.9.9 --namespace krateo-system --timeout 10m
```

> The Agent and ModelConfig are namespaced; they must live in the **same namespace** as the
> kagent controller + its `kagent-tool-server` `RemoteMCPServer`. These manifests use
> `krateo-system` (where the installer runs kagent) — `sed` the namespace if yours differs.

### 2. ModelConfig: Gemini on Vertex AI (ADC)

[`modelconfig-vertex-gemini.yaml`](./modelconfig-vertex-gemini.yaml) is a `GeminiVertexAI`
`ModelConfig` named **`vertex-gemini`** using **Application Default Credentials** — the same
auth the installer's own autopilot uses (`vertexAI.enabled=true`). On GKE that means Workload
Identity: the kagent controller pod's ServiceAccount must map to a GCP SA with
`roles/aiplatform.user`, with the Vertex AI API enabled. Only `model` +
`geminiVertexAI.{projectID,location}` are required — set them to match your installer's
`vertexAI` values.

**Not on Workload Identity?** Add a key Secret and reference it (the kagent reference
blueprint does exactly this):

```bash
kubectl -n krateo-system create secret generic kagent-vertex \
  --from-file=key.json=$HOME/Downloads/<your-sa-key>.json
```
```yaml
# then add to the ModelConfig spec:
  apiKeySecret: kagent-vertex
  apiKeySecretKey: key.json
```

Swap to Anthropic/OpenAI/etc. by changing `provider`/`model` (and the auth field) — the agent
only references the ModelConfig by name (`vertex-gemini`).

## Apply

```bash
kubectl apply -f kagent/modelconfig-vertex-gemini.yaml
kubectl apply -f kagent/agent-installer-expert.yaml
```

Then open the kagent dashboard (or its A2A endpoint) and chat with **`krateo-installer-expert`**.

## What to ask it

- *"How does one `helm install` bring up the whole platform, and why a post-install hook?"*
- *"Why is teardown split across three hooks, and which one fixes the portal/demo-system wedge?"*
- *"How do I override snowplow's replica count without breaking exposure?"* (componentValues)
- *"A reinstall crashloops core-provider with a GVK→GVR error — what's the fix?"*
- *"The stack is stuck with only some CompositionDefinitions Ready — which gate is unmet?"*

It will use the `k8s_*` / `helm_*` tools to inspect the live resources and explain precisely,
and will ask for confirmation before anything destructive.
