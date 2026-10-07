"""The device scripts: they must fit the console (no single quotes, short lines), and install.sh
and rollback.sh are run for real in a sandbox (a fake install directory, failures injected)."""
import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from lithify import console

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


if __name__ == "__main__":
    unittest.main()
