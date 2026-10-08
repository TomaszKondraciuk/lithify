"""The installers' files as Windows and the shells read them: encodings, line endings, the firewall
rule they share with lithify/hostos.py, and every message in both languages."""
import re
import unittest
from pathlib import Path

from lithify import hostos

ROOT = Path(__file__).resolve().parent.parent
PS1 = ROOT / "installer" / "install.ps1"
# PowerShell's string literals: '...' ('' inside) and "..." (`" or "" inside)
LITERAL = r"""(?:'(?:[^']|'')*'|"(?:[^"`]|`.|"")*")"""


def ps1_text() -> str:
    return PS1.read_bytes().decode("utf-8-sig")


class EncodingTest(unittest.TestCase):
    def test_what_windows_reads_is_encoded_as_it_needs(self):
        # Windows PowerShell 5.1 reads a script without a BOM in the ANSI code page: its Polish
        # texts would be garbled. With the BOM it cannot go through `irm | iex`: get.ps1 does that.
        self.assertTrue(PS1.read_bytes().startswith(b"\xef\xbb\xbf"))
        for name in ("installer/get.ps1", "Lithify-Windows.cmd"):  # (cmd.exe: the console's code page)
            data = (ROOT / name).read_bytes()
            self.assertFalse(data.startswith(b"\xef\xbb\xbf"), name)
            data.decode("ascii")
        cmd = (ROOT / "Lithify-Windows.cmd").read_bytes()
        self.assertEqual(cmd.count(b"\n"), cmd.count(b"\r\n"))  # (CRLF: what cmd.exe reads reliably)

    def test_the_launchers_and_installers_point_at_each_other(self):
        cmd = (ROOT / "Lithify-Windows.cmd").read_text(encoding="ascii")
        self.assertIn(r'"%~dp0installer\install.ps1"', cmd)
        self.assertIn("/main/installer/install.ps1", cmd)
        self.assertIn("/main/installer/install.ps1", (ROOT / "installer" / "get.ps1").read_text(encoding="ascii"))
        for name in ("Lithify-Linux.sh", "Lithify-macOS.command"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn('"$here/installer/install.sh"', text, name)
            self.assertIn("/main/installer/install.sh", text, name)


class FirewallRuleTest(unittest.TestCase):
    def test_install_ps1_adds_the_rule_hostos_looks_for(self):
        text = ps1_text()
        m = re.search(r"\$FirewallArgs = @\(([^)]*)\)", text)
        self.assertIsNotNone(m)
        expand = {"$FirewallRule": re.search(r"\$FirewallRule = '([^']*)'", text)[1],
                  "$FirewallPorts": re.search(r"\$FirewallPorts = '([^']*)'", text)[1]}
        args = []
        for word in re.findall(r"""'([^']*)'|"([^"]*)\"""", m[1]):
            arg = word[0] or word[1]
            for k, v in expand.items():
                arg = arg.replace(k, v)
            args.append(arg)
        self.assertEqual(args, hostos.firewall_rule_command())


class MessagesTest(unittest.TestCase):
    def test_every_message_of_install_ps1_is_in_both_languages(self):
        text = ps1_text()
        # L takes one array: a second argument on the next line works only after a comma (two
        # separate arguments there end the command, and the file does not parse).
        self.assertIsNone(re.search(rf"(?<![\w-])L\s+{LITERAL}\s+{LITERAL}", text))
        bare = []
        for line in text.splitlines():
            m = re.search(rf"\b(?:Say|Info|Warn|Fail|Later)\s+({LITERAL})", line)
            if m and not re.fullmatch(r'"Python: \$py"', m[1]):
                bare.append(line.strip())
        self.assertEqual(bare, [])
        for en, pl in re.findall(rf"(?<![\w-])L\s+({LITERAL}),\s*({LITERAL})", text):
            self.assertNotEqual(en, pl)  # (a copy would be a text that was never translated)


    def test_every_message_of_install_sh_is_in_both_languages(self):
        text = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
        self.assertIn("L() {", text)
        bare = []
        for line in text.splitlines():
            m = re.match(r'\s*(?:say|info|warn|die|later|ask)\s+"(.*)', line)
            # (a command shown for copying is indented; "Python 3.12: /usr/bin/python3" has no words)
            if m and "$(L " not in line and not m[1].startswith("  ") and not m[1].startswith("Python $(py_version"):
                bare.append(line.strip())
        self.assertEqual(bare, [])


class GoPublicTest(unittest.TestCase):
    """.github/scripts/go-public.py: the repository's name instead of the placeholder, once."""

    def test_the_files_name_the_repository_and_keep_their_bytes(self):
        if b"OWNER/lithify" not in (ROOT / "README.md").read_bytes():
            self.skipTest("this repository names itself already (go-public.py ran)")
        import importlib.util
        import shutil
        import tempfile
        import tomllib

        spec = importlib.util.spec_from_file_location("go_public", ROOT / ".github" / "scripts" / "go-public.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            for pattern in (*mod.FILES, "versions.toml"):
                for f in ROOT.glob(pattern):
                    (tmp / f.relative_to(ROOT)).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(f, tmp / f.relative_to(ROOT))
            changed = mod.go_public(tmp, "alice/lithify")
            self.assertIn("installer/install.ps1", changed)
            self.assertIn("versions.toml", changed)
            for pattern in mod.FILES:
                for f in tmp.glob(pattern):
                    self.assertNotIn(b"OWNER/lithify", f.read_bytes(), f)
            self.assertTrue((tmp / "installer" / "install.ps1").read_bytes().startswith(b"\xef\xbb\xbf"))
            cmd = (tmp / "Lithify-Windows.cmd").read_bytes()
            self.assertEqual(cmd.count(b"\n"), cmd.count(b"\r\n"))
            self.assertIn(b"https://raw.githubusercontent.com/alice/lithify/main/installer/install.ps1", cmd)
            pins = tomllib.loads((tmp / "versions.toml").read_text(encoding="utf-8"))
            self.assertEqual(pins["release"]["url"], "https://github.com/alice/lithify/releases/latest/download")
            self.assertEqual(mod.go_public(tmp, "alice/lithify"), [])  # (once is enough)


if __name__ == "__main__":
    unittest.main()
