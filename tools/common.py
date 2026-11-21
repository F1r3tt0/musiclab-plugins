"""Shared helpers for the registry tools. Python 3, standard library only."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGINS_DIR = ROOT / "plugins"
SCHEMA_PATH = ROOT / "schema" / "plugin.schema.json"
REJECTED_PATH = ROOT / "rejected.json"
SCHEMA_VERSION = 1

PLATFORMS = ["windows-x64", "macos-universal", "macos-arm64", "linux-x64", "linux-arm64"]
ARCHIVES = ["zip", "tar.gz", "tar.xz", "dmg", "none"]

# SPDX ids of OSI-approved licences (the ones plugins realistically use, plus the
# common long tail). Extend by pull request.
OSI_LICENSES = frozenset("""
0BSD AAL AFL-3.0 AGPL-3.0-only AGPL-3.0-or-later Apache-1.1 Apache-2.0 APSL-2.0
Artistic-2.0 BlueOak-1.0.0 BSD-1-Clause BSD-2-Clause BSD-2-Clause-Patent BSD-3-Clause
BSD-3-Clause-LBNL BSL-1.0 CAL-1.0 CATOSL-1.1 CDDL-1.0 CECILL-2.1 CPAL-1.0 CPL-1.0
ECL-2.0 EFL-2.0 EPL-1.0 EPL-2.0 EUPL-1.1 EUPL-1.2 GPL-2.0-only GPL-2.0-or-later
GPL-3.0-only GPL-3.0-or-later ISC LGPL-2.0-only LGPL-2.0-or-later LGPL-2.1-only
LGPL-2.1-or-later LGPL-3.0-only LGPL-3.0-or-later LiLiQ-P-1.1 LiLiQ-R-1.1
LiLiQ-Rplus-1.1 LPL-1.02 LPPL-1.3c MirOS MIT MIT-0 MPL-1.1 MPL-2.0 MS-PL MS-RL
MulanPSL-2.0 NCSA Nokia NPOSL-3.0 OFL-1.1 OSL-3.0 PostgreSQL Python-2.0 QPL-1.0
RPL-1.5 SimPL-2.0 Sleepycat UPL-1.0 Unlicense Unicode-3.0 VSL-1.0 W3C Zlib ZPL-2.0
GPL-2.0 GPL-3.0 LGPL-2.1 LGPL-3.0 AGPL-3.0
""".split())

BAD_TAG_RE = re.compile(
    r"(nightly|latest|(^|[-_.])(alpha|beta|rc\d*|pre|preview|dev|snapshot|canary)([-_.\d]|$))", re.I)
INSTALLER_RE = re.compile(r"\.(exe|msi|pkg|deb|rpm|appimage|apk|ipa)$", re.I)
GH_RELEASE_URL_RE = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/releases/download/([^/]+)/([^/?#]+)$"
)
USER_AGENT = "musiclab-plugins-tools"


def load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def archive_kind(name: str) -> str | None:
    n = name.lower()
    if n.endswith(".zip"):
        return "zip"
    if n.endswith(".tar.gz") or n.endswith(".tgz"):
        return "tar.gz"
    if n.endswith(".tar.xz"):
        return "tar.xz"
    if n.endswith(".dmg"):
        return "dmg"
    return None


def guess_platform(name: str) -> str | None:
    """Platform key for a release asset name, or None when it is not one we support."""
    n = re.sub(r"[^a-z0-9]+", " ", name.lower())
    toks = set(n.split())
    arm = any(t.startswith(("arm", "aarch")) for t in toks) or "apple silicon" in n
    if re.search(r"\b(armhf|riscv64|i686|i386|x86(?! 64)|win32|wasm|ppc64le)\b", n):
        return None
    if toks & {"win", "win64", "windows", "w64", "msvc"}:
        return None if arm else "windows-x64"
    if toks & {"mac", "macos", "osx", "darwin", "macosx"}:
        if arm:
            return "macos-arm64"
        if "x64" in toks or "x86 64" in n or "intel" in toks:
            return None
        return "macos-universal"
    if toks & {"linux", "lnx", "ubuntu"}:
        return "linux-arm64" if arm else "linux-x64"
    return None


def candidate_assets(assets: list[dict]) -> dict[str, list[dict]]:
    """Group a release's archive assets by platform; installers and sources are dropped."""
    out: dict[str, list[dict]] = {}
    for a in assets:
        name = a["name"]
        low = name.lower()
        if INSTALLER_RE.search(low) or archive_kind(name) is None:
            continue
        if re.search(r"(source|[-_.]src[-_.]|setup|install|wasm|android|symbols|dsyms?|debug|docs?[-_.])", low):
            continue
        plat = guess_platform(name)
        if plat:
            out.setdefault(plat, []).append(a)
    return out


