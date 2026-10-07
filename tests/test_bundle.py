import errno
import io
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from lithify import bundle, config, hostos, platforms, updates

LOCK = '''
[[package]]
name = "rustls"
version = "0.23.45"

[[package]]
name = "hyper"
version = "0.14.32"

[[package]]
name = "hyper"
version = "1.12.0"
'''


class BundleTest(unittest.TestCase):
    def test_lock_versions(self):
        self.assertEqual(bundle.lock_versions(LOCK, "hyper"), ["0.14.32", "1.12.0"])
        self.assertEqual(bundle.lock_versions(LOCK, "tokio"), [])

    def test_stage_renders_speaker_files_with_checksums(self):
        with tempfile.TemporaryDirectory() as d:
            src, dest = Path(d) / "bundle", Path(d) / "stage"
            src.mkdir()
            for f in bundle.BUNDLE_FILES:
                (src / f).write_text("lithify=0.1.0\n" if f == "VERSIONS" else f, encoding="utf-8")
            s = config.Speaker("lazienka", "192.168.1.40", "Łazienka", "auto",
                               dict(config.LIBRESPOT_DEFAULTS), dict(config.AGENT_DEFAULTS))
            stock = [{"name": "cast_shell", "command": []}]
            out = bundle.stage(src, s, platforms.LS9, stock, "http://192.168.1.110:8095", dest)
            sums = dict(reversed(line.split("  ")) for line in (out / "SHA256SUMS").read_text().splitlines())
            self.assertEqual(set(sums), {*bundle.BUNDLE_FILES, "settings.default", "install.conf", "process.json"})
            self.assertIn("name=Łazienka", (out / "settings.default").read_text(encoding="utf-8"))
            self.assertEqual(bundle.read_versions(out), {"lithify": "0.1.0"})
            # Without a stock list (web page updates) the Cast service list is left alone, and
            # settings travel only on purpose.
            out = bundle.stage(src, s, platforms.LS9, None, "http://192.168.1.110:8095", dest)
            self.assertFalse((out / "process.json").exists())
            self.assertFalse((out / "settings.patch").exists())
            out = bundle.stage(src, s, platforms.LS9, None, "http://192.168.1.110:8095", dest, push=["name"])
            self.assertIn("name=Łazienka", (out / "settings.patch").read_text(encoding="utf-8"))
            self.assertIn("settings.patch", (out / "SHA256SUMS").read_text())

    @staticmethod
    def _root(d: str, librespot: str) -> Path:
        root = Path(d) / "root"
        root.mkdir()
        (root / "versions.toml").write_text(f'[librespot]\nrepo = "x"\n{librespot}\n\n'
                                            '[alsa_lib]\nversion = "1.2.16"\n\n'
                                            '[rust]\ntoolchain = "1.90.0"\n', encoding="utf-8")
        return root

    def test_user_pins_only_move_forward(self):
        # librespot pinned to a release: a newer release chosen by --latest is used, an older one not
        with tempfile.TemporaryDirectory() as d:
            root, user = self._root(d, 'ref = "v0.8.0"'), Path(d) / "user.toml"
            bundle.write_user_pins({"librespot": "v9.9.9", "alsa_lib": "1.0.0", "rust": "1.90.0"}, user)
            pins = bundle.load_pins(root, user)
            self.assertEqual(pins["librespot"]["ref"], "v9.9.9")          # newer: used
            self.assertEqual(pins["librespot"]["pinned_ref"], "v0.8.0")
            self.assertEqual(pins["librespot"]["backports"], [])
            self.assertEqual(pins["alsa_lib"]["version"], "1.2.16")       # older: ignored
        self.assertGreater(bundle.version_key("v0.10.0"), bundle.version_key("v0.9.1"))
        self.assertGreater(bundle.version_key("1.2.16.1"), bundle.version_key("1.2.16"))

    def test_librespot_on_a_branch_takes_the_chosen_commit(self):
        pinned, chosen = "a" * 40, "b" * 40
        with tempfile.TemporaryDirectory() as d:
            root, user = self._root(d, f'ref = "dev"\ncommit = "{pinned}"'), Path(d) / "user.toml"
            self.assertEqual(bundle.load_pins(root, user)["librespot"]["commit"], pinned)
            bundle.write_user_pins({"librespot": chosen, "alsa_lib": "1.2.16", "rust": "1.90.0"}, user)
            self.assertIn(f'[librespot]\ncommit = "{chosen}"', user.read_text(encoding="utf-8"))
            pins = bundle.load_pins(root, user)
            self.assertEqual((pins["librespot"]["ref"], pins["librespot"]["commit"]), ("dev", chosen))
            self.assertEqual(pins["librespot"]["pinned_commit"], pinned)  # build() keeps the newer of the two
            # a release chosen while librespot was pinned to releases means nothing on a branch
            user.write_text('[librespot]\nref = "v9.9.9"\n', encoding="utf-8")
            pins = bundle.load_pins(root, user)
            self.assertEqual((pins["librespot"]["ref"], pins["librespot"]["commit"]), ("dev", pinned))

    def test_stage_needs_a_bundle(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(bundle.BuildError):
            bundle.stage(Path(d), None, platforms.LS9, None, "", Path(d) / "x")

    @unittest.skipIf(os.name == "nt", "file modes: Linux and macOS")
    def test_staged_settings_are_readable_by_this_user_only(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "bundle"
            src.mkdir()
            for f in bundle.BUNDLE_FILES:
                (src / f).write_text(f, encoding="utf-8")
            s = config.Speaker("lazienka", "192.168.1.40", "Łazienka", "auto", dict(config.LIBRESPOT_DEFAULTS),
                               {**config.AGENT_DEFAULTS, "ui_pin": "246813"})
            out = bundle.stage(src, s, platforms.LS9, None, "http://192.168.1.110:8095", Path(d) / "stage",
                               push=["ui_pin"])
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o700)
            for f in ("settings.default", "settings.patch", "install.conf"):
                self.assertEqual(stat.S_IMODE((out / f).stat().st_mode), 0o600, f)


class LatestPinsTest(unittest.TestCase):
    """`build --latest` tries newer versions; the user's pins file learns them only once a bundle
    built with them is in place."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name)
        self.root = BundleTest._root(tmp.name, 'ref = "v0.8.0"')
        self.user = self.d / "config" / "versions.toml"
        self.user.parent.mkdir()
        for target, name, value in ((bundle, "USER_PINS", self.user),
                                    (updates, "latest_librespot", lambda repo: ("v0.9.0", "github")),
                                    (updates, "latest_alsa", lambda: "1.2.17"),
                                    (updates, "latest_rust", lambda: "1.95.0"),
                                    (updates, "librespot_branch", lambda lp: {"head": "c" * 40, "behind": 3}),
                                    (bundle, "_prune_images", lambda keep: None)):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(bundle.shutil, "which", return_value="/usr/bin/docker")
        patch.start()
        self.addCleanup(patch.stop)

    def build(self, _build, root: Path | None = None) -> Path:
        with mock.patch.object(bundle, "_build", side_effect=_build), mock.patch("sys.stdout"):
            return bundle.build(root=root or self.root, cache=self.d / "cache", latest=True)

    def user_text(self) -> str | None:
        return self.user.read_text(encoding="utf-8") if self.user.exists() else None

    def test_a_build_killed_after_choosing_newer_versions_leaves_the_user_pins(self):
        for before in (None, '[librespot]\nref = "v0.8.1"\n'):
            with self.subTest(before=before):
                if before is None:
                    self.user.unlink(missing_ok=True)
                else:
                    self.user.write_text(before, encoding="utf-8")
                seen: dict = {}

                def killed(force, root, cache, pins, seen=seen):
                    # What a SIGKILL right now would leave behind (no handler runs after one).
                    seen["file"], seen["pins"] = self.user_text(), pins
                    raise KeyboardInterrupt

                with self.assertRaises(KeyboardInterrupt):
                    self.build(killed)
                self.assertEqual(seen["file"], before)
                self.assertEqual(self.user_text(), before)
                # (the newer versions were the ones being built)
                self.assertEqual(seen["pins"]["librespot"]["ref"], "v0.9.0")
                self.assertEqual(seen["pins"]["alsa_lib"]["version"], "1.2.17")
                self.assertEqual(seen["pins"]["rust"]["toolchain"], "1.95.0")

    def test_newer_versions_that_do_not_build_are_not_kept(self):
        def fails(force, root, cache, pins):
            raise bundle.BuildError("cargo build failed")

        with self.assertRaisesRegex(bundle.BuildError, "staying with the previous ones"):
            self.build(fails)
        self.assertIsNone(self.user_text())

    def test_versions_that_built_are_remembered_once_the_bundle_is_in_place(self):
        def builds(force, root, cache, pins):
            self.assertIsNone(self.user_text())  # not before the bundle is swapped in
            return cache / "bundle"

        self.build(builds)
        pins = bundle.load_pins(self.root, self.user)
        self.assertEqual((pins["librespot"]["ref"], pins["alsa_lib"]["version"], pins["rust"]["toolchain"]),
                         ("v0.9.0", "1.2.17", "1.95.0"))

    def test_a_branch_commit_is_kept_back_the_same_way(self):
        (self.d / "branch").mkdir()
        root = BundleTest._root(str(self.d / "branch"), f'ref = "dev"\ncommit = "{"a" * 40}"')

        def killed(force, root_, cache, pins):
            self.assertEqual(pins["librespot"]["commit"], "c" * 40)
            raise SystemExit(1)

        with self.assertRaises(SystemExit):
            self.build(killed, root)
        self.assertIsNone(self.user_text())
        self.build(lambda force, root_, cache, pins: cache / "bundle", root)
        self.assertIn(f'[librespot]\ncommit = "{"c" * 40}"', self.user_text())
        self.assertEqual(bundle.load_pins(root, self.user)["librespot"]["commit"], "c" * 40)


def running(pid: int) -> bool:
    """Alive, and not merely waiting to be reaped."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()[0]
    except (OSError, IndexError):
        # Reaped between the two looks (ENOENT or ESRCH) - unless there is no /proc (macOS),
        # where os.kill's answer is all there is.
        return not Path("/proc/self/stat").exists()
    return state != "Z"


class FullDisk:
    """build.log on a disk that is full once the command runs: the first line goes in, no more."""

    def open(self, mode: str, encoding: str | None = None):
        class Log(io.StringIO):
            lines = 0

            def write(self, s: str) -> int:
                Log.lines += 1
                if Log.lines > 1:
                    raise OSError(errno.EFBIG, "File too large")
                return super().write(s)

        return Log()


def _describe(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/status").read_text() + Path(f"/proc/{pid}/cmdline").read_text()
    except OSError as e:
        return str(e)


class CommandTest(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "process groups: Linux and macOS")
    def test_a_timeout_ends_grandchildren_that_hold_the_output_open(self):
        with tempfile.TemporaryDirectory() as d:
            for kw in ({}, {"log": Path(d) / "build.log"}):
                with self.subTest(logged=bool(kw)):
                    start = time.monotonic()
                    r = bundle.sh(["sh", "-c", "sleep 20; true"], check=False, timeout=1, **kw)
                    self.assertLess(time.monotonic() - start, 4)
                    self.assertEqual(r.returncode, 124)
            with self.assertRaisesRegex(bundle.BuildError, "did not finish within 1 s"):
                bundle.sh(["sh", "-c", "sleep 20; true"], timeout=1, log=Path(d) / "build.log")
            pidfile = Path(d) / "pid"
            bundle.sh(["sh", "-c", f"sleep 20 & echo $! > '{pidfile}'; wait; true"], check=False, timeout=1)
            pid = int(pidfile.read_text())
            deadline = time.monotonic() + 3
            while running(pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(running(pid), _describe(pid))
            self.assertEqual(bundle._running, {})

    def test_output_still_reaches_the_log(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "build.log"
            r = bundle.sh([sys.executable, "-c", "print('compiling'); print('done')"], log=log)
            self.assertEqual(r.stdout, "compiling\ndone\n")
            self.assertIn("compiling\ndone\n", log.read_text(encoding="utf-8"))
            self.assertEqual(bundle.sh([sys.executable, "-c", "print('quick')"]).stdout, "quick\n")

    def test_a_long_command_tells_how_far_it_has_come(self):
        lines = ["#1 [internal] load build definition from Dockerfile", "#5 [1/3] FROM docker.io/x/y:1",
                 "#5 DONE 30.2s", "#6 [2/3] RUN apt-get update", "#6 0.312 Get:1 http://deb.debian.org",
                 "#6 [2/3] RUN apt-get update", "#7 [3/3] RUN rustup toolchain install"]
        steps = bundle.docker_steps()
        self.assertEqual([n for n in map(steps, lines) if n],
                         ["builder image: step 1 of 3", "builder image: step 2 of 3", "builder image: step 3 of 3"])
        classic = bundle.docker_steps()
        self.assertEqual(classic("Step 2/7 : RUN apt-get update"), "builder image: step 2 of 7")
        crates = bundle.crates_compiled(every=2)
        said = [crates(f"   Compiling crate{i} v1.0.{i}") for i in range(1, 6)]
        self.assertEqual(said, [None, "crates compiled: 2", None, "crates compiled: 4", None])
        self.assertIsNone(crates("    Finished `release` profile"))
        with tempfile.TemporaryDirectory() as d, mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = "print('#5 [1/2] FROM x'); print('#5 DONE'); print('#6 [2/2] RUN make')"
            bundle.sh([sys.executable, "-c", code], log=Path(d) / "build.log", progress=bundle.docker_steps())
        self.assertEqual(out.getvalue(), "    builder image: step 1 of 2\n    builder image: step 2 of 2\n")

    def test_a_log_that_cannot_be_written_stops_the_container_and_says_why(self):
        started, stopped = [], []
        real_start = bundle._start

        def start(cmd, env, container, **kw):  # stands in for `docker run`: it prints, and runs on
            code = "import time\nwhile True:\n    print('compiling', flush=True)\n    time.sleep(0.05)\n"
            started.append(real_start([sys.executable, "-c", code], env, container, **kw))
            return started[-1]

        with mock.patch.object(bundle, "_start", side_effect=start), \
                mock.patch.object(bundle, "_stop", side_effect=stopped.append), \
                self.assertRaisesRegex(bundle.BuildError, r"was stopped: \[Errno 27\] File too large"):
            bundle.sh(["docker", "run", "--rm", "img", "cargo", "build"], log=FullDisk())
        self.assertEqual(len(stopped), 1)
        self.assertTrue(stopped[0].startswith("lithify-"))  # its container went (docker rm -f) ...
        self.assertIsNotNone(started[0].wait(timeout=5))  # ... and so did the command

    def test_a_command_that_cannot_start_says_so(self):
        with self.assertRaisesRegex(bundle.BuildError, "cannot run no-such-tool-here"):
            bundle.sh(["no-such-tool-here", "--version"])

    def test_containers_carry_their_owner(self):
        seen = []

        def run(cmd, limit, env, container):
            seen.append((cmd, container, env))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch.object(bundle, "_run_captured", side_effect=run):
            bundle.sh(["docker", "run", "--rm", "img", "true"])
        cmd, container, env = seen[0]
        self.assertEqual(cmd[cmd.index("--label") + 1], f"lithify-build={socket.gethostname()}:{os.getpid()}")
        self.assertEqual(cmd[cmd.index("--name") + 1], container)
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")

    def test_only_containers_of_processes_that_are_gone_are_removed(self):
        gone = subprocess.Popen([sys.executable, "-c", ""])
        gone.wait()
        me = socket.gethostname()
        listing = f"aaa {me}:{gone.pid}\nbbb {me}:{os.getpid()}\nccc another-pc:{gone.pid}\nddd \n"
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, listing if cmd[:2] == ["docker", "ps"] else "", "")

        with mock.patch.object(bundle.shutil, "which", return_value="/usr/bin/docker"), \
                mock.patch.object(bundle.subprocess, "run", side_effect=run), mock.patch("sys.stdout"):
            self.assertEqual(bundle.remove_stale_containers(), 1)
        self.assertEqual(calls[-1], ["docker", "rm", "-f", "aaa"])

    def test_stopping_ends_the_running_commands_and_starts_no_new_one(self):
        with mock.patch.object(bundle, "_stopping", bundle.threading.Event()), mock.patch.object(bundle, "_stop"):
            started = time.monotonic()
            # (a command that runs while the build is told to stop)
            timer = bundle.threading.Timer(0.5, bundle.stop_all)
            timer.start()
            r = bundle.sh([sys.executable, "-c", "import time; time.sleep(20)"], check=False, timeout=30)
            timer.join()
            self.assertLess(time.monotonic() - started, 10)
            self.assertNotEqual(r.returncode, 0)
            with self.assertRaisesRegex(bundle.BuildError, "being stopped"):
                bundle.sh([sys.executable, "-c", ""])


class FetchTest(unittest.TestCase):
    def test_a_release_comes_over_https_only(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(bundle.BuildError, "must be https"):
                bundle.fetch("http://example.com/releases/latest/download", cache=Path(d))
            self.assertEqual(list(Path(d).iterdir()), [])  # refused before anything was touched
        reply = mock.MagicMock()  # redirected to plain HTTP on the way
        reply.__enter__.return_value.geturl.return_value = "http://mirror.example/SHA256SUMS"
        with mock.patch.object(bundle.urllib.request, "urlopen", return_value=reply), \
                self.assertRaisesRegex(bundle.BuildError, "must be https"):
            bundle._download("https://example.com/SHA256SUMS")


class SwapTest(unittest.TestCase):
    @staticmethod
    def bundle_dir(d: Path, tag: str) -> Path:
        d.mkdir(parents=True)
        for f in bundle.BUNDLE_FILES:
            (d / f).write_text(f"{tag} {f}", encoding="utf-8")
        bundle.write_sums(d, bundle.BUNDLE_FILES)
        return d

    def test_a_swap_cut_off_between_its_renames_is_finished_next_time(self):
        with tempfile.TemporaryDirectory() as t, mock.patch("sys.stdout"):
            cache = Path(t)
            self.bundle_dir(cache / "bundle.old", "previous")  # the computer stopped after the first rename
            self.assertTrue(bundle.recover_bundle(cache))
            self.assertEqual((cache / "bundle" / "VERSIONS").read_text(encoding="utf-8"), "previous VERSIONS")
            self.assertFalse((cache / "bundle.old").exists())
            # a swap that meets the same state brings the old one back first, then replaces it
            shutil.move(cache / "bundle", cache / "bundle.old")
            bundle._swap_in(self.bundle_dir(cache / "bundle.new", "new"), cache / "bundle", cache)
            self.assertEqual((cache / "bundle" / "VERSIONS").read_text(encoding="utf-8"), "new VERSIONS")
            self.assertEqual(sorted(p.name for p in cache.iterdir()), ["bundle", "bundle.lock"])

    def test_an_incomplete_bundle_set_aside_is_not_brought_back(self):
        with tempfile.TemporaryDirectory() as t:
            cache = Path(t)
            (cache / "bundle.old").mkdir()
            (cache / "bundle.old" / "VERSIONS").write_text("x", encoding="utf-8")
            self.assertFalse(bundle.recover_bundle(cache))
            self.assertFalse((cache / "bundle").exists())

    def test_a_rename_that_fails_keeps_the_previous_bundle(self):
        with tempfile.TemporaryDirectory() as t:
            cache = Path(t)
            self.bundle_dir(cache / "bundle", "previous")
            new = self.bundle_dir(cache / "bundle.new", "new")
            real = hostos.replace

            def refused(src, dst, *args, **kw):
                if Path(src) == new:
                    raise PermissionError(13, "in use")  # (Windows: a virus scanner, every attempt)
                return real(src, dst, *args, **kw)

            with mock.patch.object(hostos, "replace", side_effect=refused), self.assertRaises(PermissionError):
                bundle._swap_in(new, cache / "bundle", cache)
            self.assertEqual((cache / "bundle" / "VERSIONS").read_text(encoding="utf-8"), "previous VERSIONS")

    def test_windows_rides_out_a_short_refusal(self):
        attempts = []

        def replace(src, dst):
            attempts.append(src)
            if len(attempts) < 3:
                raise PermissionError(13, "in use")

        with mock.patch.object(hostos, "WINDOWS", True), mock.patch.object(hostos.os, "replace", side_effect=replace), \
                mock.patch.object(hostos.time, "sleep"):
            hostos.replace("a", "b")
        self.assertEqual(len(attempts), 3)
        with mock.patch.object(hostos, "WINDOWS", False), \
                mock.patch.object(hostos.os, "replace", side_effect=PermissionError(13, "no")), \
                self.assertRaises(PermissionError):
            hostos.replace("a", "b")  # elsewhere a refusal is final


SOURCE_FILES = {"build/Dockerfile": "FROM x\n", "build/patches/a.patch": "--- a\n",
                "agent/src/main.rs": "fn main() {}\n", "agent/ui/app.js": "start()\n",
                "device/ls9/install.sh": "echo ok\n"}
LITTER = {"build/.DS_Store": "finder", "build/patches/a.patch~": "backup", "agent/src/.main.rs.swp": "vim",
          "agent/target/release/agent": "binary", "agent/ui/Thumbs.db": "explorer", "device/desktop.ini": "x",
          "device/__pycache__/x.pyc": "cache", "agent/notes.log": "ignored by git"}


class SourcesTest(unittest.TestCase):
    @staticmethod
    def tree(root: Path, files: dict) -> Path:
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
        (root / "src").mkdir(exist_ok=True)
        (root / "src" / "Cargo.lock").write_text("lock", encoding="utf-8")
        (root / "src" / "Cargo.toml").write_text("toml", encoding="utf-8")
        return root

    def hashes(self, root: Path) -> tuple[str, str]:
        return (bundle.agent_build_id(root, "rustc 1.90.0"),
                bundle.librespot_stamp(root, {"librespot": {"ref": "dev"}}, root / "src", "rustc 1.90.0"))

    def test_build_hashes_skip_editor_litter_and_build_output(self):
        with tempfile.TemporaryDirectory() as d:
            clean = self.tree(Path(d) / "clean", SOURCE_FILES)
            messy = self.tree(Path(d) / "messy", {**SOURCE_FILES, **{k: v for k, v in LITTER.items()
                                                                     if not k.endswith(".log")}})
            self.assertEqual(self.hashes(clean), self.hashes(messy))
            self.assertEqual(self.hashes(clean), self.hashes(clean))  # stable
            self.assertEqual([f.relative_to(messy).as_posix() for f in bundle.sources(messy, "agent", "device")],
                             ["agent/src/main.rs", "agent/ui/app.js", "device/ls9/install.sh"])
            (messy / "agent" / "src" / "main.rs").write_text("fn main() { changed() }\n", encoding="utf-8")
            (messy / "build" / "patches" / "a.patch").write_text("--- b\n", encoding="utf-8")
            agent, stamp = self.hashes(messy)
            self.assertNotEqual(agent, self.hashes(clean)[0])
            self.assertNotEqual(stamp, self.hashes(clean)[1])

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_a_git_checkout_hashes_what_git_knows(self):
        with tempfile.TemporaryDirectory() as d:
            clean = self.tree(Path(d) / "clean", SOURCE_FILES)
            repo = self.tree(Path(d) / "repo", {**SOURCE_FILES, **LITTER, ".gitignore": "*.log\n"})
            subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "add", "build", "agent/src/main.rs", "device"], check=True,
                           capture_output=True)  # agent/ui/app.js stays untracked: new, not ignored
            self.assertEqual(self.hashes(repo), self.hashes(clean))
            (repo / "agent" / "ui" / "new.js").write_text("more()\n", encoding="utf-8")  # a new source file
            self.assertNotEqual(self.hashes(repo)[0], self.hashes(clean)[0])


class LockTest(unittest.TestCase):
    def test_a_lock_held_too_long_is_worth_retrying_and_the_block_errors_pass_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            with bundle.bundle_lock(cache, shared=False):
                start = time.monotonic()
                with self.assertRaises(bundle.LockTimeoutError), bundle.bundle_lock(cache, timeout=0.2):
                    pass
                self.assertLess(time.monotonic() - start, 3)
            with self.assertRaises(TimeoutError) as raised, bundle.bundle_lock(cache, timeout=0.2):
                raise TimeoutError("from the block")
            self.assertNotIsInstance(raised.exception, bundle.BuildError)


if __name__ == "__main__":
    unittest.main()
