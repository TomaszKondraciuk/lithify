"""`lithify serve`: build + update service used by the web page on the speakers (LAN only).

Only the speakers listed in config.toml may call it, and each only for its own files. Requests from
web browsers (any `Origin` header) and requests whose `Host` is a foreign name (DNS rebinding) are
refused, and connections from other addresses are closed before they are read. Bundles are staged
per speaker from config.toml, so the page installs exactly what `lithify install` would.
"""
from __future__ import annotations

import collections
import contextlib
import hashlib
import http.server
import ipaddress
import itertools
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from . import bundle, config, hostos, platforms, service, updates
from .device import ROOT

HOME = str(Path.home())
# The running server, and whether it should start again with new code (see service.RESTART).
_SERVER: http.server.HTTPServer | None = None
_RESTART = threading.Event()
STATE = bundle.CACHE / "companion-build.json"
# A build that takes longer than this has hung (a fresh build with every download takes ~15 min).
BUILD_TIMEOUT = 2 * 3600
# How long a build that is asked to stop (SIGTERM) gets to remove its containers before it is killed.
BUILD_GRACE = 30
# "Check now" (fresh=1) asks the upstream sources at most this often; meanwhile it gets that answer.
FRESH_EVERY = 20.0
# How long staging waits for a build that is swapping its bundle in, and when to ask again then.
STAGE_LOCK_WAIT = 20.0
RETRY_AFTER = 10
# Control characters (terminal escapes among them) as they appear in the log: written out.
_CONTROL = str.maketrans({c: f"\\x{c:02x}" for c in itertools.chain(range(0x20), range(0x7F, 0xA0))}
                         | {ord("\\"): "\\\\"})


def printable(text: str) -> str:
    """What a peer sent, safe to print: no control characters, so no terminal escapes."""
    return str(text).translate(_CONTROL)


class _Check:
    """The last update check (asking the internet and the dependencies takes a while). One runs
    at a time: a caller that comes while it runs waits for its answer instead of starting another."""
    lock = threading.Lock()
    result: dict | None = None
    at = -math.inf                           # when `result` was computed (time.monotonic)
    generation = 0                           # +1 when a new build makes `result` old
    running: threading.Event | None = None   # set once the running check has its answer


def _usable(fresh: bool) -> bool:
    """Is the last answer good enough for this caller? (with _Check.lock held)"""
    if _Check.result is None:
        return False
    age = time.monotonic() - _Check.at
    if fresh:
        return age < FRESH_EVERY
    # A full answer is kept for 30 min; one with sources that did not answer for 2 min only.
    rows = _Check.result["rows"]
    complete = bool(rows) and all(r["status"] != "unknown" for r in rows) and \
        any(r["component"] == "dependencies" for r in rows)
    return age <= (1800 if complete else 120)


def check_updates(fresh: bool) -> dict:
    while True:
        with _Check.lock:
            if _usable(fresh):
                result = _Check.result
                break
            if _Check.running is None:  # this caller checks; the network is asked without the lock
                done = _Check.running = threading.Event()
                generation, result = _Check.generation, None
                break
            running = _Check.running
        running.wait(600)  # a check is running: its answer is this caller's too
    if result is None:
        try:
            # The dependency check reads the build's sources: not while a build replaces them.
            rows = updates.report(deps=not build_status()["running"])
            result = {"rows": rows, "text": updates.format_report(rows), "checked": _now()}
        finally:
            with _Check.lock:
                if result is not None:
                    _Check.result = result
                    # (a build that finished meanwhile made this answer old already)
                    _Check.at = time.monotonic() if _Check.generation == generation else -math.inf
                _Check.running = None
            done.set()
    return {**result, "bundle": bundle.read_versions(bundle.CACHE / "bundle")}


def _forget_check() -> None:
    """A new bundle: the next check compares with it."""
    with _Check.lock:
        _Check.generation += 1
        _Check.at = -math.inf


class _Build:
    """The companion's build, one at a time. Handler threads and the build's own thread read and
    change it under `lock` (re-entrant: a change saves the state while it holds the lock)."""
    lock = threading.RLock()
    running = False
    ok: bool | None = None
    started: str | None = None
    finished: str | None = None
    error = ""
    phase = ""  # the build's current step ("compiling librespot …"), shown on the speaker's page
    proc: subprocess.Popen | None = None  # the `lithify build` running now


