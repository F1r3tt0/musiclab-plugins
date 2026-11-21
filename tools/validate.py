#!/usr/bin/env python3
"""Validate the plugin manifests.

    python tools/validate.py                      # offline checks on every manifest
    python tools/validate.py --verify             # also download every asset, check size + sha256
    python tools/validate.py --verify --base origin/main   # ... only manifests changed vs. a git ref

Assets are only downloaded and hashed (and a zip/tar file listing is read). They are never
extracted or run. Exit status is 1 when anything fails.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from common import (BAD_TAG_RE, GH_RELEASE_URL_RE, INSTALLER_RE, OSI_LICENSES, PLUGINS_DIR,
                    SCHEMA_PATH, ApiError, archive_kind, bundle_roots, download_and_hash,
                    gh_api, load_json)


def check_manifest(path: Path, m: dict, errors: list[str]) -> None:
    def err(msg: str) -> None:
        errors.append(f"{path.name}: {msg}")

    if m.get("id") != path.stem:
        err(f"id {m.get('id')!r} does not match the file name {path.stem!r}")
    if m.get("license") not in OSI_LICENSES:
        err(f"licence {m.get('license')!r} is not an OSI-approved SPDX id (see tools/common.py)")
    repos = {m.get(k, "").lower().removeprefix("https://github.com/") for k in ("repo", "releases")}
    formats = set(m.get("formats", []))
    for v in m.get("versions", []):
        ver = v.get("version", "")
        if BAD_TAG_RE.search(ver):
            err(f"version {ver!r} looks like a prerelease / nightly / latest")
        try:
            date.fromisoformat(v.get("date", ""))
        except ValueError:
            err(f"version {ver}: bad date {v.get('date')!r}")
        for plat, a in v.get("assets", {}).items():
            where = f"version {ver} {plat}"
            url = a.get("url", "")
            mt = GH_RELEASE_URL_RE.match(url)
            if not mt:
                err(f"{where}: url must be a https://github.com/<owner>/<repo>/releases/download/<tag>/<file> link")
                continue
            owner_repo = f"{mt.group(1)}/{mt.group(2)}".lower()
            if owner_repo not in repos:
                err(f"{where}: url repo {owner_repo} is neither `repo` nor `releases`")
            if BAD_TAG_RE.search(mt.group(3)):
                err(f"{where}: release tag {mt.group(3)!r} looks like a prerelease / nightly / latest")
            fname = mt.group(4)
            if INSTALLER_RE.search(fname):
                err(f"{where}: installers are not accepted ({fname})")
            kind = archive_kind(fname)
            if a.get("archive") != (kind or "none"):
                err(f"{where}: archive {a.get('archive')!r} does not match the file name {fname!r}")
            for b in a.get("bundles", []):
                p = b.get("path", "")
                if p.startswith("/") or ".." in p.split("/") or "\\" in p or ":" in p:
                    err(f"{where}: bundle path {p!r} must be relative, with forward slashes and no '..'")
                if b.get("format") not in formats:
                    err(f"{where}: bundle {p!r} is {b.get('format')} but `formats` is {sorted(formats)}")
                if not p.lower().endswith("." + str(b.get("format"))):
                    err(f"{where}: bundle path {p!r} does not end in .{b.get('format')}")


def verify_manifest(m: dict, errors: list[str]) -> None:
    pid = m["id"]
    prerelease_cache: dict[str, bool | None] = {}
    for v in m["versions"]:
        for plat, a in v["assets"].items():
            where = f"{pid}.json: version {v['version']} {plat}"
            mt = GH_RELEASE_URL_RE.match(a["url"])
            if mt:
                rel = f"{mt.group(1)}/{mt.group(2)}/{mt.group(3)}"
                if rel not in prerelease_cache:
                    try:
                        data = gh_api(f"repos/{mt.group(1)}/{mt.group(2)}/releases/tags/{mt.group(3)}",
                                      accept_404=True)
                    except ApiError as e:
                        print(f"  warning: could not read release {rel}: {e}", file=sys.stderr)
                        data = {}
                    prerelease_cache[rel] = None if data is None else bool(data.get("prerelease") or data.get("draft"))
                if prerelease_cache[rel] is None:
                    errors.append(f"{where}: release {rel} does not exist")
                elif prerelease_cache[rel]:
                    errors.append(f"{where}: release {rel} is a prerelease or draft")
            print(f"  downloading {a['url']} ({a['size']} bytes)", flush=True)
            try:
                size, sha, names = download_and_hash(a["url"], list_names=True)
            except Exception as e:  # noqa: BLE001 - report any download failure
                errors.append(f"{where}: download failed: {e}")
                continue
            if size != a["size"]:
                errors.append(f"{where}: size is {size}, manifest says {a['size']}")
            if sha != a["sha256"]:
                errors.append(f"{where}: sha256 is {sha}, manifest says {a['sha256']}")
            if names is not None:
                listed = set(names) | {n.rstrip("/") for n in names}
                inner = a.get("inner", "").strip("/")
                for b in a["bundles"]:
                    full = f"{inner}/{b['path']}" if inner else b["path"]
                    if full not in listed and not any(n.startswith(full + "/") for n in listed):
                        errors.append(f"{where}: bundle {full!r} is not in the archive "
                                      f"(found: {[p for p, _ in bundle_roots(names)]})")


def changed_manifests(base: str) -> set[str]:
    out = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD", "--", "plugins/"],
                         capture_output=True, text=True, check=True, cwd=PLUGINS_DIR.parent).stdout
    return {Path(line).name for line in out.splitlines() if line.endswith(".json")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify", action="store_true", help="download assets and check size and sha256")
    ap.add_argument("--base", help="with --verify: only manifests changed relative to this git ref")
    ap.add_argument("--report", help="write failures as Markdown to this file")
    args = ap.parse_args()

    try:
        import jsonschema
    except ImportError:
        print("jsonschema is missing: pip install -r requirements.txt", file=sys.stderr)
        return 2

    schema = load_json(SCHEMA_PATH)
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())

    errors: list[str] = []
    seen: dict[str, str] = {}
    loaded: list[tuple[Path, dict]] = []
    files = sorted(PLUGINS_DIR.glob("*.json"))
    for p in PLUGINS_DIR.iterdir():
        if p.is_file() and p.suffix != ".json":
            errors.append(f"{p.name}: only .json manifests belong in plugins/")
    for path in files:
        try:
            m = load_json(path)
        except json.JSONDecodeError as e:
            errors.append(f"{path.name}: invalid JSON: {e}")
            continue
        schema_errors = sorted(validator.iter_errors(m), key=lambda e: list(e.absolute_path))
        for e in schema_errors:
            loc = "/".join(str(x) for x in e.absolute_path) or "(root)"
            errors.append(f"{path.name}: schema: {loc}: {e.message}")
        if schema_errors:
            continue
        if m["id"] in seen:
            errors.append(f"{path.name}: duplicate id {m['id']!r} (also in {seen[m['id']]})")
        seen[m["id"]] = path.name
        check_manifest(path, m, errors)
        loaded.append((path, m))
    print(f"Checked {len(files)} manifest(s).")

    if args.verify and not errors:
        only = changed_manifests(args.base) if args.base else None
        for path, m in loaded:
            if only is not None and path.name not in only:
                continue
            print(f"Verifying {path.name}")
            verify_manifest(m, errors)
        if only is not None and not only:
            print("No manifests changed; nothing to verify.")

    if args.report and errors:
        Path(args.report).write_text("\n".join(f"- {e}" for e in errors) + "\n", encoding="utf-8")
    if errors:
        print("\nFAILED:", file=sys.stderr)
        for e in errors:
            print("  " + e, file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
