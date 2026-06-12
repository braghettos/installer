# Krateo chart standard — app / crds / agent

The `Chart.yaml` structure every Krateo chart must follow. A component repo (e.g.
`krateo-authn-chart`) holds up to three charts; each carries the **full metadata block**, and the
load-bearing rule is **`sources` → the braghettos fork of the codebase**.

## The `sources` rule (hard rule)

`sources` MUST point to the **braghettos fork** of the git repo that holds the code — never
straight to `krateoplatformops`. **If braghettos hasn't forked the upstream codebase yet, fork it**
(`gh repo fork krateoplatformops/<x>` → `braghettos/<x>`). This is how a dedicated agent discovers
its component's codebase + chart structurally (not hardcoded in a prompt):

- **app** chart → the codebase fork (e.g. `https://github.com/braghettos/authn`)
- **crds** chart → the same codebase fork (the CRDs come from that code)
- **agent** chart → the codebase fork **and** the chart repo it packages
  (`https://github.com/braghettos/krateo-authn-chart`)

## Required metadata (every chart)

```yaml
apiVersion: v2
name: <name>
description: <one line>
type: application
version: <see below>
appVersion: "<see below>"
home: https://krateo.io
icon: https://github.com/krateoplatformops/krateo/blob/main/docs/media/logo.svg
keywords:
  - krateo
  - platformops
  - <component>
  - <role>          # authentication | crd | ai-agent | ...
sources:
  - https://github.com/braghettos/<codebase>          # the braghettos fork (hard rule)
  # agent charts ALSO list the chart they package:
  - https://github.com/braghettos/krateo-<component>-chart
```

## Per-chart specifics

| | **app** (`chart/`) | **crds** (`crd-chart/`) | **agent** (`kagent/chart/`) |
|--|--------------------|--------------------------|------------------------------|
| `name` | `<component>` (`authn`) | `<component>-crd` (`authn-crd`) | `krateo-<component>-agent` |
| `version` | `CHART_VERSION` (tag-driven) | literal, pinned (decoupled) | literal, pinned (shares the repo) |
| `appVersion` | upstream app version | CRD version | agent version |
| `keywords` role | the domain (`authentication`) | `crd` | `kagent`, `ai-agent` |
| `sources` | codebase fork | codebase fork | codebase fork **+** chart repo |

## Why

The braghettos fork of the codebase is the org's own copy (consistent with the chart forks). A
dedicated agent is the expert on its component *because* its chart's `sources` tells it exactly
where the code and the chart live; it reads them (via github tools) instead of guessing. Full,
uniform metadata makes every chart discoverable and self-describing.
