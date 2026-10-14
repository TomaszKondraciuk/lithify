"""The device scripts: they must fit the console (no single quotes, short lines), and install.sh
and rollback.sh are run for real in a sandbox (a fake install directory, failures injected)."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path

from lithify import console, platforms

DEVICE = Path(__file__).resolve().parent.parent / "device"
SHELL = shutil.which("mksh") or shutil.which("dash")
TOOLS = SHELL and all(shutil.which(t) for t in ("busybox", "curl", "openssl"))


class DeviceScriptsTest(unittest.TestCase):
    def test_scripts_fit_the_console_upload(self):
        scripts = sorted(DEVICE.rglob("*.sh"))
        self.assertTrue(scripts)
        for path in scripts:
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                with self.subTest(script=path.name, line=n):
                    self.assertNotIn("'", line)
                    # The agent prepends a few variable lines; keep a margin.
                    self.assertLessEqual(len(line), console.MAX_LINE - 10)

    def test_files_stay_readable_for_the_services(self):
        # The console's umask is 077 while librespot and the agent run as uid 1000: every script
        # that writes files into the install directory must open them up again (a rollback once
        # left librespot.args unreadable and librespot started without its arguments).
        for name in ("install.sh", "rollback.sh"):
            text = (DEVICE / "ls9" / name).read_text(encoding="utf-8")
            with self.subTest(script=name):
                self.assertIn('chmod 644 "$f"', text)
                self.assertIn("chmod 755", text)

    def test_install_paths_come_from_the_caller(self):
        for name in ("install.sh", "persist.sh", "rollback.sh"):
            text = (DEVICE / "ls9" / name).read_text(encoding="utf-8")
            with self.subTest(script=name):
                self.assertIn("D=${LITHIFY_BASE:-/lsync/lithify}", text)


@unittest.skipUnless(TOOLS, "needs mksh or dash, busybox, curl and openssl")
class InstallSandboxTest(unittest.TestCase):
    """install.sh and rollback.sh against a fake /lsync/lithify, as the console runs them."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lithify-sandbox-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.base, self.stage, self.bin = self.tmp / "lithify", self.tmp / "stage", self.tmp / "bin"
        for d in (self.base / "alsa", self.base / "cache", self.stage, self.bin):
            d.mkdir(parents=True)
        self._version(self.base, "OLD")
        (self.base / "alsa" / "alsa.conf").write_text("conf OLD\n")
        self._stage("NEW")

    def _version(self, d: Path, label: str):
        (d / "librespot").write_text(f"librespot {label}\n")
        (d / "lithify-agent").write_text(f"#!/bin/sh\nexit 0\n# {label}\n")
        (d / "VERSIONS").write_text(f"librespot={label}\n")
        (d / "SHA256SUMS").write_text(f"sums {label}\n")

    def _stage(self, label: str):
        self._version(self.stage, label)
        (self.stage / "SHA256SUMS").unlink()
        alsa = self.tmp / "alsa-src" / "alsa"
        alsa.mkdir(parents=True)
        (alsa / "alsa.conf").write_text(f"conf {label}\n")
        with tarfile.open(self.stage / "alsa.tar", "w") as t:
            t.add(alsa, arcname="alsa")
        (self.stage / "settings.default").write_text("name=x\n")
        names = ["librespot", "lithify-agent", "alsa.tar", "VERSIONS", "settings.default"]
        sums = "".join(f"{hashlib.sha256((self.stage / n).read_bytes()).hexdigest()}  {n}\n" for n in names)
        (self.stage / "SHA256SUMS").write_text(sums)

    def _run(self, script: str, *args: str) -> str:
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "LITHIFY_BASE": str(self.base),
               "LITHIFY_LEGACY": str(self.tmp / "legacy")}
        r = subprocess.run(["sh", "-c", f"umask 077; exec {SHELL} {DEVICE / 'ls9' / script} \"$@\"", "sh", *args],
                           env=env, cwd=self.tmp, capture_output=True, text=True, timeout=60)
        return r.stdout + r.stderr

    def _fail_mv_of(self, name: str):
        fake = self.bin / "mv"
        fake.write_text(f'#!/bin/sh\ncase "$1" in {name}) echo "mv: no space left" >&2; exit 1;; esac\n'
                        f'exec {shutil.which("mv")} "$@"\n')
        fake.chmod(0o755)

    def _label(self) -> str:
        return (self.base / "VERSIONS").read_text().strip()

    def test_install_keeps_the_old_version_and_opens_the_files(self):
        out = self._run("install.sh", f"file://{self.stage}")
        self.assertIn("INSTALL_OK", out)
        self.assertEqual(self._label(), "librespot=NEW")
        self.assertEqual((self.base / "alsa" / "alsa.conf").read_text(), "conf NEW\n")
        self.assertEqual((self.base / "prev" / "VERSIONS").read_text(), "librespot=OLD\n")
        self.assertEqual((self.base / "prev" / "alsa" / "alsa.conf").read_text(), "conf OLD\n")
        self.assertEqual((self.base / "prev" / "SHA256SUMS").read_text(), "sums OLD\n")
        self.assertEqual((self.base / "librespot").stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.base / "VERSIONS").stat().st_mode & 0o777, 0o644)
        self.assertFalse((self.base / "new").exists())
        self.assertFalse((self.base / ".installing").exists())

    def test_a_failed_replace_puts_the_old_version_back(self):
        self._fail_mv_of("lithify-agent")
        out = self._run("install.sh", f"file://{self.stage}")
        self.assertIn("FAIL install lithify-agent", out)
        self.assertNotIn("INSTALL_OK", out)
        self.assertEqual((self.base / "librespot").read_text(), "librespot OLD\n")  # moved before, restored
        self.assertEqual(self._label(), "librespot=OLD")
        self.assertFalse((self.base / "new").exists())
        # The retry keeps the last complete version in prev/, so a rollback still gets OLD.
        (self.bin / "mv").unlink()
        self.assertIn("INSTALL_OK", self._run("install.sh", f"file://{self.stage}"))
        self.assertEqual((self.base / "prev" / "VERSIONS").read_text(), "librespot=OLD\n")
        out = self._run("rollback.sh", "version")
        self.assertIn("VERSION_ROLLED_BACK", out)
        self.assertEqual(self._label(), "librespot=OLD")
        self.assertEqual((self.base / "SHA256SUMS").read_text(), "sums OLD\n")
        self.assertEqual((self.base / "alsa" / "alsa.conf").read_text(), "conf OLD\n")
        self.assertEqual((self.base / "lithify-agent").stat().st_mode & 0o777, 0o755)

    def test_a_bad_download_changes_nothing(self):
        (self.stage / "librespot").write_text("tampered\n")
        out = self._run("install.sh", f"file://{self.stage}")
        self.assertIn("FAIL checksum librespot", out)
        self.assertEqual(self._label(), "librespot=OLD")
        self.assertFalse((self.base / "new").exists())
        self.assertFalse((self.base / "prev").exists())


