#!/usr/bin/env python3
"""Fresh-install readiness audit: for every component the installer pins, check its source repo
for (a) open PRs and (b) RECENT branches ahead of the default branch with no open PR (un-PR'd work).

Runs in CI on an installer tag push. Emits a GitHub Step Summary + ::warning:: annotations.
Non-blocking by default; pass --fail-on-findings to exit non-zero when anything is flagged.

Tunables (env):
  AHEAD_DAYS            only report ahead-branches whose tip commit is newer than this (default 60);
                        keeps ancient/stale/upstream branches out of the signal.
  MAX_BRANCH_COMPARES   cap branch comparisons per repo to bound runtime on repos with 100s of
                        branches (default 80); the summary notes when a repo was capped.
  AUDIT_ORG            org (default braghettos).  INSTALLER_ROOT  repo root (default .).
Inputs: chart/files/component-pins.yaml, chart/Chart.yaml, .github/component-source-repos.yaml
Auth:   GH_TOKEN (a PAT with org read; GITHUB_TOKEN is repo-scoped and won't see sibling repos).
"""
import os, sys, json, datetime, urllib.request, urllib.parse, yaml

ORG = os.environ.get("AUDIT_ORG", "braghettos")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
ROOT = os.environ.get("INSTALLER_ROOT", ".")
AHEAD_DAYS = int(os.environ.get("AHEAD_DAYS", "60"))
MAX_CMP = int(os.environ.get("MAX_BRANCH_COMPARES", "80"))
SHOW = 8  # max ahead-branches / PRs shown per repo before collapsing to "…+N more"
API = "https://api.github.com"
CUTOFF = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=AHEAD_DAYS)


def gh(path, params=None):
    url = f"{API}{path}" + (("?" + urllib.parse.urlencode(params)) if params else "")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def gh_paginated(path, cap_pages=10):
    out, page = [], 1
    while page <= cap_pages:
        chunk = gh(path, {"per_page": 100, "page": page})
        out += chunk
        if len(chunk) < 100:
            break
        page += 1
    return out


def load_charts():
    pins = yaml.safe_load(open(f"{ROOT}/chart/files/component-pins.yaml"))
    charts = {c.get("chart", c["name"]) for c in (pins.get("components") or []) if c.get("name")}
    cy = yaml.safe_load(open(f"{ROOT}/chart/Chart.yaml"))
    for d in (cy.get("dependencies") or []):
        charts.add(d["name"])
    return charts


def audit_repo(repo):
    default = gh(f"/repos/{ORG}/{repo}")["default_branch"]
    prs_all = gh(f"/repos/{ORG}/{repo}/pulls", {"state": "open", "per_page": 100})
    # Draft PRs are intentionally parked / not-ready — excluded from the audit entirely: not listed
    # as open PRs, and (because pr_heads still includes them) their branch is not re-flagged as an
    # ahead-branch either. So a draft PR fully disappears from the report — mark a PR draft to hide it.
    pr_heads = {p["head"]["ref"] for p in prs_all}
    prs = [p for p in prs_all if not p.get("draft")]
    pr_list = [f"#{p['number']} {p['title'][:70]}" for p in prs]
    branches = [b for b in gh_paginated(f"/repos/{ORG}/{repo}/branches")
                if b["name"] != default and b["name"] not in pr_heads]
    capped = len(branches) if len(branches) > MAX_CMP else 0
    ahead = []  # (tip_datetime, "branch (+N, date \"subj\")")
    for b in branches[:MAX_CMP]:
        name = b["name"]
        base = urllib.parse.quote(default, safe=""); head = urllib.parse.quote(name, safe="")
        try:
            cmp = gh(f"/repos/{ORG}/{repo}/compare/{base}...{head}")
        except Exception:
            continue
        if cmp.get("ahead_by", 0) <= 0:
            continue
        commits = cmp.get("commits") or []
        tip = commits[-1]["commit"] if commits else {}
        date = (tip.get("committer") or {}).get("date", "")
        try:
            when = datetime.datetime.fromisoformat(date.replace("Z", "+00:00"))
        except Exception:
            when = None
        if when and when < CUTOFF:       # stale -> not part of the actionable signal
            continue
        subj = (tip.get("message") or "").splitlines()[0][:52]
        ahead.append((when or CUTOFF, f"{name} (+{cmp['ahead_by']}, {date[:10]} \"{subj}\")"))
    ahead.sort(key=lambda x: x[0], reverse=True)   # most-recent first
    return default, pr_list, [s for _, s in ahead], capped


def collapse(items):
    if not items:
        return "—"
    if len(items) <= SHOW:
        return "<br>".join(items)
    return "<br>".join(items[:SHOW]) + f"<br>…+{len(items) - SHOW} more"


def main():
    fail_on = "--fail-on-findings" in sys.argv
    charts = load_charts()
    cfg = yaml.safe_load(open(f"{ROOT}/.github/component-source-repos.yaml"))
    chart_repo, extra = cfg.get("repos") or {}, cfg.get("extra_repos") or []

    repo_charts, unmapped = {}, []
    for ch in sorted(charts):
        (repo_charts.setdefault(chart_repo[ch], []).append(ch) if ch in chart_repo else unmapped.append(ch))
    for r in extra:
        repo_charts.setdefault(r, []).append("(engine/app source)")

    rows, flagged, errors = [], 0, 0
    for repo in sorted(repo_charts):
        try:
            default, prs, ahead, capped = audit_repo(repo)
        except Exception as e:
            rows.append((repo, repo_charts[repo], f"ERROR: {e}", [], 0)); errors += 1
            print(f"::warning title=Audit error::{repo}: {e}"); continue
        rows.append((repo, repo_charts[repo], prs, ahead, capped))
        if prs or ahead:
            flagged += 1
            print(f"::warning title=Unmerged work in {repo}::{len(prs)} open PR(s), "
                  f"{len(ahead)} recent un-PR'd ahead-branch(es)")

    L = ["# 🔎 Component fresh-install readiness audit",
         f"_{len(repo_charts)} source repos · open PRs + branches ahead of default with a commit "
         f"in the last {AHEAD_DAYS}d and no PR_\n",
         "| repo | components | open PRs | recent un-PR'd branches |",
         "|------|-----------|----------|-------------------------|"]
    for repo, chs, prs, ahead, capped in rows:
        pr_txt = collapse(prs) if isinstance(prs, list) else prs
        ah_txt = collapse(ahead)
        if capped:
            note = f"_(scanned {MAX_CMP} of {capped} branches)_"
            ah_txt = note if ah_txt == "—" else ah_txt + "<br>" + note
        flag = "⚠️ " if (isinstance(prs, list) and prs) or ahead else ""
        L.append(f"| {flag}`{repo}` | {', '.join(chs)} | {pr_txt} | {ah_txt} |")
    if unmapped:
        L.append(f"\n> ⚠️ **Unmapped charts** (add to `.github/component-source-repos.yaml`): "
                 f"{', '.join('`' + c + '`' for c in unmapped)}")
        print(f"::warning title=Unmapped charts::{', '.join(unmapped)}")
    L.append(f"\n**{flagged}/{len(repo_charts)} repos have unmerged work"
             f"{f'; {errors} errored' if errors else ''}"
             f"{f'; {len(unmapped)} unmapped' if unmapped else ''}.**")
    summary = "\n".join(L)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        open(os.environ["GITHUB_STEP_SUMMARY"], "a").write(summary + "\n")
    print(summary)
    if fail_on and (flagged or unmapped):
        sys.exit(1)


if __name__ == "__main__":
    main()
