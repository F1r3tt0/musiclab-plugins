#!/usr/bin/env python3
"""Combine plugins/*.json into index.json (and copy the schema next to it).

    python tools/build_index.py [--out _site]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from common import PLUGINS_DIR, SCHEMA_PATH, SCHEMA_VERSION, load_json


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_site", help="output directory (default _site)")
    args = ap.parse_args()

    plugins = [load_json(p) for p in sorted(PLUGINS_DIR.glob("*.json"))]
    plugins.sort(key=lambda m: m["id"])
    index = {
        "schema_version": SCHEMA_VERSION,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "plugins": plugins,
    }
    out = Path(args.out)
    (out / "schema").mkdir(parents=True, exist_ok=True)
    (out / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    shutil.copyfile(SCHEMA_PATH, out / "schema" / "plugin.schema.json")
    print(f"Wrote {out / 'index.json'} with {len(plugins)} plugin(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