# The Cast service list (shortened) as the speaker has it from the factory.
STOCK_LIST = json.dumps([{"name": "cast_shell", "command": ["/system/chrome/cast_shell"]},
                         {"name": "setup", "command": ["/system/chrome/setup_server"]}], indent=2) + "\n"

# pidof for the sandbox: only the programs it started itself (pids in $SANDBOX/pids/NAME, started
# as $SANDBOX/progs/NAME), never a process of this computer; one that has ended is not there.
FAKE_PIDOF = r"""#!/bin/sh
out=
for n in "$@"; do
  [ -f "$SANDBOX/pids/$n" ] || continue
  for p in $(cat "$SANDBOX/pids/$n"); do
    grep -q "$SANDBOX/progs/" /proc/$p/cmdline 2>/dev/null || continue
    [ "$(cut -d" " -f3 /proc/$p/stat 2>/dev/null)" = Z ] && continue
    out="$out $p"
  done
done
[ -n "$out" ] && echo $out
exit 0
"""

# ip for the sandbox: the speaker's routing table, with two fail-fast routes; deletions are noted.
FAKE_IP = """#!/bin/sh
case "$*" in
  route) cat "$SANDBOX/routes" ;;
  "route del "*) echo "$*" >> "$SANDBOX/ip.log" ;;
  *) echo "ip: not in the sandbox: $*" >&2; exit 1 ;;
esac
"""
ROUTES = ("default via 192.168.1.1 dev wlan0  metric 304 \nunreachable 151.101.2.248 \n"
          "unreachable 199.232.0.0/16 \n192.168.1.0/24 dev wlan0  proto kernel  scope link  src 192.168.1.109 \n")


