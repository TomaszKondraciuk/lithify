"""`lithify wizard`: who may use its server, what its state says, how a failed installation is
explained, what it shows while one runs, and the page it serves. No Docker, network or speaker:
discovery, the speaker probe and the `lithify` commands are fakes."""
import argparse
import contextlib
import http.client
import io
import json
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from lithify import bundle, cli, config, console, discovery, hostos, platforms, wizard

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "lithify" / "wizard_ui"
FIREWALL = {"ports": hostos.FIREWALL_PORTS, "lan": "192.168.1.0/24", "commands": ["ufw ...", "firewall-cmd ..."]}
SPEAKER = {"host": "192.168.1.109", "name": "Kuchnia", "model": "WiFi Speaker V2", "spotify_esdk": "3.88"}

# What `lithify install` prints for a first installation that builds the bundle.
FRESH = [
    '==> wrote /home/u/.config/lithify/config.toml: Lithe Audio WiFi Speaker V2 at 192.168.1.109 (platform ls9), '
    'Spotify name "Kuchnia"',
    "==> builder image lithify-builder:alsa-1.2.14-rust-1.90.0",
    "==> librespot dev 1a2b3c4",
    "    dev at 1a2b3c4d5e6f",
    "==> compiling librespot (armv7, static, NEON)",
    "==> lithify-agent (unit tests on the host, then armv7)",
    "==> bundle",
    "==> bundle ready: /home/u/.cache/lithify/bundle",
    "==> installing v0.7.1-12-g1a2b3c4 on kuchnia (192.168.1.109, ls9)",
    "==> sending settings to the speaker: name",
    "==> the Cast service list changed: a reboot activates it (~1 min without sound)",
    "==> speaker back after 63 s",
    "Speaker kuchnia (192.168.1.109)  Lithe Audio WiFi Speaker V2",
    "  web page   http://192.168.1.109:8090/",
]


class Phases:
    """The phases a Task goes through for these lines (and the build's steps)."""

    def __init__(self, lines: list[str]):
        self.task = wizard.Task("install", 0.0)
        self.seen: list[str] = []
        self.steps: list[tuple[str | None, float]] = []
        for i, line in enumerate(lines):
            self.task.add(line, float(i))
            if self.task.phase and (not self.seen or self.seen[-1] != self.task.phase):
                self.seen.append(self.task.phase)
            if self.task.step and (not self.steps or self.steps[-1][0] != self.task.step):
                self.steps.append((self.task.step, self.task.progress))

    def state(self, phase: str) -> str:
        return self.task.phases[phase]["state"]


class PhaseTest(unittest.TestCase):
    def test_a_first_installation_goes_through_every_phase_in_order(self):
        p = Phases(FRESH)
        self.assertEqual(p.seen, ["prepare", "build", "install", "restart", "check"])
        self.assertEqual([s for s, _ in p.steps], ["image", "source", "compile", "agent", "bundle"])
        progress = [f for _, f in p.steps]
        self.assertEqual(progress, sorted(progress))  # (the bar only grows)
        self.assertEqual(Phases(FRESH[:8]).task.progress, 1.0)  # "bundle ready" ends the build
        self.assertEqual([p.state(x) for x in ("prepare", "build", "install", "restart")], ["done"] * 4)
        self.assertEqual(p.state("check"), "active")

    def test_the_bar_moves_while_the_image_builds_and_librespot_compiles(self):
        task = wizard.Task("install", 0.0)
        seen = []
        lines = [FRESH[0], FRESH[1], "    builder image: step 1 of 4",
                 *(f"    builder image: downloaded {mb} MB" for mb in (50, 500, 1000, 1500)),
                 *(f"    builder image: step {k} of 4" for k in range(2, 5)), "    builder image: saving it", FRESH[2],
                 FRESH[4], *(f"    crates compiled: {n}" for n in range(10, 400, 10)), FRESH[5],
                 "    crates compiled: 10"]  # (the agent's crates do not move the bar back)
        for i, line in enumerate(lines):
            task.add(line, float(i))
            seen.append((task.step, task.progress))
        image = [f for s, f in seen if s == "image"]
        compile_ = [f for s, f in seen if s == "compile"]
        # (the base image moves the bar through the image's first 40%, its steps through the rest)
        self.assertEqual([round(f, 3) for f in image], [0.02, 0.02, 0.027, 0.09, 0.132, 0.132, 0.174, 0.216,
                                                        0.258, 0.278])
        self.assertEqual((image[0], round(image[-1], 2)), (0.02, 0.28))  # (saved: just before its part's end)
        self.assertEqual((compile_[0], compile_[-1]), (0.35, 0.35 + 0.5 * 0.98))  # (never past its part)
        self.assertEqual([f for _, f in seen if f is not None], sorted(f for _, f in seen if f is not None))
        self.assertEqual(seen[-1], ("agent", 0.88))

    def test_a_compiler_killed_for_memory_is_named(self):
        lines = [FRESH[0], FRESH[1], "==> compiling librespot (armv7, static, NEON)",
                 "error: docker run ... failed:", "error: could not compile `librespot` (bin \"librespot\")",
                 "  process didn't exit successfully: `rustc --crate-name librespot ...` (signal: 9, SIGKILL: kill)"]
        self.assertEqual(wizard.classify(lines, "build")[0], "build_memory")

    def test_windows_asking_for_the_firewall_rule_is_a_step_of_its_own(self):
        ask = f"==> {hostos.FIREWALL_ASK} to let speakers download from this computer (a firewall rule): choose Yes"
        task = wizard.Task("install", 0.0)
        task.add(ask, 0.0)  # (a speaker configured before: nothing written first)
        self.assertEqual((task.phase, task.step), ("prepare", "firewall"))
        task = wizard.Task("install", 0.0)
        for i, line in enumerate([FRESH[0], ask, FRESH[1]]):
            task.add(line, float(i))
            if i == 1:
                self.assertEqual((task.phase, task.step), ("prepare", "firewall"))
        self.assertEqual((task.phase, task.step), ("build", "image"))

    def test_a_bundle_built_before_skips_the_build(self):
        p = Phases([line for line in FRESH if not any(w in line for w in ("builder", "librespot", "agent", "bundle"))])
        self.assertEqual(p.seen, ["prepare", "install", "restart", "check"])
        self.assertEqual(p.state("build"), "skipped")

    def test_a_published_bundle_is_downloaded_in_the_build_phase(self):
        p = Phases(["==> librespot ok", "==> lithify-agent ok", "==> alsa.tar ok", "==> VERSIONS ok",
                    "==> installing v0.7.1 on kuchnia (192.168.1.109, ls9)"])
        self.assertEqual(p.seen, ["build", "install"])
        self.assertEqual(p.steps[0], ("download", 0.25))

    def test_phases_only_move_on(self):
        self.assertIsNone(wizard.phase_of("==> librespot dev 1a2b3c4", "install"))
        self.assertIsNone(wizard.phase_of("==> speaker back after 63 s", "done"))  # (over: nothing begins)
        self.assertIsNone(wizard.phase_of("==> wrote /x/config.toml: ...", "build"))
        self.assertEqual(wizard.phase_of("==> restarting librespot and the agent", "install"), "restart")
        self.assertEqual(wizard.phase_of("==> speaker back after 70 s", "restart"), "check")

    def test_other_lines_begin_nothing(self):
        for line in ("librespot dev", "    dev at 1a2b3c", "warning: the Windows firewall may keep ...",
                     "==> something new"):
            self.assertIsNone(wizard.phase_of(line, "prepare"), line)