def asset_score(name: str) -> int:
    n = name.lower()
    s = -2 if n.endswith(".dmg") else 0  # prefer archives we can list and unpack
    for good in ("vst3", "clap", "plugin"):
        if good in n:
            s += 3
    for bad in ("standalone", "portable", "content", "deps", "doc", "manual", "preset"):
        if bad in n:
            s -= 4
    return s


# ---------------------------------------------------------------- GitHub API

_last_search = 0.0


class ApiError(Exception):
    pass


def token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None


def gh_api(path: str, params: dict | None = None, search: bool = False, accept_404: bool = False):
    """GET https://api.github.com/<path>. Waits politely on rate limits; raises ApiError."""
    global _last_search
    url = "https://api.github.com/" + path.lstrip("/")
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT,
               "X-GitHub-Api-Version": "2022-11-28"}
    if token():
        headers["Authorization"] = "Bearer " + token()
    if search:  # search allows ~30/min with a token, 10/min without
        gap = 2.3 if token() else 6.5
        wait = _last_search + gap - time.time()
        if wait > 0:
            time.sleep(wait)
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                if search:
                    _last_search = time.time()
                data = json.load(r)
                if r.headers.get("X-RateLimit-Remaining") == "0":
                    _sleep_until_reset(r.headers.get("X-RateLimit-Reset"))
                return data
        except urllib.error.HTTPError as e:
            if search:
                _last_search = time.time()
            if e.code == 404 and accept_404:
                return None
            if e.code in (403, 429):
                retry = e.headers.get("Retry-After")
                if retry:
                    _bounded_sleep(float(retry) + 1)
                    continue
                if e.headers.get("X-RateLimit-Remaining") == "0":
                    _sleep_until_reset(e.headers.get("X-RateLimit-Reset"))
                    continue
                if attempt < 2:  # secondary limit without headers
                    _bounded_sleep(30)
                    continue
            raise ApiError(f"{e.code} for {url}: {e.reason}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == 3:
                raise ApiError(f"network error for {url}: {e}") from e
            time.sleep(3)
    raise ApiError(f"gave up on {url}")


def _bounded_sleep(sec: float):
    if sec > 120:
        raise ApiError(f"rate limit: would have to wait {int(sec)}s; set GITHUB_TOKEN or retry later")
    print(f"  (rate limit, waiting {int(sec)}s)", file=sys.stderr)
    time.sleep(sec)


def _sleep_until_reset(reset: str | None):
    if reset:
        wait = int(reset) - time.time() + 2
        if wait > 0:
            _bounded_sleep(wait)


# ------------------------------------------------------------------ download

def download_and_hash(url: str, max_bytes: int = 600 * 1024 * 1024, list_names: bool = False):
    """Download url, return (size, sha256_hex, names). Never extracts or runs anything.

    names is the archive's file listing (zip / tar only) when list_names is set.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    h = hashlib.sha256()
    size = 0
    with tempfile.TemporaryDirectory(prefix="mlplug-") as tmp:
        dest = Path(tmp) / "asset.bin"
        with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise ApiError(f"asset larger than {max_bytes} bytes")
                h.update(chunk)
                f.write(chunk)
        names = _list_archive(dest, url) if list_names else None
    return size, h.hexdigest(), names


def _list_archive(path: Path, url: str) -> list[str] | None:
    low = url.lower()
    try:
        if low.endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                return z.namelist()
        if low.endswith((".tar.gz", ".tgz", ".tar.xz")):
            with tarfile.open(path) as t:
                return [n[2:] if n.startswith("./") else n for n in t.getnames()]
    except (zipfile.BadZipFile, tarfile.TarError, OSError):
        return None
    return None


def bundle_roots(names: list[str]) -> list[tuple[str, str]]:
    """(path, format) for every .vst3 / .clap bundle found in an archive listing."""
    found: dict[str, str] = {}
    for n in names:
        parts = n.strip("/").split("/")
        if parts[0] == "__MACOSX":
            continue
        for i, p in enumerate(parts):
            pl = p.lower()
            if pl.endswith(".vst3") or pl.endswith(".clap"):
                found["/".join(parts[: i + 1])] = "vst3" if pl.endswith(".vst3") else "clap"
                break
    return sorted(found.items())
