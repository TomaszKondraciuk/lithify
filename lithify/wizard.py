"""`lithify wizard`: the whole setup in the web browser, step by step, for people who never open a
terminal.

A small web server on 127.0.0.1 serves one page (lithify/wizard_ui) and a JSON API. The page says
what will happen, checks this computer (Docker, disk space), finds the speaker, asks for its name
in Spotify, installs, and says what to do next. Long steps run in a thread of their own and the
page polls them. The installation is `lithify install` in a child process, exactly as on the
command line, so nothing here does it a second way.

Only the browser it opened may use it: every API request carries the random token of the address
the wizard opened (as a header, which no other web page can make the browser send), `Host` must
name this server (no DNS rebinding), and a request from another site is refused.
"""
from __future__ import annotations

import collections
import contextlib
import copy
import hmac
import http.server
import importlib.resources
import ipaddress
import json
import locale
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Callable
from pathlib import Path

from . import __version__, bundle, config, console, discovery, hostos
from .companion import scrub
from .device import ROOT, Device

OS = "windows" if hostos.WINDOWS else "macos" if hostos.MACOS else "linux"
HEADER = "X-Lithify-Wizard"  # carries the token of the address the wizard opened
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
SECURITY_HEADERS = (("Content-Security-Policy", CSP), ("X-Content-Type-Options", "nosniff"),
                    ("Cache-Control", "no-store"), ("Referrer-Policy", "no-referrer"), ("X-Frame-Options", "DENY"),
                    ("Cross-Origin-Resource-Policy", "same-origin"))
FILES = {"/": "index.html", "/index.html": "index.html", "/wizard.css": "wizard.css", "/wizard.js": "wizard.js",
         "/icon.svg": "icon.svg"}
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}
MAX_LINES = 400        # output lines a task keeps (the page shows them as the technical log)
MAX_BODY = 4096        # a request body: a host and a name
DOCKER_TIMEOUT = 20    # `docker info`; Docker Desktop that is still starting answers slowly
DOCKER_START_WAIT = 180
DOCKER_POLL = 5.0
DISK_MIN = 6 * 1024 ** 3  # a first build: the toolchain, librespot's dependencies and their build
PROBE_TIMEOUT = 60.0   # a console that does not answer gives up after ~20 s (console.Console)
INSTALL_TIMEOUT = 3 * 3600  # a build with every download takes ~15 min, the speaker a few
CANCEL_GRACE = 30      # how long a stopped build gets to remove its containers
DOCKER_LINKS = {"windows": "https://docs.docker.com/desktop/setup/install/windows-install/",
                "macos": "https://docs.docker.com/desktop/setup/install/mac-install/",
                "linux": "https://docs.docker.com/engine/install/"}
DOCKER_GROUP_LINK = "https://docs.docker.com/engine/install/linux-postinstall/"
DOCKER_GROUP_CMD = "sudo usermod -aG docker $USER"
DOCKER_START_CMD = "sudo systemctl enable --now docker"
PAGE_RE = re.compile(r"http://[A-Za-z0-9.:\[\]-]{1,260}:[0-9]{1,5}/")
_ESCAPES = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f\x7f]")
BUSY = (409, {"error": "another step is still running", "error_key": "busy"})

# ── what `lithify install` prints ───────────────────────────────────────────

PHASES = ("prepare", "build", "install", "restart", "check")
# The progress lines of `lithify install` ("==> ...", cli.say and bundle.say) that begin a phase.
# (A published bundle is downloaded instead of built: "<file> ok" for each of its files.)
_PHASE_STARTS = (
    ("prepare", ("looking for Lithe Audio speakers", "found ", "wrote ", "added speaker", hostos.FIREWALL_ASK)),
    ("build", ("builder image", "newer stable release", "librespot", "compiling librespot", "lithify-agent",
               "bundle", "the published bundle", *(f"{f} ok" for f in bundle.BUNDLE_FILES))),
    ("install", ("installing ", "sending settings", "checking that the speaker", "checking whether the speaker")),
    ("restart", ("the Cast service list changed", "restarting librespot")),
    ("check", ("speaker back after", "removed the old install", "companion running")),
)
# The build's own steps, and how far along each one is: a first build spends most of its time on
# the builder image and on compiling librespot (a later one reuses both).
_BUILD_STEPS = (("builder image", "image", 0.02), ("librespot: ", "reuse", 0.85), ("librespot ", "source", 0.3),
                ("compiling librespot", "compile", 0.35), ("lithify-agent", "agent", 0.88),
                ("bundle ready", None, 1.0), ("bundle", "bundle", 0.97))
# How far a long step has come (bundle.docker_steps, bundle.crates_compiled), and the part of the
# bar it moves through: the builder image up to its next step, librespot's crates likewise.
_STEP_NOTE = re.compile(r" {4}(?:builder image: step (\d+) of (\d+)|crates compiled: (\d+)|"
                        r"builder image: downloaded (\d+) MB|builder image: (saving) it)")