class ClassifyTest(unittest.TestCase):
    def key(self, *lines: str, phase: str | None = "install") -> str:
        return wizard.classify(list(lines), phase)[0]

    def test_docker(self):
        self.assertEqual(self.key("==> builder image lithify-builder:x", "error: docker build -q ... failed:",
                                  "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
                                  "Is the docker daemon running?", phase="build"), "docker_not_running")
        self.assertEqual(self.key("error: docker build ... failed:", 'error during connect: Get "http://%2F%2F.%2F'
                                  'pipe%2Fdocker_engine/v1.24/info": open //./pipe/docker_engine: The system cannot '
                                  "find the file specified.", phase="build"), "docker_not_running")
        self.assertEqual(self.key("error: Docker is required to build (or use `lithify fetch` for a published "
                                  "release)", phase="prepare"), "docker_missing")
        self.assertEqual(self.key("error: docker build ... failed:", "permission denied while trying to connect to "
                                  "the Docker daemon socket at unix:///var/run/docker.sock", phase="build"),
                         "docker_permission")

    def test_the_speaker(self):
        self.assertEqual(self.key("error: cannot reach the speaker console 192.168.1.109:23: timed out",
                                  phase="prepare"), "unreachable")
        self.assertEqual(self.key("error: cannot reach the speaker console 192.168.1.109:23: [Errno 111] Connection "
                                  "refused", phase="prepare"), "unreachable")
        self.assertEqual(self.key("error: 192.168.1.109 does not answer on the service console (TCP 23); is it an LS9 "
                                  "Lithe speaker?", phase="prepare"), "unreachable")
        self.assertEqual(self.key("error: 192.168.1.109: not a supported Lithe Audio platform (no LS9 service "
                                  "console). LS10 models (WiFi Speaker V3, PRO 2, iO1) are not supported yet - see "
                                  "docs/platforms.md", phase="prepare"), "unsupported")
        self.assertEqual(self.key("==> speaker back after", "error: librespot does not answer 420 s after the reboot"),
                         "not_back")
        self.assertEqual(self.key("error: install failed on the speaker:", "FAIL space: 1200 KB free in "
                                  "/lsync/lithify, 30000 KB needed"), "speaker_space")

    def test_a_speaker_that_cannot_download_is_the_firewall_not_an_unreachable_speaker(self):
        self.assertEqual(self.key("==> installing v0.7.1 on kuchnia (192.168.1.109, ls9)",
                                  "error: install failed on the speaker:",
                                  "curl: (7) Failed to connect to 192.168.1.10 port 18096 after 2 ms: Connection "
                                  "refused", "FAIL download SHA256SUMS"), "firewall")
        # (the pre-install probe of cli.cmd_install)
        self.assertEqual(self.key("error: the speaker cannot download from this computer (http://192.168.1.10:18096: "
                                  "curl: (28) Connection timed out after 8001 milliseconds). Also check ..."),
                         "firewall")

    def test_a_warning_before_the_error_does_not_count(self):
        self.assertEqual(self.key("warning: the Windows firewall may keep the speaker from downloading the update",
                                  "==> installing v0.7.1 on kuchnia (192.168.1.109, ls9)",
                                  "error: cannot reach the speaker console 192.168.1.109:23: timed out"), "unreachable")

    def test_this_computer(self):
        self.assertEqual(self.key("==> bundle", "error: [Errno 28] No space left on device: "
                                  "'/home/u/.cache/lithify/bundle.new'", phase="build"), "no_space")
        self.assertEqual(self.key("error: another build is running (`lithify build`, or the speaker's web page "
                                  "updating)", phase="prepare"), "build_busy")
        self.assertEqual(self.key("error: /home/u/.config/lithify/config.toml is not valid TOML (Expected '=' "
                                  "(at line 3, column 5)); it was left as it is", phase="prepare"), "config")

    def test_compiler_errors_inside_the_cli_error_are_read_with_it(self):
        key, detail = wizard.classify(["==> compiling librespot (armv7, static, NEON)",
                                       "error: docker run --rm ... cargo build ... failed:",
                                       "error[E0425]: cannot find value `x`", "No space left on device"], "build")
        self.assertEqual(key, "no_space")
        self.assertTrue(detail.startswith("error: docker run"))

    def test_a_failed_build_shows_its_last_30_lines(self):
        lines = ["==> compiling librespot (armv7, static, NEON)", "error: docker run ... failed:",
                 *(f"   Compiling crate-{i} v1.0.0" for i in range(40)), "error: could not compile `librespot-core`"]
        key, detail = wizard.classify(lines, "build")
        self.assertEqual(key, "build_failed")
        self.assertEqual(detail.splitlines(), lines[-30:])

    def test_anything_else_is_unknown_with_what_was_said(self):
        self.assertEqual(wizard.classify(["==> installing ...", "error: something new"], "install"),
                         ("unknown", "error: something new"))
        traceback = ["Traceback (most recent call last):", '  File "x.py", line 1', "KeyError: 'y'"]
        self.assertEqual(wizard.classify(traceback, "install"), ("unknown", "\n".join(traceback)))


class ChecksTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name)
        for target, name, value in ((bundle, "CACHE", self.cache), (wizard, "_published", lambda: False)):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def check(self, docker=("ok", "27.3.1"), os_name="linux") -> dict:
        with mock.patch.object(wizard, "docker_state", return_value=docker), mock.patch.object(wizard, "OS", os_name):
            result = wizard.check_computer()
        return {"ok": result["ok"], **{c["key"]: c for c in result["checks"]}}

    def test_everything_ready(self):
        got = self.check()
        self.assertTrue(got["ok"])
        self.assertEqual([got[k]["status"] for k in ("python", "docker", "disk", "bundle")], ["ok"] * 4)
        self.assertEqual(got["docker"]["params"], {"version": "27.3.1"})
        self.assertEqual(got["bundle"]["msg"], "bundle_absent")

    def test_docker_missing_links_to_the_download_for_this_system(self):
        for os_name in ("windows", "macos", "linux"):
            got = self.check(("missing", ""), os_name)
            self.assertFalse(got["ok"])
            self.assertEqual((got["docker"]["status"], got["docker"]["fix"]), ("bad", "fix_docker_missing"))
            self.assertEqual(got["docker"]["link"], wizard.DOCKER_LINKS[os_name])

    def test_docker_not_running_offers_to_start_it_or_the_command(self):
        self.assertEqual(self.check(("not_running", ""), "windows")["docker"]["action"], "start-docker")
        self.assertEqual(self.check(("not_running", ""), "macos")["docker"]["action"], "start-docker")
        linux = self.check(("not_running", ""), "linux")["docker"]
        self.assertEqual((linux["action"], linux["commands"]), (None, [wizard.DOCKER_START_CMD]))

    def test_linux_users_outside_the_docker_group_get_the_command(self):
        got = self.check(("permission", "permission denied"), "linux")["docker"]
        self.assertEqual(got["commands"], ["sudo usermod -aG docker $USER"])
        self.assertEqual(got["link"], wizard.DOCKER_GROUP_LINK)

    def test_a_bundle_built_before_needs_no_docker(self):
        (self.cache / "bundle").mkdir()
        (self.cache / "bundle" / "VERSIONS").write_text("librespot=v0.7.1\n", encoding="utf-8")
        got = self.check(("missing", ""))
        self.assertTrue(got["ok"])
        self.assertEqual(got["docker"]["msg"], "docker_not_needed")
        self.assertEqual(got["bundle"]["params"], {"version": "v0.7.1"})

    def test_a_published_bundle_needs_no_docker_either(self):
        with mock.patch.object(wizard, "_published", lambda: True):
            got = self.check(("missing", ""))
        self.assertTrue(got["ok"])
        self.assertEqual((got["docker"]["msg"], got["bundle"]["msg"]), ("docker_published", "bundle_published"))

    def test_little_disk_space_is_a_warning(self):
        usage = mock.Mock(free=2 * 1024 ** 3)
        with mock.patch.object(wizard.shutil, "disk_usage", return_value=usage):
            got = self.check()
        self.assertTrue(got["ok"])  # (a warning does not stop anyone)
        self.assertEqual((got["disk"]["status"], got["disk"]["params"]), ("warn", {"free": "2.0"}))

    def test_what_docker_info_says(self):
        def state(rc=0, out="", err="", raised=None) -> str:
            done = subprocess.CompletedProcess([], rc, out, err)
            with mock.patch.object(wizard.shutil, "which", return_value="/usr/bin/docker"), \
                    mock.patch.object(hostos, "run", side_effect=raised, return_value=done):
                return wizard.docker_state()[0]

        self.assertEqual(state(0, "27.3.1\n"), "ok")
        self.assertEqual(state(1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the "
                                      "docker daemon running?"), "not_running")
        self.assertEqual(state(0, "\n", "Cannot connect to the Docker daemon"), "not_running")
        self.assertEqual(state(1, "", "permission denied while trying to connect to the Docker daemon socket"),
                         "permission")
        self.assertEqual(state(raised=subprocess.TimeoutExpired(["docker"], 20)), "slow")
        with mock.patch.object(wizard.shutil, "which", return_value=None):
            self.assertEqual(wizard.docker_state(), ("missing", ""))


class FakeDevice:
    """The speaker as the probe sees it: what it reports, and what its console says."""
    info_reply: dict | None = None
    platform_reply: object = platforms.LS9

    def __init__(self, speaker):
        self.host = speaker.host

    def info(self) -> dict:
        return dict(self.info_reply or {})

    def platform(self):
        if isinstance(self.platform_reply, Exception):
            raise self.platform_reply
        return self.platform_reply