_SAVED = ("running", "ok", "started", "finished", "error")


def _update(**fields) -> None:
    """Change the build's state and save it, as one step."""
    with _Build.lock:
        for k, v in fields.items():
            setattr(_Build, k, v)
        _save_state()


def build_status() -> dict:
    with _Build.lock:
        return {k: getattr(_Build, k) for k in (*_SAVED, "phase")}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def scrub(text: str) -> str:
    """Errors and build logs are shown on the speakers' web page: leave out this computer's home
    (written either way on Windows)."""
    return text.replace(HOME, "~").replace(HOME.replace("\\", "/"), "~")


def _save_state() -> None:
    """The build's state on disk, so the page still shows the last build after a restart. Written
    whole, and under the lock: two threads never mix their writes."""
    with _Build.lock:
        text = json.dumps({k: getattr(_Build, k) for k in _SAVED})
        try:
            hostos.write_atomic(STATE, text)  # never half a file, even when the computer stops in the middle
        except OSError as e:  # (a full disk) the state in memory still answers the speakers
            print(f"cannot save the build state: {e}", flush=True)


def _load_state() -> None:
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(st, dict):
        return
    with _Build.lock:
        _Build.ok, _Build.started, _Build.finished, _Build.error = (st.get("ok"), st.get("started"),
                                                                   st.get("finished"), st.get("error") or "")
        if st.get("running"):  # the companion stopped during a build: that build did not finish
            _update(ok=False, finished=_now(), error="interrupted: the companion was restarted")


def _starts(cmd: list[str]) -> bool:
    """Does the code on disk start at all (after Lithify updated itself)?"""
    try:
        return subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _run_build(latest: bool) -> None:
    """Build in a separate `lithify build` process, so it always runs the code on disk (also
    right after Lithify updated itself). It runs in a process group of its own and ends with the
    companion: a build that hangs, or a companion that stops, ends it with all it started."""
    head = ""
    ok, error = False, ""
    try:
        head = updates.head(ROOT)
        cmd = [sys.executable, str(ROOT / "bin" / "lithify"), "build", "--exit-with-parent",
               *(["--latest"] if latest else [])]
        # Read as it runs: its "==> step" lines tell the page where the build is.
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace", **hostos.own_group())
        with _Build.lock:
            _Build.proc = p
        hung = threading.Event()
        # SIGTERM first: the build stops its containers on the way out; killed after BUILD_GRACE.
        timer = threading.Timer(BUILD_TIMEOUT, lambda: (hung.set(), hostos.terminate_tree(p, BUILD_GRACE)))
        timer.daemon = True
        timer.start()
        out: collections.deque[str] = collections.deque(maxlen=300)
        try:
            if p.stdout is None:  # (stdout=PIPE always gives one)
                raise RuntimeError("the build's output cannot be read")
            for line in p.stdout:
                out.append(line)
                if line.startswith("==> "):
                    with _Build.lock:
                        _Build.phase = scrub(line[4:].strip())
            rc = p.wait()
        finally:
            timer.cancel()
        ok = rc == 0
        tail = scrub("".join(out).strip())
        if hung.is_set():
            error = f"the build did not finish within {BUILD_TIMEOUT // 3600} h and was stopped\n" + tail[-1500:]
        elif not ok:
            error = tail[-2000:]
    except Exception as e:  # noqa: BLE001 - reported to the page
        ok, error = False, scrub(str(e))[-2000:]
    finally:
        with _Build.lock:
            _Build.proc = None
            _update(ok=ok, error=error, finished=_now(), running=False)
        _forget_check()  # the next check compares with the new build
    if not ok:
        bundle.remove_stale_containers()  # (what a build that was stopped left running)
    now = updates.head(ROOT)
    if head and now and now != head:
        # Lithify updated itself: serve the new code from now on (systemd keeps the process),
        # unless it does not even start - then back to the code that works.
        if _starts([sys.executable, str(ROOT / "bin" / "lithify"), "--version"]):
            print("lithify updated itself: restarting the companion", flush=True)
            _RESTART.set()  # the worker ends; its supervisor starts it with the new code
            if _SERVER:
                _SERVER.shutdown()
            return
        undone, why = updates.undo_update(ROOT, head)
        if undone:
            print(f"lithify's update does not start: back to {head[:12]}", flush=True)
            _update(ok=False, error="the update of Lithify itself does not start; it was undone")
        else:
            print(f"lithify's update does not start, and going back to {head[:12]} failed: {why}", flush=True)
            _update(ok=False, error=scrub(f"the update of Lithify itself does not start, and undoing it failed "
                                          f"({why}); on the computer: git -C {ROOT} reset --keep {head[:12]}"))


