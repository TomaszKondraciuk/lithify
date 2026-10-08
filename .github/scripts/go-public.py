#!/usr/bin/env python3
"""Make Lithify's files name its public repository: once, before the first release.

    python3 .github/scripts/go-public.py <owner>/<repository>

The launchers, the installers and the READMEs download from the repository, which they call
OWNER/lithify until then; versions.toml gets the address of the releases' bundle, which every
install then downloads instead of building it. Nothing else changes, byte for byte (install.ps1
keeps its BOM, Lithify-Windows.cmd its CRLF line endings). See docs/releasing.md.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PLACEHOLDER = b"OWNER/lithify"
FILES = ("Lithify-Windows.cmd", "Lithify-macOS.command", "Lithify-Linux.sh", "installer/install.ps1",
         "installer/install.sh", "installer/get.ps1", "README.md", "docs/*.md", "docs/pl/*.md")
RELEASE_URL = re.compile(rb'(?ms)^(\[release\][^\[]*?^url\s*=\s*)"[^"]*"')


def go_public(root: Path, repo: str) -> list[str]:
    """The files changed (relative to root)."""
    changed = []
    for pattern in FILES:
        for path in sorted(root.glob(pattern)):
            data = path.read_bytes()
            new = data.replace(PLACEHOLDER, repo.encode())
            if new != data:
                path.write_bytes(new)
                changed.append(path.relative_to(root).as_posix())
    versions = root / "versions.toml"
    data = versions.read_bytes()
    url = f"https://github.com/{repo}/releases/latest/download".encode()
    new, n = RELEASE_URL.subn(lambda m: m[1] + b'"' + url + b'"', data, count=1)
    if n != 1:
        raise SystemExit("versions.toml has no [release] url = \"...\" line")
    if new != data:
        versions.write_bytes(new)
        changed.append("versions.toml")
    return changed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("repo", help="the repository on GitHub, e.g. alice/lithify")
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2], help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}", a.repo):
        p.error(f"{a.repo!r} is not owner/repository")
    changed = go_public(a.root, a.repo)
    for f in changed:
        print(f"changed {f}")
    if not changed:
        print("nothing to change: the files name a repository already")
    print("next: commit and push this, make the repository public, then push a tag (git tag v0.1.0 && "
          "git push origin v0.1.0): the release workflow builds and publishes the bundle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