_STEP_SPAN = {"image": (0.02, 0.3), "compile": (0.35, 0.85)}
LIBRESPOT_CRATES = 290  # about how many crates a librespot build compiles (284 for librespot 0.8, 2026-10)
# The builder image of a first build: its base image (rust-musl-cross, 780 MB in 2026-10) takes
# the first part of that step's time, its own steps (Rust, alsa-lib) the rest.
IMAGE_DOWNLOAD_MB, IMAGE_DOWNLOAD_PART = 800, 0.4
# Why an installation failed, from what it printed: the first match wins (so a speaker's failed
# download, which also says "Connection refused", is the firewall, not an unreachable speaker).
_ERRORS = (
    ("docker_permission", r"permission denied while trying to connect to the docker|docker_engine: access is denied"),
    ("docker_missing", r"docker is required to build"),
    ("docker_not_running", r"cannot connect to the docker daemon|is the docker daemon running|error during connect|"
                           r"docker daemon is not running|docker_engine|dockerdesktoplinuxengine"),
    ("build_memory", r"signal: 9, SIGKILL|out of memory|cannot allocate memory|memory allocation of \d+ bytes failed"),
    ("speaker_space", r"fail space|install failed on the speaker:[\s\S]*no space left"),
    ("no_space", r"no space left on device|not enough space on the disk|enospc|disk quota exceeded"),
    ("unsupported", r"not a supported lithe audio platform|are not supported yet"),
    ("firewall", r"cannot download from this computer|curl: \((?:7|28)\)|fail download|firewall|"
                 r"(?:cannot|can't|could not) reach this computer"),
    ("not_back", r"after the reboot"),
    ("unreachable", r"cannot reach the speaker console|does not answer on the service console|unexpected console "
                    r"banner|no route to host|network is unreachable|host is unreachable|name or service not known|"
                    r"nodename nor servname|getaddrinfo failed"),
    ("build_busy", r"another build is running"),
    ("config", r"config\.toml|is not valid toml|several speakers configured|unknown speaker"),
)
_ERROR_RES = tuple((key, re.compile(rx, re.IGNORECASE)) for key, rx in _ERRORS)


def phase_of(line: str, current: str | None) -> str | None:
    """The phase a line of `lithify install` begins, or None. Phases only move on: a line that
    would go back to an earlier one begins nothing."""
    if not line.startswith("==> ") or (current is not None and current not in PHASES):  # ("done": over)
        return None
    text = line[4:]
    for phase, starts in _PHASE_STARTS:
        if text.startswith(starts):
            ahead = current is None or PHASES.index(phase) > PHASES.index(current)
            return phase if ahead else None
    return None


def build_step(text: str) -> tuple[str | None, float] | None:
    """The build's step a progress line (without "==> ") begins, and how far along that is."""
    if text.endswith(" ok") and text[:-3] in bundle.BUNDLE_FILES:  # (a published bundle's download)
        return "download", (bundle.BUNDLE_FILES.index(text[:-3]) + 1) / len(bundle.BUNDLE_FILES)
    for start, step, done in _BUILD_STEPS:
        if text.startswith(start):
            return step, done
    return None


def error_block(lines: list[str]) -> list[str]:
    """What the CLI said when it failed: its "error: ..." line to the end. That is the first one
    after the last progress line (an error's text may quote more "error:" lines, a compiler's);
    without one, the last lines of the output."""
    last = max((i for i, line in enumerate(lines) if line.startswith("==> ")), default=0)
    for i in range(last, len(lines)):
        if lines[i].startswith("error: "):
            return lines[i:i + 60]
    return lines[-40:]


def classify(lines: list[str], phase: str | None) -> tuple[str, str]:
    """Why `lithify install` failed: an error key (the page explains it and says what to do) and the
    lines that tell it. A failed build without a known cause shows its last 30 lines."""
    text = "\n".join(error_block(lines))
    for key, rx in _ERROR_RES:
        if rx.search(text):
            return key, text
    if phase == "build":
        return "build_failed", "\n".join(lines[-30:])
    return "unknown", text


def extras(key: str | None, firewall: dict | None = None) -> dict:
    """What the page offers next to an explanation: a download link, commands to copy, a button."""
    if key in ("docker_missing", "docker_desktop_missing"):
        return {"link": DOCKER_LINKS[OS]}
    if key in ("docker_not_running", "docker_slow", "docker_start_timeout"):
        if OS != "linux":
            return {"action": "start-docker"}
        return {"commands": [DOCKER_START_CMD]} if key == "docker_not_running" else {}
    if key == "docker_permission" and OS == "linux":
        return {"commands": [DOCKER_GROUP_CMD], "link": DOCKER_GROUP_LINK}
    if key == "firewall" and OS == "linux" and firewall:
        return {"commands": list(firewall["commands"])}
    return {}


# ── checks ──────────────────────────────────────────────────────────────────

def check_host(host) -> str | None:
    """`host` when it can be a speaker's address (config.py's rule; never one that reads as an option)."""
    if not isinstance(host, str):
        return None
    host = host.strip()
    return host if config.valid_host(host) and not host.startswith(("-", ".")) else None


def check_name(name) -> str | None:
    """The name for Spotify as the speaker keeps it, or None when it cannot be one (the rule of
    agent/rules.tsv, which the speaker applies too)."""
    if not isinstance(name, str):
        return None
    try:
        return config.normalize(config.OPTIONS["name"], name)
    except ValueError:
        return None


def default_name(speaker_name: str) -> str:
    """What `lithify install` calls a speaker in Spotify (cli.write_config): its name and
    "(librespot)", so it is not mixed up with the speaker's own Spotify, within 64 characters."""
    tag = " (librespot)"
    return f"{(speaker_name or 'Speaker').strip()[:64 - len(tag)].rstrip()}{tag}"


