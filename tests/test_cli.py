"""`lithify` writing config.toml (a file that is there is added to, never replaced), and how its
commands end on a signal."""
import argparse
import io
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

from lithify import cli, config, platforms, removal

ROOT = Path(__file__).resolve().parent.parent


class FakeDevice:
    """The speaker as `lithify setup` sees it, without a network."""

    def __init__(self, speaker):
        self.s = speaker

    def reachable(self) -> bool:
        return True

    def platform(self):
        return platforms.LS9

    def info(self) -> dict:
        return {"speaker_name": "Kuchnia", "manufacturer": "Lithe Audio", "model": "WiFi Speaker V2"}


class WriteConfigTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name)
        self.target = self.d / "config.toml"
        for name, value in (("Device", FakeDevice), ("USER_CONFIG", self.d / "user" / "config.toml"),
                            ("say", lambda msg: None)):
            patch = mock.patch.object(cli, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_new_file_gets_the_template(self):
        cli.write_config(self.target, "192.168.1.109", interactive=False)
        s = config.load(str(self.target), env={}).speaker(None)
        self.assertEqual((s.id, s.host, s.name), ("kuchnia", "192.168.1.109", "Kuchnia (librespot)"))
        self.assertEqual(self.backups(), [])

    def test_an_existing_file_gets_the_speaker_added_and_keeps_the_rest(self):
        before = '[companion]\nlisten = "0.0.0.0:8095"\n\n[defaults.librespot]\nbitrate = 160\n'
        self.target.write_text(before, encoding="utf-8")
        cli.write_config(self.target, "192.168.1.109", interactive=False)
        text = self.target.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(before.rstrip("\n")))
        cfg = config.load(str(self.target), env={})
        self.assertEqual(cfg.companion["listen"], "0.0.0.0:8095")
        self.assertEqual((cfg.speaker(None).id, cfg.speaker(None).librespot["bitrate"]), ("kuchnia", 160))
        self.assertEqual([b.read_text(encoding="utf-8") for b in self.backups()], [before])
        # the same speaker again: refused, and the file stays as it is
        with self.assertRaisesRegex(config.ConfigError, "already has a speaker"):
            cli.write_config(self.target, "192.168.1.109", interactive=False)
        self.assertEqual(self.target.read_text(encoding="utf-8"), text)

    def test_a_file_that_cannot_take_a_speaker_is_left_alone(self):
        for text in ("[[speakers]\nid = 1\n", 'speakers = "none"\n'):
            with self.subTest(text=text):
                self.target.write_text(text, encoding="utf-8")
                with self.assertRaises(config.ConfigError):
                    cli.write_config(self.target, "192.168.1.109", interactive=False)
                self.assertEqual(self.target.read_text(encoding="utf-8"), text)

    def test_setup_force_starts_anew_and_never_loses_the_original(self):
        original = '[defaults.librespot]\nbitrate = 96\n'
        self.target.write_text(original, encoding="utf-8")
        cli.write_config(self.target, "192.168.1.109", interactive=False, replace=True)
        self.assertEqual(config.load(str(self.target), env={}).speaker(None).librespot["bitrate"], 320)
        cli.write_config(self.target, "192.168.1.109", interactive=False, replace=True)  # `setup --force` again
        kept = [b.read_text(encoding="utf-8") for b in self.backups()]
        self.assertEqual(len(kept), 2)
        self.assertIn(original, kept)  # the user's own file is still there

    def backups(self) -> list[Path]:
        return sorted(self.d.glob("config.toml.*.bak"))

    def test_a_host_that_is_not_one_is_refused_before_anything_is_written(self):
        with self.assertRaises(RuntimeError):
            cli.write_config(self.target, "192.168.1.109\n", interactive=False)
        self.assertFalse(self.target.exists())

    def test_auto_setup_adds_to_the_configuration_that_was_read(self):
        self.target.write_text('[defaults.agent]\nui_pin = "246813"\n', encoding="utf-8")
        cfg = config.load(str(self.target), env={})
        self.assertEqual(cfg.speakers, [])
        got = cli.auto_setup(argparse.Namespace(config=None, host="192.168.1.109"), cfg)
        self.assertEqual(got.speaker(None).agent["ui_pin"], "246813")
        self.assertEqual(got.path, self.target)
        self.assertFalse(cli.USER_CONFIG.exists())  # not written somewhere else


REMOVED = ("the stock Cast service list is back\nstopping the programs of Lithify\ndeleted /lsync/lithify\n"
           "STOCK_RESTORED")


class UninstallDevice:
    """A speaker for `lithify uninstall`: what rollback.sh says, and whether it was restarted."""

    said = REMOVED
    fails = False
    log: ClassVar[list] = []

    def __init__(self, speaker):
        self.s = speaker

    def rollback(self, mode, purge=False):
        if self.fails:
            raise RuntimeError("cannot reach the speaker console")
        self.log.append(("rollback", self.s.id, mode, purge))
        return self.said

    def reboot(self, wait_for_librespot=True):
        self.log.append(("reboot", self.s.id, wait_for_librespot))
        return 60.0


