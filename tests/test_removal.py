"""`lithify uninstall --computer`: what it finds to remove on this computer, and what it never
touches (the home folder, a copy of Lithify someone works in, other programs' Docker objects)."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lithify import bundle, hostos, removal, service


def done(out: str = "", code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, out, "")


class PlacesTest(unittest.TestCase):
    def test_a_path_entry_goes_as_windows_compares_it(self):
        entry = "C:\\Users\\Ala\\AppData\\Local\\lithify\\bin"
        value = "C:\\Windows;c:\\users\\ala\\appdata\\local\\lithify\\BIN\\;D:\\x"
        self.assertEqual(removal.without_entry(value, entry), "C:\\Windows;D:\\x")
        self.assertEqual(removal.without_entry(f"{entry};", entry), "")  # (an empty entry stays as it was)
        self.assertIsNone(removal.without_entry("C:\\Windows;C:\\Users\\Ala\\AppData\\Local\\other\\bin", entry))

    def test_only_folders_named_for_lithify_are_deleted(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            env = {"HOME": str(home), "USERPROFILE": str(home)}
            for ok in (home / ".cache" / "lithify", home / ".local" / "share" / "lithify-tools",
                       home / "Library" / "Logs" / "lithify", home / "lithify" / "logs"):
                self.assertTrue(removal.deletable(ok, env), ok)
            for no in (home, home.parent, Path(home.anchor), home / "builds", home / ".cache"):
                self.assertFalse(removal.deletable(no, env), no)

    def test_the_command_is_the_installers_only_when_it_runs_this_copy(self):
        with tempfile.TemporaryDirectory() as d:
            root, command = Path(d) / "lithify", Path(d) / "bin" / "lithify"
            command.parent.mkdir()
            command.write_text(f"#!/bin/sh\nexec '/usr/bin/python3' '{root / 'bin' / 'lithify'}' \"$@\"\n")
            self.assertTrue(removal.made_by_installer(command, root))
            self.assertFalse(removal.made_by_installer(command, Path(d) / "another"))
            command.write_text("#!/bin/sh\necho my own lithify\n")
            self.assertFalse(removal.made_by_installer(command, root))
            self.assertFalse(removal.made_by_installer(Path(d) / "missing", root))


class PlanTest(unittest.TestCase):
    """The installers' layout, in a home folder of its own; nothing of this computer's is asked."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.env = {"HOME": str(self.home), "USERPROFILE": str(self.home)}
        self.root = self.home / ".local" / "share" / "lithify"
        self.tools = self.home / ".local" / "share" / "lithify-tools"
        self.command = self.home / ".local" / "bin" / "lithify"
        for d in (self.root / "bin", self.tools / "bin", self.command.parent, self.home / ".config" / "lithify",
                  self.home / ".cache" / "lithify" / "logs"):
            d.mkdir(parents=True)
        (self.root / "bin" / "lithify").write_text("# Lithify\n")
        self.command.write_text(f"#!/bin/sh\nexec '{self.tools / 'bin' / 'python3'}' '{self.root / 'bin' / 'lithify'}' "
                                "\"$@\"\n")
        (self.home / ".config" / "lithify" / "config.toml").write_text("[[speakers]]\n")
        for target, attr, value in ((service, "installed", lambda: ""), (removal.shutil, "which", lambda _name: None),
                                    (hostos, "firewall_rules_exist", lambda: False)):
            patch = mock.patch.object(target, attr, value)
            patch.start()
            self.addCleanup(patch.stop)

    def plan(self, root=None) -> removal.Plan:
        return removal.plan(self.env, windows=False, root=root or self.root)

    def test_an_installed_copy_goes_whole(self):
        p = self.plan()
        removed = [path for part in p.parts for path in part.paths]
        self.assertEqual(removed, [self.home / ".config" / "lithify", self.home / ".cache" / "lithify", self.command])
        self.assertEqual(p.later, [self.root, self.tools])  # (in use until the command ends)
        self.assertEqual(p.notes, [])
        self.assertEqual(removal.remove(p.parts), [])
        self.assertFalse(any(path.exists() for path in removed))
        self.assertTrue(self.root.exists() and self.tools.exists())

    def test_a_copy_of_ones_own_stays(self):
        mine = self.home / "src" / "lithify"
        (mine / "bin").mkdir(parents=True)
        p = self.plan(root=mine)
        self.assertNotIn(self.command, [path for part in p.parts for path in part.paths])  # it runs another copy
        self.assertNotIn(mine, p.later)
        self.assertTrue(any(str(mine) in n and "stays" in n for n in p.notes))

    def test_a_cache_somewhere_else_is_named_not_emptied(self):
        elsewhere = self.home / "builds"
        elsewhere.mkdir()
        self.env["LITHIFY_CACHE"] = str(elsewhere)
        p = self.plan()
        self.assertNotIn(elsewhere, [path for part in p.parts for path in part.paths])
        self.assertTrue(any(str(elsewhere) in n for n in p.notes))

    def test_the_helper_goes_first(self):
        gone = []
        with mock.patch.object(service, "installed", return_value="the systemd user service lithify-companion"), \
                mock.patch.object(service, "uninstall", side_effect=lambda: gone.append("helper")):
            p = self.plan()
            self.assertIn("lithify-companion", p.parts[0].what)
            self.assertEqual(removal.remove(p.parts[:1]), [])
        self.assertEqual(gone, ["helper"])

    def test_windows_leaves_its_lithify_folder_for_the_end(self):
        local, roaming = self.home / "Local", self.home / "Roaming"
        for d in (local / "lithify" / "app" / "bin", local / "lithify" / "cache", roaming / "lithify"):
            d.mkdir(parents=True)
        temp = local / "Temp"
        temp.mkdir()
        (temp / "lithify-install.ps1").write_text("# the installer\n")
        env = {**self.env, "LOCALAPPDATA": str(local), "APPDATA": str(roaming), "TEMP": str(temp)}
        with mock.patch.object(removal, "_registry_parts", return_value=[]):
            p = removal.plan(env, windows=True, root=local / "lithify" / "app")
        self.assertEqual([path for part in p.parts for path in part.paths],
                         [temp / "lithify-install.ps1", roaming / "lithify", local / "lithify" / "cache"])
        self.assertEqual(p.later, [local / "lithify"])
        self.assertEqual(p.notes, [])


