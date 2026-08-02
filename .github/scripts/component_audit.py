#!/usr/bin/env python3
"""Fresh-install readiness audit: for every component the installer pins, check its source repo
for (a) open PRs and (b) RECENT branches ahead of the default branch with no PR (un-PR'd work).
Branches whose work was already merged via a (squash/rebase) PR are detected and omitted — such a
merge rewrites commit SHAs, so the branch stays "ahead" of main even though its work has landed.

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


def closed_pr_heads(repo):
    """Head-ref -> (state, pr_number) for closed PRs touched inside the audit window, so an
    ahead-branch can be classified without a per-branch API call. A squash/rebase merge rewrites the
    commit SHAs, so the branch stays ahead_by>0 even though its work is in main; this is how we tell
    'already merged' (state 'merged') from 'PR closed unmerged' (abandoned) from 'never PR'd'.
    Fetched newest-first by update time and stopped once past CUTOFF — an ahead-branch only reaches
    classification if its tip commit is within the window, so any (squash/rebase) merge that produced
    it is recent too and is covered here. Newest PR per head wins (first seen under the desc sort)."""
    out, page = {}, 1
    while page <= 10:
        try:
            chunk = gh(f"/repos/{ORG}/{repo}/pulls",
                       {"state": "closed", "sort": "updated", "direction": "desc",
                        "per_page": 100, "page": page})
        except Exception:
            break                      # partial map -> unlisted branches fall through to "none"
        stop = False
        for p in chunk:
            upd = (p.get("updated_at") or "").replace("Z", "+00:00")
            try:
                when = datetime.datetime.fromisoformat(upd)
            except Exception:
                when = None
            if when and when < CUTOFF:  # sorted desc -> everything after this is older too
                stop = True; break
            ref = p["head"]["ref"]
            if ref not in out:
                out[ref] = ("merged", p["number"]) if p.get("merged_at") else ("closed", p["number"])
        if stop or len(chunk) < 100:
            break
        page += 1
    return out


def audit_repo(repo):
    default = gh(f"/repos/{ORG}/{repo}")["default_branch"]
    prs_all = gh(f"/repos/{ORG}/{repo}/pulls", {"state": "open", "per_page": 100})
    # Draft PRs are intentionally parked / not-ready — excluded from the audit entirely: not listed
    # as open PRs, and (because pr_heads still includes them) their branch is not re-flagged as an
    # ahead-branch either. So a draft PR fully disappears from the report — mark a PR draft to hide it.
    pr_heads = {p["head"]["ref"] for p in prs_all}
    prs = [p for p in prs_all if not p.get("draft")]
    pr_list = [f"#{p['number']} {p['title'][:70]}" for p in prs]
    closed_heads = closed_pr_heads(repo)   # head-ref -> (merged|closed, #) within the audit window
    branches = [b for b in gh_paginated(f"/repos/{ORG}/{repo}/branches")
                if b["name"] != default and b["name"] not in pr_heads]
    capped = len(branches) if len(branches) > MAX_CMP else 0
    ahead, merged_via_pr = [], 0  # ahead: (tip_datetime, "branch (+N, date \"subj\")")
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
        state, prnum = closed_heads.get(name, ("none", None))
        if state == "merged":            # squash/rebase-merged: work is in main, branch just stale
            merged_via_pr += 1
            continue
        subj = (tip.get("message") or "").splitlines()[0][:52]
        tag = f" [PR #{prnum} closed unmerged]" if state == "closed" else ""
        ahead.append((when or CUTOFF, f"{name} (+{cmp['ahead_by']}, {date[:10]} \"{subj}\"){tag}"))
    ahead.sort(key=lambda x: x[0], reverse=True)   # most-recent first
    return default, pr_list, [s for _, s in ahead], capped, merged_via_pr


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
            default, prs, ahead, capped, merged = audit_repo(repo)
        except Exception as e:
            rows.append((repo, repo_charts[repo], f"ERROR: {e}", [], 0, 0)); errors += 1
            print(f"::warning title=Audit error::{repo}: {e}"); continue
        rows.append((repo, repo_charts[repo], prs, ahead, capped, merged))
        if prs or ahead:
            flagged += 1
            print(f"::warning title=Unmerged work in {repo}::{len(prs)} open PR(s), "
                  f"{len(ahead)} recent un-PR'd ahead-branch(es)"
                  f"{f', {merged} merged-via-PR omitted' if merged else ''}")

    L = ["# 🔎 Component fresh-install readiness audit",
         f"_{len(repo_charts)} source repos · non-draft open PRs + branches ahead of default with a "
         f"commit in the last {AHEAD_DAYS}d and no PR (branches already merged via PR are omitted)_\n",
         "| repo | components | open PRs | recent un-PR'd branches |",
         "|------|-----------|----------|-------------------------|"]
    for repo, chs, prs, ahead, capped, merged in rows:
        pr_txt = collapse(prs) if isinstance(prs, list) else prs
        ah_txt = collapse(ahead)
        if merged:
            mnote = f"_(+{merged} already merged via PR, omitted)_"
            ah_txt = mnote if ah_txt == "—" else ah_txt + "<br>" + mnote
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