class UninstallTest(unittest.TestCase):
    def setUp(self):
        UninstallDevice.log = []
        UninstallDevice.fails = False
        UninstallDevice.said = REMOVED
        # (main() would also make this test process end on SIGTERM like on Ctrl-C)
        for name, value in (("Device", UninstallDevice), ("say", lambda msg: None), ("stop_like_ctrl_c", lambda: None)):
            patch = mock.patch.object(cli, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.cfg = config.Config(None, [config.Speaker("kuchnia", "192.0.2.7", "Kuchnia"),
                                        config.Speaker("salon", "192.0.2.8", "Salon")], {})
        load = mock.patch.object(config, "load", return_value=self.cfg)
        load.start()
        self.addCleanup(load.stop)
        self.removed = []
        self.plan = removal.Plan([removal.Part("the configuration", remove=lambda: self.removed.append("conf") or "")],
                                 [], [])

    def run_cli(self, *args) -> int:
        with mock.patch.object(removal, "plan", return_value=self.plan), \
                mock.patch.object(sys, "stdin", io.StringIO("")):  # (no terminal: nobody answers)
            return cli.main(["uninstall", *args])

    def test_the_restart_that_finishes_it_is_the_default(self):
        with mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(self.run_cli("--speaker", "kuchnia"), 0)
        self.assertEqual(UninstallDevice.log, [("rollback", "kuchnia", "stock", True), ("reboot", "kuchnia", False)])
        self.assertNotIn("STOCK_RESTORED", out.getvalue())
        self.assertIn("lithify uninstall --computer", out.getvalue())
        self.assertEqual(self.removed, [])  # the computer only when asked for

    def test_no_reboot_leaves_the_restart_for_later(self):
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(self.run_cli("--speaker", "kuchnia", "--no-reboot"), 0)
        self.assertEqual(UninstallDevice.log, [("rollback", "kuchnia", "stock", True)])

    def test_a_speaker_without_lithify_is_not_restarted(self):
        UninstallDevice.said = "NOTHING_TO_REMOVE"
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(self.run_cli("--speaker", "salon"), 0)
        self.assertEqual(UninstallDevice.log, [("rollback", "salon", "stock", True)])

    def test_all_removes_every_speaker_then_this_computer(self):
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(self.run_cli("--all", "--yes"), 0)
        self.assertEqual([e[:2] for e in UninstallDevice.log],
                         [("rollback", "kuchnia"), ("reboot", "kuchnia"), ("rollback", "salon"), ("reboot", "salon")])
        self.assertEqual(self.removed, ["conf"])

    def test_this_computer_stays_when_a_speaker_cannot_be_reached(self):
        UninstallDevice.fails = True
        with mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(self.run_cli("--all", "--yes"), 1)
        self.assertEqual(self.removed, [])
        self.assertIn("lithify uninstall --computer", err.getvalue())

    def test_this_computer_needs_a_yes(self):
        with mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(self.run_cli("--computer"), 1)  # (no terminal, no --yes)
        self.assertEqual(self.removed, [])
        self.assertIn("nothing was removed", out.getvalue())
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(self.run_cli("--computer", "--yes"), 0)
        self.assertEqual(self.removed, ["conf"])
        self.assertEqual(UninstallDevice.log, [])  # the speakers stay as they are

    def test_one_speaker_is_named_only_without_all(self):
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(self.run_cli("--all", "--speaker", "kuchnia"), 2)
        self.assertEqual(UninstallDevice.log, [])

    def test_the_answer_falls_back_to_the_default(self):
        with mock.patch.object(sys, "stdin", io.StringIO("")):
            self.assertTrue(cli.yes("restart?", None, default=True))
            self.assertFalse(cli.yes("delete?", None))
            self.assertFalse(cli.yes("restart?", False, default=True))

    def test_a_terminal_with_nothing_to_read_gets_the_default(self):
        class Nul(io.StringIO):  # Windows' NUL: a terminal as far as isatty() goes, and empty
            def isatty(self):
                return True

        with mock.patch.object(sys, "stdin", Nul("")), mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(cli.ask("speaker IP address", "192.0.2.7"), "192.0.2.7")
            self.assertTrue(cli.yes("restart?", None, default=True))
            self.assertFalse(cli.yes("delete?", None))


@unittest.skipIf(os.name == "nt", "signals: Linux and macOS")
class SignalTest(unittest.TestCase):
    def test_sigterm_ends_a_command_like_ctrl_c_and_lets_it_clean_up(self):
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d) / "cleaned"
            # ("ready" inside the try: the signal may come before the next line runs)
            code = ("import pathlib, sys, time; sys.path.insert(0, sys.argv[1]); from lithify import cli\n"
                    "cli.stop_like_ctrl_c()\n"
                    "try:\n    print('ready', flush=True)\n    time.sleep(30)\n"
                    "except KeyboardInterrupt:\n"
                    "    time.sleep(0.5)  # stopping containers: a second SIGTERM must not cut this short\n"
                    "    pathlib.Path(sys.argv[2]).write_text('cleaned up')\n")
            p = subprocess.Popen([sys.executable, "-c", code, str(ROOT), str(marker)], stdout=subprocess.PIPE,
                                 text=True)
            self.addCleanup(p.stdout.close)
            self.assertEqual(p.stdout.readline().strip(), "ready")
            p.send_signal(signal.SIGTERM)
            time.sleep(0.1)
            p.send_signal(signal.SIGTERM)
            self.assertEqual(p.wait(timeout=20), 0)
            self.assertEqual(marker.read_text(), "cleaned up")

    def test_a_signal_set_to_be_ignored_stays_ignored(self):
        # (`nohup lithify build &` must survive the terminal closing)
        code = ("import signal, sys; sys.path.insert(0, sys.argv[1]); from lithify import cli\n"
                "signal.signal(signal.SIGHUP, signal.SIG_IGN)\ncli.stop_like_ctrl_c()\n"
                "print(signal.getsignal(signal.SIGHUP) == signal.SIG_IGN,\n"
                "      signal.getsignal(signal.SIGTERM) is cli._interrupt)")
        out = subprocess.run([sys.executable, "-c", code, str(ROOT)], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.stdout.split(), ["True", "True"])


if __name__ == "__main__":
    unittest.main()
