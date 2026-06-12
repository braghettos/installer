# `krateo-installer-agent` — the installer's federated specialist agent

A [kagent](https://kagent.dev) Agent that **installs, configures and evolves** the Krateo
PlatformOps platform by editing the `Installer` CR, and answers questions about the
compose-of-compositions umbrella (bootstrap/reconcile/teardown). It is a **federated specialist**:
the `krateo-autopilot` orchestrator routes to it over A2A (it is **not** a standalone entry point).

Packaged as a dedicated agent **chart** under `chart/`, per the
[`AGENTS-VERSIONING.md` `/kagent` standard](https://github.com/braghettos/krateo-autopilot/blob/main/AGENTS-VERSIONING.md)
— published to `oci://ghcr.io/braghettos/krateo/krateo-installer-agent`.

## How it ships

It is an **installer component** (gated on `features.observabilityAgents`): the umbrella emits its
CompositionDefinition + Composition like any other component, and registers it on the orchestrator
via `componentValues.krateo-autopilot.extraAgents: [{ name: krateo-installer-agent }]`. The
autopilot then routes install/evolve/diagnose requests to it. No manual `kubectl apply` needed.

## What the chart contains (`chart/`)

| Template | Purpose |
|----------|---------|
| `agent.yaml` | the `Agent` (`krateo-installer-agent`) — k8s/helm tools, HITL approval on the mutating ones, the installer system prompt + A2A skills |
| `modelconfig.yaml` | optional GeminiVertexAI (ADC) `ModelConfig` (`modelConfig.create`); set `create=false` to reference the autopilot's by name |
| `rbac.yaml` | narrow `Role`/`RoleBinding` — `patch` on `installers.composition.krateo.io` (+ read `compositiondefinitions`), bound to the kagent tool-server ServiceAccount |

Key values: `modelConfig.{name,create,model,vertexAI}`, `hitlApproval`, `rbac.{create,serviceAccountName}`.

## Provisioning model

The agent provisions/evolves the platform by **patching the `Installer` CR** — not cluster-admin.
core-provider diffs the declared spec and provisions the difference in dependency order; the
apiserver validates every patch against the generated schema. See
**[AGENT-DRIVEN-PROVISIONING.md](./AGENT-DRIVEN-PROVISIONING.md)** for the full walkthrough.

## Standalone (agent-only) use

To run just the agent (the "spawn only the agent" demo) on a cluster with kagent installed:

```bash
helm install krateo-installer-agent oci://ghcr.io/braghettos/krateo/krateo-installer-agent \
  --version 0.1.0 -n krateo-system \
  --set modelConfig.vertexAI.projectID=<your-gcp-project>
```

Then register it on your autopilot via `extraAgents` (or talk to it directly for the demo).

## Related

- [AGENT-DRIVEN-PROVISIONING.md](./AGENT-DRIVEN-PROVISIONING.md) — provisioning via the Installer CR
- [AUTOPILOT-DESIGN.md](./AUTOPILOT-DESIGN.md) — the orchestrator + fleet design
- The `krateo-autopilot` repo's `AGENTS-VERSIONING.md` — the agents packaging/versioning/`/kagent` standard
