import contextlib
import http.client
import io
import json
import math
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from lithify import bundle, companion, config, hostos, updates

SPEAKERS = {"192.168.1.40"}
NAMES = {"pc.lan"}


def speaker(sid: str = "lazienka", host: str = "192.168.1.40", name: str = "Łazienka") -> config.Speaker:
    return config.Speaker(sid, host, name, "auto", dict(config.LIBRESPOT_DEFAULTS), dict(config.AGENT_DEFAULTS))


def make_bundle(cache: Path) -> None:
    src = cache / "bundle"
    src.mkdir(parents=True)
    for f in bundle.BUNDLE_FILES:
        (src / f).write_text("lithify=0.1.0\n" if f == "VERSIONS" else f, encoding="utf-8")
    bundle.write_sums(src, bundle.BUNDLE_FILES)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class GuardTest(unittest.TestCase):
    def test_speakers_without_a_browser_origin_are_served(self):
        self.assertTrue(companion.request_allowed("192.168.1.40", "192.168.1.10:8095", None, SPEAKERS, NAMES))
        self.assertTrue(companion.request_allowed("192.168.1.40", "pc.lan:8095", None, SPEAKERS, NAMES))
        self.assertTrue(companion.request_allowed("192.168.1.40", "[fe80::1]:8095", None, SPEAKERS, NAMES))

    def test_browsers_rebinding_and_strangers_are_refused(self):
        # any Origin: a web page in a browser (CSRF)
        self.assertFalse(companion.request_allowed("192.168.1.40", "192.168.1.10:8095", "http://evil.example",
                                                   SPEAKERS, NAMES))
        self.assertFalse(companion.request_allowed("192.168.1.40", "192.168.1.10:8095", "null", SPEAKERS, NAMES))
        # a foreign name in Host: DNS rebinding
        self.assertFalse(companion.request_allowed("192.168.1.40", "evil.example:8095", None, SPEAKERS, NAMES))
        # not a configured speaker (loopback included)
        self.assertFalse(companion.request_allowed("127.0.0.1", "127.0.0.1:8095", None, SPEAKERS, NAMES))
        self.assertFalse(companion.request_allowed("192.168.1.150", "192.168.1.10:8095", None, SPEAKERS, NAMES))

    def test_home_paths_are_scrubbed(self):
        self.assertEqual(companion.scrub(f"no bundle in {companion.HOME}/.cache/lithify/bundle"),
                         "no bundle in ~/.cache/lithify/bundle")

    def test_peers_cannot_put_terminal_escapes_into_the_log(self):
        cfg = config.Config(None, [speaker()], dict(config.COMPANION_DEFAULTS))
        handler = companion.make_handler(cfg, "http://192.168.1.10:8095", companion.Speakers(cfg))
        peer = mock.Mock(client_address=("192.168.1.40", 40000))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            handler.log_message(peer, '"%s" %s %s', "GET /\x1b[2J\x1b]0;pwned\x07\nfake line HTTP/1.0", "404", "-")
        self.assertNotIn("\x1b", out.getvalue())
        self.assertIn("\\x1b[2J", out.getvalue())
        self.assertEqual(out.getvalue().count("\n"), 1)

    def test_ipv4_speakers_are_known_over_ipv6_too(self):
        self.assertEqual(companion._peer("::ffff:192.168.1.40"), "192.168.1.40")
        self.assertEqual(companion._peer("192.168.1.40"), "192.168.1.40")
        self.assertEqual(companion._peer("fe80::1"), "fe80::1")


class StagerTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name)
        patch = mock.patch.object(bundle, "CACHE", self.cache)
        patch.start()
        self.addCleanup(patch.stop)
        make_bundle(self.cache)
        self.clock = Clock()
        self.st = companion.Stager(self.cache / "companion", "http://192.168.1.10:8095", clock=self.clock)

    def versions(self, prefix: str) -> list[Path]:
        return sorted(p for p in (self.cache / "companion").iterdir() if p.name.startswith(prefix))

    def test_restages_only_when_bundle_or_settings_change(self):
        s = speaker()
        first = self.st.get(s)
        stamp = (first / "SHA256SUMS").stat().st_mtime_ns
        os.utime(first / "SHA256SUMS", ns=(stamp - 10**9, stamp - 10**9))
        again = self.st.get(s)
        self.assertEqual(first, again)
        self.assertEqual((again / "SHA256SUMS").stat().st_mtime_ns, stamp - 10**9)  # untouched
        s.name = "Kuchnia"
        changed = self.st.get(s)
        self.assertIn("name=Kuchnia", (changed / "settings.default").read_text(encoding="utf-8"))
        self.assertFalse(any(p.name.startswith(".lazienka") for p in (self.cache / "companion").iterdir()))
        # one directory per version; the earlier one stays a while (a download may still read it)
        self.assertEqual(self.versions("lazienka-"), sorted([first, changed]))
        self.clock.now += companion.Stager.KEEP
        self.st.get(s)
        self.assertEqual(self.versions("lazienka-"), [changed])

    def test_speakers_whose_ids_begin_alike_keep_their_files(self):
        living, room = speaker("living", "192.168.1.40", "Living"), speaker("living-room", "192.168.1.41", "Room")
        theirs = self.st.get(room)
        mine = self.st.get(living)
        living.name = "Living (new name)"
        newest = self.st.get(living)
        self.clock.now += companion.Stager.KEEP
        self.st.get(living)  # living's earlier version goes now...
        self.assertFalse(mine.exists())
        self.assertTrue(newest.is_dir())
        self.assertTrue((theirs / ".complete").is_file())  # ...and living-room's files stay
        self.assertEqual(self.st.get(room), theirs)

    def test_a_download_finishes_with_the_version_it_began_with(self):
        s = speaker()
        began = self.st.for_download(s, "SHA256SUMS")
        s.name = "Kuchnia"  # meanwhile the settings change (or a build finishes)
        newest = self.st.get(s)  # the page asks for the newest files
        self.assertNotEqual(began, newest)
        self.assertEqual(self.st.for_download(s, "librespot"), began)  # the running download goes on from its version
        self.assertTrue((began / "librespot").is_file())
        self.assertEqual(self.st.for_download(s, "SHA256SUMS"), newest)  # the next one begins with the newest
        self.assertEqual(self.st.for_download(s, "librespot"), newest)
        self.clock.now += companion.Stager.KEEP
        self.st.get(s)
        self.assertFalse(began.exists())

    def test_a_version_that_comes_back_is_kept_again_when_it_is_replaced_again(self):
        s = speaker()
        first = self.st.get(s)
        s.name = "Kuchnia"
        second = self.st.get(s)
        s.name = "Łazienka"  # the change is undone: the first version is current again
        self.clock.now += 100
        self.assertEqual(self.st.get(s), first)
        s.name = "Kuchnia"
        self.clock.now += companion.Stager.KEEP
        self.assertEqual(self.st.get(s), second)
        self.assertTrue(first.is_dir())  # replaced only now: it stays KEEP seconds from now
        self.clock.now += companion.Stager.KEEP
        self.st.get(s)
        self.assertFalse(first.exists())

    def test_a_speaker_asking_during_a_bundle_swap_waits_for_the_new_bundle(self):
        s = speaker()
        self.st.get(s)
        new = self.cache / "bundle.new"
        new.mkdir()
        for f in bundle.BUNDLE_FILES:
            (new / f).write_text("lithify=0.2.0\n" if f == "VERSIONS" else f"{f} 2", encoding="utf-8")
        bundle.write_sums(new, bundle.BUNDLE_FILES)
        got: dict = {}
        with bundle.bundle_lock(self.cache, shared=False):  # what _swap_in holds for its two renames
            os.replace(self.cache / "bundle", self.cache / "bundle.old")
            asker = threading.Thread(target=lambda: got.update(d=self.st.get(s)))  # (no SHA256SUMS now)
            asker.start()
            time.sleep(0.3)
            os.replace(new, self.cache / "bundle")
        asker.join(10)
        self.assertEqual(bundle.read_versions(got["d"]), {"lithify": "0.2.0"})
        self.assertEqual(self.st.get(s), got["d"])  # named after the new bundle: not staged again

    @unittest.skipIf(os.name == "nt", "file modes: Linux and macOS")
    def test_what_an_earlier_lithify_staged_is_made_private_or_removed(self):
        s = speaker()
        d = self.st.get(s)
        root = self.cache / "companion"
        # As an earlier Lithify left them: open modes, and the layout from before versions had
        # directories of their own (<id>/ with a .fingerprint).
        os.chmod(root, 0o775)
        os.chmod(d, 0o775)
        os.chmod(d / "settings.default", 0o664)
        for legacy in (root / "lazienka", root / "lazienka-gora"):
            legacy.mkdir()
            (legacy / ".fingerprint").write_text("x", encoding="utf-8")
            (legacy / "settings.default").write_text("ui_pin=246813\n", encoding="utf-8")
        self.assertEqual(self.st.get(s), d)
        self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((d / "settings.default").stat().st_mode), 0o600)
        self.assertFalse((root / "lazienka").exists())  # this speaker's old layout goes
        self.assertTrue((root / "lazienka-gora").exists())  # another speaker's is not this one's to remove

    @unittest.skipIf(os.name == "nt", "file modes: Linux and macOS")
    def test_staged_settings_are_readable_by_this_user_only(self):
        s = speaker()
        s.agent["ui_pin"] = "246813"
        d = self.st.get(s)
        self.assertIn("ui_pin=246813", (d / "settings.default").read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE((self.cache / "companion").stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)
        for f in ("settings.default", "install.conf"):
            self.assertEqual(stat.S_IMODE((d / f).stat().st_mode), 0o600, f)


class SpeakersTest(unittest.TestCase):
    def test_an_address_the_speaker_no_longer_has_is_refused(self):
        cfg = config.Config(None, [speaker(host="speaker.lan")], dict(config.COMPANION_DEFAULTS))
        answers = [{"192.168.1.150"}, set()]
        speakers = companion.Speakers(cfg, resolve=lambda host: answers.pop(0))
        srv = companion.Server(("127.0.0.1", 0), companion.make_handler(cfg, "http://127.0.0.1:1", speakers), speakers)
        self.addCleanup(srv.server_close)
        self.assertFalse(srv.verify_request(None, ("192.168.1.150", 40000)))  # (not looked up yet)
        speakers.refresh()
        self.assertTrue(srv.verify_request(None, ("192.168.1.150", 40000)))
        speakers.refresh()  # the name no longer gives that address
        self.assertFalse(srv.verify_request(None, ("192.168.1.150", 40000)))
        with self.assertRaises(TypeError):
            speakers.table["10.0.0.1"] = cfg.speakers[0]  # a table is replaced, never changed

    def test_a_failed_lookup_keeps_the_last_addresses_and_never_stops_the_others(self):
        cfg = config.Config(None, [speaker("lazienka", "lazienka.local"), speaker("kuchnia", "a..b"),
                                   speaker("biuro", "192.168.1.107")], dict(config.COMPANION_DEFAULTS))
        answers = [{"192.168.1.150"}, socket.gaierror(-2, "Name or service not known"), {"192.168.1.151"}]

        def resolve(host: str) -> set[str]:
            if host == "a..b":
                return companion._resolve(host)  # the real lookup: UnicodeError, not OSError
            if host != "lazienka.local":
                return {host}
            answer = answers.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        speakers = companion.Speakers(cfg, resolve=resolve)
        speakers.refresh()
        self.assertEqual(speakers.table["192.168.1.150"].id, "lazienka")
        speakers.refresh()  # the name server does not answer this time
        self.assertEqual(speakers.table["192.168.1.150"].id, "lazienka")  # still let in
        self.assertEqual(speakers.table["192.168.1.107"].id, "biuro")  # (the others are as ever)
        self.assertEqual(speakers.table["a..b"].id, "kuchnia")  # never resolved: the host as written
        speakers.refresh()  # a new address: the old one goes
        self.assertNotIn("192.168.1.150", speakers.table)
        self.assertEqual(speakers.table["192.168.1.151"].id, "lazienka")


class ServerTest(unittest.TestCase):
    """A real server on 127.0.0.1, for a speaker configured as 127.0.0.1."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name)
        for target, value in ((bundle, ("CACHE", self.cache)), (companion, ("STATE", self.cache / "state.json"))):
            patch = mock.patch.object(target, *value)
            patch.start()
            self.addCleanup(patch.stop)
        self.release = threading.Event()
        self.addCleanup(self.release.set)

        def slow(host: str) -> set[str]:
            self.release.wait(10)  # a name server that takes its time
            return {host}

        cfg = config.Config(None, [speaker(host="127.0.0.1")], dict(config.COMPANION_DEFAULTS))
        self.speakers = companion.Speakers(cfg, resolve=slow, every=0.05).start()
        self.addCleanup(self.speakers.stop)
        handler = companion.make_handler(cfg, "http://127.0.0.1:8095", self.speakers)
        self.srv = companion.Server(("127.0.0.1", 0), handler, self.speakers)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def request(self, path: str, method: str = "GET") -> tuple[int, dict, bytes]:
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        try:
            c.request(method, path)
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            c.close()

    def test_a_slow_name_lookup_never_holds_up_a_speaker(self):
        start = time.monotonic()
        status, _, body = self.request("/api/build/status")
        self.assertEqual(status, 200)
        self.assertLess(time.monotonic() - start, 3)  # (the lookup itself takes 10 s)
        self.assertIn("running", json.loads(body))

    def test_an_unexpected_error_gets_an_answer_and_one_log_line(self):
        out = io.StringIO()
        with mock.patch.object(companion.Stager, "get", side_effect=RuntimeError("boom\n\x1b[31mred")), \
                contextlib.redirect_stdout(out):
            status, _, body = self.request("/api/latest")
        self.assertEqual(status, 500)
        self.assertIn("boom", json.loads(body)["error"])
        logged = [line for line in out.getvalue().splitlines() if "internal error" in line]
        self.assertEqual(len(logged), 1)
        self.assertNotIn("\x1b", out.getvalue())

    def test_staging_while_a_build_swaps_its_bundle_in_asks_to_come_back(self):
        make_bundle(self.cache)
        with mock.patch.object(companion, "STAGE_LOCK_WAIT", 0.2), bundle.bundle_lock(self.cache, shared=False):
            status, headers, body = self.request("/api/latest")
        self.assertEqual(status, 503)
        self.assertEqual(headers.get("Retry-After"), str(companion.RETRY_AFTER))
        self.assertIn("error", json.loads(body))
        status, _, body = self.request("/api/latest")  # the swap is done
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["versions"], {"lithify": "0.1.0"})
        status, _, body = self.request("/bundle/lazienka/SHA256SUMS")
        self.assertEqual(status, 200)
        self.assertIn(b"settings.default", body)

    def test_update_everything_downloads_the_newest_release_when_there_are_releases(self):
        saved = companion.build_status()
        self.addCleanup(lambda: [setattr(companion._Build, k, v) for k, v in saved.items()])
        for url, path, source in (("https://example.org/r", "/api/build?latest=1", "release"),
                                  ("https://example.org/r", "/api/build", "build"),  # (build again: a build)
                                  ("", "/api/build?latest=1", "build")):
            started = threading.Event()
            with self.subTest(url=url, path=path), mock.patch.object(bundle, "release_url", lambda u=url: u), \
                    mock.patch.object(companion, "_run_build", side_effect=lambda *a, s=started: s.set()) as run:
                companion._Build.running = False
                status, _, body = self.request(path, "POST")
                self.assertTrue(started.wait(5))
            self.assertEqual((status, json.loads(body)["source"]), (202, source))
            self.assertEqual(run.call_args[0][1], source == "release")

    def test_a_download_of_the_release_runs_lithify_fetch(self):
        saved = companion.build_status()
        self.addCleanup(lambda: [setattr(companion._Build, k, v) for k, v in saved.items()])
        ran = []

        class Proc:
            stdout = iter(["==> librespot ok\n", "==> VERSIONS ok\n"])

            def __init__(self, cmd, **_kw):
                ran.append(cmd)

            def wait(self):
                return 0

        with mock.patch.object(companion.subprocess, "Popen", Proc), mock.patch.object(updates, "head", lambda _r: ""):
            companion._run_build(True, release=True)
        self.assertEqual(ran[0][2:], ["fetch", "--if-newer", "--exit-with-parent"])
        self.assertEqual((companion._Build.ok, companion._Build.phase), (True, "VERSIONS ok"))

    def test_a_speaker_that_goes_away_is_one_log_line(self):
        out, err = io.StringIO(), io.StringIO()
        try:
            raise BrokenPipeError(32, "Broken pipe")
        except BrokenPipeError:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.srv.handle_error(None, ("127.0.0.1", 40000))
        self.assertEqual(out.getvalue().count("\n"), 1)
        self.assertIn("connection lost", out.getvalue())
        self.assertEqual(err.getvalue(), "")


class CheckTest(unittest.TestCase):
    def setUp(self):
        saved = {k: getattr(companion._Check, k) for k in ("result", "at", "generation", "running")}
        self.addCleanup(lambda: [setattr(companion._Check, k, v) for k, v in saved.items()])
        companion._Check.result, companion._Check.at, companion._Check.running = None, -math.inf, None
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.calls = 0

        def report(deps: bool = True) -> list[dict]:
            self.calls += 1
            time.sleep(0.3)  # the internet takes its time
            return [{"component": "librespot", "status": "current"}]

        for target, name, value in ((bundle, "CACHE", Path(tmp.name)), (updates, "report", report),
                                    (updates, "format_report", lambda rows: "text"),
                                    (bundle, "release_url", lambda: "")):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_with_releases_the_check_tells_the_newest_one(self):
        newest = {"lithify": "0.2.0", "librespot": "v0.9.0", "built": "2026-11-01T10:00:00Z"}
        with mock.patch.object(bundle, "release_url", lambda: "https://example.org/r"), \
                mock.patch.object(bundle, "release_versions", return_value=newest) as asked:
            got = companion.check_updates(False)
            self.assertTrue(companion._usable(False))  # (kept like a full answer)
        asked.assert_called_once_with("https://example.org/r")
        self.assertEqual(self.calls, 0)  # (no upstream source is asked: nothing is built here)
        self.assertEqual((got["source"], got["release"], got["rows"]), ("release", newest, []))
        self.assertIn("can_build", got)

    def test_releases_out_of_reach_are_said_and_asked_again_soon(self):
        failed = bundle.BuildError("download failed: timed out")
        with mock.patch.object(bundle, "release_url", lambda: "https://example.org/r"), \
                mock.patch.object(bundle, "release_versions", side_effect=failed):
            got = companion.check_updates(False)
            self.assertIsNone(got["release"])
            self.assertIn("timed out", got["release_error"])
            companion._Check.at -= 121
            self.assertFalse(companion._usable(False))  # (an incomplete answer: kept for 2 minutes)

    def test_without_releases_the_upstream_sources_are_asked(self):
        got = companion.check_updates(False)
        self.assertEqual((got["source"], self.calls), ("build", 1))

    def test_callers_during_a_check_wait_for_its_answer(self):
        answers = []
        threads = [threading.Thread(target=lambda: answers.append(companion.check_updates(False))) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(self.calls, 1)
        self.assertEqual(len(answers), 5)
        self.assertEqual({a["checked"] for a in answers}, {answers[0]["checked"]})

    def test_check_now_asks_the_sources_at_most_every_20_seconds(self):
        first = companion.check_updates(True)
        self.assertEqual(companion.check_updates(True), first)  # the answer just computed
        self.assertEqual(self.calls, 1)
        companion._Check.at -= companion.FRESH_EVERY
        companion.check_updates(True)
        self.assertEqual(self.calls, 2)

    def test_a_new_build_makes_the_last_answer_old(self):
        companion.check_updates(False)
        companion._forget_check()
        companion.check_updates(False)
        self.assertEqual(self.calls, 2)


class BuildStateTest(unittest.TestCase):
    def setUp(self):
        saved = companion.build_status()
        saved["proc"] = companion._Build.proc
        self.addCleanup(lambda: [setattr(companion._Build, k, v) for k, v in saved.items()])

    def test_changes_from_many_threads_leave_a_whole_state_file(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(companion, "STATE", Path(d) / "companion-build.json"):
            def change(i: int) -> None:
                for j in range(25):
                    companion._update(phase=f"{i}-{j}", error=f"error {i} {j} " * 40)

            threads = [threading.Thread(target=change, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
            state = json.loads(companion.STATE.read_text(encoding="utf-8"))
            self.assertEqual(set(state), {"running", "ok", "started", "finished", "error"})
            self.assertEqual([p.name for p in Path(d).iterdir()], ["companion-build.json"])  # no temporary files

    @unittest.skipIf(os.name == "nt", "signals: Linux and macOS")
    def test_a_build_ends_with_the_companion_after_being_asked_first(self):
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d) / "stopped"
            code = ("import pathlib, signal, sys, time\n"
                    "def bye(*_):\n    pathlib.Path(sys.argv[1]).write_text('containers removed')\n    sys.exit(130)\n"
                    "signal.signal(signal.SIGTERM, bye)\nprint('ready', flush=True)\ntime.sleep(30)\n")
            p = subprocess.Popen([sys.executable, "-c", code, str(marker)], stdout=subprocess.PIPE, text=True,
                                 **hostos.own_group())
            self.addCleanup(p.stdout.close)
            self.assertEqual(p.stdout.readline().strip(), "ready")
            companion._Build.proc = p
            with mock.patch.object(bundle, "remove_stale_containers") as remove, \
                    contextlib.redirect_stdout(io.StringIO()):
                companion.stop_build()
            self.assertEqual(marker.read_text(), "containers removed")  # SIGTERM came first
            self.assertIsNotNone(p.poll())
            remove.assert_called_once()


class AddressTest(unittest.TestCase):
    def test_listening_everywhere_tells_the_speakers_the_lan_address(self):
        cfg = config.Config(None, [speaker()], {**config.COMPANION_DEFAULTS, "listen": "0.0.0.0:8095"})
        with mock.patch.object(bundle, "lan_ip_for", return_value="192.168.1.10"):
            self.assertEqual(companion.listen_address(cfg), ("0.0.0.0", 8095))
            self.assertEqual(companion.public_url(cfg), "http://192.168.1.10:8095")
            cfg.companion["listen"] = "[::]:8096"
            self.assertEqual(companion.public_url(cfg), "http://192.168.1.10:8096")
            cfg.companion["listen"] = "192.168.1.105:8095"
            self.assertEqual(companion.public_url(cfg), "http://192.168.1.105:8095")
            cfg.companion["listen"] = "auto"
            self.assertEqual(companion.public_url(cfg), "http://192.168.1.10:8095")


if __name__ == "__main__":
    unittest.main()
