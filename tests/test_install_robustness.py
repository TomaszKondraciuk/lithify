"""What makes the first install work on any computer: a published bundle before a build, the build's
tests on this computer's own CPU, and a clear answer when the speaker cannot reach this computer."""
import argparse
import base64
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lithify import bundle, cli, device, hostos, platforms

RELEASE = {"release": {"url": "https://example.org/releases/latest/download"}}


class BundleSourceTest(unittest.TestCase):
    def test_a_published_bundle_comes_first(self):
        with mock.patch.object(bundle, "load_pins", return_value=RELEASE), \
                mock.patch.object(bundle, "fetch") as fetch, mock.patch.object(bundle, "build") as build:
            cli.get_bundle()
        fetch.assert_called_once_with(RELEASE["release"]["url"])
        build.assert_not_called()

    def test_a_release_that_cannot_be_downloaded_is_built_here(self):
        with mock.patch.object(bundle, "load_pins", return_value=RELEASE), \
                mock.patch.object(bundle, "fetch", side_effect=OSError("no route to host")), \
                mock.patch.object(bundle, "build") as build, mock.patch.object(cli, "say"):
            cli.get_bundle()
        build.assert_called_once_with()

    def test_without_a_release_or_when_asked_to_it_is_built(self):
        with mock.patch.object(bundle, "load_pins", return_value={"release": {"url": ""}}), \
                mock.patch.object(bundle, "fetch") as fetch, mock.patch.object(bundle, "build") as build:
            cli.get_bundle()
        fetch.assert_not_called()
        build.assert_called_once_with()
        with mock.patch.object(bundle, "load_pins", return_value=RELEASE), \
                mock.patch.object(bundle, "fetch") as fetch, mock.patch.object(bundle, "build") as build:
            cli.get_bundle(force_build=True)
        fetch.assert_not_called()
        build.assert_called_once_with()


class HostTripleTest(unittest.TestCase):
    def test_the_agent_tests_run_on_the_computers_own_cpu(self):
        arm = "rustc 1.99.0 (0123abcd 2026-09-01)\nbinary: rustc\nhost: aarch64-unknown-linux-gnu\nrelease: 1.99.0\n"
        self.assertEqual(bundle.host_triple(arm), "aarch64-unknown-linux-gnu")
        self.assertEqual(bundle.host_triple("rustc 1.99.0 (0123abcd 2026-09-01)\n"), "x86_64-unknown-linux-gnu")


class ReachTest(unittest.TestCase):
    def setUp(self):
        self.d = device.Device.__new__(device.Device)  # (its console is replaced below)

    def test_the_speaker_says_whether_it_can_download_from_here(self):
        with mock.patch.object(device.Device, "exec", return_value="HTTP200") as ex:
            self.assertEqual(self.d.can_download("http://10.1.2.3:18096/VERSIONS"), (True, "HTTP200"))
        self.assertIn("http://10.1.2.3:18096/VERSIONS", ex.call_args.args[0])
        said = "curl: (28) Connection timed out after 8001 milliseconds\nHTTP000"
        with mock.patch.object(device.Device, "exec", return_value=said):
            reachable, why = self.d.can_download("http://10.1.2.3:18096/VERSIONS")
        self.assertFalse(reachable)
        self.assertIn("timed out", why)

    def test_the_advice_fits_the_system_and_the_network(self):
        with mock.patch.object(hostos, "WINDOWS", True), mock.patch.object(hostos, "MACOS", False):
            self.assertIn("Private", hostos.firewall_advice("10.1.2.3"))
        with mock.patch.object(hostos, "WINDOWS", False), mock.patch.object(hostos, "MACOS", True):
            self.assertIn("Allow", hostos.firewall_advice())
        with mock.patch.object(hostos, "WINDOWS", False), mock.patch.object(hostos, "MACOS", False):
            linux = hostos.firewall_advice("10.1.2.3")
            fallback = hostos.firewall_advice("not-an-address")
        self.assertIn("10.1.2.0/24", linux)
        self.assertIn("18096:18099", linux)
        self.assertIn("192.168.0.0/16", fallback)
        self.assertIn("VPN", linux)


