# Music Lab plugin registry

A list of free, open-source audio plugins (VST3 and CLAP) that Music Lab can offer to install from its **Get Plug-ins** window, or when a song needs a plugin the computer does not have.

The list is plain JSON: one manifest per plugin in [`plugins/`](plugins), combined into a single [`index.json`](https://f1r3tt0.github.io/musiclab-plugins/index.json) that is published to GitHub Pages on every change to `main`.

- Index: <https://f1r3tt0.github.io/musiclab-plugins/index.json>
- Manifest schema: [`schema/plugin.schema.json`](schema/plugin.schema.json)

This repository lists plugins; it does not host them. Every download link points at the plugin's own GitHub release.

## Inclusion rules

A plugin can be listed when all of these hold:

1. **Open source, OSI-approved licence.** `license` is the SPDX id of a licence on the OSI list (see `OSI_LICENSES` in [`tools/common.py`](tools/common.py)). Free to download but closed source is not enough.
2. **Plain archives from GitHub releases.** Each asset is a `.zip`, `.tar.gz`, `.tar.xz` or `.dmg` hosted on a GitHub release. Installers (`.exe`, `.msi`, `.pkg`, `.deb`, `.rpm`) are never accepted, because Music Lab never runs installers; it only unpacks a bundle and copies it into a plug-in folder.
3. **Stable releases only.** No prereleases, drafts, or moving tags such as `nightly` or `latest`. Each version points at a fixed, tagged release.
4. **A real asset for at least one platform.** Platform keys: `windows-x64`, `macos-universal`, `macos-arm64`, `linux-x64`, `linux-arm64`.
5. **VST3 and/or CLAP.** `formats` and every bundle say which.
6. **Maintained enough to be useful:** a release in the last three years, not archived.
7. **Reasonable size.** Archives over about 600 MB are not listed.

## How to add a plugin

```sh
pip install -r requirements.txt
python tools/add.py owner/repo      # drafts plugins/<id>.json from the latest stable release
```

`add.py` uses the GitHub API (set `GITHUB_TOKEN` to avoid the low anonymous rate limit), picks the plugin archive for each platform, downloads it only to compute `size` and `sha256` and to read the archive's file list, and writes the draft. Nothing is extracted or run.

Then **review the draft by hand**; `kind`, `tags` and `provides` are left for you, and `validate.py` fails until they are filled in:

- is each chosen asset the right one for its platform, and does `bundles` list the right `.vst3` / `.clap` paths?
- is `license` correct (check the repository's licence file, not only the badge)?
- `provides`: the plugin names a host scan reports (usually the bundle name without the extension).

```sh
python tools/validate.py            # schema and rules
python tools/validate.py --verify   # also re-downloads and checks size and sha256
```

Open a pull request. CI runs the same checks and re-downloads the assets you changed. See [CONTRIBUTING.md](CONTRIBUTING.md) for the details, including finding candidates with `tools/discover.py`.

## Manifest format

```json
{
  "id": "surge-xt",
  "name": "Surge XT",
  "developer": "Surge Synth Team",
  "description": "At most 200 characters.",
  "homepage": "https://example.org/",
  "repo": "https://github.com/owner/source-repo",
  "releases": "https://github.com/owner/releases-repo",
  "license": "GPL-3.0-or-later",
  "kind": "instrument",
  "tags": ["synth"],
  "formats": ["vst3", "clap"],
  "provides": ["Surge XT", "Surge XT Effects"],
  "notes": "Optional.",
  "versions": [
    {
      "version": "1.3.4",
      "date": "2024-08-11",
      "assets": {
        "windows-x64": {
          "url": "https://github.com/owner/releases-repo/releases/download/1.3.4/plugin-win64.zip",
          "sha256": "64 hex digits",
          "size": 49093875,
          "archive": "zip",
          "inner": "optional folder inside the archive",
          "bundles": [{ "path": "Surge XT.vst3", "format": "vst3" }]
        }
      }
    }
  ]
}
```

`kind` is `instrument`, `effect`, `midi` or `bundle`. `versions` are newest first. `bundles[].path` is relative to the archive root (or to `inner` when set), with forward slashes; a bundle is a `.vst3` or `.clap` file or folder.

## What Music Lab does with it

Music Lab downloads `index.json`, shows the plugins that have an asset for the current platform, and installs a plugin by downloading the asset, **checking its size and sha256 before unpacking anything**, and only then copying the listed bundles into the user's plug-in folder. A hash or size mismatch aborts the install. It never runs anything from the archive.

## Automation

| Workflow | When | What |
|---|---|---|
| `pr.yml` | pull requests | schema and rules, then downloads and verifies the changed manifests' assets |
| `pages.yml` | push to `main` | builds `index.json` and publishes it to GitHub Pages |
| `weekly-verify.yml` | weekly | re-verifies every asset; opens an issue for failures and newer upstream releases |
| `discover.yml` | weekly | searches GitHub for new candidates; keeps one issue, "New plugin candidates" |

## Licence

The registry data and tools in this repository are released under **CC0 1.0** ([LICENSE](LICENSE)). Each listed plugin keeps its own licence, given in its manifest's `license` field; CC0 here does not change it.
