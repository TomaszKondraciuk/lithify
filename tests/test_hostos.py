"""What differs between Linux, macOS and Windows: paths, locks, files for the speaker, and the
service definitions. The Windows and macOS branches are checked here as text and data (they run
for real only on those systems)."""
import contextlib
import io
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from lithify import bundle, hostos, service, updates

POSIX_USER = os.name != "nt" and os.geteuid() != 0  # (root may remove anything: nothing to test)
GIT = shutil.which("git")


def git(*args, cwd=None) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class PathsTest(unittest.TestCase):
    def test_each_system_keeps_files_where_it_should(self):
        win = {"APPDATA": r"C:\Users\Ola\AppData\Roaming", "LOCALAPPDATA": r"C:\Users\Ola\AppData\Local"}
        self.assertEqual(hostos.config_dir(win, windows=True), Path(win["APPDATA"]) / "lithify")
        self.assertEqual(hostos.cache_dir(win, windows=True), Path(win["LOCALAPPDATA"]) / "lithify" / "cache")
        self.assertEqual(hostos.log_dir(win, windows=True), Path(win["LOCALAPPDATA"]) / "lithify" / "logs")
        unix = {"HOME": "/home/ola"}
        self.assertEqual(hostos.config_dir(unix, windows=False), Path("/home/ola/.config/lithify"))
        self.assertEqual(hostos.cache_dir(unix, windows=False), Path("/home/ola/.cache/lithify"))
        self.assertEqual(hostos.log_dir(unix, windows=False, macos=True), Path("/home/ola/Library/Logs/lithify"))
        self.assertEqual(hostos.config_dir({**unix, "XDG_CONFIG_HOME": "/x"}, windows=False), Path("/x/lithify"))
        self.assertEqual(hostos.cache_dir({**unix, "LITHIFY_CACHE": "/c"}, windows=False), Path("/c"))