def _docker_on_path() -> None:
    """Docker Desktop's commands where a shell started before its installation does not look:
    "Check again" then works without starting Lithify again (and the installation finds them)."""
    if OS == "windows":
        dirs = [Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Docker" / "Docker" / "resources" / "bin"]
    elif OS == "macos":
        dirs = [Path("/usr/local/bin"), Path("/opt/homebrew/bin"), Path.home() / ".docker" / "bin",
                Path("/Applications/Docker.app/Contents/Resources/bin")]
    else:
        return
    path = os.environ.get("PATH", "").split(os.pathsep)
    missing = [str(d) for d in dirs if d.is_dir() and str(d) not in path]
    if missing:
        os.environ["PATH"] = os.pathsep.join([*path, *missing])


def docker_state() -> tuple[str, str]:
    """Docker here: ("ok", its version), or ("missing" | "permission" | "not_running" | "slow",
    what it said)."""
    _docker_on_path()
    if not shutil.which("docker"):
        return "missing", ""
    try:
        r = hostos.run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=DOCKER_TIMEOUT, text=True,
                       encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return "slow", ""
    except OSError as e:
        return "missing", str(e)
    out, said = r.stdout.strip(), f"{r.stdout}\n{r.stderr}".strip()
    if re.search(r"permission denied|access is denied", said, re.IGNORECASE):
        return "permission", said
    down = re.search(r"cannot connect|error during connect|daemon is not running", said, re.IGNORECASE)
    if r.returncode == 0 and out and not down:
        return "ok", out.splitlines()[0][:40]
    return "not_running", said


def _item(key: str, status: str, msg: str, params: dict | None = None, problem: bool = False) -> dict:
    """One line of the computer check: ok | warn | bad and a message key for the page; for a problem
    also how to fix it (a key: the page has a text per system where they differ, "fix_x_windows")
    and what it offers (a link, commands, a button)."""
    return {"key": key, "status": status, "msg": msg, "params": params or {}, "problem": msg if problem else None,
            "fix": f"fix_{msg}" if problem else None, "link": None, "commands": [], "action": None,
            **(extras(msg) if problem else {})}


def _existing(p: Path) -> Path:
    while not p.exists() and p.parent != p:
        p = p.parent
    return p


def _published() -> bool:
    """Does versions.toml name a published bundle? (`lithify install` then downloads it, see cli.get_bundle.)"""
    try:
        return bool(bundle.load_pins().get("release", {}).get("url"))
    except (OSError, ValueError, KeyError):
        return False


def check_computer() -> dict:
    """What this computer has for an installation. Docker builds the speaker's software, unless a
    bundle is built already; then it is not needed. A published bundle is downloaded instead (Docker
    only builds one when that download fails)."""
    ready = (bundle.CACHE / "bundle" / "VERSIONS").exists()
    published = not ready and _published()
    checks = [_item("python", "ok", "python_ok", {"version": platform.python_version()})]
    if ready:
        checks.append(_item("docker", "ok", "docker_not_needed"))
    else:
        state, said = docker_state()
        if state == "ok":
            checks.append(_item("docker", "ok", "docker_ok", {"version": said}))
        elif published:
            checks.append(_item("docker", "ok", "docker_published"))
        else:
            checks.append(_item("docker", "bad", f"docker_{state}", problem=True))
    free = shutil.disk_usage(_existing(bundle.CACHE)).free
    gb = {"free": f"{free / 1024 ** 3:.1f}"}
    checks.append(_item("disk", "warn", "disk_low", gb) if free < DISK_MIN and not (ready or published)
                  else _item("disk", "ok", "disk_ok", gb))
    version = bundle.read_versions(bundle.CACHE / "bundle").get("librespot", "") if ready else ""
    checks.append(_item("bundle", "ok", "bundle_present", {"version": version}) if ready
                  else _item("bundle", "ok", "bundle_published" if published else "bundle_absent"))
    return {"checked": time.time(), "ok": all(c["status"] != "bad" for c in checks), "bundle": ready,
            "checks": checks}


def open_docker_desktop() -> bool:
    """Start Docker Desktop (Windows, macOS); False when it is not installed. It must outlive the
    wizard: no process group of the wizard's, no window of its."""
    if OS == "windows":
        detached = getattr(subprocess, "DETACHED_PROCESS", 0x08) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP",
                                                                            0x200)
        # (64-bit Program Files also from a 32-bit Python; Windows' variable names ignore case)
        roots = (os.environ.get("PROGRAMW6432"), os.environ.get("PROGRAMFILES"), r"C:\Program Files")
        for root in dict.fromkeys(filter(None, roots)):
            exe = Path(root) / "Docker" / "Docker" / "Docker Desktop.exe"
            if exe.is_file():
                subprocess.Popen([str(exe)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, close_fds=True, creationflags=detached)
                return True
        return False
    if OS == "macos":
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            return subprocess.run(["open", "-a", "Docker"], capture_output=True, stdin=subprocess.DEVNULL,
                                  timeout=30).returncode == 0
    return False


def probe_speaker(host: str, with_info: bool) -> dict:
    """Is the speaker at `host` one Lithify supports? (Read-only: what it reports, and its system's
    build properties over the console.) A librespot it runs already is named, so that installing
    again keeps its name. With `with_info`, also its name and model, for an address typed in."""
    d = Device(config.Speaker("probe", host, "probe"))
    info = d.info()
    lr = info.get("librespot") or {}
    out: dict = {"librespot_name": discovery.printable(lr.get("name")),
                 "librespot_version": discovery.printable(lr.get("version"))}
    answered = True
    if with_info:
        name, model = discovery.printable(info.get("speaker_name")), discovery.printable(info.get("model"))
        out.update({k: v for k, v in (("name", name), ("official_name", name), ("model", model)) if v})
        out["official_listed"] = bool(info.get("official_spotify"))  # (hidden while Lithify works, if so set)
        answered = any(info.get(k) for k in ("model", "official_spotify", "cast", "librespot"))
    try:
        plat = d.platform()
    except console.ConsoleError as e:  # (LS10 models have no service console)
        return {**out, "supported": False, "reason_key": "no_console" if answered else "not_found",
                "reason": scrub(str(e))}
    except RuntimeError as e:
        return {**out, "supported": False, "reason_key": "unsupported_model", "reason": scrub(str(e))}
    except (OSError, ValueError) as e:
        return {**out, "supported": False, "reason_key": "probe_failed", "reason": scrub(str(e))}
    if not plat.supported:
        return {**out, "supported": False, "reason_key": "unsupported_model", "reason": plat.key}
    return {**out, "supported": True, "reason_key": None, "reason": "", "platform": plat.key}


def _safe_probe(host: str, with_info: bool) -> dict:
    try:
        return probe_speaker(host, with_info)
    except Exception as e:  # noqa: BLE001 - one odd device must not end the search
        return {"supported": False, "reason_key": "probe_failed", "reason": scrub(str(e))}


def firewall_help() -> dict:
    """The ports the speaker connects to on this computer, and the commands that open them on Linux."""
    ip = discovery.local_ipv4()
    lan = str(ipaddress.ip_network(f"{ip}/24", strict=False)) if ip else "192.168.1.0/24"
    ports = hostos.FIREWALL_PORTS
    firewalld = " ".join(f"--add-port={p}/tcp" for p in ports.split(","))
    return {"ports": ports, "lan": lan, "commands": [
        f"sudo ufw allow proto tcp from {lan} to any port {ports.replace('-', ':')}",
        f"sudo firewall-cmd --permanent {firewalld} && sudo firewall-cmd --reload"]}


def child_env(name: str | None) -> dict:
    """The environment of a `lithify` child: its output as it comes, in UTF-8, and the name for
    Spotify (config.py: LITHIFY_NAME). The speaker is chosen here: LITHIFY_HOST never redirects it."""
    env = {k: v for k, v in os.environ.items() if k not in ("LITHIFY_HOST", "LITHIFY_NAME")}
    env.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    if name:
        env["LITHIFY_NAME"] = name
    return env


def stream(cmd: list[str], env: dict, on_line: Callable[[str], None],
           on_start: Callable[[subprocess.Popen], None]) -> int:
    """Run `cmd` in a process group of its own (so it can be stopped with all it started) and pass
    on its output line by line, stdout and stderr in the order they came; its exit code."""
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             env=env, text=True, encoding="utf-8", errors="replace", **hostos.own_group())
    except OSError as e:
        on_line(f"error: cannot run {cmd[0]}: {e}")
        return 127
    on_start(p)
    if p.stdout is None:  # (stdout=PIPE always gives one)
        return p.wait()
    with p.stdout:
        for line in p.stdout:
            on_line(line.rstrip("\r\n"))
    return p.wait()