class ProbeTest(unittest.TestCase):
    def probe(self, platform_reply, info=None, with_info=False) -> dict:
        with mock.patch.object(wizard, "Device", FakeDevice), \
                mock.patch.object(FakeDevice, "platform_reply", platform_reply), \
                mock.patch.object(FakeDevice, "info_reply", info or {}):
            return wizard.probe_speaker("192.168.1.109", with_info)

    def test_supported_unsupported_and_silent_speakers(self):
        self.assertEqual(self.probe(platforms.LS9)["supported"], True)
        got = self.probe(RuntimeError("192.168.1.109: not a supported Lithe Audio platform"))
        self.assertEqual((got["supported"], got["reason_key"]), (False, "unsupported_model"))
        silent = console.ConsoleError("cannot reach the speaker console 192.168.1.109:23: timed out")
        self.assertEqual(self.probe(silent)["reason_key"], "no_console")  # found by discovery: it answers
        typed = self.probe(silent, info={"host": "192.168.1.109", "librespot": None}, with_info=True)
        self.assertEqual(typed["reason_key"], "not_found")  # a typed address where nothing answers

    def test_a_typed_address_gets_its_name_and_model(self):
        got = self.probe(platforms.LS9, {"speaker_name": "Salon\x1b[2J", "model": "WiFi PRO"}, with_info=True)
        self.assertEqual((got["name"], got["model"], got["supported"]), ("Salon[2J", "WiFi PRO", True))
        self.assertEqual((got["official_listed"], got["librespot_name"]), (False, ""))

    def test_a_librespot_that_runs_already_is_named(self):
        got = self.probe(platforms.LS9, {"librespot": {"version": "0.8.0", "name": "Łazienka [Lithify]"},
                                         "official_spotify": "3.88.29"}, with_info=True)
        self.assertEqual((got["librespot_name"], got["librespot_version"], got["official_listed"]),
                         ("Łazienka [Lithify]", "0.8.0", True))


class Fakes:
    """`lithify` children as the wizard starts them, without running anything."""

    def __init__(self, install: list[str], rc: int = 0, serve_rc: int = 0, serve_out: tuple = ()):
        self.install, self.rc, self.serve_rc, self.serve_out = install, rc, serve_rc, serve_out
        self.calls: list[tuple[list[str], dict]] = []
        self.release = threading.Event()  # (set: a blocking install ends, as a stopped one would)
        self.block = False

    def stream(self, cmd, env, on_line, on_start) -> int:
        self.calls.append((cmd, env))
        proc = mock.Mock(pid=4242)
        proc.poll.return_value = None
        on_start(proc)
        if "serve" in cmd:
            for line in self.serve_out:
                on_line(line)
            return self.serve_rc
        for line in self.install:
            on_line(line)
        if self.block:
            self.release.wait(10)
            return 130
        return self.rc

    def quick(self, cmd, env, timeout=60) -> tuple[int, str]:
        self.calls.append((cmd, env))
        return 0, "http://192.168.1.109:8091/\n"


