# Updating the installer to deploy a new component version

How to bump a platform component (snowplow, portal, frontend, sse-proxy, agents, …) or the
engine/cdc and ship it through the installer. Written for automated sessions; follow it verbatim.

- **Repo:** `braghettos/krateo-installer`
- **Registry:** `oci://ghcr.io/braghettos/krateo`
- **Chain:** edit pin → PR to `main` → tag `0.2.N` (CI publishes) → `helm upgrade` → verify

## 1. Where versions are pinned

- **Components** (everything the umbrella deploys): `chart/files/component-pins.yaml` → each
  entry's `version:`. This is chart *content* (loaded via `.Files.Get`), so it survives
  `helm upgrade --reuse-values` and always propagates on a chart bump.
- **Engine / cdc:** `chart/Chart.yaml` → the `krateo-core-provider` dependency `version:`
  (the core-provider-chart pins the cdc image). cdc is NOT in `component-pins.yaml` — it is the
  umbrella's bootstrap Helm subchart.

## 2. Bump → release

1. **Branch off `origin/main`.** Never a stale/long-lived branch — divergent branches silently
   *regress* pins (installer 0.2.208–0.2.213 rolled the cdc pin backward this way).
2. **Confirm the target chart version is published** before pinning it:
   `GET https://ghcr.io/v2/braghettos/krateo/<chart>/tags/list` (anon token dance). A git tag on
   the source repo is NOT proof the OCI chart is published.
3. Edit the `version:` in `component-pins.yaml` (or the `krateo-core-provider` dep in
   `Chart.yaml`). **Forward only** — pin ≥ the live version, or the umbrella will try to
   *downgrade* the component (prunes the served GVK) and churn/wedge.
4. Commit → PR to `main` → merge. Commit trailers: `Co-Authored-By: Claude ...` +
   `Claude-Session: ...`, author Diego.
5. **Tag the next `0.2.N` on `main`** → the `release-oci` CI publishes the installer chart.
   (`bump-consumer-chart-image` auto-opens the core-provider-chart pin PR when a new cdc image is
   released; merge + tag that chart first if you're shipping a cdc change.)

## 3. Deploy

```
helm upgrade installer oci://ghcr.io/braghettos/krateo/installer \
  --version 0.2.N -n krateo-system -f <live-values.yaml> --timeout 10m
```

- **Do NOT use `--reuse-values`** — it freezes the cdc/chart-inspector image tags at the *old*
  subchart defaults (engine still rolls via appVersion, so it looks fine but the controllers
  don't). Always pass `-f <live-values>` instead. Recover the live values with
  `helm -n krateo-system get values installer`.
- Minimal live-values: `bootstrap.coreProvider.enabled=true`, `exposure.type=LoadBalancer`,
  plus any `componentValues` the cluster runs (e.g. `snowplow.env`, `krateo-core-provider.otel`).

## 4. Critical gotchas (each of these has bitten a live deploy)

- **Verify the *running* cdc image**, not the published one: check the
  `installers-v0-2-N-controller` container image after deploy. The ghcr "latest" tag can be
  ahead of what the deployed installer actually pins. cdc **must be ≥ 1.3.10** (reconcile
  step-1.5 sanitize + step-4.7 adopt) or an out-of-band / migration GVK change wedges the umbrella.
- **Composition version bumps trigger the ownership-strip wedge.** After the deploy, if the
  umbrella (or a composition) goes `Ready=False` / `Synced=False` with
  `... exists and cannot be imported into the current release: invalid ownership metadata; ...
  must be set to "Helm"`, the migrated child CR came up *unowned*. helm's import gate blocks it
  *before* cdc's sanitize/adopt can run — the reconcile can't fix it. **Recover by stamping** helm
  ownership on every unowned composition child, then nudge:

  ```
  kubectl -n krateo-system label   <res> <name> app.kubernetes.io/managed-by=Helm --overwrite
  kubectl -n krateo-system annotate <res> <name> \
    meta.helm.sh/release-name=<UMBRELLA_RELEASE> \
    meta.helm.sh/release-namespace=krateo-system --overwrite
  ```

  helm reports unowned objects one at a time (whack-a-mole) — scan *all* `composition.krateo.io`
  children and stamp them in one pass, then re-check until 0 remain.
  - **`<UMBRELLA_RELEASE>`** = `krateo` on fresh 0.2.214+ installs (the chart's stable
    `krateo.io/release-name` label); `installer-cv2zwx7v` on the legacy installer-test cluster.
    Confirm with `helm -n krateo-system list -a`.
  - **Portal widgets** (`widgets.templates.krateo.io/*`, `restactions.templates.krateo.io/*`) are
    owned by release **`portal`**, not the umbrella.
  - This is a known cdc/core-provider bug (a full-PUT strips helm labels on CRD-version
    migration); the durable fix is delegated to the core-provider session. Stamping is the interim.

## 5. Verify

- Umbrella `installers.composition.krateo.io/installer`: `Ready=True` and `Synced=True`.
- Revision **flat** over ~2 min (`helm -n krateo-system list -a` → umbrella rev unchanged) — no
  reconcile churn.
- **0 unowned** `composition.krateo.io` children.
- The bumped component's CompositionDefinition `version:` AND its app helm release chart version
  both show the new version (the release chart-metadata field can lag under apply-if-changed if
  live already matches the render — cosmetic, but a real bump should advance it).
