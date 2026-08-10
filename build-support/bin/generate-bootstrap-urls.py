# Copyright 2026 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).
#
# From the Pants repo root, run: `python3 build-support/bin/generate-bootstrap-urls.py`
#
# Scans all GitHub releases whose tag or name contains "vertice", collects their `.pex`
# release assets, and writes `bootstrap-urls.json` at the repo root. That file is consumed
# by the scie-pants launcher via the `PANTS_BOOTSTRAP_URLS` env var: it maps the pex filename
# the launcher computes from the *running* Pants version (which includes the `+vertice.N`
# local version segment) to the real download URL of the matching release asset.
#
# The freshly-scanned URLs are *merged into* the existing `bootstrap-urls.json` (whatever is
# already checked out at the repo root, i.e. main's copy) rather than overriding it wholesale.
# This means entries recorded by earlier runs are never dropped if a release stops showing up
# in `gh release list` (deleted, beyond the query limit, or a transient scan failure); the
# freshly-scanned URL wins on conflict for any key present in both.
#

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Final, TypedDict

VERSION_MARKER: Final[Path] = Path("src/python/pants/VERSION")
OUTPUT_PATH: Final[Path] = Path("bootstrap-urls.json")

# Matches a pex asset name like `pants.2.32.0-cp314-darwin_arm64.pex`, capturing the
# `-cp314-darwin_arm64.pex` suffix (python tag + platform + extension) separately from the
# version, since the version baked into the asset name may not match the release tag's version
# (e.g. a release tag can be cut from a different underlying build).
ASSET_MATCHER: Final[re.Pattern] = re.compile(r"^pants\.[^-]+(?P<suffix>-cp\d+[a-z]*-.+\.pex)$")


class GithubTaggedRelease(TypedDict):
    tagName: str
    name: str


class GithubReleaseAsset(TypedDict):
    name: str
    url: str


def current_repo() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo:
        return repo
    result = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def list_vertice_releases(repo: str) -> list[GithubTaggedRelease]:
    """Uses `gh` to list all releases whose tag or name mentions "vertice"."""
    result = subprocess.run(
        [
            "gh",
            "release",
            "list",
            "--repo",
            repo,
            "--json",
            "tagName,name",
            "--limit",
            "1000",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    releases: list[GithubTaggedRelease] = json.loads(result.stdout)
    return [r for r in releases if "vertice" in r["tagName"].lower() or "vertice" in r["name"].lower()]


def list_pex_assets(repo: str, tag: str) -> list[GithubReleaseAsset]:
    """Uses `gh` to get all `.pex` assets for a release."""
    result = subprocess.run(
        [
            "gh",
            "release",
            "view",
            tag,
            "--repo",
            repo,
            "--json",
            "assets",
            "--jq",
            '[.assets[] | select(.name|endswith(".pex")) | {name, url}]',
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def load_existing_ptex() -> dict[str, str]:
    """Reads the `ptex` mapping from the current `bootstrap-urls.json`, if any."""
    if not OUTPUT_PATH.exists():
        return {}
    data = json.loads(OUTPUT_PATH.read_text())
    return dict(data.get("ptex", {}))


def main() -> None:
    if not VERSION_MARKER.exists():
        raise FileNotFoundError(
            "This helper script must be run from the root of the Pants repository."
        )

    repo = current_repo()
    print(f"Scanning {repo} for vertice releases")

    releases = list_vertice_releases(repo)
    print(f"Found {len(releases)} vertice release(s): {[r['tagName'] for r in releases]}")

    scanned_ptex: dict[str, str] = {}
    for release in releases:
        tag = release["tagName"]
        full_version = tag.removeprefix("release_")
        print(f"\nFetching pex assets for release: {tag}")

        for asset in list_pex_assets(repo, tag):
            asset_name = asset["name"]
            match = ASSET_MATCHER.match(asset_name)
            if not match:
                print(f"    Skipping (unrecognized name): {asset_name}")
                continue

            key = f"pants.{full_version}{match['suffix']}"
            print(f"    {key} -> {asset['url']}")
            scanned_ptex[key] = asset["url"]

    # Merge the freshly-scanned URLs into the existing file rather than overriding it, so
    # previously-recorded entries survive even if their release no longer shows up in the scan.
    # Freshly-scanned URLs win on conflict.
    existing_ptex = load_existing_ptex()
    ptex = {**existing_ptex, **scanned_ptex}
    print(
        f"\nMerged {len(scanned_ptex)} scanned URL(s) into {len(existing_ptex)} existing "
        f"entry/entries -> {len(ptex)} total"
    )

    if not ptex:
        print("\nNo vertice pex assets found - nothing to write.")
        sys.exit(0)

    print(f"\nWriting {len(ptex)} bootstrap URL(s) to {OUTPUT_PATH}")
    OUTPUT_PATH.write_text(json.dumps({"ptex": ptex}, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
