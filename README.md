# Krateo PlatformOps Installer (umbrella)

Compose-of-compositions blueprint that installs the full Krateo PlatformOps platform from a
single `Installer` Composition. The umbrella registers each component's CompositionDefinition
(Pass A) and emits each Composition once its dependencies are `Ready` (Pass B), resolving
exposure (`service.type`) and the portal config (peer LoadBalancer IPs) by reconciliation.

- **Chart:** `oci://ghcr.io/braghettos/charts/installer`
- **Install guide:** see **[QUICKSTART.md](./QUICKSTART.md)** — kind (with MetalLB) and managed GKE.
- **Kind:** `Installer` (`composition.krateo.io`).

## Layout

```
chart/                     the umbrella chart (Chart.yaml, values.yaml, values.schema.json, templates/)
  templates/definitions.yaml   Pass A — emit component CompositionDefinitions
  templates/compositions.yaml  Pass B — emit gated Compositions + exposure/config wiring
  templates/_helpers.tpl       inst.* helpers (apiVersion, crdExists, depsReady, lbip, ...)
compositiondefinition.yaml install the umbrella itself as a CompositionDefinition
```

## Releasing

Pushing a semver tag triggers `.github/workflows/release-oci.yaml`, which packages and pushes
`chart/` to `oci://ghcr.io/braghettos/charts/installer:<tag>` (CHART_VERSION is substituted from
the tag). Component charts live in their own `braghettos/*` repos and publish the same way.
