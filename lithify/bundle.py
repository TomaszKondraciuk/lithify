"""Build the speaker bundle (Docker cross-build) and stage it for one speaker."""
from __future__ import annotations

import contextlib
import hashlib
import http.client
import http.server
import itertools
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import tomllib
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from . import __version__, config, console, hostos, platforms
from .device import ROOT

CACHE = hostos.cache_dir()
TARGET = "armv7-unknown-linux-musleabihf"
BUNDLE_FILES = ["librespot", "lithify-agent", "alsa.tar", "VERSIONS"]
BINARIES = ["librespot", "lithify-agent", "alsa.tar"]   # what makes a new version
KEY_CRATES = ["rustls", "hyper", "h2", "tokio", "symphonia", "libmdns", "webpki-roots"]
# Components `lithify build --latest` may move to a newer stable release than versions.toml pins.
PINNABLE = {"librespot": "ref", "alsa_lib": "version", "rust": "toolchain"}
USER_PINS = Path(os.environ.get("LITHIFY_PINS") or hostos.config_dir() / "versions.toml")
# Build caches live in Docker volumes, not on this computer's disk: fast with Docker Desktop too
# (its shared folders are slow on Windows and macOS), and none of the build's many files has to
# suit the host's file system (long paths, names that differ only in case, owners).
CARGO_VOLUME = "lithify-cargo"
AGENT_TARGET_VOLUME = "lithify-target-agent"
TARGET_VOLUME_PREFIX = "lithify-target-"


# librespot's release profile for the speaker: code for its Cortex-A7 with NEON, the whole program
# optimised at once (fat LTO, one codegen unit: slower to build, smaller and faster to run).
LIBRESPOT_ENV = ("RUSTFLAGS=-C target-cpu=cortex-a7 -C target-feature=+neon", "CARGO_PROFILE_RELEASE_STRIP=symbols",
                 "CARGO_PROFILE_RELEASE_LTO=fat", "CARGO_PROFILE_RELEASE_CODEGEN_UNITS=1")
# Fat LTO (one optimisation over librespot and all its crates) makes the smallest binary, but its
# last step holds 2.2 GB at once: a computer with 4 GB and a desktop runs out (the compiler is
# killed, after the desktop froze). Thin LTO holds 1.2 GB, for a binary 14% larger (librespot 0.8,
# measured 2026-10).
LIBRESPOT_LEAN_ENV = tuple(e.replace("LTO=fat", "LTO=thin") for e in LIBRESPOT_ENV)
FAT_LTO_MEMORY = 3 * 1024 ** 3  # free memory (swap included) below which a build starts with thin LTO
_OUT_OF_MEMORY = re.compile(r"signal: 9, SIGKILL|out of memory|cannot allocate memory|"
                            r"memory allocation of \d+ bytes failed", re.IGNORECASE)


# Quick commands (versions, inspect, git) get this long; long build steps pass their own limits.
DEFAULT_TIMEOUT = 300
_containers = itertools.count()
# Every container a build starts carries this label, valued "<computer>:<pid>" of the process
# that runs it: `lithify serve` removes the ones whose process is gone.
BUILD_LABEL = "lithify-build"
# The commands running now and their containers, so that a build which must end can stop them.
_running: dict[subprocess.Popen, str | None] = {}
_running_lock = threading.Lock()
_stopping = threading.Event()


class BuildError(RuntimeError):
    pass


class LockTimeoutError(BuildError):
    """A lock still held elsewhere when the wait for it ran out (worth trying again soon)."""


@contextlib.contextmanager
def _lock(path: Path, shared: bool = False, wait: bool = True, busy: str = "", timeout: float | None = 60.0):
    """A lock on `path` for the block, between processes too (the CLI and the companion). Only
    taking the lock is translated into BuildError; what the block raises passes unchanged."""
    with contextlib.ExitStack() as held:
        try:
            held.enter_context(hostos.file_lock(path, shared=shared, wait=wait, timeout=timeout))
        except BlockingIOError:
            raise BuildError(busy or f"{path} is in use") from None
        except TimeoutError:
            raise LockTimeoutError(busy or f"{path.name} is still in use after {timeout:g} s (a build replacing "
                                      "the bundle); try again in a moment") from None
        yield


def bundle_lock(cache: Path, shared: bool = True, timeout: float | None = 60.0):
    """Held while the bundle is read (staging) or replaced, so a stage never mixes two builds."""
    return _lock(cache / "bundle.lock", shared=shared, timeout=timeout)


def say(msg: str) -> None:
    print(f"==> {msg}", flush=True)


# How far a long command has come, as an indented line for the person waiting: `lithify install`
# prints it, and the wizard moves its progress bar by it (wizard.Task.add).
Progress = Callable[[str], str | None]
_DOCKER_STEP = re.compile(r"#\d+ \[(?:[^\]]* )?(\d+)/(\d+)\] |Step (\d+)/(\d+) : ")
# BuildKit's progress of a layer it downloads: "#4 sha256:a293... 84.93MB / 266.07MB 81.7s"
_DOCKER_LAYER = re.compile(r"#\d+ sha256:([0-9a-f]{12})[0-9a-f]* ([\d.]+)([kMG]?B) / [\d.]+[kMG]?B")
# After its last step BuildKit saves and unpacks the image: minutes on a slow disk
_DOCKER_SAVE = re.compile(r"#\d+ exporting to image")
_UNITS = {"B": 1, "kB": 1e3, "MB": 1e6, "GB": 1e9}
_COMPILING = re.compile(r"\s+Compiling \S+ v")
# The last crate: librespot itself, which cargo compiles and then links, optimizing the whole
# program at once (LTO): minutes without a line.
_LAST_CRATE = re.compile(r"\s+Compiling librespot v")
LAST_CRATE_NOTE = "librespot itself: the last step, several minutes without output"
DOWNLOAD_NOTE_MB = 50  # a note every 50 MB of a download: the base image of a first build is about 800 MB