class ServerCase(unittest.TestCase):
    """A real wizard server on 127.0.0.1 (port 0) with its own state."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for target, name, value in ((bundle, "CACHE", Path(tmp.name)), (wizard, "firewall_help", lambda: FIREWALL),
                                    (bundle, "remove_stale_containers", lambda: 0)):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.key = secrets.token_hex(16)
        self.wiz = wizard.Wizard()
        self.srv = wizard.Server(("127.0.0.1", 0), self.wiz, self.key)
        self.port = self.srv.server_address[1]
        self.serving = threading.Thread(target=self.srv.serve_forever, args=(0.05,), daemon=True)  # (quick to stop)
        self.serving.start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def request(self, method: str, path: str, body=None, headers: dict | None = None,
                host: str | None = None) -> tuple[int, dict, bytes]:
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            data = None if body is None else json.dumps(body).encode()
            c.request(method, path, body=data, headers={"Host": host or f"127.0.0.1:{self.port}",
                                                        **({"Content-Type": "application/json"} if data else {}),
                                                        **(headers or {})})
            r = c.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        finally:
            c.close()

    def api(self, method: str, path: str, body=None, **kw) -> tuple[int, dict]:
        kw["headers"] = {wizard.HEADER: self.key, **kw.get("headers", {})}
        status, _, raw = self.request(method, path, body, **kw)
        return status, json.loads(raw)

    def until(self, check, timeout: float = 10) -> dict:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            st = self.api("GET", "/api/state")[1]
            if check(st):
                return st
            time.sleep(0.02)
        self.fail(f"the state never got there: {self.wiz.state()}")

    def finished(self) -> dict:
        return self.until(lambda st: st["task"] and st["task"]["finished"])


class GuardTest(ServerCase):
    def test_the_api_needs_the_token(self):
        status, _, _ = self.request("GET", "/api/state")
        self.assertEqual(status, 403)
        wrong = self.key[::-1]
        self.assertEqual(self.api("GET", "/api/state", headers={wizard.HEADER: wrong})[0], 403)
        self.assertEqual(self.api("POST", "/api/quit", {}, headers={wizard.HEADER: ""})[0], 403)
        self.assertEqual(self.api("GET", "/api/state")[0], 200)
        self.assertTrue(self.serving.is_alive())  # (the refused quit did nothing)

    def test_a_foreign_host_is_refused_static_files_too(self):
        for host in ("evil.example", f"evil.example:{self.port}", f"127.0.0.1:{self.port + 1}",
                     f"192.168.1.102:{self.port}"):
            with self.subTest(host=host):
                self.assertEqual(self.api("GET", "/api/state", host=host)[0], 421)
                self.assertEqual(self.request("GET", "/", host=host)[0], 421)
        self.assertEqual(self.api("GET", "/api/state", host=f"localhost:{self.port}")[0], 200)
        self.assertEqual(self.api("GET", "/api/state", host=f"LOCALHOST:{self.port}")[0], 200)

    def test_other_sites_are_refused(self):
        for headers in ({"Origin": "http://evil.example"}, {"Origin": "null"},
                        {"Origin": f"http://127.0.0.1:{self.port + 1}"}, {"Sec-Fetch-Site": "cross-site"},
                        {"Sec-Fetch-Site": "same-site"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.api("GET", "/api/state", headers=headers)[0], 403)
                self.assertEqual(self.api("POST", "/api/discover", {}, headers=headers)[0], 403)
        own = {"Origin": f"http://127.0.0.1:{self.port}", "Sec-Fetch-Site": "same-origin"}
        self.assertEqual(self.api("GET", "/api/state", headers=own)[0], 200)
        self.assertEqual(self.api("GET", "/api/state", headers={"Origin": f"http://localhost:{self.port}"})[0], 200)
        self.assertEqual(self.request("OPTIONS", "/api/install")[0], 403)  # (a preflight gets no permission)

    def test_an_unexpected_error_gets_an_answer_and_one_line(self):
        err = io.StringIO()
        with mock.patch.object(wizard.Wizard, "state", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stderr(err):
            status, body = self.api("GET", "/api/state")
        self.assertEqual((status, body), (500, {"error": "internal error: boom"}))
        self.assertEqual(err.getvalue().count("\n"), 1)
        self.assertNotIn(self.key, err.getvalue())

    def test_bodies_are_small_json_objects(self):
        self.assertEqual(self.api("POST", "/api/add-host", ["192.168.1.109"])[0], 400)
        self.assertEqual(self.api("POST", "/api/add-host", {"host": "x" * 5000})[0], 400)
        self.assertEqual(self.api("POST", "/api/nothing", {})[0], 404)
        self.assertEqual(self.api("GET", "/api/nothing")[0], 404)


class PageTest(ServerCase):
    def test_the_page_and_its_files_with_their_types_and_the_csp(self):
        csp = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
               "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        html, js, css = "text/html; charset=utf-8", "text/javascript; charset=utf-8", "text/css; charset=utf-8"
        for path, ctype in (("/", html), ("/index.html", html), ("/wizard.js", js), ("/wizard.css", css),
                            ("/icon.svg", "image/svg+xml")):
            with self.subTest(path=path):
                status, headers, body = self.request("GET", path)
                self.assertEqual((status, headers["content-type"]), (200, ctype))
                self.assertEqual(headers["content-security-policy"], csp)
                self.assertEqual(headers["x-content-type-options"], "nosniff")
                self.assertEqual(headers["cache-control"], "no-store")
                self.assertEqual(headers["referrer-policy"], "no-referrer")
                self.assertEqual(body, (UI / ("index.html" if path == "/" else path[1:])).read_bytes())
        status, headers, _ = self.request("GET", "/api/state")  # (refused answers carry them too)
        self.assertEqual((status, headers["content-security-policy"]), (403, csp))

    def test_nothing_else_is_served(self):
        for path in ("/cli.py", "/../cli.py", "/wizard_ui/index.html", "/%2e%2e/cli.py", "/favicon.ico", "/index.htm"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)

    def test_the_page_keeps_to_its_csp(self):
        html = (UI / "index.html").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"<script(?![^>]*\bsrc=)", html), [])  # no inline script
        self.assertNotIn("<style", html)
        self.assertIsNone(re.search(r"\sstyle=", html))
        self.assertIsNone(re.search(r"\son[a-z]+=", html))  # no inline event handlers
        self.assertNotIn("<style", (UI / "icon.svg").read_text(encoding="utf-8"))
        js = (UI / "wizard.js").read_text(encoding="utf-8")
        for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            self.assertNotIn(unsafe, js)
        self.assertNotIn(".style", js)

    def test_every_text_has_both_languages(self):
        js = (UI / "wizard.js").read_text(encoding="utf-8")
        key = re.compile(r"(?:^|[\s,{])([a-z][a-z0-9_]*):\s+'", re.MULTILINE)
        en = set(key.findall(js[js.index("    en: {"):js.index("    pl: {")]))
        pl_at = js.index("    pl: {")
        pl = set(key.findall(js[pl_at:js.index("\n  };\n", pl_at)]))
        self.assertEqual(en, pl)
        html = (UI / "index.html").read_text(encoding="utf-8")
        used = set(re.findall(r'data-t(?:-aria)?="([a-z0-9_]+)"', html)) | set(re.findall(r"\bt\('([a-z0-9_]+)'", js))
        self.assertEqual(used - en, set())
        # what the server sends: error keys, check messages, reasons, phases and the build's steps
        sent = {f"err_{k}" for k, _ in wizard._ERRORS} | {f"r_{k}" for k in ("no_console", "not_found",
                                                                             "unsupported_model", "no_answer",
                                                                             "probe_failed")}
        sent |= {f"err_{k}" for k in ("build_failed", "unknown", "cancelled", "timeout", "docker_desktop_missing",
                                      "docker_start_timeout")}
        sent |= {f"ph_{p}" for p in wizard.PHASES} | {f"sub_{s}" for _, s, _ in wizard._BUILD_STEPS if s}
        sent |= {"sub_download", "helper_failed", "helper_no_systemd", "docker_published", "bundle_published",
                 *(f"docker_{s}" for s in ("ok", "missing", "not_running", "permission", "slow", "not_needed"))}
        self.assertEqual(sent - en, set())
        # how to fix each problem, on every system (a text of its own where they differ)
        problems = {k for k, _ in wizard._ERRORS} | {"build_failed", "unknown", "cancelled", "timeout", "docker_slow",
                                                     "docker_desktop_missing", "docker_start_timeout"}
        for problem in sorted(problems):
            for os_name in ("windows", "macos", "linux"):
                self.assertTrue({f"fix_{problem}_{os_name}", f"fix_{problem}"} & en, (problem, os_name))


class StateTest(ServerCase):
    def test_the_state_says_where_the_wizard_is(self):
        status, st = self.api("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(set(st), {"rev", "now", "version", "os", "lang", "step", "task", "computer", "speakers",
                                   "searched", "chosen", "outcome", "installed", "firewall"})
        self.assertEqual((st["step"], st["task"], st["speakers"], st["installed"]), ("welcome", None, [], False))
        self.assertEqual(st["os"], wizard.OS)
        self.assertEqual(st["firewall"]["ports"], "8095,18096-18099")

    def test_a_task_reports_its_lines_and_how_it_ended(self):
        with mock.patch.object(wizard, "docker_state", return_value=("ok", "27.3.1")):
            self.assertEqual(self.api("POST", "/api/check-computer", {})[0], 202)
            st = self.finished()
        task = st["task"]
        self.assertLessEqual({"kind", "started", "lines", "finished", "ok", "error_key", "error_detail", "phase",
                              "phases", "elapsed"}, set(task))
        self.assertEqual((task["kind"], task["ok"], task["error_key"], st["step"]), ("check", True, None, "computer"))
        self.assertIsInstance(task["lines"], list)
        self.assertEqual([c["key"] for c in st["computer"]["checks"]], ["python", "docker", "disk", "bundle"])
        rev = st["rev"]
        self.assertEqual(self.api("GET", "/api/state")[1]["rev"], rev)  # (nothing changed: the page draws nothing)


class FlowTest(ServerCase):
    """Find, choose and install, with fake children."""

    def setUp(self):
        super().setUp()
        for target, name, value in ((discovery, "discover", lambda: [SPEAKER, {**SPEAKER, "host": "192.168.1.110",
                                                                                 "name": "Garaż"}]),
                                    (wizard, "probe_speaker", self.probe)):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    @staticmethod
    def probe(host: str, with_info: bool) -> dict:
        if host == "192.168.1.110":
            return {"supported": False, "reason_key": "unsupported_model", "reason": "not supported"}
        return {"supported": True, "reason_key": None, "reason": "", "platform": "ls9",
                **({"name": "Salon", "model": "WiFi PRO"} if with_info else {})}

    def choose(self) -> None:
        self.assertEqual(self.api("POST", "/api/discover", {})[0], 202)
        st = self.finished()
        self.assertEqual([(s["host"], s["supported"]) for s in st["speakers"]],
                         [("192.168.1.109", True), ("192.168.1.110", False)])
        self.assertEqual(st["speakers"][1]["reason_key"], "unsupported_model")
        self.assertEqual(self.api("POST", "/api/step", {"step": "name", "host": "192.168.1.110"})[0], 400)
        status, _ = self.api("POST", "/api/step", {"step": "name", "host": "192.168.1.109"})
        self.assertEqual(status, 200)
        chosen = self.api("GET", "/api/state")[1]["chosen"]
        self.assertEqual((chosen["default_name"], chosen["official_name"]), ("Kuchnia (librespot)", "Kuchnia"))

    def install(self, fakes: Fakes, name: str = "Kuchnia") -> dict:
        with mock.patch.object(wizard, "stream", fakes.stream), mock.patch.object(wizard, "quick", fakes.quick):
            self.assertEqual(self.api("POST", "/api/install", {"host": "192.168.1.109", "name": name})[0], 202)
            return self.finished()

    def test_a_successful_installation(self):
        self.choose()
        fakes = Fakes(FRESH, serve_out=("==> companion running on http://192.168.1.10:8095: systemd user service",))
        with mock.patch.dict("os.environ", {"LITHIFY_HOST": "192.168.1.199"}):
            st = self.install(fakes, name="  Kuchnia ")
        cmd, env = fakes.calls[0]
        self.assertEqual(cmd, [sys.executable, str(ROOT / "bin" / "lithify"), "install", "--host", "192.168.1.109",
                               "--reboot"])
        self.assertEqual((env["LITHIFY_NAME"], env["PYTHONUNBUFFERED"], env["PYTHONIOENCODING"]),
                         ("Kuchnia", "1", "utf-8"))
        self.assertNotIn("LITHIFY_HOST", env)  # (the speaker is the one chosen here)
        self.assertEqual(fakes.calls[1][0][2:], ["serve", "--install-service"])
        self.assertNotIn("LITHIFY_NAME", fakes.calls[1][1])
        self.assertEqual(fakes.calls[2][0][2:], ["ui", "--speaker", "192.168.1.109"])
        task = st["task"]
        self.assertEqual((task["ok"], task["phase"], st["step"], st["installed"]), (True, "done", "done", True))
        self.assertEqual({p["key"]: p["state"] for p in task["phases"]},
                         {"prepare": "done", "build": "done", "install": "done", "restart": "done", "check": "done"})
        self.assertEqual(st["outcome"], {"host": "192.168.1.109", "page_url": "http://192.168.1.109:8091/",
                                         "spotify_name": "Kuchnia", "page_quiet": False, "helper_ok": True,
                                         "helper_key": None, "helper_detail": ""})
        self.assertIn("==> compiling librespot (armv7, static, NEON)", task["lines"])

    def test_a_failed_installation_says_why_and_stays_on_its_step(self):
        self.choose()
        st = self.install(Fakes(["==> installing v0.7.1 on kuchnia (192.168.1.109, ls9)",
                                 "error: the speaker cannot download from this computer (http://192.168.1.10:18096: "
                                 "curl: (7) Failed to connect). Also check ..."], rc=1))
        task = st["task"]
        self.assertEqual((task["ok"], task["error_key"], task["error_fix"], st["step"], st["installed"]),
                         (False, "firewall", "fix_firewall", "install", False))
        self.assertIn("cannot download from this computer", task["error_detail"])
        self.assertEqual({p["key"]: p["state"] for p in task["phases"]}["install"], "failed")
        if wizard.OS == "linux":
            self.assertEqual(task["error_commands"], FIREWALL["commands"])

    def test_a_failed_build_names_its_log(self):
        self.choose()
        st = self.install(Fakes([*FRESH[:5], "error: docker run ... failed:", "error: could not compile"], rc=1))
        self.assertEqual(st["task"]["error_key"], "build_failed")
        self.assertTrue(st["task"]["error_params"]["log"].endswith("build.log"))

    def test_the_helper_failing_is_not_the_installation_failing(self):
        self.choose()
        st = self.install(Fakes(FRESH, serve_rc=1, serve_out=(
            "error: no systemd user session here: run `lithify serve` yourself (e.g. from your desktop's autostart)",)))
        self.assertEqual((st["task"]["ok"], st["installed"]), (True, True))
        self.assertEqual((st["outcome"]["helper_ok"], st["outcome"]["helper_key"]), (False, "helper_no_systemd"))
        self.assertIn("no systemd user session", st["outcome"]["helper_detail"])

    def test_cancel_stops_the_installation(self):
        self.choose()
        fakes = Fakes(FRESH[:5])
        fakes.block = True
        stopped = []

        def terminate(p) -> None:
            stopped.append(p.pid)
            fakes.release.set()

        with mock.patch.object(wizard, "stream", fakes.stream), mock.patch.object(wizard, "terminate", terminate):
            self.assertEqual(self.api("POST", "/api/cancel", {})[0], 409)  # (nothing runs yet)
            self.assertEqual(self.api("POST", "/api/install", {"host": "192.168.1.109", "name": "Kuchnia"})[0], 202)
            self.until(lambda st: st["task"]["phase"] == "build")
            self.assertEqual(self.api("POST", "/api/install", {"host": "192.168.1.109", "name": "Kuchnia"})[0], 409)
            self.assertEqual(self.api("POST", "/api/step", {"step": "speaker"})[0], 409)
            self.assertEqual(self.api("POST", "/api/cancel", {})[0], 202)
            st = self.finished()
        self.assertEqual(stopped, [4242])
        task = st["task"]
        self.assertEqual((task["ok"], task["error_key"], task["cancelled"]), (False, "cancelled", True))

    def test_names_and_speakers_are_checked(self):
        self.choose()
        for body in ({"host": "192.168.1.109", "name": ""}, {"host": "192.168.1.109", "name": "x" * 65},
                     {"host": "192.168.1.109", "name": "a\nb"}, {"host": "192.168.1.109"}):
            self.assertEqual(self.api("POST", "/api/install", body), (400, {
                "error": "the name must have 1 to 64 characters", "error_key": "bad_name"}))
        for host in ("192.168.1.110", "192.168.1.177", "-x", None):
            self.assertEqual(self.api("POST", "/api/install", {"host": host, "name": "Kuchnia"})[1]["error_key"],
                             "bad_speaker")

    def test_a_typed_address_is_probed_and_listed_first(self):
        self.assertEqual(self.api("POST", "/api/add-host", {"host": "-oProxyCommand=x"})[1]["error_key"], "bad_host")
        self.assertEqual(self.api("POST", "/api/add-host", {"host": "192.168.1.1 ; rm"})[0], 400)
        self.assertEqual(self.api("POST", "/api/add-host", {"host": " salon.local "})[0], 202)
        st = self.finished()
        self.assertEqual(st["speakers"][0], {"host": "salon.local", "name": "Salon", "model": "WiFi PRO",
                                             "official_name": "", "official_listed": False, "librespot_name": "",
                                             "librespot_version": "", "supported": True, "reason_key": None,
                                             "reason": "", "platform": "ls9", "manual": True})
        self.assertEqual(self.api("POST", "/api/discover", {})[0], 202)  # (a new search keeps it)
        self.assertEqual([s["host"] for s in self.finished()["speakers"]], ["192.168.1.109", "192.168.1.110",
                                                                             "salon.local"])

    def test_the_official_spotify_is_listed_only_when_discovery_met_it(self):
        self.assertTrue(wizard._speaker({**SPEAKER, "found_by": "spotify"})["official_listed"])
        self.assertTrue(wizard._speaker(SPEAKER)["official_listed"])
        for by in ("lithify", "libre"):
            self.assertFalse(wizard._speaker({**SPEAKER, "found_by": by})["official_listed"], by)
        self.assertFalse(wizard._speaker(SPEAKER, manual=True)["official_listed"])

    def test_a_typo_is_refused_and_a_silent_address_gives_way_to_the_next(self):
        for host in ("192.168.0", "192.168.0.256", "1234", "192.168.000.1"):
            with self.subTest(host=host):
                self.assertEqual(self.api("POST", "/api/add-host", {"host": host})[1]["error_key"], "bad_host")

        def probe(host: str, with_info: bool) -> dict:
            if host == "192.168.1.41":
                return {"supported": False, "reason_key": "not_found", "reason": "timed out"}
            return {"supported": True, "platform": "ls9", "name": "Salon"}

        with mock.patch.object(wizard, "probe_speaker", probe):
            self.api("POST", "/api/add-host", {"host": "192.168.1.41"})
            self.assertEqual(self.finished()["speakers"][0]["reason_key"], "not_found")
            self.api("POST", "/api/add-host", {"host": "192.168.1.40"})
            hosts = [s["host"] for s in self.finished()["speakers"]]
        self.assertEqual(hosts, ["192.168.1.40"])

    def test_a_speaker_that_runs_lithify_keeps_its_name(self):
        def probe(host: str, with_info: bool) -> dict:
            return {"supported": True, "platform": "ls9", "librespot_name": "Łazienka [Lithify]",
                    "librespot_version": "0.8.0", "name": "Łazienka", "official_listed": False}

        with mock.patch.object(wizard, "probe_speaker", probe):
            self.api("POST", "/api/add-host", {"host": "192.168.1.40"})
            self.finished()
        self.assertEqual(self.api("POST", "/api/step", {"step": "name", "host": "192.168.1.40"})[0], 200)
        chosen = self.api("GET", "/api/state")[1]["chosen"]
        self.assertEqual((chosen["default_name"], chosen["current_name"], chosen["official_name"]),
                         ("Łazienka [Lithify]", "Łazienka [Lithify]", ""))  # (its official Spotify is hidden)

    def test_a_speaker_that_never_answers_counts_as_silent(self):
        def slow(host: str, with_info: bool) -> dict:
            time.sleep(3)
            return {"supported": True}

        with mock.patch.object(wizard, "probe_speaker", slow), mock.patch.object(wizard, "PROBE_TIMEOUT", 0.2):
            self.api("POST", "/api/add-host", {"host": "192.168.1.150"})
            st = self.finished()
        self.assertEqual((st["speakers"][0]["supported"], st["speakers"][0]["reason_key"]), (False, "no_answer"))


class RunTest(unittest.TestCase):
    def test_finish_ends_the_wizard_and_the_exit_code_says_whether_it_installed(self):
        told = []
        with mock.patch.object(wizard, "firewall_help", lambda: FIREWALL), \
                mock.patch.object(wizard, "_tell", lambda url, *_: told.append(url)):
            result = []
            t = threading.Thread(target=lambda: result.append(wizard.run(open_browser=False)), daemon=True)
            t.start()
            end = time.monotonic() + 10
            while not told and time.monotonic() < end:
                time.sleep(0.02)
            url = wizard_address(told[0])
            c = http.client.HTTPConnection("127.0.0.1", url["port"], timeout=10)
            c.request("POST", "/api/quit", body=b"{}", headers={wizard.HEADER: url["token"],
                                                                 "Content-Type": "application/json"})
            self.assertEqual(c.getresponse().status, 200)
            c.close()
            t.join(10)
        self.assertEqual(result, [1])  # nothing was installed

    def test_a_port_in_use_is_an_error_not_a_traceback(self):
        err = io.StringIO()
        with mock.patch.object(wizard, "firewall_help", lambda: FIREWALL), contextlib.redirect_stderr(err):
            taken = wizard.Server(("127.0.0.1", 0), wizard.Wizard(), "x")
            self.addCleanup(taken.server_close)
            self.assertEqual(wizard.run(open_browser=False, port=taken.server_address[1]), 1)
            self.assertEqual(wizard.run(open_browser=False, port=70000), 1)
        self.assertEqual(err.getvalue().count("error: cannot start the setup page"), 2)


def wizard_address(url: str) -> dict:
    m = re.fullmatch(r"http://127\.0\.0\.1:(\d+)/\?t=([0-9a-f]{32})", url)
    if not m:
        raise AssertionError(f"not the wizard's address: {url}")
    return {"port": int(m.group(1)), "token": m.group(2)}


class CliTest(unittest.TestCase):
    def test_lithify_wizard_starts_it_with_its_options(self):
        with mock.patch.object(wizard, "run", return_value=0) as run, \
                mock.patch.object(cli, "stop_like_ctrl_c"), \
                mock.patch.object(config, "load", return_value=config.Config(None, [], {})):
            self.assertEqual(cli.main(["wizard", "--no-browser", "--port", "18200", "--lang", "pl"]), 0)
            run.assert_called_once_with(lang="pl", open_browser=False, port=18200, config_path=None)
            run.reset_mock()
            cli.main(["--config", "my.toml", "wizard"])
            run.assert_called_once_with(lang=None, open_browser=True, port=0, config_path="my.toml")


class StopHereError(Exception):
    pass


class InstallChoiceTest(unittest.TestCase):
    """`lithify install --host` installs on that speaker: one not configured yet is added first."""

    def setUp(self):
        self.devices: list[str] = []
        devices = self.devices

        class Device:
            def __init__(self, speaker):
                devices.append(speaker.host)

            def platform(self):
                raise StopHereError  # (far enough: the speaker is chosen)

        patch = mock.patch.object(cli, "Device", Device)
        patch.start()
        self.addCleanup(patch.stop)

    @staticmethod
    def speaker(sid: str, host: str) -> config.Speaker:
        return config.Speaker(sid, host, sid, "auto", dict(config.LIBRESPOT_DEFAULTS), dict(config.AGENT_DEFAULTS))

    def run_install(self, cfg: config.Config, host: str | None, added: config.Config | None = None) -> list:
        args = argparse.Namespace(speaker=None, host=host, config=None, build=False, settings=False, reboot=True)
        with mock.patch.object(cli, "auto_setup", return_value=added) as setup, self.assertRaises(StopHereError):
            cli.cmd_install(args, cfg)
        return setup.call_args_list

    def test_a_new_host_is_added_and_installed_not_the_configured_speaker(self):
        a = self.speaker("kuchnia", "192.168.1.109")
        both = config.Config(None, [a, self.speaker("salon", "192.168.1.110")], {})
        self.assertEqual(len(self.run_install(config.Config(None, [a], {}), "192.168.1.110", both)), 1)
        self.assertEqual(self.devices, ["192.168.1.110"])

    def test_a_configured_host_is_used_as_it_is(self):
        speakers = [self.speaker("kuchnia", "192.168.1.109"), self.speaker("salon", "192.168.1.110")]
        cfg = config.Config(None, speakers, {})
        self.assertEqual(self.run_install(cfg, "192.168.1.110"), [])
        self.assertEqual(self.devices, ["192.168.1.110"])

    def test_without_host_the_one_configured_speaker(self):
        cfg = config.Config(None, [self.speaker("kuchnia", "192.168.1.109")], {})
        self.assertEqual(self.run_install(cfg, None), [])
        self.assertEqual(self.devices, ["192.168.1.109"])

    def test_the_first_install_writes_the_name_from_the_environment(self):
        written = {}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(cli, "write_config", lambda target, host, **kw: written.update(kw, host=host)), \
                mock.patch.object(config, "load", return_value=config.Config(None, [], {})), \
                mock.patch.dict("os.environ", {"LITHIFY_NAME": "Kuchnia"}):
            cli.auto_setup(argparse.Namespace(config=str(Path(d) / "config.toml"), host="192.168.1.109"))
        self.assertEqual((written["host"], written["name"], written["interactive"]),
                         ("192.168.1.109", "Kuchnia", False))


if __name__ == "__main__":
    unittest.main()
