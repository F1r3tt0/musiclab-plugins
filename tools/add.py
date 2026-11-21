#!/usr/bin/env python3
"""Draft a manifest from a GitHub repo's latest stable release.

    python tools/add.py owner/repo [--tag TAG] [--print] [--max-size-mb 600]

Uses the GitHub API (unauthenticated, or GITHUB_TOKEN when set), picks the VST3/CLAP
archive for each platform, downloads it only to compute size and sha256 (and to read the
archive's file list), and writes plugins/<id>.json. The draft is deliberately incomplete:
`kind`, `tags` and `provides` need a human, and validate.py fails until they are filled in.
If plugins/<id>.json already exists, the new release is added as the newest version.
Nothing downloaded is ever extracted or run.
"""
from __future__ import annotations

import argparse
import json
import sys

from common import (PLUGINS_DIR, ApiError, archive_kind, asset_score, bundle_roots,
                    candidate_assets, download_and_hash, gh_api)


def build_version(release: dict, repo: str, max_bytes: int) -> dict:
    tag = release["tag_name"]
    groups = candidate_assets(release["assets"])
    if not groups:
        raise ApiError(f"release {tag} has no zip / tar / dmg assets for a supported platform "
                       "(installers are not accepted)")
    assets: dict = {}
    for plat in ("windows-x64", "macos-universal", "macos-arm64", "linux-x64", "linux-arm64"):
        if plat not in groups:
            continue
        best = sorted(groups[plat], key=lambda a: -asset_score(a["name"]))[0]
        url = best["browser_download_url"]
        print(f"  {plat}: {best['name']} ({best['size']} bytes)", file=sys.stderr)
        try:
            size, sha, names = download_and_hash(url, max_bytes=max_bytes, list_names=True)
        except ApiError as e:
            print(f"    skipped: {e}", file=sys.stderr)
            continue
        entry: dict = {"url": url, "sha256": sha, "size": size,
                       "archive": archive_kind(best["name"]) or "none", "bundles": []}
        if names is None:
            print("    note: cannot list this archive; fill in `bundles` by hand", file=sys.stderr)
        else:
            roots = bundle_roots(names)
            entry["bundles"] = [{"path": p, "format": f} for p, f in roots]
            if not roots:
                print("    skipped: no .vst3 / .clap found in this archive", file=sys.stderr)
                continue
        if not entry["bundles"] and entry["archive"] == "dmg":
            print("    skipped: a .dmg cannot be listed or unpacked by Music Lab; "
                  "list a zip / tar asset for this platform if the project has one", file=sys.stderr)
            continue
        assets[plat] = entry
    if not assets:
        raise ApiError("no usable assets")
    return {"version": tag.lstrip("vV"), "date": release["published_at"][:10], "assets": assets}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", help="GitHub owner/repo that publishes the release")
    ap.add_argument("--tag", help="use this release tag instead of the latest stable release")
    ap.add_argument("--print", action="store_true", dest="print_only", help="print instead of writing a file")
    ap.add_argument("--max-size-mb", type=int, default=600, help="skip assets larger than this")
    args = ap.parse_args()

    if args.repo.count("/") != 1:
        ap.error("repo must look like owner/repo")
    try:
        info = gh_api(f"repos/{args.repo}")
        path = f"repos/{args.repo}/releases/tags/{args.tag}" if args.tag else f"repos/{args.repo}/releases/latest"
        release = gh_api(path)
        if release.get("prerelease") or release.get("draft"):
            raise ApiError("that release is a prerelease or draft")
        print(f"{args.repo}: release {release['tag_name']}", file=sys.stderr)
        version = build_version(release, args.repo, args.max_size_mb * 1024 * 1024)
    except ApiError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    pid = info["name"].lower().replace("_", "-").replace(".", "-")
    target = PLUGINS_DIR / f"{pid}.json"
    if target.exists() and not args.print_only:
        manifest = json.loads(target.read_text(encoding="utf-8"))
        if any(v["version"] == version["version"] for v in manifest["versions"]):
            print(f"{target.name} already lists {version['version']}", file=sys.stderr)
            return 0
        manifest["versions"].insert(0, version)
        print(f"Added {version['version']} to {target}", file=sys.stderr)
    else:
        lic = (info.get("license") or {}).get("spdx_id") or "NOASSERTION"
        formats = sorted({b["format"] for a in version["assets"].values() for b in a["bundles"]})
        manifest = {
            "id": pid,
            "name": info["name"],
            "developer": info["owner"]["login"],
            "description": (info.get("description") or "TODO")[:200],
            "repo": info["html_url"],
            "license": lic,
            "kind": "effect",  # TODO: instrument / effect / midi / bundle
            "tags": [],
            "formats": formats,
            "provides": [],  # TODO: plugin names as a host scan reports them
            "versions": [version],
        }
        hp = info.get("homepage") or ""
        if hp.startswith("https://"):
            manifest["homepage"] = hp
        print(f"Draft for {pid}. Review: kind, tags, provides, description, licence, bundle paths.",
              file=sys.stderr)
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    if args.print_only:
        sys.stdout.write(text)
    else:
        target.write_text(text, encoding="utf-8")
        print(f"Wrote {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