def docker_steps() -> Progress:
    """The steps of `docker build` as they begin (BuildKit's plain output, or the classic builder's),
    and how much of its base image it has downloaded (the longest part of a first build there)."""
    seen: set[tuple[str, str] | str] = set()
    layers: dict[str, float] = {}
    told = 0

    def note(line: str) -> str | None:
        nonlocal told
        if layer := _DOCKER_LAYER.match(line):
            layers[layer[1]] = max(layers.get(layer[1], 0.0), float(layer[2]) * _UNITS[layer[3]])
            mb = int(sum(layers.values()) // 1e6)
            if mb // DOWNLOAD_NOTE_MB > told // DOWNLOAD_NOTE_MB:
                told = mb
                return f"builder image: downloaded {mb} MB"
            return None
        if _DOCKER_SAVE.match(line):
            if "save" in seen:
                return None
            seen.add("save")
            return "builder image: saving it"
        m = _DOCKER_STEP.match(line)
        step = (m[1] or m[3], m[2] or m[4]) if m else None
        if step is None or step in seen:
            return None
        seen.add(step)
        return f"builder image: step {step[0]} of {step[1]}"
    return note


def crates_compiled(every: int = 10) -> Progress:
    """Every `every` crates cargo begins to compile, and the last one (librespot itself)."""
    count = 0

    def note(line: str) -> str | None:
        nonlocal count
        if not _COMPILING.match(line):
            return None
        count += 1
        if _LAST_CRATE.match(line):
            return LAST_CRATE_NOTE
        return f"crates compiled: {count}" if count % every == 0 else None
    return note


def version_key(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


COMMIT_RE = re.compile(r"[0-9a-f]{40}")


def load_pins(root: Path = ROOT, user: Path | None = None, chosen: dict | None = None) -> dict:
    """versions.toml, with newer versions chosen by `lithify build --latest` (user file) on top;
    an older choice there never holds back what versions.toml pins. librespot followed on a
    branch (a `commit` in versions.toml) takes the commit chosen there; whether that one is
    newer is told by the history, in build(). `chosen`: versions about to be tried, taken as if
    they were the user file (which gets them only once a bundle built with them is in place)."""
    pins = tomllib.loads((root / "versions.toml").read_text(encoding="utf-8"))
    lp = pins["librespot"]
    lp["pinned_ref"] = lp["ref"]
    lp.setdefault("backports", [])
    if lp.get("commit"):
        lp["pinned_commit"] = lp["commit"]
    if chosen is not None:
        choice = tomllib.loads(_choices_text(chosen))
    else:
        user = USER_PINS if user is None else user
        choice = {}
        if user.is_file():
            try:
                choice = tomllib.loads(user.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:  # a damaged file must not stop every build
                print(f"warning: ignoring {user}: {e}", file=sys.stderr)
    for section, key in PINNABLE.items():
        table = choice.get(section)
        if not isinstance(table, dict):
            continue
        if section == "librespot" and lp.get("commit"):
            c = table.get("commit")
            if isinstance(c, str) and COMMIT_RE.fullmatch(c):
                lp["commit"] = c
            continue  # (a release chosen before librespot was followed on a branch: ignored)
        v = table.get(key)
        if isinstance(v, str) and v and version_key(v) > version_key(pins[section][key]):
            pins[section][key] = v
    return pins


def _choices_text(values: dict) -> str:
    """The user file for the versions `--latest` chose; librespot's is a commit when it is followed
    on a branch."""
    key_of = lambda section, key: "commit" if COMMIT_RE.fullmatch(values[section]) else key  # noqa: E731
    body = "".join(f'[{section}]\n{key_of(section, key)} = "{values[section]}"\n\n' for section, key in PINNABLE.items()
                   if values.get(section))
    return ("# Newer stable versions chosen by `lithify build --latest` (\"update everything\" on the\n"
            "# speaker's web page). Versions older than versions.toml are ignored; delete this file\n"
            "# to build exactly what versions.toml pins.\n\n" + body)


def write_user_pins(values: dict, user: Path | None = None) -> None:
    """Remember the versions `--latest` chose (and built) for the builds after this one."""
    hostos.write_atomic(USER_PINS if user is None else user, _choices_text(values))


def bump_to_latest(root: Path = ROOT, user: Path | None = None) -> tuple[list[str], dict | None]:
    """Choose the newest stable alsa-lib and Rust, and librespot's newest release, or the
    newest commit of its branch when it is followed on one. Returns what changed and the
    versions to build (None: nothing newer). Nothing is written here: build() remembers them
    once a bundle built with them is in place."""
    from . import updates  # (imports this module)
    user = USER_PINS if user is None else user
    pins = load_pins(root, user)
    lp = pins["librespot"]
    branch = bool(lp.get("commit"))
    current = {section: pins[section][key] for section, key in PINNABLE.items()}
    if branch:
        current["librespot"] = lp["commit"]
    newest = {"librespot": updates.librespot_branch(lp)["head"] if branch else updates.latest_librespot(lp["repo"])[0],
              "alsa_lib": updates.latest_alsa(), "rust": updates.latest_rust()}

    def is_newer(section: str, v: str | None) -> bool:
        if not v:
            return False
        if section == "librespot" and branch:
            return v != current[section]  # a branch only moves forward
        return version_key(v) > version_key(current[section])

    newer = {s: v for s, v in newest.items() if is_newer(s, v)}
    label = lambda s, v: f"{lp['ref']} {v[:7]}" if s == "librespot" and branch else v  # noqa: E731
    changes = [f"{s} {label(s, current[s])} -> {label(s, v)}" for s, v in newer.items()]
    return changes, ({**current, **newer} if newer else None)


def image_tag(pins: dict) -> str:
    return f"lithify-builder:alsa-{pins['alsa_lib']['version']}-rust-{pins['rust']['toolchain']}"


def sh(cmd: list, log: Path | None = None, check: bool = True, timeout: float | None = None,
       env: dict | None = None, progress: Progress | None = None) -> subprocess.CompletedProcess:
    """Run a command. With `log`, its output goes there line by line as it comes (the web page
    shows the log's tail during a long compile), and `progress` picks from it the lines printed
    for the person waiting. A command still running after `timeout` is
    stopped together with everything it started and its container; failures are BuildError
    (with `check`). Nothing here waits for a person: no input, and git never asks for a password."""
    cmd = [str(c) for c in cmd]
    limit = DEFAULT_TIMEOUT if timeout is None else timeout
    container = None
    if cmd[:2] == ["docker", "run"]:
        container = f"lithify-{os.getpid()}-{next(_containers)}"
        cmd = [*cmd[:2], "--name", container, "--label", f"{BUILD_LABEL}={_owner()}", *cmd[2:]]
    full_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}
    try:
        r = (_run_logged(cmd, log, limit, full_env, container, progress) if log
             else _run_captured(cmd, limit, full_env, container))
    except subprocess.TimeoutExpired:
        _stop(container)
        if check:
            raise BuildError(f"{' '.join(cmd[:6])} ... did not finish within {int(limit)} s") from None
        return subprocess.CompletedProcess(cmd, 124, "", f"timed out after {int(limit)} s")
    except OSError as e:  # it ran, but its output could not be kept (build.log on a full disk)
        _stop(container)
        raise BuildError(f"{' '.join(cmd[:6])} ... was stopped: {e}") from None
    except BaseException:
        _stop(container)  # Ctrl-C, or the companion stopping: the container must not run on
        raise
    if check and r.returncode != 0:
        # (137: the container's command was killed, which the system does when memory runs out)
        killed = "\n(killed: signal: 9, SIGKILL)" if r.returncode in (137, -9) else ""
        raise BuildError(f"{' '.join(cmd[:6])} ... failed:\n{(r.stdout + r.stderr)[-3000:]}{killed}")
    return r


def _owner() -> str:
    """Who runs a container: this computer and process (see remove_stale_containers)."""
    return f"{socket.gethostname()}:{os.getpid()}"


def _start(cmd: list[str], env: dict, container: str | None, **kw) -> subprocess.Popen:
    """Start `cmd` in a process group of its own (so a timeout ends everything it started) and
    keep it in _running until it is done."""
    with _running_lock:
        if _stopping.is_set():
            raise BuildError("the build is being stopped")
        try:
            p = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                 errors="replace", **hostos.own_group(), **kw)
        except OSError as e:
            raise BuildError(f"cannot run {cmd[0]}: {e}") from None
        _running[p] = container
    return p


def _done(p: subprocess.Popen) -> None:
    with _running_lock:
        _running.pop(p, None)


def _run_captured(cmd: list[str], limit: float, env: dict, container: str | None) -> subprocess.CompletedProcess:
    p = _start(cmd, env, container, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = p.communicate(timeout=limit)
    except BaseException:  # the time is up, Ctrl-C, or the build is being stopped
        hostos.kill_tree(p)
        with contextlib.suppress(Exception):
            p.communicate(timeout=10)  # (what is left in the pipes; they close with the group)
        raise
    finally:
        _done(p)
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def _run_logged(cmd: list[str], log: Path, limit: float, env: dict, container: str | None,
                progress: Progress | None = None) -> subprocess.CompletedProcess:
    fired = threading.Event()
    with log.open("a", encoding="utf-8") as f:
        f.write(f"$ {' '.join(cmd)}\n")
        f.flush()
        p = _start(cmd, env, container, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        # The whole group goes when the time is up: that closes the pipe, and the loop below ends
        # (killing the command alone would leave a grandchild holding the pipe open).
        timer = threading.Timer(limit, lambda: (fired.set(), hostos.kill_tree(p)))
        timer.daemon = True
        timer.start()
        out = []
        try:
            if p.stdout is None:  # (stdout=PIPE always gives one)
                raise BuildError(f"{cmd[0]}: its output cannot be read")
            for line in p.stdout:
                out.append(line)
                f.write(line)
                f.flush()
                if progress and (note := progress(line)):
                    print(f"    {note}", flush=True)
            rc = p.wait()
        except BaseException:
            hostos.kill_tree(p)
            with contextlib.suppress(Exception):
                p.wait(timeout=10)
            raise
        finally:
            timer.cancel()
            _done(p)
            if p.stdout:
                p.stdout.close()
        f.write("\n")
    if fired.is_set():
        raise subprocess.TimeoutExpired(cmd, limit)
    return subprocess.CompletedProcess(cmd, rc, "".join(out), "")


def _stop(container: str | None) -> None:
    if container:
        with contextlib.suppress(OSError, subprocess.SubprocessError):  # (best effort: never hide the real error)
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, stdin=subprocess.DEVNULL,
                           timeout=60, check=False, **hostos.no_window())


def stop_all() -> None:
    """End every command this process still runs, with its container, and start no new one: the
    process is about to end (the companion that started this build is gone)."""
    with _running_lock:
        _stopping.set()
        running = list(_running.items())
    for p, container in running:
        hostos.kill_tree(p)
        _stop(container)


def remove_stale_containers() -> int:
    """Remove the build containers of processes of this computer that are gone (a build killed
    together with its companion, or a computer that stopped in the middle of one): they would run
    on for up to an hour. Returns how many."""
    if not shutil.which("docker"):
        return 0
    try:
        r = subprocess.run(["docker", "ps", "-a", "--filter", f"label={BUILD_LABEL}",
                            "--format", '{{.ID}} {{.Label "' + BUILD_LABEL + '"}}'],
                           capture_output=True, stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                           errors="replace", timeout=60, **hostos.no_window())
    except (OSError, subprocess.SubprocessError):
        return 0
    me = socket.gethostname()
    stale = []
    for line in r.stdout.splitlines() if r.returncode == 0 else []:
        cid, _, owner = line.strip().partition(" ")
        host, _, pid = owner.rpartition(":")
        if cid and host == me and pid.isdigit() and not hostos.pid_alive(int(pid)):
            stale.append(cid)
    if stale:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["docker", "rm", "-f", *stale], capture_output=True, stdin=subprocess.DEVNULL, timeout=120,
                           **hostos.no_window())
        print(f"removed {len(stale)} build container(s) left by a build that was stopped", flush=True)
    return len(stale)


def lock_versions(lock: str, name: str) -> list[str]:
    out, lines = [], lock.splitlines()
    for i, line in enumerate(lines):
        if line == f'name = "{name}"' and i + 1 < len(lines) and lines[i + 1].startswith("version = "):
            out.append(lines[i + 1].split('"')[1])
    return out


def _patch_libmdns(img: str, src: Path, cache: Path, root: Path, log: Path) -> str:
    """libmdns needs SO_REUSEPORT, which the speaker's Linux 3.8 lacks. The crate's source is
    copied out of the cargo volume and patched in the container (no `patch` on Windows)."""
    versions = lock_versions((src / "Cargo.lock").read_text(encoding="utf-8"), "libmdns")
    if not versions:
        return "libmdns not used"
    ver = versions[0]
    script = ('d=/patches/libmdns-$V; rm -rf "$d"; '
              'src=$(ls -d /cargo/registry/src/*/libmdns-$V 2>/dev/null | head -n 1); '
              '[ -n "$src" ] || { echo LIBMDNS_MISSING; exit 0; }; cp -r "$src" "$d" || exit 1; '
              'if patch -d "$d" -p1 --forward -s < /p/libmdns-no-reuseport.patch; then echo LIBMDNS_PATCHED; '
              'elif grep -qF "set_reuse_port(true)?" "$src/src/address_family.rs"; then echo LIBMDNS_FAILED; '
              'else rm -rf "$d"; echo LIBMDNS_NOT_NEEDED; fi')
    out = sh([*docker_run(img, [(cache / "patches", "/patches"), (root / "build" / "patches", "/p", True)],
                          env=(f"V={ver}",)), "sh", "-c", script], log).stdout
    if "LIBMDNS_PATCHED" in out:
        with (src / "Cargo.toml").open("a", encoding="utf-8", newline="\n") as f:
            f.write(f'\n[patch.crates-io]\nlibmdns = {{ path = "/patches/libmdns-{ver}" }}\n')
        return f"libmdns {ver} patched (SO_REUSEPORT optional)"
    if "LIBMDNS_NOT_NEEDED" in out:
        return f"libmdns {ver} no longer requires SO_REUSEPORT"
    if "LIBMDNS_MISSING" in out:
        raise BuildError(f"libmdns {ver} is not in the cargo registry")
    raise BuildError(f"libmdns {ver}: build/patches/libmdns-no-reuseport.patch does not apply")


def _mount(source: Path | str, target: str, readonly: bool = False) -> list[str]:
    """--mount for a directory of this computer (a Path) or a Docker volume (its name)."""
    kind = "bind" if isinstance(source, Path) else "volume"
    field = f"source={source.resolve() if isinstance(source, Path) else source}"
    if "," in field or '"' in field:  # the value is CSV: quote it (a Windows path may hold commas)
        field = '"' + field.replace('"', '""') + '"'
    return ["--mount", f"type={kind},{field},target={target}" + (",readonly" if readonly else "")]


def _user() -> list[str]:
    """Where files have owners (Linux, macOS) the build runs as this user, so everything it writes
    stays theirs; Docker Desktop on Windows gives the user the files anyway."""
    return [] if hostos.WINDOWS else ["-u", f"{os.getuid()}:{os.getgid()}"]


def docker_run(img: str, mounts=(), workdir: str = "/", env: tuple = (), toolchain: str = "") -> list:
    """`docker run` with the cargo volume and `mounts` ((source, target[, readonly]) each).
    RUSTUP_TOOLCHAIN pins the image's Rust and overrides a project's rust-toolchain.toml
    (librespot's asks for rustfmt and clippy, which rustup would download on every run)."""
    cmd = ["docker", "run", "--rm", "--init", *_user(), "-e", "HOME=/tmp", "-e", "CARGO_HOME=/cargo",
           *_mount(CARGO_VOLUME, "/cargo")]
    if toolchain:
        cmd += ["-e", f"RUSTUP_TOOLCHAIN={toolchain}"]
    # The image names its cross linker in /root/.cargo/config.toml, which a different
    # CARGO_HOME hides; the environment form works with any CARGO_HOME.
    cmd += ["-e", f"CARGO_TARGET_{TARGET.upper().replace('-', '_')}_LINKER={TARGET}-gcc"]
    for m in mounts:
        cmd += _mount(*m)
    for e in env:
        cmd += ["-e", e]
    return [*cmd, "-w", workdir, img]


def _own_volumes(img: str, names: list[str]) -> None:
    """A new volume belongs to root; the build runs as the user (Linux, macOS)."""
    if hostos.WINDOWS:
        return
    mounts = [arg for i, n in enumerate(names) for arg in _mount(n, f"/v{i}")]
    sh(["docker", "run", "--rm", *mounts, img, "chown", f"{os.getuid()}:{os.getgid()}",
        *(f"/v{i}" for i in range(len(names)))])


def _forget_old_caches(cache: Path, keep_target: str) -> None:
    """Build caches of earlier releases: target volumes of other builder images, and the cache
    directories from before the volumes."""
    r = sh(["docker", "volume", "ls", "-q", "--filter", f"name={TARGET_VOLUME_PREFIX}"], check=False)
    for v in r.stdout.split():
        if v.startswith(TARGET_VOLUME_PREFIX) and v not in (keep_target, AGENT_TARGET_VOLUME):
            sh(["docker", "volume", "rm", v], check=False)
    for old in sorted({cache / "cargo-home", cache / "target-agent", *cache.glob("target-*")}):
        if old.is_dir():
            hostos.rmtree(old)
            print(f"    removed the old build cache {old.name} (the build keeps it in a Docker volume now)")


# Never sources: build output and caches (directories), and what file managers and editors leave.
_NOT_SOURCES = {"target", "__pycache__", ".git"}
_LITTER = {".DS_Store", "Thumbs.db", "desktop.ini"}


def _litter(name: str) -> bool:
    return name in _LITTER or name.endswith(("~", ".swp", ".swo"))


def _git_files(root: Path, dirs: tuple[str, ...]) -> list[Path] | None:
    """git's files of `dirs`: the tracked ones and new ones it does not ignore. None when `root`
    is not a git checkout or git cannot tell."""
    if not (root / ".git").exists():
        return None
    try:
        r = sh(["git", "-C", root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *dirs],
               check=False, timeout=60)
    except BuildError:  # no git
        return None
    if r.returncode:
        return None
    return [root / p for p in set(r.stdout.split("\0")) if p]


def sources(root: Path, *dirs: str) -> list[Path]:
    """The files under `root`/`dirs` that make a build, in a stable order (they are hashed): git's
    list in a checkout, otherwise every file but build output, caches and editor litter."""
    files = _git_files(root, dirs)
    if files is None:
        files = []
        for d in dirs:
            for top, subdirs, names in os.walk(root / d):
                subdirs[:] = [s for s in subdirs if s not in _NOT_SOURCES]
                files += [Path(top) / n for n in names]
    return sorted(f for f in files if not _litter(f.name) and f.is_file()
                  and not _NOT_SOURCES.intersection(f.relative_to(root).parts[:-1]))


def host_triple(rustc_vv: str) -> str:
    """The CPU the builder image runs on, from `rustc -vV` ("host: aarch64-unknown-linux-gnu")."""
    return next((line.split(":", 1)[1].strip() for line in rustc_vv.splitlines() if line.startswith("host:")),
                "x86_64-unknown-linux-gnu")


def librespot_stamp(root: Path, pins: dict, src: Path, rustc: str, env: tuple = LIBRESPOT_ENV) -> str:
    """What librespot's binary is made from: the pins, its release profile, build/ (the builder
    image and the patches), its resolved dependencies and the compiler. The same stamp as the
    last build's: that binary is used again."""
    h = hashlib.sha256(json.dumps(pins, sort_keys=True).encode() + " ".join(env).encode())
    for f in sources(root, "build"):
        h.update(f.read_bytes())
    h.update((src / "Cargo.lock").read_bytes() + (src / "Cargo.toml").read_bytes() + rustc.encode())
    return h.hexdigest()


def agent_build_id(root: Path, rustc: str) -> str:
    """The agent's build id. It changes only with the agent's sources (which embed device/ scripts
    and the web page) or the toolchain: the same sources give the same binary, so nothing is
    reinstalled for nothing, and a page opened before a real update reloads itself."""
    h = hashlib.sha256(rustc.encode())
    for f in sources(root, "agent", "device"):
        h.update(f.relative_to(root).as_posix().encode() + b"\0" + f.read_bytes())
    return h.hexdigest()[:12]


def build(force: bool = False, root: Path = ROOT, cache: Path = CACHE, latest: bool = False) -> Path:
    """Build the bundle. With `latest`, librespot, alsa-lib and Rust first move to their newest
    stable releases. Those are remembered for later builds (the user's versions.toml) only once
    the bundle built with them is in place: a build that fails, or is killed, leaves the previous
    choice as it was."""
    if not shutil.which("docker"):
        raise BuildError("Docker is required to build (or use `lithify fetch` for a published release)")
    with _lock(cache / "build.lock", wait=False, busy="another build is running (`lithify build`, or the "
                                                       "speaker's web page updating)"):
        changed, chosen = bump_to_latest(root) if latest else ([], None)
        for c in changed:
            say(f"newer stable release: {c}")
        pins = load_pins(root, chosen=chosen)
        try:
            final = _build(force, root, cache, pins)
        except BuildError as e:
            if changed:
                raise BuildError(f"{e}\n(the newer versions did not build; staying with the previous ones)") from None
            raise
        if chosen:
            try:
                write_user_pins(chosen)
            except OSError as e:  # the bundle is fine; a plain build would only go back to the older versions
                print(f"warning: cannot remember the new versions in {USER_PINS}: {e}", file=sys.stderr)
        _prune_images(image_tag(pins))
        say(f"bundle ready: {final}")
        return final


def build_memory(img: str) -> int:
    """The memory a build can have now, in bytes: what the system Docker runs on has free (this
    computer, or Docker Desktop's virtual machine), swap included; 0 when it cannot be told."""
    r = sh(["docker", "run", "--rm", img, "cat", "/proc/meminfo"], check=False)
    kb = {}
    for line in r.stdout.splitlines():
        name, _, rest = line.partition(":")
        if rest.split() and rest.split()[0].isdigit():
            kb[name] = int(rest.split()[0])
    return (kb.get("MemAvailable", 0) + kb.get("SwapFree", 0)) * 1024 if r.returncode == 0 else 0


def compile_librespot(img: str, run: list, lp: dict, log: Path) -> str:
    """Compile librespot with the profile this computer can spare the memory for: "fat", or "thin"
    when little is free, or when the compiler was killed for memory (a second try)."""
    free = build_memory(img)
    lto = "thin" if 0 < free < FAT_LTO_MEMORY else "fat"
    if lto == "thin":
        print(f"    {free // 2 ** 20} MB of memory free: thin LTO (fat LTO wants about 3 GB at once)", flush=True)
    say("compiling librespot (armv7, static, NEON)")
    try:
        _compile_librespot(run, lp, log, lto)
    except BuildError as e:
        if lto == "thin" or not _OUT_OF_MEMORY.search(str(e)):
            raise
        lto = "thin"
        say("compiling librespot again with thin LTO, which needs less memory (the compiler ran out of it)")
        _compile_librespot(run, lp, log, lto)
    return lto


def _compile_librespot(run: list, lp: dict, log: Path, lto: str) -> None:
    env = LIBRESPOT_ENV if lto == "fat" else LIBRESPOT_LEAN_ENV
    sh([*run, "env", *env, "cargo", "build", "--release", "--no-default-features",
        "--features", ",".join(lp["features"])], log, timeout=3600, progress=crates_compiled())


def _build(force: bool, root: Path, cache: Path, pins: dict) -> Path:
    lp = pins["librespot"]
    for d in ("patches", "out"):
        (cache / d).mkdir(parents=True, exist_ok=True)
    log = cache / "build.log"
    log.write_text(f"lithify build {datetime.now(UTC).isoformat()}\n", encoding="utf-8")
    img = image_tag(pins)
    say(f"builder image {img}")
    # (Its steps go to the log as they run: a first build of the image takes minutes.)
    sh(["docker", "build", "--build-arg", f"BASE={pins['rust']['builder_base']}",
        "--build-arg", f"ALSA_VER={pins['alsa_lib']['version']}",
        "--build-arg", f"ALSA_CONFIG_DIR={platforms.LS9.base}/alsa",
        "--build-arg", f"RUST_TOOLCHAIN={pins['rust']['toolchain']}", "-t", img, root / "build"], log, timeout=3600,
       env={"BUILDKIT_PROGRESS": "plain"}, progress=docker_steps())
    # (`rustc -vV` begins with the line `rustc --version` prints, so the stamps stay the same.)
    rustc_vv = sh(["docker", "run", "--rm", img, "rustc", "-vV"]).stdout
    rustc = rustc_vv.splitlines()[0].strip() if rustc_vv.strip() else ""
    # The agent's unit tests run on this computer's own CPU: x86_64, or arm64 on Apple Silicon Macs
    # and Raspberry Pis (Docker gives them the image's arm64 variant, which links no x86_64 code).
    host = host_triple(rustc_vv)
    # One target directory per builder image: a new alsa-lib or Rust release always relinks
    # everything (cargo cannot see a changed static C library on its own).
    image_id = sh(["docker", "image", "inspect", "--format", "{{.Id}}", img]).stdout.strip().split(":")[-1][:12]
    target = TARGET_VOLUME_PREFIX + image_id
    _forget_old_caches(cache, target)
    _own_volumes(img, [CARGO_VOLUME, target, AGENT_TARGET_VOLUME])

    say(f"librespot {lp['ref']}" + (f" {lp['commit'][:7]}" if lp.get("commit") else ""))
    mirror = cache / "librespot.git"
    if mirror.is_dir():
        if sh(["git", "-C", mirror, "fetch", "-q", "--prune", "origin"], log, check=False, timeout=180).returncode:
            print("    GitHub unreachable: using the local librespot mirror")
    else:
        sh(["git", "-c", "core.autocrlf=false", "clone", "-q", "--mirror", lp["repo"], mirror], log, timeout=600)
    src = cache / "librespot"
    hostos.rmtree(src, quiet=False)
    # The sources exactly as published (Git for Windows would turn LF into CRLF).
    sh(["git", "-c", "core.autocrlf=false", "clone", "-q", "--branch", lp["ref"], mirror, src], log)
    git = ["git", "-C", src, "-c", "core.autocrlf=false", "-c", "user.name=lithify",
           "-c", "user.email=lithify@localhost"]
    if lp.get("commit"):
        # Followed on a branch: the chosen commit, unless versions.toml pins a newer one (a choice
        # made by an older Lithify must not hold back what this one was tested with).
        commit = lp["commit"]
        pinned = lp.get("pinned_commit", commit)
        if commit != pinned and sh(["git", "-C", mirror, "merge-base", "--is-ancestor", commit, pinned],
                                   check=False).returncode == 0:
            commit = pinned
        if sh([*git, "checkout", "-q", commit], log, check=False).returncode:
            raise BuildError(f"librespot commit {commit[:12]} is not on {lp['ref']} (or GitHub was unreachable)")
        print(f"    {lp['ref']} at {commit[:12]}")
    # The backports are fixes taken from librespot's development branch for the pinned release;
    # a newer release has them (or their successors) already.
    backports = lp["backports"] if lp["ref"] == lp["pinned_ref"] else []
    if lp["backports"] and not backports:
        print(f"    backports for {lp['pinned_ref']} not needed for {lp['ref']}")
    for sha in backports:
        p = root / "build" / "backports" / f"{sha}.patch"
        if sh([*git, "apply", "--check", p], check=False).returncode == 0:
            sh([*git, "am", "-q", "--3way", "--committer-date-is-author-date", p], log)
            print(f"    backport {sha} applied")
        elif sh([*git, "apply", "--reverse", "--check", p], check=False).returncode == 0:
            print(f"    backport {sha} already in {lp['ref']}, skipped")
        else:
            raise BuildError(f"backport {sha} does not apply to {lp['ref']}: drop it from versions.toml or refresh it")
    for name in lp.get("local_patches", []):
        p = root / "build" / "patches" / f"{name}.patch"
        if sh([*git, "apply", "--check", p], check=False).returncode == 0:
            sh([*git, "apply", p], log)
            print(f"    local patch {name} applied")
        elif sh([*git, "apply", "--reverse", "--check", p], check=False).returncode == 0:
            print(f"    local patch {name} already in librespot, skipped")  # upstream took it in
        else:
            raise BuildError(f"local patch {name} does not apply to {lp['ref']}: refresh build/patches/{name}.patch")

    tc = pins["rust"]["toolchain"]
    run = docker_run(img, [(src, "/src"), (cache / "patches", "/patches", True), (target, "/target")], "/src",
                     ("CARGO_TARGET_DIR=/target",), tc)
    if lp.get("update_deps"):
        up = ["env", "CARGO_RESOLVER_INCOMPATIBLE_RUST_VERSIONS=allow", "cargo", "update"]
        r = sh([*run, *up], log, check=False, timeout=900)
        if r.returncode:
            r = sh([*run, *up], log, timeout=900)
        print(f"    dependencies refreshed: {(r.stdout + r.stderr).count(' Updating ')} crates updated")
    if sh([*run, "cargo", "fetch"], log, check=False, timeout=900).returncode:
        sh([*run, "cargo", "fetch"], log, timeout=900)
    print("    " + _patch_libmdns(img, src, cache, root, log))

    # (a binary made with either profile is used again: whichever this computer could build)
    stamps = {kind: librespot_stamp(root, pins, src, rustc, env)
              for kind, env in (("fat", LIBRESPOT_ENV), ("thin", LIBRESPOT_LEAN_ENV))}
    stamp_file, binary = cache / "librespot.stamp", cache / "out" / "librespot"
    last = stamp_file.read_text() if stamp_file.exists() else ""
    lto = next((k for k, v in stamps.items() if v == last), None) if binary.exists() and not force else None
    if lto:
        say("librespot: sources, dependencies and toolchain unchanged, reusing the last build")
    else:
        stamp_file.unlink(missing_ok=True)
        lto = compile_librespot(img, run, lp, log)
        # Out of the volume: the next build reuses it from here.
        sh([*docker_run(img, [(target, "/target", True), (cache / "out", "/out")]),
            "cp", f"/target/{TARGET}/release/librespot", "/out/librespot"], log)
        stamp_file.write_text(stamps[lto])

    say("lithify-agent (unit tests on the host, then armv7)")
    built = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    agent_build = agent_build_id(root, rustc)
    # The whole repository is mounted: the agent embeds device/ scripts and its web page.
    arun = docker_run(img, [(root, "/repo", True), (AGENT_TARGET_VOLUME, "/target")], "/repo/agent",
                      ("CARGO_TARGET_DIR=/target", f"LITHIFY_BUILD={agent_build}"), tc)
    sh([*arun, "cargo", "test", "--release", "--locked", "--target", host], log, timeout=900)
    sh([*arun, "cargo", "build", "--release", "--locked"], log, timeout=900)

    say("bundle")
    # Assembled next to the current bundle and swapped in at the end: a failure here keeps the
    # last good bundle, and a speaker asking meanwhile still gets a complete one.
    final, bundle = cache / "bundle", cache / "bundle.new"
    hostos.rmtree(bundle, quiet=False)
    bundle.mkdir()
    shutil.copy2(binary, bundle / "librespot")
    sh([*docker_run(img, [(AGENT_TARGET_VOLUME, "/target", True), (bundle, "/out")]), "sh", "-c",
        f"cp /target/{TARGET}/release/lithify-agent /out/ && tar -C /opt/alsa-conf -cf /out/alsa.tar alsa"], log)
    lock = (src / "Cargo.lock").read_text(encoding="utf-8")
    describe = sh(["git", "-C", src, "describe", "--tags", "--always", "--dirty"]).stdout.strip()
    versions = {
        "lithify": __version__,
        "librespot": describe,
        "librespot_ref": lp["ref"],
        "librespot_commit": sh(["git", "-C", src, "rev-parse", "HEAD"]).stdout.strip(),
        "backports": " ".join(backports),
        "local_patches": " ".join(lp.get("local_patches", [])),
        "deps": "newest compatible as of " + datetime.now(UTC).strftime("%Y-%m-%d")
                if lp.get("update_deps") else "release lockfile",
        "key_crates": " ".join(f"{c}-{'+'.join(lock_versions(lock, c))}" for c in KEY_CRATES),
        "alsa_lib": pins["alsa_lib"]["version"],
        "rust": rustc.split()[1] if len(rustc.split()) > 1 else rustc,
        "librespot_lto": lto,
        "agent_build": agent_build,
        "built": built,
    }
    hostos.write_lf(bundle / "VERSIONS", "".join(f"{k}={v}\n" for k, v in versions.items()))
    write_sums(bundle, BUNDLE_FILES)
    _swap_in(bundle, final, cache)
    return final


# What a bundle directory holds when it is complete.
_COMPLETE = (*BUNDLE_FILES, "SHA256SUMS")


def _complete(d: Path) -> bool:
    return all((d / f).is_file() for f in _COMPLETE)


def _swap_in(new: Path, final: Path, cache: Path) -> None:
    """Put `new` in place of the current bundle, which is set aside first and deleted only once
    the new one is in place. A swap cut off between its two renames (the computer stopped) left
    the last bundle set aside: it comes back first."""
    old = final.with_name(final.name + ".old")
    # (a long wait: this is the end of a build that may have taken an hour)
    with bundle_lock(cache, shared=False, timeout=600):
        if not final.exists() and _complete(old):
            hostos.replace(old, final)
        hostos.rmtree(old)  # (what an earlier swap left behind; `final` is in place)
        if final.exists():
            hostos.replace(final, old)
        try:
            hostos.replace(new, final)
        except OSError:
            if not final.exists() and old.exists():
                hostos.replace(old, final)  # the previous bundle stays
            raise
    hostos.rmtree(old)


def recover_bundle(cache: Path = CACHE) -> bool:
    """No bundle, but a complete one set aside by a swap the computer stopped half-way: it is the
    last good bundle, so it comes back (instead of a speaker hearing "no bundle" until the next
    build). True when it did."""
    final = cache / "bundle"
    old = final.with_name(final.name + ".old")
    if final.exists() or not _complete(old):
        return False
    with bundle_lock(cache, shared=False, timeout=600):
        if final.exists() or not _complete(old):
            return False
        hostos.replace(old, final)
    print(f"restored the last bundle from {old.name} (a build was cut off while replacing it)", flush=True)
    return True


def _prune_images(keep: str) -> None:
    """Builder images of earlier alsa-lib or Rust releases (about 3 GB each)."""
    r = sh(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}", "lithify-builder"], check=False)
    for tag in r.stdout.split():
        if tag != keep and sh(["docker", "image", "rm", tag], check=False).returncode == 0:
            print(f"    removed the old builder image {tag}")


def pending_dependency_updates(root: Path = ROOT, cache: Path = CACHE) -> list[str] | None:
    """Newer semver-compatible versions of librespot's dependencies than the last build used
    (`cargo update --dry-run` on that build's sources); None when it cannot be checked."""
    src = cache / "librespot"
    if not (src / "Cargo.lock").exists() or not shutil.which("docker"):
        return None
    pins = load_pins(root)
    img = image_tag(pins)
    if sh(["docker", "image", "inspect", img], check=False).returncode:
        return None
    run = docker_run(img, [(src, "/src"), (cache / "patches", "/patches", True)], "/src", (), pins["rust"]["toolchain"])
    try:
        r = sh([*run, "env", "CARGO_RESOLVER_INCOMPATIBLE_RUST_VERSIONS=allow", "cargo", "update", "--dry-run"],
               check=False, timeout=240)
    except BuildError:  # docker itself cannot run
        return None
    if r.returncode:
        return None
    found = re.findall(r"Updating (\S+) v(\S+) -> v(\S+)", r.stdout + r.stderr)
    return [f"{name} {old} -> {new}" for name, old, new in found]


def _https(url: str) -> None:
    """A release comes over HTTPS only: its checksums come from the same place as its files, so
    on plain HTTP anyone on the way could replace both."""
    if not url.startswith("https://"):
        raise BuildError(f"release URL must be https: {url}")


def _download(url: str) -> bytes:
    _https(url)
    try:
        with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 (URL checked above)
            _https(r.geturl())  # (not redirected to plain HTTP either)
            return r.read()
    except (OSError, http.client.HTTPException) as e:  # (a cut-off reply too)
        raise BuildError(f"download {url} failed: {e or type(e).__name__}") from None


def fetch(url: str, cache: Path = CACHE) -> Path:
    """Download a published bundle (SHA256SUMS and the files it lists), verifying every file;
    the current bundle is replaced only when all of them arrived intact."""
    _https(url)
    with _lock(cache / "build.lock", wait=False, busy="a build is running: try again when it has finished"):
        return _fetch(url.rstrip("/"), cache)


def _fetch(url: str, cache: Path) -> Path:
    tmp = cache / "bundle.new"
    hostos.rmtree(tmp, quiet=False)
    tmp.mkdir(parents=True)
    sums = _download(f"{url}/SHA256SUMS")
    expected = {}
    for line in sums.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        digest, name = line.split(maxsplit=1)
        name = name.strip().lstrip("*")
        if name not in BUNDLE_FILES:
            raise BuildError(f"unexpected file {name!r} in {url}/SHA256SUMS")
        expected[name] = digest
    missing = sorted(set(BUNDLE_FILES) - set(expected))
    if missing:
        raise BuildError(f"the release lacks {', '.join(missing)}")
    for name, digest in expected.items():
        data = _download(f"{url}/{name}")
        if hashlib.sha256(data).hexdigest() != digest:
            raise BuildError(f"checksum mismatch for {name}")
        (tmp / name).write_bytes(data)
        say(f"{name} ok")
    (tmp / "SHA256SUMS").write_bytes(sums)
    for exe in ("librespot", "lithify-agent"):
        (tmp / exe).chmod(0o755)
    _swap_in(tmp, cache / "bundle", cache)
    return cache / "bundle"


def release_url() -> str:
    """Where versions.toml says Lithify's releases are ("": none, every bundle is built here)."""
    try:
        return load_pins().get("release", {}).get("url", "") or ""
    except (OSError, ValueError, KeyError):
        return ""


def release_versions(url: str) -> dict:
    """What the newest published bundle holds: its VERSIONS (one small download)."""
    versions = parse_versions(_download(f"{url.rstrip('/')}/VERSIONS").decode("utf-8", "replace"))
    if not versions.get("built"):
        raise BuildError(f"{url.rstrip('/')}/VERSIONS does not say when it was built")
    return versions


def release_is_newer(release: dict, here: dict) -> bool:
    """Is a published bundle newer than the one this computer has? By when each was built (UTC,
    2026-10-08T08:13:19Z): one built here after the release (`lithify build --latest`) is never
    replaced by the older release."""
    return release.get("built", "") > here.get("built", "")


def write_sums(d: Path, files: list[str]) -> None:
    lines = [f"{hashlib.sha256((d / f).read_bytes()).hexdigest()}  {f}\n" for f in files]
    hostos.write_lf(d / "SHA256SUMS", "".join(lines))


def parse_versions(text: str) -> dict:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def read_versions(d: Path) -> dict:
    try:
        text = (d / "VERSIONS").read_text(encoding="utf-8")
    except FileNotFoundError:  # (none yet, or a build is swapping its bundle in right now)
        return {}
    return parse_versions(text)


def stage(bundle: Path, speaker: config.Speaker, platform: platforms.Platform, stock: list[dict] | None,
          companion_url: str, dest: Path, push: list[str] | None = None) -> Path:
    """Bundle + this speaker's files, with fresh checksums: settings.default (used only when the
    speaker has no settings yet), install.conf, and settings.patch with the `push` settings
    (sent on purpose: they replace the speaker's own). The settings may hold the web page's PIN:
    `dest` and those files are this user's only (Linux, macOS)."""
    if not (bundle / "VERSIONS").exists():
        raise BuildError(f"no bundle in {bundle}: run `lithify build` (or `lithify fetch`)")
    hostos.rmtree(dest, quiet=False)
    hostos.private_dir(dest)
    for f in BUNDLE_FILES:
        shutil.copy2(bundle / f, dest / f)
    # (LF endings on every computer: the speaker reads these line by line)
    hostos.write_lf(dest / "settings.default", config.settings_conf(speaker), private=True)
    hostos.write_lf(dest / "install.conf", config.install_conf(speaker, companion_url, platform.key), private=True)
    files = [*BUNDLE_FILES, "settings.default", "install.conf"]
    if push:
        hostos.write_lf(dest / "settings.patch", config.settings_patch(speaker, push), private=True)
        files.append("settings.patch")
    if stock is not None:
        hostos.write_lf(dest / "process.json", platforms.render_process_list(stock, platform.base))
        files.append("process.json")
    write_sums(dest, files)
    return dest


def lan_ip_for(host: str) -> str:
    """The local address the speaker can reach us at."""
    return console.local_ip_for(host)


class QuietFiles(http.server.SimpleHTTPRequestHandler):
    timeout = 30  # a connection that stays silent is closed

    def log_message(self, *args):  # (keeps the CLI's output clean)
        pass


class _SpeakerOnly(http.server.ThreadingHTTPServer):
    """Answers one address only: the staged files hold the speaker's settings (its PIN too)."""
    daemon_threads = True
    allow_reuse_address = not hostos.WINDOWS  # (Windows: a second server would share the port)
    client_ip = ""

    def verify_request(self, request, client_address) -> bool:
        return client_address[0] == self.client_ip


@contextlib.contextmanager
def serve(directory: Path, speaker_host: str):
    """Serve `directory` over HTTP to the speaker for the duration of the block; yields the base URL."""
    speaker_ip = socket.gethostbyname(speaker_host)
    ip = lan_ip_for(speaker_ip)
    # A known port, so a firewall rule can let the speaker in (Windows).
    httpd = hostos.bind_server(_SpeakerOnly, ip, partial(QuietFiles, directory=str(directory)))
    httpd.client_ip = speaker_ip
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://{ip}:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
