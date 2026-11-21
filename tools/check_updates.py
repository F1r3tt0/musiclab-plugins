#!/usr/bin/env python3
"""List listed plugins whose upstream has a newer stable release than the newest version here.

    python tools/check_updates.py [--out updates.md]

Prints a Markdown bullet list (nothing when everything is current). To take an update:
    python tools/add.py owner/repo        # adds the new release as the newest version
"""
from __future__ import annotations

import argparse
import sys

from common import BAD_TAG_RE, PLUGINS_DIR, ApiError, gh_api, load_json


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="write the list to this file (only when non-empty)")
    args = ap.parse_args()
    lines = []
    for p in sorted(PLUGINS_DIR.glob("*.json")):
        m = load_json(p)
        repo = (m.get("releases") or m["repo"]).removeprefix("https://github.com/")
        try:
            rel = gh_api(f"repos/{repo}/releases/latest", accept_404=True)
        except ApiError as e:
            lines.append(f"- `{m['id']}`: could not check {repo}: {e}")
            continue
        if not rel or BAD_TAG_RE.search(rel["tag_name"]):
            continue
        newest = rel["tag_name"].lstrip("vV")
        listed = [v["version"] for v in m["versions"]]
        if newest not in listed:
            lines.append(f"- `{m['id']}`: upstream {repo} has {rel['tag_name']} "
                         f"({rel['published_at'][:10]}); newest listed is {listed[0]}. "
                         f"Run `python tools/add.py {repo}`.")
    text = "\n".join(lines)
    if text:
        print(text)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
