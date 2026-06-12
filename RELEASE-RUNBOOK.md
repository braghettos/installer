# Release runbook — consolidating on `oci://ghcr.io/braghettos/krateo`

One-time merge + tag order for the repo-sanitization / registry-consolidation work, so the
installer never references an artifact that isn't published yet.

## The one rule

The installer resolves **every** component from `oci://ghcr.io/braghettos/krateo/<chart>:<pinned>`.
So **every pinned artifact must exist at `/krateo` before the umbrella is tagged.** The app-chart
forks currently publish to `/charts` (or bare `braghettos`) — those are the artifacts that don't
exist at `/krateo` yet and gate everything.

Per repo, the sub-order is always: **merge PR → confirm the release CI run is green → tag →
confirm the artifact pushed.** Merging flips the workflow's `OCI_REPO` to `/krateo`; the semver tag
triggers `release-oci.yaml`.

## Pre-flight (done)

- `krateo-core-provider-chart` standalone CompositionDefinition bumped `0.35.2` → **`0.35.4`** to
  match the installer pin. (Already pushed to that PR.)

## Tier 1 — component repos (independent; may run in parallel)

For the app-chart forks the **tag value = the chart version** (they use the `CHART_VERSION`
placeholder). Charts that are literal-versioned (marked †) publish at their own pinned versions, so
their repo tag is just a release marker.

| Repo | PR | Tag | Publishes to `/krateo/…` |
|------|----|-----|---------------------------|
| `krateo-core-provider-chart` | #1 | `0.35.4` | core-provider:0.35.4, core-provider-crd:0.35.4, **krateo-core-provider-agent:0.1.0** |
| `krateo-authn-chart` | #1 | `0.22.2` | authn:0.22.2, authn-crd:0.22.2 (pinned), **krateo-authn-agent:0.1.0** |
| `krateo-snowplow-chart` | #5 | `0.30.259` | snowplow:0.30.259, snowplow-crd:0.20.6, **krateo-snowplow-agent:0.1.0** |
| `krateo-frontend-chart` | #5 | `1.0.12` | frontend:1.0.12, frontend-crd:1.0.25, **krateo-frontend-agent:0.1.0** |
| `krateo-oasgen-provider-chart` | #1 | `0.9.0` | oasgen-provider:0.9.0, oasgen-provider-crd:0.9.0 |
| `krateo-portal-chart` | #6 | `1.2.2` | portal:1.2.2 |
| `krateo-clickstack-chart` | #1 | `0.1.2` † | krateo-clickstack:0.1.2, otel-collector-deployment:0.1.1, otel-collector-daemonset:0.1.1, krateo-sse-proxy:0.1.1, **krateo-clickstack-agent:0.1.0** |
| `krateo-autopilot` | #1 | `0.1.9` | krateo-autopilot:0.1.9 (all specialists federated out) |
| `krateo-installer-charts` | #1 | `0.x` † | hyperdx-provider:0.1.1, kagent:0.1.0 (appVersion kagent 0.9.7), kagent-crds:0.1.0, clickhouse-mcp-server:0.1.7 |

## Tier 2 — the umbrella (LAST)

Only after **all** Tier-1 artifacts are confirmed at `/krateo`:

| Repo | PR | Tag |
|------|----|-----|
| `krateo-installer` | #1 | the installer release version → publishes `/krateo/installer` **and** `/krateo/krateo-installer-agent:0.1.0` (the federated agent, in the same CI run) |

## Why this is low-risk even if the order slips

- Tagging only **publishes**; nothing is ever unpublished. Merging `krateo-installer-charts #1`
  (which drops the moved/duplicated charts) does **not** delete existing `/krateo` artifacts from
  its prior releases.
- The CRD/duplicated artifacts (`authn-crd`, `frontend`, `frontend-crd`, `snowplow-crd`,
  `krateo-autopilot`, `kagent`, `hyperdx-provider`, `clickhouse-mcp-server`, `otel-*`,
  `krateo-sse-proxy`) **already exist** at `/krateo` from earlier installer-charts releases — the
  fork tags just re-publish them as the new single source.
- The genuinely-new-to-`/krateo` artifacts are the **app charts** `authn, snowplow, frontend,
  core-provider, oasgen-provider, portal, clickstack` — these tags are what actually gate a working
  install.

## Verify before Tier 2

```sh
helm pull oci://ghcr.io/braghettos/krateo/core-provider     --version 0.35.4
helm pull oci://ghcr.io/braghettos/krateo/snowplow          --version 0.30.259
helm pull oci://ghcr.io/braghettos/krateo/portal            --version 1.2.2
helm pull oci://ghcr.io/braghettos/krateo/krateo-autopilot  --version 0.1.7
```

## Related

- Org repo-sanitization standard (naming / registry / CI / topics / README).
- `kagent/AUTOPILOT-DESIGN.md` + the `krateo-autopilot` repo's `AGENTS-VERSIONING.md`
  (agents packaging / versioning / federation / eval).
