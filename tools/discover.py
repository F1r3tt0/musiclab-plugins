#!/usr/bin/env python3
"""Find GitHub repositories that could be listed in the registry.

    python tools/discover.py [--verbose] [--top 30] [--max-repos 120] [--markdown issue.md]

Searches repositories by topic (vst3, clap, ...) and, with a token, code search too. Keeps
repos that have an OSI licence, a stable release in the last 3 years that carries
VST3/CLAP-style zip / tar / dmg assets (installers do not count), and that are neither in
plugins/ nor in rejected.json. Writes candidates.json and prints a Markdown table.

It only reads release metadata from the GitHub API. It never downloads or runs an asset;
hashing is add.py's job.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from common import (BAD_TAG_RE, OSI_LICENSES, PLUGINS_DIR, REJECTED_PATH, ROOT, ApiError,
                    candidate_assets, gh_api, load_json, token)

TOPICS = ["vst3", "vst3-plugin", "clap", "clap-plugin", "audio-plugin", "juce-plugin"]
CODE_QUERIES = ["FORMATS VST3 filename:CMakeLists.txt", "clap_entry language:C++"]
MAX_AGE_DAYS = 3 * 365


def listed_repos() -> set[str]:
    out = set()
    for p in PLUGINS_DIR.glob("*.json"):
        m = load_json(p)
        for k in ("repo", "releases"):
            if m.get(k):
                out.add(m[k].lower().removeprefix("https://github.com/"))
    return out


def rejected_repos() -> dict[str, str]:
    if not REJECTED_PATH.exists():
        return {}
    return {r["repo"].lower(): r.get("reason", "") for r in load_json(REJECTED_PATH)}


def search_repos(topics: list[str], pages: int) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for t in topics:
        for page in range(1, pages + 1):
            try:
                data = gh_api("search/repositories", {"q": f"topic:{t}", "sort": "stars", "order": "desc",
                                                     "per_page": 100, "page": page}, search=True)
            except ApiError as e:
                print(f"warning: search topic:{t} page {page}: {e}", file=sys.stderr)
                break
            for r in data.get("items", []):
                found.setdefault(r["full_name"].lower(), r)
            if len(data.get("items", [])) < 100:
                break
    return found


def search_code(known: dict[str, dict], limit: int) -> None:
    """Add repos that code search finds (needs a token). Fetches repo details for new ones."""
    added = 0
    for q in CODE_QUERIES:
        try:
            data = gh_api("search/code", {"q": q, "per_page": 50}, search=True)
        except ApiError as e:
            print(f"warning: code search {q!r}: {e}", file=sys.stderr)
            continue
        for item in data.get("items", []):
            full = item["repository"]["full_name"]
            if full.lower() in known or added >= limit:
                continue
            try:
                known[full.lower()] = gh_api(f"repos/{full}")
                added += 1
            except ApiError as e:
                print(f"warning: {full}: {e}", file=sys.stderr)


def evaluate(repo: dict, now: datetime) -> tuple[dict | None, str | None]:
    """(candidate, None) or (None, reason)."""
    full = repo["full_name"]
    try:
        rel = gh_api(f"repos/{full}/releases/latest", accept_404=True)
    except ApiError as e:
        return None, f"API error: {e}"
    if not rel:
        return None, "no stable release"
    tag = rel["tag_name"]
    if BAD_TAG_RE.search(tag) or BAD_TAG_RE.search(rel.get("name") or ""):
        return None, f"latest release {tag!r} looks like a nightly / prerelease"
    date = datetime.fromisoformat(rel["published_at"].replace("Z", "+00:00"))
    if now - date > timedelta(days=MAX_AGE_DAYS):
        return None, f"latest release {tag} is older than 3 years ({date.date()})"
    names = [a["name"] for a in rel["assets"]]
    groups = candidate_assets(rel["assets"])
    if not groups:
        shown = ", ".join(names[:4]) + (" ..." if len(names) > 4 else "")
        return None, "no zip/tar/dmg plugin assets (installers only or none)" + (f": {shown}" if names else "")
    return {
        "repo": full,
        "url": repo["html_url"],
        "license": repo["license"]["spdx_id"],
        "stars": repo["stargazers_count"],
        "description": repo.get("description") or "",
        "release": tag,
        "date": str(date.date()),
        "assets": {p: [a["name"] for a in lst] for p, lst in groups.items()},
    }, None


def table(cands: list[dict]) -> str:
    lines = ["| Repo | Licence | Stars | Release | Date | Assets per platform |",
             "|---|---|---|---|---|---|"]
    for c in cands:
        assets = "<br>".join(f"{p}: {', '.join(f'`{n}`' for n in ns)}" for p, ns in c["assets"].items())
        lines.append(f"| [{c['repo']}]({c['url']}) | {c['license']} | {c['stars']} | {c['release']} "
                     f"| {c['date']} | {assets} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verbose", action="store_true", help="also list rejected repos and why")
    ap.add_argument("--top", type=int, default=30, help="rows to print / put in the issue (default 30)")
    ap.add_argument("--max-repos", type=int, default=120, help="release lookups per run (default 120)")
    ap.add_argument("--min-stars", type=int, default=5)
    ap.add_argument("--pages", type=int, default=2, help="search result pages (100 repos) per topic")
    ap.add_argument("--code-search", action="store_true", default=None,
                    help="also use code search (default: on when a token is set)")
    ap.add_argument("--out", default=str(ROOT / "candidates.json"))
    ap.add_argument("--markdown", help="write the candidate table here (used for the weekly issue)")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    skip_listed, skip_rejected = listed_repos(), rejected_repos()
    print(f"Searching topics {', '.join(TOPICS)} ...", file=sys.stderr)
    repos = search_repos(TOPICS, args.pages)
    if args.code_search or (args.code_search is None and token()):
        search_code(repos, limit=40)
    print(f"{len(repos)} repos found.", file=sys.stderr)

    rejected: list[dict] = []
    candidates: list[dict] = []
    todo: list[dict] = []
    for r in sorted(repos.values(), key=lambda r: -r["stargazers_count"]):
        full = r["full_name"]
        lic = (r.get("license") or {}).get("spdx_id")
        reason = None
        if full.lower() in skip_listed:
            continue  # already in the registry: not interesting at all
        if full.lower() in skip_rejected:
            continue  # maintainers rejected it before
        if r.get("archived"):
            reason = "archived"
        elif r.get("fork"):
            reason = "fork"
        elif r["stargazers_count"] < args.min_stars:
            reason = f"fewer than {args.min_stars} stars"
        elif lic not in OSI_LICENSES:
            reason = f"licence {lic or 'none'} is not OSI-approved"
        if reason:
            rejected.append({"repo": full, "reason": reason})
        else:
            todo.append(r)

    for i, r in enumerate(todo[: args.max_repos], 1):
        print(f"[{i}/{min(len(todo), args.max_repos)}] {r['full_name']}", file=sys.stderr)
        try:
            cand, why = evaluate(r, now)
        except ApiError as e:
            print(f"stopping early: {e}", file=sys.stderr)
            break
        if cand:
            candidates.append(cand)
        else:
            rejected.append({"repo": r["full_name"], "reason": why})
    if len(todo) > args.max_repos:
        print(f"{len(todo) - args.max_repos} repos not examined (raise --max-repos).", file=sys.stderr)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "candidates": candidates,
                   "rejected": rejected}, f, indent=2, ensure_ascii=False)
        f.write("\n")

    md = f"{len(candidates)} candidate(s); top {min(args.top, len(candidates))} by stars.\n\n" + \
         table(candidates[: args.top])
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(md + "\n")
    print(md)
    if args.verbose:
        print("\n### Rejected\n\n| Repo | Why |\n|---|---|")
        for r in rejected:
            print(f"| {r['repo']} | {r['reason']} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
