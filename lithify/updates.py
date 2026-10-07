"""Compare the pins in versions.toml with the newest upstream stable releases."""
from __future__ import annotations

import contextlib
import http.client
import os
import re
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import __version__, bundle, hostos
from .device import ROOT

TAG_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)$")
# git with nobody to answer it: a password prompt (a terminal one, or Git Credential Manager's
# window) fails at once instead of waiting forever.
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}


def git(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    """Run git without input and within `timeout` (its helpers too); OSError (no git) or
    TimeoutExpired otherwise."""
    return hostos.run(["git", *args], timeout, env={**os.environ, **GIT_ENV}, text=True, encoding="utf-8",
                      errors="replace")


def head(root: Path = ROOT) -> str:
    """The commit this checkout is at; "" when `root` is not a git checkout itself (git would
    answer for a repository it lies in, say a home directory kept in git) or git cannot tell."""
    if not (root / ".git").exists():
        return ""
    try:
        r = git("-C", str(root), "rev-parse", "HEAD", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def undo_update(root: Path, commit: str) -> tuple[bool, str]:
    """Back to `commit` after an update of Lithify that does not work. `git reset --keep` never
    discards uncommitted changes: where they are in the way it refuses, and nothing changes.
    Returns whether it went back, and git's reason when not."""
    if not (root / ".git").exists():
        return False, f"{root} is not a git checkout"
    try:
        r = git("-C", str(root), "reset", "-q", "--keep", commit, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def _get(url: str, timeout: float = 20.0) -> str:
    if not url.startswith("https://"):  # (fixed public addresses, all of them HTTPS)
        raise ValueError(f"not an https:// address: {url}")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - https:// checked above
            return r.read().decode("utf-8", "replace")
    except http.client.HTTPException as e:  # a cut-off reply counts as no answer
        raise OSError(f"{url}: {e!r}") from None


def latest_librespot(repo: str) -> tuple[str | None, str]:
    tags, source = "", "github"
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        tags = git("ls-remote", "--tags", "--refs", repo).stdout
    mirror = bundle.CACHE / "librespot.git"
    if not tags and mirror.is_dir():
        try:
            tags = git("-C", str(mirror), "tag", timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            tags = ""
        source = "local mirror (GitHub unreachable)"
    found = sorted({tuple(map(int, m.groups())) for m in (TAG_RE.search(line) for line in tags.splitlines()) if m})
    return (f"v{'.'.join(map(str, found[-1]))}" if found else None), source


def librespot_branch(lp: dict, mirror: Path | None = None) -> dict:
    """librespot followed on a branch: the branch's newest commit ("head"), and how many commits
    the one the next build uses is behind it ("behind", counted in the build's local mirror;
    None when that cannot tell)."""
    ref, commit = lp["ref"], lp["commit"]
    newest, source, behind = None, "github", None
    try:
        out = git("ls-remote", lp["repo"], f"refs/heads/{ref}").stdout.split()
        newest = out[0] if out and bundle.COMMIT_RE.fullmatch(out[0]) else None
    except (OSError, subprocess.SubprocessError):
        pass
    mirror = bundle.CACHE / "librespot.git" if mirror is None else mirror
    if not mirror.is_dir():
        return {"head": newest, "behind": behind, "source": source}

    def in_mirror(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
        return git("-C", str(mirror), *args, timeout=timeout)

    try:
        if newest is None:
            r = in_mirror("rev-parse", f"refs/heads/{ref}")
            newest = r.stdout.strip() if r.returncode == 0 else None
            source = "local mirror (GitHub unreachable)"
        elif in_mirror("cat-file", "-e", f"{newest}^{{commit}}").returncode:
            in_mirror("fetch", "-q", "--prune", "origin", timeout=120)  # (only when there is something new)
        if newest:
            r = in_mirror("rev-list", "--count", f"{commit}..{newest}")
            behind = int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else None
    except (OSError, subprocess.SubprocessError):
        pass
    return {"head": newest, "behind": behind, "source": source}


def latest_alsa() -> str | None:
    try:
        page = _get("https://www.alsa-project.org/files/pub/lib/")
    except OSError:
        return None
    vers = {tuple(map(int, v.split("."))) for v in re.findall(r"alsa-lib-(\d+(?:\.\d+)+)\.tar\.bz2", page)}
    return ".".join(map(str, max(vers))) if vers else None


def latest_rust() -> str | None:
    for _ in range(6):  # static.rust-lang.org is behind Fastly, which some ISPs reach only intermittently
        try:
            toml = _get("https://static.rust-lang.org/dist/channel-rust-stable.toml", timeout=10)
        except OSError:
            continue
        m = re.search(r'\[pkg\.rust\]\nversion = "(\d+\.\d+\.\d+)', toml)
        if m:
            return m.group(1)
    return None


def lithify_status(root=ROOT) -> dict:
    """Is this Lithify a git checkout with an upstream, and how many commits behind is it?"""
    if not (root / ".git").exists():
        return {"source": "local copy", "behind": None}
    try:
        git("-C", str(root), "fetch", "-q", timeout=60)
        r = git("-C", str(root), "rev-list", "--count", "HEAD..@{u}", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return {"source": "git", "behind": None}
    behind = int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else None
    return {"source": "git", "behind": behind}


def self_update(root=ROOT) -> bool:
    """`git pull --ff-only` when this checkout has an upstream; True when the code changed. A slow
    or unreachable GitHub never stops a build: that is a warning, and the build goes on with the
    code it has."""
    if not (root / ".git").exists():
        return False
    try:
        if git("-C", str(root), "rev-parse", "--abbrev-ref", "@{u}", timeout=30).returncode:
            return False  # no upstream: nothing to update from
        before = head(root)
        r = git("-C", str(root), "pull", "--ff-only", "-q", timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"warning: Lithify could not update itself ({e}); building with this version", file=sys.stderr)
        return False
    if r.returncode:
        why = (r.stderr or r.stdout).strip().splitlines()
        print(f"warning: Lithify could not update itself ({why[-1] if why else f'git exit code {r.returncode}'}); "
              "building with this version", file=sys.stderr)
        return False
    return head(root) != before


def _status(pinned, latest) -> str:
    if not latest:
        return "unknown"
    return "outdated" if bundle.version_key(latest) > bundle.version_key(pinned) else "current"


def report(deps: bool = True) -> list[dict]:
    """Newest stable upstream versions next to what the next build uses. The sources are asked
    at the same time: the check takes as long as the slowest one, not all of them together."""
    pins = bundle.load_pins()
    lp = pins["librespot"]
    branch = bool(lp.get("commit"))
    with ThreadPoolExecutor(max_workers=5) as pool:
        lib_f = pool.submit(librespot_branch, lp) if branch else pool.submit(latest_librespot, lp["repo"])
        alsa_f, rust_f, me_f = pool.submit(latest_alsa), pool.submit(latest_rust), pool.submit(lithify_status)
        deps_f = pool.submit(bundle.pending_dependency_updates) if deps else None
        lib = lib_f.result()
        alsa, rust, me = alsa_f.result(), rust_f.result(), me_f.result()
        pending = deps_f.result() if deps_f else None
    rows = []
    hint = "\"update everything\" (or `lithify build --latest`) builds it"
    if branch:
        # The branch's newest commit; how many commits it is ahead of what the next build uses.
        head, behind = lib["head"], lib["behind"]
        rows.append({"component": "librespot", "pinned": f"{lp['ref']} {lp['commit'][:7]}",
                     "latest": head and f"{lp['ref']} {head[:7]}", "behind": behind, "source": lib["source"],
                     "status": "unknown" if head is None else "current" if head == lp["commit"] or behind == 0
                     else "outdated", "hint": hint})
    else:
        rows.append({"component": "librespot", "pinned": lp["ref"], "latest": lib[0], "source": lib[1], "hint": hint})
    rows.append({"component": "alsa-lib", "pinned": pins["alsa_lib"]["version"], "latest": alsa,
                 "source": "alsa-project.org", "hint": hint})
    rows.append({"component": "rust", "pinned": pins["rust"]["toolchain"], "latest": rust,
                 "source": "static.rust-lang.org", "hint": hint})
    for r in rows:
        r.setdefault("status", _status(r["pinned"], r["latest"]))
    if deps:
        rows.append({"component": "dependencies", "pinned": "last build",
                     "latest": None if pending is None else f"{len(pending)} newer",
                     "status": "unknown" if pending is None else ("outdated" if pending else "current"),
                     "items": pending or [], "source": "crates.io",
                     "hint": "every new build takes the newest compatible versions"})
    behind = me["behind"]
    rows.append({"component": "lithify", "pinned": __version__,
                 "latest": None if behind is None else (__version__ if behind == 0 else f"{behind} commits newer"),
                 "status": "unknown" if behind is None else ("outdated" if behind else "current"),
                 "source": me["source"], "hint": "\"update everything\" pulls it first"})
    return rows


def format_report(rows: list[dict]) -> str:
    out = []
    for r in rows:
        if r["component"] == "lithify" and r["latest"] is None:
            out.append(f"{'lithify':<12} {r['pinned']} ({r['source']}: no update source)")
            continue
        if r["component"] == "dependencies":
            text = {"current": "all at their newest compatible versions",
                    "outdated": f"{len(r['items'])} newer compatible versions   <- {r['hint']}",
                    "unknown": "could not check (no earlier build, or no network)"}[r["status"]]
            out.append(f"{'dependencies':<12} {text}")
            out.extend(f"{'':<14}{item}" for item in r["items"][:20])
            continue
        tail = {"current": "", "outdated": f"   <- {r['hint']}", "unknown": "   (could not check)"}[r["status"]]
        if "behind" in r:  # followed on a branch
            newer = f" ({r['behind']} newer commits)" if r.get("behind") else ""
            out.append(f"{r['component']:<12} builds {r['pinned']:<12} newest {r['latest'] or '?'}{newer}{tail}")
            continue
        out.append(f"{r['component']:<12} pinned {r['pinned']:<12} latest stable {r['latest'] or '?'}{tail}")
        out.extend(f"{'':<14}{item}" for item in r.get("items", [])[:20])
    return "\n".join(out)
