# Contributing

Thank you for helping to list good free plugins. By contributing you agree that your changes are released under CC0 1.0, like the rest of the repository.

Read the inclusion rules in [README.md](README.md) first.

## Workflow: discover, review, add, PR

1. **Discover.** `python tools/discover.py` searches GitHub (topics `vst3`, `clap`, `audio-plugin`, `juce-plugin`, ...; plus code search when `GITHUB_TOKEN` is set) and prints a table of repositories that have an OSI licence, a stable release in the last three years with plugin-looking zip / tar / dmg assets, and are not yet listed or rejected. It writes `candidates.json` and, with `--verbose`, says why each other repo was rejected. It never downloads or runs assets. A weekly workflow does the same and keeps one issue, "New plugin candidates".
2. **Review.** Open the repository. Check the licence file, that the release really has VST3/CLAP builds, and that it is a plugin (not a host or framework). If it does not belong, add it to [`rejected.json`](rejected.json) with a one-line reason so it stops coming back.
3. **Add.** `python tools/add.py owner/repo` drafts `plugins/<id>.json` from the latest stable release (downloading the assets only to hash them), then you fill in `kind`, `tags`, `provides` and tidy `description`.
4. **Validate.** `python tools/validate.py --verify`.
5. **Pull request.** One plugin per PR. CI validates and re-downloads the assets you changed.

Set `GITHUB_TOKEN` (any token, no scopes needed) when running the tools; unauthenticated GitHub API use is limited to 60 requests an hour.

## Updating a plugin

Run `python tools/add.py owner/repo` again: the new release is added as the newest entry of `versions`. Keep older versions unless their assets are gone. The weekly issue lists plugins with a newer upstream release.

## Manifest rules

- File `plugins/<id>.json`; `id` is lowercase letters, digits and dashes and equals the file name.
- `description` is at most 200 characters, plain text.
- `license` is an SPDX id from the OSI list in `tools/common.py`. If a licence is OSI-approved but missing from the list, add it in the same PR.
- `repo` is where the source lives; `releases` is only needed when binaries are published from a different repository.
- Asset URLs are `https://github.com/<owner>/<repo>/releases/download/<tag>/<file>`, on `repo` or `releases`.
- No installers, no prereleases, no `nightly` / `latest`.
- `provides` lists the names a host scan reports for the plugin, so a song missing one can offer the install.
- Prefer one asset per platform that contains only the plugin bundles. Skip a platform rather than list an installer or a `.dmg` you cannot check.
- `sha256` and `size` must match the file exactly; `validate.py --verify` checks it.

## Removing a plugin

Open a PR deleting the manifest (and say why), for example when the project goes closed source or its releases are removed.

## Tools

All Python 3 with the standard library, apart from `jsonschema` (`pip install -r requirements.txt`).

| Tool | Purpose |
|---|---|
| `tools/validate.py` | schema and rules; `--verify` downloads and checks assets; `--base REF` limits that to manifests changed since REF |
| `tools/add.py` | draft or update a manifest from a repo's latest stable release |
| `tools/discover.py` | find candidate repos |
| `tools/check_updates.py` | list newer upstream releases |
| `tools/build_index.py` | build `index.json` into `_site/` |

Downloaded assets are only hashed and their file lists read. Never run or unpack them as part of this workflow.
