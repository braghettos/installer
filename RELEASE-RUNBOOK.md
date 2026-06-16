# Release runbook

How to cut releases in the Krateo installer ecosystem. Everything publishes to the single
consolidated registry **`oci://ghcr.io/braghettos/krateo`**, and the installer pins every component
from it.

> **History:** the one-time migration that consolidated all charts onto `/krateo` (off the old
> `/charts` + bare-`braghettos` locations) is **complete** — every component repo now publishes to
> `/krateo` via the canonical CI below. This runbook is the steady-state process; the migration
> ordering it used to hold is no longer needed.

## The invariants

1. **One registry.** Every chart → `oci://ghcr.io/braghettos/krateo/<chart>:<version>`. Nothing is
   ever unpublished; tagging only *adds*.
2. **The installer pins only published versions.** A version listed in `chart/values.yaml`
   `components[].version` (or a bootstrap subchart in `Chart.yaml`) **must already exist at
   `/krateo`** before the umbrella is tagged — so a component release always precedes the installer
   release that pins it.
3. **A component GVR/schema change ships as a new installer version**, never an in-place edit of a
   running one — because `values.schema.json` types `componentValues` against each pinned chart's
   real schema (regenerated per release). See
   [QUICKSTART — Changing component versions](./QUICKSTART.md#changing-component-versions).

## Canonical CI (byte-identical across repos)

| Repo kind | Workflows | Trigger → effect |
|---|---|---|
| **chart repos** (`krateo-*-chart`, `krateo-installer`, …) | `lint.yaml` + `release-oci.yaml` | semver tag `X.Y.Z` → discover every first-class chart (`chart/`, `crds-subchart/`, `kagent/chart/`, …; vendored subcharts skipped), substitute `CHART_VERSION`/`SOURCE_REF`→tag and `APP_VERSION`→the code repo's latest semver tag, package & push each to `/krateo/<chart>:<tag>`. `workflow_dispatch` accepts `chart_version`/`app_version` inputs. |
| **code repos** (`krateo-authn`, `krateo-snowplow`, `krateo-sse-proxy`, …) | `release-pullrequest.yaml` + `release-tag.yaml` | PR → build/test; semver tag → multi-platform image (`docker/build-push-action@v7`, `linux/amd64,linux/arm64`) to `ghcr.io/<repo>:<tag>`, **and** if a `make generate` target exists, generate CRDs and publish them (with `helm.sh/resource-policy: keep` injected) to the repo's `-chart` repo's CRD sub-chart. Single `make generate` entry point. |

CI is intentionally identical across repos so any repo can be reasoned about the same way — the
same `release-oci.yaml` is byte-for-byte shared by all chart repos, and the same `release-tag.yaml`
by all code repos.

## Recipe A — release a chart change (chart repo)

```bash
# in e.g. krateo-snowplow-chart, after merging the change to main:
git tag 1.0.11 && git push origin 1.0.11        # tag value = the chart version
gh run watch -R braghettos/krateo-snowplow-chart   # release-oci goes green
helm show chart oci://ghcr.io/braghettos/krateo/snowplow --version 1.0.11   # confirm published
```

## Recipe B — release a code change (code repo)

```bash
# in e.g. krateo-sse-proxy, after merging:
git tag 0.1.4 && git push origin 0.1.4
# release-tag.yaml: builds + pushes the multi-arch image, and (if `make generate` exists)
# publishes the regenerated CRDs to krateo-sse-proxy-chart's CRD sub-chart.
```

Then bump the consuming chart's `appVersion`/image tag and run **Recipe A** for that chart.

## Recipe C — cut a new installer version (the main one)

When you change a pinned component version, an installer template/value, or the schema:

```bash
# 0. Make sure every NEW pinned component version is already published (invariant #2).
# 1. Edit chart/values.yaml components[].version (and/or Chart.yaml bootstrap subchart pins).
# 2. Regenerate the typed componentValues schema (pulls each pinned chart's values.schema.json):
python3 hack/gen-componentvalues-schema.py chart
# 3. Lint locally (CHART_VERSION must be a real semver for lint):
#    (CI lints on PR; for a local check, substitute a dummy version first.)
# 4. PR → merge → tag the installer release:
git tag 0.2.88 && git push origin 0.2.88
# release-oci publishes BOTH /krateo/installer:0.2.88 AND /krateo/krateo-installer-agent
# (the federated agent in kagent/chart/, in the same CI run).
helm show values oci://ghcr.io/braghettos/krateo/installer --version 0.2.88 | grep -A3 '^localModel:'
```

> **Provenance order for a coordinated change** (e.g. a new autopilot feature): release the
> **component** first (Recipe A/B), confirm it at `/krateo`, *then* re-pin + release the installer.
> Example from this repo's history: autopilot `0.1.12` was merged + tagged, then the installer
> re-pinned it (`components[].version: 0.1.12`), regen'd the schema, and released `0.2.87`.

## Live upgrade (in place, on a running cluster)

`helm upgrade installer … --version <new>` bumps the seed `installer` CompositionDefinition; the
self-reconcile picks up the new chart version. But the live **Installer CR is frozen** at the old
component defaults and **new CR fields are pruned** unless you edit through the **version-qualified
GVR** (`installers.v<new>.composition.krateo.io`). Full recipe + the nudges (CR re-apply, the
version-qualified patch, stale-CRD-cache restart, helm-ownership re-point) are in the
`installer-version-upgrade-nudges` project memory. For a clean validation, a fresh install of the
new version is the lowest-risk path.

## Verify before relying on a release

```bash
helm show chart oci://ghcr.io/braghettos/krateo/installer        --version <new>
helm show chart oci://ghcr.io/braghettos/krateo/krateo-autopilot --version <pinned>
# spot-check any component you bumped:
helm show chart oci://ghcr.io/braghettos/krateo/<component>      --version <pinned>
```

## Related

- [QUICKSTART.md](./QUICKSTART.md) — install, the version table (the current pinned set), changing component versions
- [README.md](./README.md) / [ARCHITECTURE.md](./ARCHITECTURE.md) — the two render modes, self-bootstrap, engine internals
- [CHART-STANDARD.md](./CHART-STANDARD.md) — the `app`/`crds`/`agent` chart shapes + the `sources` rule
- [kagent/ADDING-AN-AGENT.md](./kagent/ADDING-AN-AGENT.md) — releasing a new autopilot-orchestrated agent
- `krateo-autopilot` repo's `AGENTS-VERSIONING.md` — agent packaging/versioning/federation