class FirewallTest(unittest.TestCase):
    """Windows: the rule that lets the speakers in, and no block of Lithify's Python (a "Cancel" when
    Windows asked about Python makes one, and a block wins over the rule)."""

    def test_the_pythons_lithify_listens_with(self):
        with tempfile.TemporaryDirectory() as d:
            venv, base = Path(d, "venv", "Scripts"), Path(d, "Python312")
            for f in (venv / "python.exe", venv / "pythonw.exe", base / "python.exe", base / "pythonw.exe"):
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"")
            with mock.patch.object(hostos.sys, "executable", str(venv / "python.exe")), \
                    mock.patch.object(hostos.sys, "_base_executable", str(base / "python.exe"), create=True):
                self.assertEqual(hostos.lithify_pythons(), [str(venv / "python.exe"), str(venv / "pythonw.exe"),
                                                            str(base / "python.exe"), str(base / "pythonw.exe")])

    def test_the_blocks_are_read_from_windows(self):
        out = "TCP Query User{A}C:\\py\\python.exe\n\nUDP Query User{B}C:\\py\\python.exe\n"
        said = subprocess.CompletedProcess([], 0, out, "")
        with mock.patch.object(hostos, "WINDOWS", True), \
                mock.patch.object(hostos, "lithify_pythons", return_value=["C:\\py\\python.exe"]), \
                mock.patch.object(hostos.subprocess, "run", return_value=said) as run:
            self.assertEqual(hostos.python_blocks(), ["TCP Query User{A}C:\\py\\python.exe",
                                                      "UDP Query User{B}C:\\py\\python.exe"])
        self.assertIn("'C:\\py\\python.exe'", run.call_args.args[0][-1])

    def test_one_consent_adds_the_rule_and_lifts_the_blocks(self):
        runs = []

        def run(cmd, **_kw):
            runs.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch.object(hostos, "WINDOWS", True), \
                mock.patch.object(hostos, "firewall_rule_present", side_effect=[False, True]), \
                mock.patch.object(hostos, "python_blocks", side_effect=[["TCP Query User{1}C:\\py\\python.exe"], []]), \
                mock.patch.object(hostos.subprocess, "run", side_effect=run):
            self.assertTrue(hostos.ensure_firewall_rule())
        self.assertEqual(len(runs), 1)
        script = base64.b64decode(re.search(r"'-EncodedCommand','([^']+)'", runs[0][-1])[1]).decode("utf-16-le")
        self.assertIn("& netsh 'advfirewall' 'firewall' 'add' 'rule' 'name=Lithify'", script)
        self.assertIn("'localport=8095,18096-18099'", script)
        self.assertIn("Remove-NetFirewallRule -Name 'TCP Query User{1}C:\\py\\python.exe'", script)

    def test_nothing_is_asked_when_the_firewall_is_ready(self):
        with mock.patch.object(hostos, "WINDOWS", True), \
                mock.patch.object(hostos, "firewall_rule_present", return_value=True), \
                mock.patch.object(hostos, "python_blocks", return_value=[]), \
                mock.patch.object(hostos.subprocess, "run") as run:
            self.assertTrue(hostos.ensure_firewall_rule())
            self.assertTrue(hostos.firewall_ready())
        run.assert_not_called()
        with mock.patch.object(hostos, "WINDOWS", False), mock.patch.object(hostos.subprocess, "run") as run:
            self.assertTrue(hostos.ensure_firewall_rule())
            self.assertEqual(hostos.python_blocks(), [])
        run.assert_not_called()

    def test_the_install_asks_before_the_long_build(self):
        order = []

        class StopHereError(Exception):
            pass

        def ask() -> bool:
            order.append("ask")
            return True

        def build(**_kw) -> None:
            order.append("build")
            raise StopHereError  # (the rest of the install is not this test's)

        speaker = mock.MagicMock(host="192.0.2.7", id="kuchnia")
        cfg = mock.MagicMock(speakers=[speaker])
        cfg.speaker.return_value = speaker
        args = argparse.Namespace(speaker="kuchnia", host=None, build=False, settings=False, reboot=True)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(bundle, "CACHE", Path(d)), \
                mock.patch.object(bundle, "recover_bundle"), \
                mock.patch.object(cli, "Device") as dev, \
                mock.patch.object(hostos, "firewall_ready", return_value=False), \
                mock.patch.object(hostos, "ensure_firewall_rule", side_effect=ask), \
                mock.patch.object(cli, "get_bundle", side_effect=build), \
                mock.patch.object(cli, "say", side_effect=order.append):
            dev.return_value.platform.return_value = platforms.LS9
            with self.assertRaises(StopHereError):
                cli.cmd_install(args, cfg)
        self.assertTrue(order[0].startswith(hostos.FIREWALL_ASK))
        self.assertEqual(order[1:], ["ask", "build"])


if __name__ == "__main__":
    unittest.main()