def stop_build() -> None:
    """End a running build together with the companion: left behind, it would run on (and its
    containers with it) with nobody to report to."""
    with _Build.lock:
        p = _Build.proc
    if p is not None and p.poll() is None:
        print("stopping the running build", flush=True)
        hostos.terminate_tree(p, BUILD_GRACE)
        bundle.remove_stale_containers()


def _resolve(host: str) -> set[str]:
    """Every address of `host`: OSError when it has none right now, UnicodeError for a name IDNA
    cannot encode ("a..b")."""
    return {a[4][0] for a in socket.getaddrinfo(host, None)}


def _peer(ip: str) -> str:
    """A client's address the way speakers are known: IPv4 also when it came in over IPv6."""
    if ip.lower().startswith("::ffff:") and "." in ip:
        return ip[7:]
    return ip


class Speakers:
    """The speakers' addresses: one table, replaced as a whole and never changed. A thread of its
    own looks the names up again every minute (a lookup can take seconds: the server's accept loop
    only reads the table), and an address a speaker no longer has is gone with the next lookup."""

    def __init__(self, cfg: config.Config, resolve: Callable[[str], set[str]] = _resolve, every: float = 60.0):
        self.cfg, self.resolve, self.every = cfg, resolve, every
        # Until the first lookup: the hosts as written (an IP address needs none).
        self.table: Mapping[str, config.Speaker] = MappingProxyType({s.host: s for s in cfg.speakers})
        self.known: dict[str, set[str]] = {}  # speaker id -> its addresses at the last lookup that worked
        self._stop = threading.Event()

    def refresh(self) -> None:
        """Look every name up again. A lookup that fails (the name server for a moment, mDNS that
        does not answer) keeps that speaker's last addresses - one failed lookup must not lock a
        speaker out - and never stops the others; a name that never resolved stays as written."""
        table: dict[str, config.Speaker] = {}
        for s in self.cfg.speakers:
            with contextlib.suppress(OSError, UnicodeError):
                self.known[s.id] = set(self.resolve(s.host))
            for ip in self.known.get(s.id, set()) | {s.host}:
                table[ip] = s
        self.table = MappingProxyType(table)  # (one assignment: atomic)

    def start(self) -> Speakers:
        threading.Thread(target=self._run, name="speaker-addresses", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception as e:  # noqa: BLE001 - the last table stays
                print(f"cannot look up the speakers' addresses: {e}", flush=True)
            self._stop.wait(self.every)


def request_allowed(client_ip: str, host_header: str, origin: str | None,
                    allowed_ips, allowed_names: set[str]) -> bool:
    """Speakers only. The agent and install.sh never send `Origin`; a browser always does. `Host`
    must be an IP address or the companion's own configured name (no DNS rebinding)."""
    if origin is not None or client_ip not in allowed_ips:
        return False
    host = host_header.strip()
    if host.startswith("["):
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    if host.lower() in allowed_names:
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


class Stager:
    """Per-speaker staged files, rebuilt only when the bundle or the speaker's settings changed.
    Each version gets a directory of its own (<id>-<fingerprint>): a running download never meets
    a half-written one, and nothing in use is renamed (Windows refuses that). A download starts
    with SHA256SUMS and takes every file after it from that same version: one replaced by a newer
    version stays KEEP seconds, so a build that finishes meanwhile cannot mix two versions. What is
    served is this user's only (Linux, macOS): the staged settings may hold the page's PIN."""

    KEEP = 600.0

    def __init__(self, root: Path, public_url: str, clock: Callable[[], float] = time.monotonic):
        self.root, self.public_url, self.lock, self.clock = root, public_url, threading.Lock(), clock
        self.replaced: dict[Path, float] = {}               # earlier version -> when a newer one came
        self.downloads: dict[str, tuple[Path, float]] = {}  # speaker id -> (version, when it began)

    def fingerprint(self, s: config.Speaker, plat: platforms.Platform) -> str:
        sums = bundle.CACHE / "bundle" / "SHA256SUMS"
        h = hashlib.sha256(sums.read_bytes() if sums.exists() else b"")
        h.update(config.settings_conf(s).encode())
        h.update(config.install_conf(s, self.public_url, plat.key).encode())
        return h.hexdigest()

    def _versions(self, s: config.Speaker) -> list[Path]:
        """This speaker's version directories: exactly <id>-<16 hex digits>, so a speaker whose id
        merely begins the same way ("living" and "living-room") is never touched."""
        mine = re.compile(re.escape(s.id) + r"-[0-9a-f]{16}")
        try:
            return [p for p in self.root.iterdir() if mine.fullmatch(p.name)]
        except OSError:
            return []

    def get(self, s: config.Speaker) -> Path:
        """The speaker's newest version, staged when it is not there yet."""
        plat = platforms.PLATFORMS["ls9" if s.platform == "auto" else s.platform]
        with self.lock:
            # The bundle is read under its lock: a build swapping its bundle in (between its two
            # renames there is none) waits, and makes this wait (LockTimeoutError: try again soon).
            with bundle.bundle_lock(bundle.CACHE, timeout=STAGE_LOCK_WAIT):
                want = self.fingerprint(s, plat)
                dest = self.root / f"{s.id}-{want[:16]}"
                if not (dest / ".complete").is_file():
                    self._stage(s, plat, want, dest)
            # (also what an earlier Lithify staged with the modes of its day)
            hostos.make_private(self.root, files=False)
            hostos.make_private(dest)
            self._prune(s, dest)
            return dest

    def _stage(self, s: config.Speaker, plat: platforms.Platform, want: str, dest: Path) -> None:
        hostos.private_dir(self.root)
        tmp = self.root / f".{s.id}.new"
        hostos.rmtree(tmp)
        bundle.stage(bundle.CACHE / "bundle", s, plat, None, self.public_url, tmp)
        (tmp / ".complete").write_text(want, encoding="utf-8")
        hostos.rmtree(dest)  # (an unfinished one: no .complete)
        tmp.rename(dest)

    def for_download(self, s: config.Speaker, name: str) -> Path:
        """Where a speaker's download reads `name` from: SHA256SUMS begins one with the newest
        version; the files after it come from that version while it is kept."""
        if name != "SHA256SUMS":
            with self.lock:
                d, began = self.downloads.get(s.id, (None, -math.inf))
                if d is not None and self.clock() - began < self.KEEP and (d / ".complete").is_file():
                    return d
        d = self.get(s)
        if name == "SHA256SUMS":
            with self.lock:
                self.downloads[s.id] = (d, self.clock())
        return d

    def _prune(self, s: config.Speaker, current: Path) -> None:
        """Earlier versions go KEEP seconds after a newer one replaced them, never while a download
        that began with them may still run (Windows keeps one that is still open: next time)."""
        now = self.clock()
        self.replaced.pop(current, None)  # (current again: when it is replaced next, it is kept again)
        busy = {d for d, began in self.downloads.values() if now - began < self.KEEP}
        for old in self._versions(s):
            if old == current:
                continue
            replaced = self.replaced.setdefault(old, now)
            if old not in busy and now - replaced >= self.KEEP:
                hostos.rmtree(old)
                if not old.exists():
                    self.replaced.pop(old, None)
        legacy = self.root / s.id  # how versions were staged before they had directories of their own
        if (legacy / ".fingerprint").is_file():
            hostos.rmtree(legacy)


def make_handler(cfg: config.Config, public_url: str, speakers: Speakers | None = None):
    if speakers is None:
        speakers = Speakers(cfg)
        speakers.refresh()
    allowed_names = {(urllib.parse.urlparse(public_url).hostname or "").lower()} - {""}
    stager = Stager(bundle.CACHE / "companion", public_url)

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "lithify-companion"
        timeout = 15  # a connection that stays silent is closed
        replied = False  # (this request's status line went out)

        def log_message(self, fmt, *args):
            # The request line comes from the peer: control characters are written out.
            print(f"{self.client_address[0]} {printable(fmt % args)}", flush=True)

        def send_response(self, code, message=None):
            self.replied = True
            super().send_response(code, message)

        def _send(self, code: int, body: bytes, ctype: str = "application/json", headers: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj, headers: dict | None = None) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), headers=headers)

        def _send_file(self, path: Path) -> None:
            """Stream a file (librespot is 12 MB: not read into memory for every speaker)."""
            with path.open("rb") as f:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(os.fstat(f.fileno()).st_size))
                self.end_headers()
                shutil.copyfileobj(f, self.wfile, 256 * 1024)

        def _guard(self) -> config.Speaker | None:
            table = speakers.table  # one table for the whole check (a lookup may replace it meanwhile)
            ip = _peer(self.client_address[0])
            if not request_allowed(ip, self.headers.get("Host", ""), self.headers.get("Origin"), table, allowed_names):
                self._json(403, {"error": "this companion only answers the speakers in its config.toml"})
                return None
            return table[ip]

        def _staged(self, s: config.Speaker, name: str | None = None) -> Path | None:
            """The speaker's staged files (for a download: the version it began with); None after
            an error answer."""
            try:
                return stager.get(s) if name is None else stager.for_download(s, name)
            except bundle.LockTimeoutError as e:  # a build is swapping its bundle in: in a moment
                self._json(503, {"error": scrub(str(e))}, {"Retry-After": str(RETRY_AFTER)})
            except (bundle.BuildError, OSError, ValueError) as e:
                self._json(409, {"error": scrub(str(e))})
            return None

        def do_GET(self):
            self._answer(self._get)

        def do_POST(self):
            self._answer(self._post)

        def _answer(self, handle: Callable[[], None]) -> None:
            """Every request gets an answer: an unexpected error is a 500 with one log line."""
            self.replied = False
            try:
                handle()
            except (ConnectionError, TimeoutError):
                raise  # the speaker is gone: Server.handle_error logs one line
            except Exception as e:  # noqa: BLE001 - answered, and logged
                print(f"{self.client_address[0]} {printable(self.command)} {printable(self.path)}: "
                      f"internal error: {printable(repr(e))}", flush=True)
                if self.replied:
                    self.close_connection = True  # half an answer went out: it ends there
                else:
                    self._json(500, {"error": scrub(f"internal error: {e}")})

        def _get(self) -> None:
            s = self._guard()
            if not s:
                return
            url = urllib.parse.urlparse(self.path)
            if url.path == "/api/latest":
                asked = (urllib.parse.parse_qs(url.query).get("speaker") or [s.id])[0]
                if asked != s.id:
                    return self._json(403, {"error": "a speaker can only ask for its own files"})
                d = self._staged(s)
                if d is None:
                    return None
                sums = dict(reversed(f) for f in (line.split() for line in _lines(d / "SHA256SUMS")) if len(f) == 2)
                return self._json(200, {"versions": bundle.read_versions(d),
                                        "bundle_url": f"{public_url}/bundle/{s.id}",
                                        # what makes a version, and its checksums (the agent
                                        # compares them with what it runs)
                                        "binaries": " ".join(bundle.BINARIES),
                                        **{f"sha_{f}": sums.get(f, "") for f in bundle.BINARIES}})
            if url.path == "/api/build/status":
                st = build_status()
                return self._json(200, {**st, "error": scrub(st["error"] or ""),
                                        "log_tail": scrub(_tail(bundle.CACHE / "build.log"))})
            parts = url.path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "bundle" and parts[1] == s.id:
                d = self._staged(s, parts[2])
                if d is None:
                    return None
                names = {"SHA256SUMS"} | {f[1] for f in (line.split() for line in _lines(d / "SHA256SUMS"))
                                          if len(f) == 2}
                if parts[2] in names and (d / parts[2]).is_file():
                    return self._send_file(d / parts[2])
                return self._json(404, {"error": "no such file"})
            return self._json(404, {"error": "not found"})

        def _post(self) -> None:
            if not self._guard():
                return
            url = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(url.query)
            if url.path == "/api/check":
                return self._json(200, check_updates(query.get("fresh") == ["1"]))
            if url.path == "/api/build":
                latest = query.get("latest") == ["1"]
                with _Build.lock:
                    busy = _Build.running
                    if not busy:
                        _update(running=True, ok=None, started=_now(), finished=None, error="", phase="starting")
                if busy:
                    return self._json(200, {"started": False, "running": True})
                try:
                    threading.Thread(target=_run_build, args=(latest,), daemon=True).start()
                except RuntimeError as e:
                    _update(running=False, ok=False, error=f"cannot start the build: {e}")
                    return self._json(500, {"error": f"cannot start the build: {e}"})
                return self._json(202, {"started": True, "latest": latest})
            return self._json(404, {"error": "not found"})

    return Handler


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second server take the same port unnoticed.
    allow_reuse_address = not hostos.WINDOWS

    def __init__(self, address, handler, speakers: Speakers):
        self.speakers = speakers
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, handler)

    def server_bind(self):
        if self.address_family == socket.AF_INET6:  # "::" takes the IPv4 speakers too
            with contextlib.suppress(OSError, AttributeError):
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()

    def verify_request(self, request, client_address) -> bool:
        # Other addresses are closed before a thread is started for them. Only the table is read
        # here: the names are looked up by a thread of their own (Speakers), never on this one.
        return _peer(client_address[0]) in self.speakers.table

    def handle_error(self, request, client_address) -> None:
        """A speaker that goes away in the middle of a download (or stops reading) is one log
        line, not a traceback."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            print(f"{client_address[0]} connection lost: {printable(exc)}", flush=True)
            return
        super().handle_error(request, client_address)


def _lines(p: Path) -> list[str]:
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def _tail(p: Path, n: int = 40) -> str:
    return "\n".join(_lines(p)[-n:])


def _wildcard(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_unspecified
    except ValueError:
        return False


def _speakers_way(cfg: config.Config) -> str:
    """This computer's address on the way to the (first) speaker."""
    return bundle.lan_ip_for(cfg.speakers[0].host if cfg.speakers else "192.0.2.1")