class DockerTest(unittest.TestCase):
    def test_only_lithifys_own_objects_are_named(self):
        said = {
            "volume": done("postgres_data\nlithify-cargo\nlithify-target-764afa5dd450\nlithify-target-agent\n"
                           "my-lithify-notes\n"),
            "images": done("lithify-builder:alsa-1.2.16.1-rust-1.99.0\nlithify-builder:<none>\n"),
            "ps": done("4f2a\n"),
        }
        with mock.patch.object(removal, "_docker", side_effect=lambda args, timeout=120: said[args[0]]):
            found = removal.docker_objects()
        ours = [bundle.CARGO_VOLUME, "lithify-target-764afa5dd450", "lithify-target-agent"]
        builder = "lithify-builder:alsa-1.2.16.1-rust-1.99.0"
        self.assertEqual(found, {"containers": ["4f2a"], "volumes": ours, "images": [builder]})
        calls = []
        with mock.patch.object(removal, "_docker", side_effect=lambda args, timeout=120: calls.append(args) or done()):
            self.assertEqual(removal.remove([removal._docker_part(found)]), [])
        self.assertEqual(calls, [["rm", "-f", "4f2a"], ["volume", "rm", *ours], ["image", "rm", builder]])

    def test_the_builders_base_image_is_named_never_removed(self):
        nothing = {"containers": [], "volumes": [], "images": []}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(removal.shutil, "which", return_value="/usr/bin/docker"), \
                mock.patch.object(removal, "docker_objects", return_value=nothing), \
                mock.patch.object(removal, "_docker", return_value=done("sha256:714d\n")), \
                mock.patch.object(service, "installed", return_value=""):
            p = removal.plan({"HOME": d, "USERPROFILE": d}, windows=False, root=removal.ROOT)
        self.assertEqual(p.parts, [])
        self.assertTrue(any("rust-musl-cross" in n and "docker image rm" in n for n in p.notes), p.notes)

    def test_docker_that_does_not_answer_is_said(self):
        with mock.patch.object(removal, "_docker", return_value=done(code=1)):
            self.assertIsNone(removal.docker_objects())
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(removal.shutil, "which", return_value="/usr/bin/docker"), \
                mock.patch.object(removal, "docker_objects", return_value=None), \
                mock.patch.object(service, "installed", return_value=""):
            p = removal.plan({"HOME": d, "USERPROFILE": d}, windows=False, root=Path(d) / "lithify")
        self.assertTrue(any("Docker does not answer" in n for n in p.notes))

    @unittest.skipIf(os.name == "nt", "the Linux and macOS layout")
    def test_a_computer_without_lithify_has_nothing_to_remove(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(removal.shutil, "which", return_value=None), \
                mock.patch.object(service, "installed", return_value=""):
            p = removal.plan({"HOME": d}, windows=False, root=Path(d) / ".local" / "share" / "lithify")
        self.assertEqual((p.parts, p.later), ([], []))


if __name__ == "__main__":
    unittest.main()