class FilesTest(unittest.TestCase):
    def test_files_for_the_speaker_have_lf_endings(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "settings.default"
            hostos.write_lf(f, "name=Łazienka\nbitrate=320\n")
            self.assertEqual(f.read_bytes(), "name=Łazienka\nbitrate=320\n".encode())

    def test_read_only_trees_are_removed(self):
        with tempfile.TemporaryDirectory() as d:
            tree = Path(d) / "repo" / ".git" / "objects"
            tree.mkdir(parents=True)
            (tree / "pack").write_text("x")
            os.chmod(tree / "pack", 0o444)
            hostos.rmtree(Path(d) / "repo", quiet=False)
            self.assertFalse((Path(d) / "repo").exists())
            hostos.rmtree(Path(d) / "missing")  # nothing there: nothing to do

    def test_locks_exclude_each_other_across_processes(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / "build.lock"
            probe = ("import sys; sys.path.insert(0, sys.argv[2]); from pathlib import Path\n"
                     "from lithify import hostos\n"
                     "try:\n    with hostos.file_lock(Path(sys.argv[1]), wait=False): print('free')\n"
                     "except BlockingIOError: print('busy')")
            root = str(Path(__file__).resolve().parent.parent)
            with hostos.file_lock(lock):
                held = subprocess.run([sys.executable, "-c", probe, str(lock), root], capture_output=True, text=True)
            free = subprocess.run([sys.executable, "-c", probe, str(lock), root], capture_output=True, text=True)
            self.assertEqual((held.stdout.strip(), free.stdout.strip()), ("busy", "free"))

    def test_a_lock_held_too_long_ends_the_wait(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / "bundle.lock"
            with hostos.file_lock(lock):
                start = time.monotonic()
                with self.assertRaises(TimeoutError), hostos.file_lock(lock, timeout=0.3):
                    pass
                self.assertLess(time.monotonic() - start, 3)
            with hostos.file_lock(lock, timeout=0.3):  # free again
                pass

    def test_atomic_writes_keep_every_backup_and_leave_nothing_behind(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "config.toml"
            self.assertIsNone(hostos.write_atomic(f, "one\n", backup=True))  # (nothing there to keep)
            first = hostos.write_atomic(f, "two\n", backup=True)
            second = hostos.write_atomic(f, "three\n", backup=True)  # the same second: a name of its own
            self.assertEqual(f.read_text(encoding="utf-8"), "three\n")
            self.assertRegex(first.name, r"^config\.toml\.\d{8}-\d{6}\.bak$")
            self.assertNotEqual(first, second)
            self.assertEqual([b.read_text(encoding="utf-8") for b in (first, second)], ["one\n", "two\n"])
            with mock.patch.object(hostos, "replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
                hostos.write_atomic(f, "four\n")
            self.assertEqual(f.read_text(encoding="utf-8"), "three\n")  # a failed write changes nothing
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()),
                             sorted(["config.toml", first.name, second.name]))

    @unittest.skipIf(os.name == "nt", "symbolic links and modes: Linux and macOS")
    def test_an_atomic_write_keeps_links_and_modes(self):
        with tempfile.TemporaryDirectory() as d:
            real = Path(d) / "dotfiles" / "config.toml"
            real.parent.mkdir()
            real.write_text("old\n", encoding="utf-8")
            os.chmod(real, 0o640)
            link = Path(d) / "config.toml"
            link.symlink_to(real)
            hostos.write_atomic(link, "new\n")
            self.assertTrue(link.is_symlink())  # still the link: the file it points to was replaced
            self.assertEqual(real.read_text(encoding="utf-8"), "new\n")
            self.assertEqual(stat.S_IMODE(real.stat().st_mode), 0o640)  # and kept its mode
            fresh = Path(d) / "versions.toml"
            hostos.write_atomic(fresh, "x\n")
            self.assertEqual(stat.S_IMODE(fresh.stat().st_mode), 0o600)  # a new file: this user's only

    @unittest.skipIf(os.name == "nt", "file modes: Linux and macOS")
    def test_what_an_earlier_lithify_staged_is_made_private(self):
        with tempfile.TemporaryDirectory() as d:
            staged = Path(d) / "lazienka-0f0ff09301cb4654"
            staged.mkdir()
            (staged / "settings.default").write_text("ui_pin=246813\n", encoding="utf-8")
            os.chmod(staged / "settings.default", 0o664)
            os.chmod(staged, 0o775)
            hostos.make_private(staged)
            self.assertEqual(stat.S_IMODE(staged.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((staged / "settings.default").stat().st_mode), 0o600)
            with mock.patch.object(hostos.os, "chmod") as chmod:
                hostos.make_private(staged)  # already private: nothing is changed
            chmod.assert_not_called()

    @unittest.skipIf(os.name == "nt", "file modes: Linux and macOS")
    def test_private_files_are_this_users_only(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "settings.default"
            f.write_text("old", encoding="utf-8")
            os.chmod(f, 0o644)
            hostos.write_lf(f, "ui_pin=246813\n", private=True)
            self.assertEqual(f.read_bytes(), b"ui_pin=246813\n")
            self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(hostos.private_dir(Path(d) / "stage").stat().st_mode), 0o700)


class RemoveTest(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "symbolic links and modes: Linux and macOS")
    def test_a_link_goes_and_what_it_points_to_stays_as_it_was(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "target"
            target.mkdir()
            (target / "keep").write_text("x", encoding="utf-8")
            os.chmod(target, 0o555)
            try:
                link = Path(d) / "link"
                link.symlink_to(target, target_is_directory=True)
                hostos.rmtree(link, quiet=False)
                self.assertFalse(os.path.lexists(link))
                tree = Path(d) / "tree"
                tree.mkdir()
                (tree / "to-target").symlink_to(target, target_is_directory=True)
                hostos.rmtree(tree, quiet=False)
                self.assertFalse(tree.exists())
                self.assertTrue((target / "keep").is_file())
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o555)  # never chmod-ed
            finally:
                os.chmod(target, 0o755)

    @unittest.skipUnless(POSIX_USER, "permissions: Linux and macOS, not as root")
    def test_read_only_and_unreadable_directories_are_removed(self):
        with tempfile.TemporaryDirectory() as d:
            tree = Path(d) / "tree"
            (tree / "read-only").mkdir(parents=True)
            (tree / "read-only" / "f").write_text("x", encoding="utf-8")
            (tree / "closed" / "sub").mkdir(parents=True)
            (tree / "closed" / "sub" / "g").write_text("y", encoding="utf-8")
            os.chmod(tree / "read-only", 0o555)
            os.chmod(tree / "closed", 0o000)
            hostos.rmtree(tree, quiet=False)
            self.assertFalse(tree.exists())

    @unittest.skipUnless(POSIX_USER, "permissions: Linux and macOS, not as root")
    def test_what_cannot_be_removed_is_named_even_when_quiet(self):
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d) / "outer"
            tree = outer / "tree"
            tree.mkdir(parents=True)
            (tree / "f").write_text("x", encoding="utf-8")
            os.chmod(outer, 0o555)  # (outside the tree: left as it is)
            try:
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    hostos.rmtree(tree)
                self.assertIn(str(tree), err.getvalue())
                self.assertTrue(tree.exists())
                self.assertEqual(stat.S_IMODE(outer.stat().st_mode), 0o555)
                with self.assertRaisesRegex(OSError, "cannot remove"):
                    hostos.rmtree(tree, quiet=False)
            finally:
                os.chmod(outer, 0o755)


class CommandsTest(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "signals: Linux and macOS")
    def test_a_command_that_ignores_sigterm_is_ended_after_the_grace(self):
        code = ("import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); "
                "time.sleep(30)")
        p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, **hostos.own_group())
        self.addCleanup(p.stdout.close)
        self.assertEqual(p.stdout.readline().strip(), "ready")
        start = time.monotonic()
        hostos.terminate_tree(p, grace=0.5)
        self.assertIsNotNone(p.poll())
        self.assertLess(time.monotonic() - start, 5)

    def test_windows_commands_get_a_group_of_their_own_and_no_window(self):
        # (under the scheduled task, pythonw has no console: each console program would open one)
        with mock.patch.object(hostos, "WINDOWS", True):
            self.assertEqual(hostos.own_group(), {"creationflags": 0x00000200 | 0x08000000})
            self.assertEqual(hostos.no_window(), {"creationflags": 0x08000000})
        with mock.patch.object(hostos, "WINDOWS", False):
            self.assertEqual(hostos.own_group(), {"start_new_session": True})
            self.assertEqual(hostos.no_window(), {})

    def test_whether_a_process_runs(self):
        self.assertTrue(hostos.pid_alive(os.getpid()))
        gone = subprocess.Popen([sys.executable, "-c", ""])
        gone.wait()
        self.assertFalse(hostos.pid_alive(gone.pid))
        self.assertFalse(hostos.pid_alive(0))

    def test_a_firewall_that_does_not_answer_is_not_waited_for(self):
        with mock.patch.object(hostos, "WINDOWS", True), \
                mock.patch.object(hostos.subprocess, "run", side_effect=subprocess.TimeoutExpired("netsh", 30)) as run:
            self.assertFalse(hostos.firewall_rule_present())
        self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def test_a_child_ends_with_its_parent_after_its_own_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d) / "cleaned"
            root = str(Path(__file__).resolve().parent.parent)
            child = ("import pathlib, sys, time; sys.path.insert(0, sys.argv[2]); from lithify import service\n"
                     "service.exit_with_parent(on_exit=lambda: pathlib.Path(sys.argv[1]).write_text('stopped'))\n"
                     "print('ready', flush=True); time.sleep(60)\n")
            parent = ("import subprocess, sys\n"
                      f"p = subprocess.Popen([sys.executable, '-c', {child!r}, *sys.argv[1:]],\n"
                      "                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)\n"
                      "print(p.stdout.readline().strip(), flush=True)\n")  # ... and the parent ends, the child runs on
            out = subprocess.run([sys.executable, "-c", parent, str(marker), root], capture_output=True, text=True,
                                 timeout=60)
            self.assertEqual(out.stdout.strip(), "ready")
            deadline = time.monotonic() + 15
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertEqual(marker.read_text(), "stopped")


class PortTest(unittest.TestCase):
    def test_a_taken_port_moves_the_server_to_the_next_one(self):
        import http.server
        import socket
        busy = socket.socket()
        try:
            busy.bind(("127.0.0.1", hostos.TEMP_PORTS[0]))
        except OSError:
            self.skipTest("the first temporary port is already in use here")
        busy.listen()
        self.addCleanup(busy.close)
        srv = hostos.bind_server(http.server.HTTPServer, "127.0.0.1", http.server.BaseHTTPRequestHandler)
        self.addCleanup(srv.server_close)
        self.assertIn(srv.server_address[1], hostos.TEMP_PORTS[1:])
        self.assertIn("8095,18096-18099", " ".join(hostos.firewall_rule_command()))


class DockerTest(unittest.TestCase):
    def test_mounts_name_volumes_and_quote_awkward_paths(self):
        self.assertEqual(bundle._mount("lithify-cargo", "/cargo"),
                         ["--mount", "type=volume,source=lithify-cargo,target=/cargo"])
        spec = bundle._mount(Path("/tmp/a,b"), "/src", readonly=True)[1]
        # (the path as this system resolves it: /private/tmp on macOS, D:\tmp on Windows)
        self.assertEqual(spec, f'type=bind,"source={Path("/tmp/a,b").resolve()}",target=/src,readonly')

    def test_no_user_mapping_on_windows(self):
        with mock.patch.object(hostos, "WINDOWS", True):
            self.assertEqual(bundle._user(), [])
            cmd = bundle.docker_run("img", [(Path("/repo"), "/repo", True)], "/repo")
            self.assertNotIn("-u", cmd)
        if os.name != "nt":
            self.assertEqual(bundle._user(), ["-u", f"{os.getuid()}:{os.getgid()}"])


class ServiceTest(unittest.TestCase):
    def test_systemd_unit_quotes_paths(self):
        unit = service.systemd_unit(["/usr/bin/python3", "/home/o la/lithify/bin/lithify", "serve"])
        self.assertIn('ExecStart=/usr/bin/python3 "/home/o la/lithify/bin/lithify" serve\n', unit)
        self.assertIn("Restart=on-failure", unit)

    def test_launchd_agent_keeps_the_companion_alive(self):
        p = plistlib.loads(service.launchd_plist(["/usr/bin/python3", "/x/bin/lithify", "serve"], Path("/tmp/l.log")))
        self.assertEqual((p["Label"], p["KeepAlive"], p["RunAtLoad"]), (service.LAUNCHD_LABEL, True, True))
        self.assertIn("/usr/local/bin", p["EnvironmentVariables"]["PATH"])

    def test_windows_task_is_valid_xml_that_starts_at_logon(self):
        cmd = [r"C:\Python312\pythonw.exe", r"C:\Users\Ola & Co\lithify\bin\lithify", "serve", "--log", r"C:\l\c.log"]
        root = ET.fromstring(service.task_xml(cmd, r"PC\Ola").encode("utf-16"))  # noqa: S314 - our own task XML
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(root.find("t:Actions/t:Exec/t:Command", ns).text, cmd[0])
        self.assertEqual(root.find("t:Actions/t:Exec/t:Arguments", ns).text,
                         '"C:\\Users\\Ola & Co\\lithify\\bin\\lithify" serve --log C:\\l\\c.log')
        self.assertIsNotNone(root.find("t:Triggers/t:LogonTrigger", ns))
        self.assertEqual(root.find("t:Settings/t:ExecutionTimeLimit", ns).text, "PT0S")

    def test_the_supervisor_restarts_a_worker_that_asks(self):
        with tempfile.TemporaryDirectory() as d:
            count = Path(d) / "runs"
            worker = [sys.executable, "-c", f"import pathlib, sys; p = pathlib.Path({str(count)!r}); "
                                            "n = len(p.read_text()) if p.exists() else 0; "
                                            "p.write_text('x' * (n + 1)); "
                                            f"sys.exit({service.RESTART} if n < 2 else 3)"]
            self.assertEqual(service.supervise(worker), 3)
            self.assertEqual(count.read_text(), "xxx")  # started three times, the last one ended for good

    def test_service_tool_errors_say_what_the_tool_said(self):
        failing = [sys.executable, "-c", "import sys; sys.stderr.write('Unit file is masked.'); sys.exit(1)"]
        with self.assertRaisesRegex(RuntimeError, "cannot enable: Unit file is masked."):
            service._run(failing, "cannot enable")
        self.assertEqual(service._run(failing, "x", check=False).returncode, 1)

    def test_no_systemctl_gives_the_friendly_message(self):
        with mock.patch.object(hostos, "WINDOWS", False), mock.patch.object(hostos, "MACOS", False), \
                mock.patch.object(service.subprocess, "run", side_effect=FileNotFoundError(2, "systemctl")), \
                self.assertRaisesRegex(RuntimeError, "no systemd user session"):
            service.install(None)

    # A worker: the first run (good code) runs well, updates the checkout to `update` and asks for a
    # restart; code whose marker says "broken" fails at once.
    WORKER = ("import pathlib, subprocess, sys, time\n"
              "root, runs, update = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]\n"
              "n = int(runs.read_text()) if runs.exists() else 0\n"
              "runs.write_text(str(n + 1))\n"
              "if (root / 'marker').read_text().startswith('broken'):\n"
              "    sys.exit(1)\n"
              "if n == 0:\n"
              "    time.sleep(0.6)\n"
              "    subprocess.run(['git', '-C', str(root), 'merge', '-q', '--ff-only', update], check=True)\n"
              "    if len(sys.argv) > 4:\n"
              "        (root / 'marker').write_text('broken, and edited by hand\\n')\n"
              f"    sys.exit({service.RESTART})\n"
              "sys.exit(0)\n")

    @staticmethod
    def checkout(d: str) -> tuple[Path, str, str]:
        repo = Path(d) / "lithify"
        git("init", "-q", "-b", "main", str(repo))
        (repo / "marker").write_text("good\n", encoding="utf-8")
        (repo / "notes.txt").write_text("notes\n", encoding="utf-8")
        git("add", ".", cwd=repo)
        git("commit", "-q", "-m", "works", cwd=repo)
        good = git("rev-parse", "HEAD", cwd=repo)
        (repo / "marker").write_text("broken\n", encoding="utf-8")
        git("commit", "-q", "-am", "broken update", cwd=repo)
        broken = git("rev-parse", "HEAD", cwd=repo)
        git("reset", "-q", "--hard", good, cwd=repo)
        return repo, good, broken

    @unittest.skipUnless(GIT, "needs git")
    def test_a_broken_self_update_is_undone_once_and_keeps_local_work(self):
        with tempfile.TemporaryDirectory() as d, mock.patch("sys.stdout"):
            repo, good, broken = self.checkout(d)
            (repo / "notes.txt").write_text("notes\nmy own edit\n", encoding="utf-8")  # uncommitted work
            runs = Path(d) / "runs"
            rc = service.supervise([sys.executable, "-c", self.WORKER, str(repo), str(runs), broken], root=repo,
                                   healthy_after=0.5)
            self.assertEqual((rc, runs.read_text()), (0, "3"))  # good, broken (undone), good again
            self.assertEqual(updates.head(repo), good)
            self.assertEqual((repo / "notes.txt").read_text(encoding="utf-8"), "notes\nmy own edit\n")

    @unittest.skipUnless(GIT, "needs git")
    def test_an_undo_that_would_lose_work_is_refused_and_never_loops(self):
        with tempfile.TemporaryDirectory() as d, mock.patch("sys.stdout"):
            repo, _good, broken = self.checkout(d)
            runs = Path(d) / "runs"
            rc = service.supervise([sys.executable, "-c", self.WORKER, str(repo), str(runs), broken, "edit"],
                                   root=repo, healthy_after=0.5)
            self.assertEqual((rc, runs.read_text()), (1, "2"))
            self.assertEqual(updates.head(repo), broken)  # where it was: going back would lose the edit
            self.assertEqual((repo / "marker").read_text(encoding="utf-8"), "broken, and edited by hand\n")


if __name__ == "__main__":
    unittest.main()