def listen_address(cfg: config.Config) -> tuple[str, int]:
    listen = config.parse_listen(cfg.companion.get("listen", "auto"))
    if listen is None:
        return _speakers_way(cfg), hostos.COMPANION_PORT
    return listen


def public_url(cfg: config.Config) -> str:
    if cfg.companion.get("url"):
        return cfg.companion["url"].rstrip("/")
    ip, port = listen_address(cfg)
    if _wildcard(ip):  # listening on every address: the speakers get the one they can reach
        ip = _speakers_way(cfg)
    return f"http://[{ip}]:{port}" if ":" in ip else f"http://{ip}:{port}"


def serve_forever(cfg: config.Config) -> int:
    """Serve until stopped; service.RESTART when Lithify updated itself (start again), else 0."""
    global _SERVER
    _load_state()
    try:
        bundle.recover_bundle()  # a swap the computer stopped half-way
    except (OSError, bundle.BuildError) as e:
        print(f"cannot check the bundle: {e}", flush=True)
    # Containers of builds that ended with an earlier companion (killed, or the computer stopped)
    # would run on for up to an hour. (Docker may be slow to answer: not in the server's way.)
    threading.Thread(target=bundle.remove_stale_containers, name="stale-containers", daemon=True).start()
    ip, port = listen_address(cfg)
    url = public_url(cfg)
    speakers = Speakers(cfg).start()
    httpd = Server((ip, port), make_handler(cfg, url, speakers), speakers)
    _SERVER = httpd
    print(f"lithify companion on {url} for: {', '.join(s.id + '=' + s.host for s in cfg.speakers)}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        speakers.stop()
        stop_build()
        httpd.server_close()
        time.sleep(0.1)
    return service.RESTART if _RESTART.is_set() else 0