@unittest.skipUnless(TOOLS and os.path.isdir("/proc/self"), "needs mksh or dash, busybox, curl, openssl and /proc")
class UninstallSandboxTest(unittest.TestCase):
    """rollback.sh stock [purge] on a fake speaker: its install directory, the Cast service list,
    its programs, routes and files in RAM, as the console runs it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lithify-uninstall-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.base, self.legacy, self.pj = self.tmp / "lithify", self.tmp / "cc", self.tmp / "process.json"
        self.ram, self.bin, self.pids = self.tmp / "ram", self.tmp / "bin", self.tmp / "pids"
        for d in (self.base / "backup", self.base / "cache", self.base / "prev", self.ram / "lithify-librespot",
                  self.bin, self.pids):
            d.mkdir(parents=True)
        (self.base / "librespot").write_text("librespot\n")
        (self.base / "cache" / "credentials.json").write_text("{}\n")
        (self.base / "backup" / "process.json.orig").write_text(STOCK_LIST)
        self.ours = platforms.render_process_list(json.loads(STOCK_LIST), str(self.base))
        self.pj.write_text(self.ours)
        for name in ("lithify-agent.log", "lithify-official.hidden", "lithify-librespot.state"):
            (self.ram / name).write_text("x\n")
        (self.ram / "lithify-librespot" / "track.tmp").write_text("audio\n")
        (self.ram / "other.log").write_text("not ours\n")
        (self.tmp / "routes").write_text(ROUTES)
        for name, text in (("pidof", FAKE_PIDOF), ("ip", FAKE_IP),
                           ("reboot", '#!/bin/sh\necho rebooted >> "$SANDBOX/reboot.log"\n')):
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o755)

    def _start(self, name: str) -> subprocess.Popen:
        """One of the speaker's programs: a process that waits, named where the fake pidof finds it."""
        p = subprocess.Popen([str(self.tmp / "progs" / name), "-c", "import time; time.sleep(60)"],
                             executable=sys.executable)
        self.addCleanup(p.kill)
        threading.Thread(target=p.wait, daemon=True).start()  # (reaped at once: an ended one is gone)
        with (self.pids / name).open("a") as f:
            f.write(f"{p.pid}\n")
        return p

    def _run(self, *args: str) -> str:
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "SANDBOX": str(self.tmp),
               "LITHIFY_BASE": str(self.base), "LITHIFY_LEGACY": str(self.legacy),
               "LITHIFY_PROCESS_LIST": str(self.pj), "LITHIFY_TMP": str(self.ram)}
        script = DEVICE / "ls9" / "rollback.sh"
        r = subprocess.run(["sh", "-c", f'umask 077; exec {SHELL} {script} "$@"', "sh", *args],
                           env=env, cwd=self.tmp, capture_output=True, text=True, timeout=60)
        return r.stdout + r.stderr

    def _gone(self, p: subprocess.Popen) -> bool:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            return False
        return True

    def test_uninstall_puts_the_stock_list_back_and_leaves_nothing(self):
        agent, librespot = self._start("lithify-agent"), self._start("librespot")
        out = self._run("stock", "purge")
        self.assertIn("STOCK_RESTORED", out)
        self.assertEqual(self.pj.read_text(), STOCK_LIST)
        self.assertFalse(self.base.exists())
        self.assertFalse(self.tmp.joinpath("lithify.removed").exists())
        self.assertTrue(self._gone(agent) and self._gone(librespot))
        self.assertEqual((self.tmp / "ip.log").read_text().splitlines(),
                         ["route del unreachable 151.101.2.248", "route del unreachable 199.232.0.0/16"])
        # In RAM: Lithify's files are gone; the stock list stays there until the reboot.
        self.assertEqual(sorted(p.name for p in self.ram.iterdir()), ["other.log", "process.json.orig"])
        self.assertEqual((self.ram / "process.json.orig").read_text(), STOCK_LIST)
        self.assertFalse((self.tmp / "reboot.log").exists())

    def test_the_reboot_is_left_to_the_caller_unless_asked_for(self):
        out = self._run("stock", "purge", "reboot")
        self.assertIn("STOCK_RESTORED", out)
        self.assertEqual((self.tmp / "reboot.log").read_text(), "rebooted\n")

    def test_a_list_a_firmware_update_put_in_stays(self):
        newer = STOCK_LIST.replace("setup_server", "setup_server_v2")
        self.pj.write_text(newer)
        out = self._run("stock", "purge")
        self.assertIn("no Lithify entries: left as it is", out)
        self.assertIn("STOCK_RESTORED", out)
        self.assertEqual(self.pj.read_text(), newer)  # not the older backup
        self.assertFalse(self.base.exists())

    def test_without_a_backup_nothing_changes(self):
        (self.base / "backup" / "process.json.orig").unlink()
        agent = self._start("lithify-agent")
        out = self._run("stock", "purge")
        self.assertIn("FAIL no stock backup", out)
        self.assertNotIn("STOCK_RESTORED", out)
        self.assertEqual(self.pj.read_text(), self.ours)
        self.assertTrue((self.base / "cache" / "credentials.json").exists())
        self.assertIsNone(agent.poll())
        self.assertFalse((self.tmp / "ip.log").exists())

    def test_a_backup_that_is_not_a_cast_list_is_refused(self):
        (self.base / "backup" / "process.json.orig").write_text("[]\n")
        out = self._run("stock", "purge")
        self.assertIn("is not a Cast service list", out)
        self.assertEqual(self.pj.read_text(), self.ours)
        self.assertTrue(self.base.exists())

    def test_an_install_from_before_the_name_goes_too(self):
        # (cc_ entries and /lsync/cc; the backup only there)
        shutil.rmtree(self.base)
        (self.legacy / "backup").mkdir(parents=True)
        (self.legacy / "backup" / "process.json.orig").write_text(STOCK_LIST)
        self.pj.write_text(self.ours.replace("lithify_", "cc_"))
        out = self._run("stock", "purge")
        self.assertIn("STOCK_RESTORED", out)
        self.assertEqual(self.pj.read_text(), STOCK_LIST)
        self.assertFalse(self.legacy.exists())

    def test_a_speaker_without_lithify_has_nothing_to_remove(self):
        shutil.rmtree(self.base)
        shutil.rmtree(self.ram)
        self.ram.mkdir()
        self.pj.write_text(STOCK_LIST)
        out = self._run("stock", "purge")
        self.assertIn("NOTHING_TO_REMOVE", out)
        self.assertNotIn("STOCK_RESTORED", out)
        self.assertFalse((self.tmp / "ip.log").exists())

    def test_an_uninstall_waiting_for_its_reboot_is_not_nothing(self):
        # Removed before, not restarted since: the next uninstall still asks for the reboot.
        self._run("stock", "purge")
        self.assertIn("STOCK_RESTORED", self._run("stock", "purge"))

    def test_a_purge_cut_off_halfway_is_finished(self):
        self.pj.write_text(STOCK_LIST)
        self.base.rename(self.tmp / "lithify.removed")
        out = self._run("stock", "purge")
        self.assertIn("STOCK_RESTORED", out)
        self.assertFalse((self.tmp / "lithify.removed").exists())

    def test_the_stock_list_alone_keeps_the_files(self):
        out = self._run("stock")
        self.assertIn("STOCK_RESTORED - reboot to finish", out)
        self.assertEqual(self.pj.read_text(), STOCK_LIST)
        self.assertTrue((self.base / "cache" / "credentials.json").exists())
        self.assertFalse((self.ram / "process.json.orig").exists())


if __name__ == "__main__":
    unittest.main()