def quick(cmd: list[str], env: dict, timeout: float = 60) -> tuple[int, str]:
    """A short `lithify` command: its exit code and output."""
    try:
        r = hostos.run(cmd, timeout=timeout, env=env, text=True, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return r.returncode, f"{r.stdout}\n{r.stderr}"


def terminate(p: subprocess.Popen) -> None:
    """Stop an installation: SIGTERM first (the build removes its containers), then everything."""
    hostos.terminate_tree(p, CANCEL_GRACE)


# ── the state ───────────────────────────────────────────────────────────────

class Task:
    """One long step (a check, a search, an installation): its output and how it ended."""

    def __init__(self, kind: str, started: float):
        self.kind, self.started = kind, started
        self.lines: collections.deque[str] = collections.deque(maxlen=MAX_LINES)
        self.count = 0  # lines seen, also those no longer kept
        self.finished = False
        self.ok: bool | None = None
        self.ended: float | None = None
        self.error_key: str | None = None
        self.error_detail = ""
        self.error_params: dict = {}
        self.extras: dict = {}
        self.cancelled = False
        self.hung = False
        self.proc: subprocess.Popen | None = None
        self.phase: str | None = None
        self.step: str | None = None       # the build's own step ("compile")
        self.progress: float | None = None  # how far along the build is
        self.phases = {p: {"state": "pending", "started": None, "ended": None} for p in PHASES}

    def enter(self, phase: str, now: float) -> None:
        """Begin `phase`: the one before it is done, those passed over were not needed."""
        for p in PHASES[:PHASES.index(phase)]:
            st = self.phases[p]
            if st["state"] == "active":
                st.update(state="done", ended=now)
            elif st["state"] == "pending":
                st["state"] = "skipped"
        self.phases[phase].update(state="active", started=now)
        self.phase, self.step, self.progress = phase, None, None

    def add(self, line: str, now: float) -> None:
        self.lines.append(line)
        self.count += 1
        if self.kind != "install":
            return
        if self.phase == "build" and (m := _STEP_NOTE.fullmatch(line)):
            self._advance(m)
        if not line.startswith("==> "):
            return
        nxt = phase_of(line, self.phase)
        if nxt:
            self.enter(nxt, now)
        if self.phase == "build" and (step := build_step(line[4:])):
            self.step, self.progress = step
        elif self.phase == "prepare":  # (Windows waits for an answer to its question until the next line)
            self.step = "firewall" if line[4:].startswith(hostos.FIREWALL_ASK) else None

    def _advance(self, m: re.Match) -> None:
        """A long step has come further: the bar moves within that step's part (never back)."""
        span = _STEP_SPAN.get(self.step or "")
        if span is None or bool(m[1] or m[4] or m[5]) != (self.step == "image"):
            return
        if m[5]:  # (saved and unpacked after its last step)
            done = 0.92
        elif m[4]:  # (the base image: the first part of the image's time)
            done = IMAGE_DOWNLOAD_PART * min(int(m[4]) / IMAGE_DOWNLOAD_MB, 1.0)
        elif m[1]:  # (step k begins; the first one is the download)
            k, n = int(m[1]), max(1, int(m[2]))
            done = IMAGE_DOWNLOAD_PART + (1 - IMAGE_DOWNLOAD_PART) * (k - 1) / n if k > 1 else 0.0
        else:
            done = int(m[3]) / LIBRESPOT_CRATES
        low, high = span
        self.progress = max(self.progress or low, low + (high - low) * min(done, 0.98))

    def end(self, ok: bool, now: float, key: str | None, detail: str, params: dict, more: dict) -> None:
        self.finished, self.ok, self.ended = True, ok, now
        self.error_key, self.error_detail, self.error_params, self.extras = key, detail, params, more
        if self.kind != "install":
            return
        for st in self.phases.values():
            if st["state"] == "active":
                st.update(state="done" if ok else "failed", ended=now)
            elif st["state"] == "pending" and ok:
                st["state"] = "skipped"
        if ok:
            self.phase, self.step, self.progress = "done", None, None

    def view(self, now: float) -> dict:
        return {"kind": self.kind, "started": self.started, "ended": self.ended,
                "elapsed": (self.ended or now) - self.started, "lines": list(self.lines), "line_count": self.count,
                "finished": self.finished, "ok": self.ok, "cancelled": self.cancelled, "error_key": self.error_key,
                "error_fix": f"fix_{self.error_key}" if self.error_key else None,
                "error_detail": self.error_detail, "error_params": dict(self.error_params),
                "error_link": self.extras.get("link"), "error_commands": list(self.extras.get("commands", [])),
                "error_action": self.extras.get("action"), "phase": self.phase, "step": self.step,
                "progress": self.progress, "phases": [{"key": p, **self.phases[p]} for p in PHASES]}


def _speaker(found: dict, manual: bool = False) -> dict:
    """A speaker in the list. One that discovery found through the official Spotify (TCP 9095)
    answered under its name: that entry stays in the Spotify apps next to Lithify's. (Found through
    Lithify's page or as a Cast speaker, its official Spotify does not answer: hidden, or stopped.)"""
    name = found.get("name") or ""
    listed = not manual and found.get("found_by", "spotify") == "spotify"
    return {"host": found["host"], "name": name, "model": found.get("model") or "", "official_name": name,
            "official_listed": listed, "librespot_name": "", "librespot_version": "", "supported": None,
            "reason_key": None, "reason": "", "platform": None, "manual": manual}


class Wizard:
    """The wizard's state, shared by the page's requests and the steps running in the background.
    Every change counts `rev` up: the page draws again only when it changed."""

    def __init__(self, lang: str | None = None, config_path: str | None = None):
        self.lang, self.config_path = lang, config_path
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.rev = 0
        self.step = "welcome"
        self.task: Task | None = None
        self.computer: dict | None = None
        self.speakers: list[dict] = []
        self.searched = False
        self.chosen: dict | None = None
        self.outcome: dict | None = None
        self.installed = False  # an installation succeeded (the exit code)
        self.firewall = firewall_help()

    # ── for the page ──
    def state(self) -> dict:
        now = time.time()
        with self.lock:
            return {"rev": self.rev, "now": now, "version": __version__, "os": OS, "lang": self.lang,
                    "step": self.step, "task": self.task.view(now) if self.task else None,
                    "computer": copy.deepcopy(self.computer), "speakers": [dict(s) for s in self.speakers],
                    "searched": self.searched, "chosen": dict(self.chosen) if self.chosen else None,
                    "outcome": dict(self.outcome) if self.outcome else None, "installed": self.installed,
                    "firewall": copy.deepcopy(self.firewall)}

    def check_computer(self, _body: dict) -> tuple[int, dict]:
        return (202, {"started": True}) if self._begin("check", self._run_check, step="computer") else BUSY

    def start_docker(self, _body: dict) -> tuple[int, dict]:
        if OS == "linux":
            return 400, {"error": "start Docker with systemctl on Linux", "error_key": "no_desktop"}
        return (202, {"started": True}) if self._begin("start-docker", self._run_start_docker) else BUSY

    def discover(self, _body: dict) -> tuple[int, dict]:
        return (202, {"started": True}) if self._begin("discover", self._run_discover, step="speaker") else BUSY

    def add_host(self, body: dict) -> tuple[int, dict]:
        host = check_host(body.get("host"))
        if host is None:
            return 400, {"error": "not an IP address or a host name", "error_key": "bad_host"}
        with self.lock:
            if not self._begin("probe", self._run_probe, host, step="speaker"):
                return BUSY
            entry = {**_speaker({"host": host}, manual=True),
                     **next((s for s in self.speakers if s["host"] == host), {}), "supported": None}
            # (an address typed before where nothing answered was a typo: the new one replaces it)
            self.speakers = [entry, *(s for s in self.speakers if s["host"] != host
                                      and not (s.get("manual") and s.get("reason_key") == "not_found"))]
            self._changed()
        return 202, {"started": True, "host": host}

    def set_step(self, body: dict) -> tuple[int, dict]:
        """Go back. (The installation and the result are steps the wizard takes itself.)"""
        step = body.get("step")
        with self.lock:
            if self.task and not self.task.finished and self.task.kind == "install":
                return BUSY
            if step not in ("welcome", "computer", "speaker"):
                return 400, {"error": f"no step {step!r} to go to", "error_key": "bad_step"}
            self.step = step
            self._changed()
        return 200, {"step": step}

    def choose(self, body: dict) -> tuple[int, dict]:
        """The speaker to install on (its name and the Install button show under the list)."""
        with self.lock:
            found = self._supported(check_host(body.get("host")))
            if found is None:
                return 400, {"error": "choose a supported speaker", "error_key": "bad_speaker"}
            self.chosen = self._choose(found)
            self._changed()
        return 200, {"chosen": found["host"]}

    def install(self, body: dict) -> tuple[int, dict]:
        name = check_name(body.get("name"))
        if name is None:
            return 400, {"error": "the name must have 1 to 64 characters", "error_key": "bad_name"}
        with self.lock:
            found = self._supported(check_host(body.get("host")))
            if found is None:
                return 400, {"error": "choose a supported speaker", "error_key": "bad_speaker"}
            if self.task and not self.task.finished:
                return BUSY
            self.chosen = {**self._choose(found), "spotify_name": name}
            self.outcome = None
            self._begin("install", self._run_install, found["host"], name, step="install")
        return 202, {"started": True}

    def cancel(self, _body: dict) -> tuple[int, dict]:
        with self.lock:
            t = self.task
            if t is None or t.finished or t.kind not in ("install", "start-docker"):
                return 409, {"error": "nothing to stop", "error_key": "nothing"}
            t.cancelled = True
            p = t.proc
            self._changed()
        if p is not None:
            threading.Thread(target=terminate, args=(p,), name="wizard-cancel", daemon=True).start()
        return 202, {"stopping": True}

    def quit(self, _body: dict) -> tuple[int, dict]:
        return 200, {"bye": True}  # (the server stops once this answer is out, see Handler.do_POST)

    def close(self) -> None:
        """The wizard ends: what it started ends with it (an installation would go on unseen)."""
        self.stopping.set()
        with self.lock:
            t = self.task
            running = t is not None and not t.finished
            if running:
                t.cancelled = True
            p = t.proc if running else None
        if p is not None and p.poll() is None:
            print(f"stopping the running installation (up to {CANCEL_GRACE} s)...", flush=True)
            terminate(p)

    # ── bookkeeping ──
    def _changed(self) -> None:
        with self.lock:
            self.rev += 1

    def _supported(self, host: str | None) -> dict | None:
        return next((s for s in self.speakers if host and s["host"] == host and s.get("supported")), None)

    @staticmethod
    def _choose(s: dict) -> dict:
        """The speaker to install on, and the name it is offered (the one it has, if Lithify runs on it)."""
        current = s.get("librespot_name") or ""
        return {"host": s["host"], "name": s.get("name") or "", "model": s.get("model") or "",
                "official_name": (s.get("official_name") or "") if s.get("official_listed") else "",
                "current_name": current, "librespot_version": s.get("librespot_version") or "",
                "default_name": current or default_name(s.get("name") or "")}

    def _begin(self, kind: str, target: Callable, *args, step: str | None = None) -> bool:
        """Start a long step in a thread of its own; False while another one runs."""
        with self.lock:
            if self.task and not self.task.finished:
                return False
            self.task = Task(kind, time.time())
            if step:
                self.step = step
            self._changed()
        threading.Thread(target=self._guarded, args=(target, *args), name=f"wizard-{kind}", daemon=True).start()
        return True

    def _guarded(self, target: Callable, *args) -> None:
        try:
            target(*args)
        except Exception as e:  # noqa: BLE001 - shown on the page: a step never stays "running"
            self._finish(False, "unknown", scrub(f"{type(e).__name__}: {e}"))

    def _line(self, text: str) -> None:
        line = scrub(_ESCAPES.sub("", text))
        with self.lock:
            if self.task:
                self.task.add(line, time.time())
                self._changed()

    def _finish(self, ok: bool, key: str | None = None, detail: str = "", params: dict | None = None) -> None:
        with self.lock:
            if self.task and not self.task.finished:
                more = extras(key, self.firewall) if key else {}
                self.task.end(ok, time.time(), key, detail[-6000:], params or {}, more)
                self._changed()

    def _cancelled(self) -> bool:
        with self.lock:
            return bool(self.task and self.task.cancelled) or self.stopping.is_set()

    def _update_speaker(self, host: str, result: dict) -> None:
        with self.lock:
            self.speakers = [{**s, **result} if s["host"] == host else s for s in self.speakers]
            self._changed()

    # ── the steps ──
    def _run_check(self) -> None:
        result = check_computer()
        with self.lock:
            self.computer = result
        self._finish(True)
        self._on_if_ready(result)

    def _on_if_ready(self, result: dict) -> None:
        """Everything is fine on this computer: on to the speakers without a click (a warning or a
        problem stays on the screen)."""
        if all(c["status"] == "ok" for c in result["checks"]) and not self.stopping.is_set():
            self._begin("discover", self._run_discover, step="speaker")

    def _pick(self, typed: str | None = None) -> None:
        """The speaker to install on, once the search is done: a typed address when it is supported,
        else the one chosen before while it is still there, else the only supported one."""
        with self.lock:
            ok = [s for s in self.speakers if s.get("supported")]
            keep = typed or (self.chosen or {}).get("host")
            found = next((s for s in ok if s["host"] == keep), None)
            if found is None and len(ok) == 1:
                found = ok[0]
            if found is not None or not typed:
                self.chosen = self._choose(found) if found else None
            self._changed()

    def _run_start_docker(self) -> None:
        if not open_docker_desktop():
            self._finish(False, "docker_desktop_missing")
            return
        self._line("Docker Desktop is starting")
        end = time.monotonic() + DOCKER_START_WAIT
        while docker_state()[0] != "ok":
            if self._cancelled():
                self._finish(False, "cancelled")
                return
            if time.monotonic() >= end:
                self._finish(False, "docker_start_timeout")
                return
            self.stopping.wait(DOCKER_POLL)
        self._line("Docker is running")
        result = check_computer()
        with self.lock:
            self.computer = result
        self._finish(True)
        self._on_if_ready(result)

    def _run_discover(self) -> None:
        found = discovery.discover()
        hosts = [f["host"] for f in found]
        with self.lock:
            typed = [s for s in self.speakers if s.get("manual") and s["host"] not in hosts]
            self.speakers = [*(_speaker(f) for f in found), *typed]  # (a typed address stays in the list)
            self.searched = True
            self._changed()
        self._line(f"found {len(found)} Lithe Audio speaker(s): {', '.join(hosts) or '-'}")
        self._probe_all(hosts, with_info=False)
        self._pick()
        self._finish(True)

    def _run_probe(self, host: str) -> None:
        self._probe_all([host], with_info=True)
        self._pick(typed=host)
        self._finish(True)

    def _probe_all(self, hosts: list[str], with_info: bool) -> None:
        """Every speaker at once (threads of their own: one that never answers must not hold up the
        wizard, not even when it ends); one still silent after PROBE_TIMEOUT counts as not answering."""
        left = set(hosts)
        done = threading.Event()

        def one(host: str) -> None:
            self._update_speaker(host, _safe_probe(host, with_info))
            with self.lock:
                left.discard(host)
                if not left:
                    done.set()

        for h in hosts:
            threading.Thread(target=one, args=(h,), name=f"wizard-probe-{h}", daemon=True).start()
        if hosts and not done.wait(PROBE_TIMEOUT):
            with self.lock:
                silent = set(left)
            for h in silent:
                self._update_speaker(h, {"supported": False, "reason_key": "no_answer", "reason": ""})

    def _lithify(self) -> list[str]:
        return [sys.executable, str(ROOT / "bin" / "lithify"), *(["--config", self.config_path] if self.config_path
                                                                  else [])]

    def _run(self, args: list[str], env: dict) -> int:
        """`lithify <args>` as a child, its output in the task's log."""
        self._line(f"$ lithify {' '.join(args)}")
        return stream([*self._lithify(), *args], env, self._line, self._started)

    def _started(self, p: subprocess.Popen) -> None:
        with self.lock:
            if self.task:
                self.task.proc = p
            stop = self._cancelled()
        if stop:  # (cancelled while it was starting)
            threading.Thread(target=terminate, args=(p,), name="wizard-cancel", daemon=True).start()

    def _hung(self) -> None:
        with self.lock:
            p = self.task.proc if self.task else None
            if self.task:
                self.task.hung = True
        if p is not None:
            terminate(p)

    def _run_install(self, host: str, name: str) -> None:
        with self.lock:
            t = self.task
            t.enter("prepare", time.time())  # (a speaker configured before prints nothing for it)
        timer = threading.Timer(INSTALL_TIMEOUT, self._hung)
        timer.daemon = True
        timer.start()
        try:
            rc = self._run(["install", "--host", host, "--reboot"], child_env(name))
        finally:
            timer.cancel()
        with self.lock:
            lines, phase, cancelled, hung = list(t.lines), t.phase, t.cancelled, t.hung
        if rc != 0:
            self._failed(lines, phase, cancelled, hung)
            return
        with self.lock:
            if t.phase != "check":
                t.enter("check", time.time())
            t.proc = None
            before = t.count
            self._changed()
        # The helper that keeps the speaker updatable from its page (`lithify serve`), as a service.
        helper = self._run(["serve", "--install-service"], child_env(None))
        problem, detail = None, ""
        if helper != 0:
            with self.lock:
                said = list(t.lines)[-(t.count - before):] if t.count > before else []
            problem = "helper_no_systemd" if any("no systemd user session" in s for s in said) else "helper_failed"
            detail = "\n".join(error_block(said))
        page = self._page_url(host)
        with self.lock:
            # (cli.cmd_install: installed, but its page did not answer within 45 s)
            quiet = any(s.startswith("warning: the agent's web page does not answer") for s in lines)
            self.outcome = {"host": host, "page_url": page, "spotify_name": name, "page_quiet": quiet,
                            "helper_ok": helper == 0, "helper_key": problem, "helper_detail": detail}
            self.installed = True
            self.step = "done"
            self._finish(True)

    def _failed(self, lines: list[str], phase: str | None, cancelled: bool, hung: bool) -> None:
        if cancelled:
            key, detail = "cancelled", ""
        elif hung:
            key, detail = "timeout", "\n".join(lines[-30:])
        else:
            key, detail = classify(lines, phase)
        if phase == "build":  # (containers of a build that was stopped would run on for up to an hour)
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                bundle.remove_stale_containers()
        params = {"log": scrub(str(bundle.CACHE / "build.log"))} if key == "build_failed" else {}
        self._finish(False, key, detail, params)

    def _page_url(self, host: str) -> str:
        """The speaker's web page, as `lithify ui` says (its port may have been changed)."""
        rc, out = quick([*self._lithify(), "ui", "--speaker", host], child_env(None))
        pages = [line.strip() for line in out.splitlines() if PAGE_RE.fullmatch(line.strip())] if rc == 0 else []
        return pages[-1] if pages else f"http://{host}:{config.OPTIONS['ui_port'].default}/"


ACTIONS: dict[str, Callable[[Wizard, dict], tuple[int, dict]]] = {
    "/api/check-computer": Wizard.check_computer, "/api/start-docker": Wizard.start_docker,
    "/api/discover": Wizard.discover, "/api/add-host": Wizard.add_host, "/api/step": Wizard.set_step,
    "/api/choose": Wizard.choose,
    "/api/install": Wizard.install, "/api/cancel": Wizard.cancel, "/api/quit": Wizard.quit,
}


# ── the server ──────────────────────────────────────────────────────────────

def ui_file(name: str) -> bytes:
    """A file of the page (found in a checkout, and in an installed package)."""
    return importlib.resources.files(__package__).joinpath("wizard_ui", name).read_bytes()


class Handler(http.server.BaseHTTPRequestHandler):
    server: Server
    server_version = "lithify-wizard"
    sys_version = ""  # (no Python version in the Server header)
    timeout = 30      # a connection that stays silent is closed
    replied = False   # (this request's status line went out)

    def log_message(self, fmt, *args) -> None:
        pass  # (the terminal shows the address and what went wrong, not every poll)

    def send_response(self, code, message=None) -> None:
        self.replied = True
        super().send_response(code, message)

    def _answer(self, handle: Callable[[], None]) -> None:
        """Every request gets an answer: an unexpected error is a 500 and one line in the terminal."""
        self.replied = False
        try:
            handle()
        except (ConnectionError, TimeoutError):
            raise  # the browser is gone: Server.handle_error says nothing
        except Exception as e:  # noqa: BLE001 - answered, and logged
            where = urllib.parse.urlsplit(self.path).path  # (never the query: the page's address has the token)
            print(f"wizard: {self.command} {where}: internal error: {e!r}", file=sys.stderr, flush=True)
            if self.replied:
                self.close_connection = True  # half an answer went out: it ends there
            else:
                self._json(500, {"error": f"internal error: {e}"})

    def end_headers(self) -> None:
        for k, v in SECURITY_HEADERS:
            self.send_header(k, v)
        super().end_headers()

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        """`Host` names this server: a page whose name an attacker points at 127.0.0.1 (DNS
        rebinding) is someone else's site and gets nothing."""
        if self.headers.get("Host", "").strip().lower() in self.server.hosts:
            return True
        self._json(421, {"error": "this server answers 127.0.0.1 and localhost only"})
        return False

    def _api_ok(self) -> bool:
        """A request of the page itself: same origin, and the token of the address the wizard opened."""
        origin, site = self.headers.get("Origin"), self.headers.get("Sec-Fetch-Site")
        if (origin is not None and origin.lower() not in self.server.origins) or site not in (None, "same-origin",
                                                                                                "none"):
            self._json(403, {"error": "requests from other sites are refused"})
            return False
        given = self.headers.get(HEADER, "").encode("utf-8", "replace")
        if not hmac.compare_digest(given, self.server.token.encode()):
            self._json(403, {"error": "no access: open the address Lithify printed when it started",
                             "error_key": "token"})
            return False
        return True

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("bad Content-Length") from None
        if not 0 <= n <= MAX_BODY:
            raise ValueError("request too large")
        data = json.loads(self.rfile.read(n) or b"{}") if n else {}
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data

    def do_GET(self) -> None:
        self._answer(self._get)

    def do_POST(self) -> None:
        self._answer(self._post)

    def _get(self) -> None:
        if not self._host_ok():
            return
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/api/"):
            if self._api_ok():
                if path == "/api/state":
                    self._json(200, self.server.wiz.state())
                else:
                    self._json(404, {"error": "not found"})
            return
        name = FILES.get(path)
        if name is None:
            self._json(404, {"error": "not found"})
            return
        self._send(200, ui_file(name), TYPES[Path(name).suffix])

    def _post(self) -> None:
        if not self._host_ok() or not self._api_ok():
            return
        path = urllib.parse.urlsplit(self.path).path
        action = ACTIONS.get(path)
        if action is None:
            self._json(404, {"error": "not found"})
            return
        try:
            body = self._body()
        except ValueError as e:  # (json.JSONDecodeError too)
            self._json(400, {"error": str(e)})
            return
        code, reply = action(self.server.wiz, body)
        self._json(code, reply)
        if path == "/api/quit":  # "Finish": the answer is out, now the wizard ends
            self.server.stop()

    def do_OPTIONS(self) -> None:  # (a preflight: other sites get no permission for anything)
        self._json(403, {"error": "requests from other sites are refused"})


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = not hostos.WINDOWS  # (Windows: a second server would share the port)

    def __init__(self, address: tuple[str, int], wiz: Wizard, token: str):
        self.wiz, self.token = wiz, token
        super().__init__(address, Handler)
        port = self.server_address[1]
        names = ("127.0.0.1", "localhost")
        self.hosts = {f"{n}:{port}" for n in names} | (set(names) if port == 80 else set())
        self.origins = {f"http://{h}" for h in self.hosts}

    def stop(self) -> None:
        """End serve_forever (from a request's thread: shutdown() waits for the loop)."""
        threading.Thread(target=self.shutdown, name="wizard-stop", daemon=True).start()

    def handle_error(self, request, client_address) -> None:
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return  # a browser that closed its connection: not worth a traceback
        super().handle_error(request, client_address)


# ── `lithify wizard` ────────────────────────────────────────────────────────

LANG_POLISH = 0x15  # Windows' primary language id of Polish


def _windows_ui_polish() -> bool | None:
    """Is Windows shown in Polish? (Its display language, which the browser follows too, not the
    regional format: an English Windows set up in Poland formats dates the Polish way.)"""
    try:
        import ctypes
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None
    return (langid & 0x3FF) == LANG_POLISH if langid else None


def _polish(lang: str | None) -> bool:
    """Does this computer's user read Polish? (for the few lines in the terminal)"""
    if lang:
        return lang == "pl"
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(var):
            return os.environ[var].lower().startswith("pl")
    if OS == "windows" and (ui := _windows_ui_polish()) is not None:
        return ui
    with contextlib.suppress(ValueError):
        return (locale.getlocale()[0] or "").lower().startswith(("pl", "polish"))
    return False


def _open(url: str) -> bool:
    """Open `url` in the default browser; False when none can be opened. Without a desktop (an SSH
    session, a server) Linux has only text browsers, which would take this terminal over."""
    if OS == "linux" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    opened: list[bool] = []

    def try_open() -> None:
        try:
            opened.append(webbrowser.open(url))
        except webbrowser.Error:
            opened.append(False)

    t = threading.Thread(target=try_open, name="wizard-browser", daemon=True)
    t.start()
    t.join(15)  # (some systems' handlers wait for the browser: the wizard does not)
    return bool(opened and opened[0])


def _tell(url: str, opened: bool, tried: bool, polish: bool) -> None:
    if polish:
        print(f"Instalator Lithify działa pod adresem:\n\n    {url}\n", flush=True)
        if opened:
            print("Otworzył się w przeglądarce; jeśli nie, otwórz powyższy adres.")
        else:
            print("Otwórz powyższy adres w przeglądarce." if not tried
                  else "Nie udało się otworzyć przeglądarki: otwórz powyższy adres w przeglądarce.")
        print('Nie zamykaj tego okna. Gdy skończysz, kliknij "Zakończ" na stronie (albo naciśnij tu Ctrl-C).',
              flush=True)
        return
    print(f"Lithify setup is running at:\n\n    {url}\n", flush=True)
    if opened:
        print("It opened in your web browser; if it did not, open the address above.")
    else:
        print("Open the address above in your web browser." if not tried
              else "No web browser could be opened: open the address above in your browser.")
    print('Leave this window open. When you are done, click "Finish" on the page (or press Ctrl-C here).',
          flush=True)


def run(lang: str | None = None, open_browser: bool = True, port: int = 0, *, config_path: str | None = None) -> int:
    """Serve the wizard on 127.0.0.1 until "Finish" (or Ctrl-C); 0 when an installation succeeded."""
    wiz = Wizard(lang=lang, config_path=config_path)
    token = secrets.token_hex(16)
    try:
        srv = Server(("127.0.0.1", port), wiz, token)
    except (OSError, OverflowError) as e:  # (a port in use, or none at all: 70000)
        print(f"error: cannot start the setup page on 127.0.0.1:{port}: {e}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{srv.server_address[1]}/?t={token}"
    _tell(url, open_browser and _open(url), open_browser, _polish(lang))
    try:
        srv.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        wiz.close()
        srv.server_close()
    return 0 if wiz.installed else 1
