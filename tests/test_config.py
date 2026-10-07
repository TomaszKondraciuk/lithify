import tempfile
import unittest
from pathlib import Path

from lithify import config

TOML = '''
[defaults.librespot]
bitrate = 160

[defaults.agent]
fastfail_hosts = ["audio-fa.scdn.co"]

[[speakers]]
id = "lazienka"
host = "192.168.1.40"
name = "Łazienka (librespot)"

[speakers.librespot]
initial_volume = 40
extra_args = ["--ap-port", "443"]
'''


def load(text: str, env: dict | None = None) -> config.Config:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.toml"
        p.write_text(text, encoding="utf-8")
        return config.load(str(p), env=env or {})


class ConfigTest(unittest.TestCase):
    def test_defaults_and_overrides_merge(self):
        s = load(TOML).speaker(None)
        self.assertEqual(s.librespot["bitrate"], 160)            # [defaults.librespot]
        self.assertEqual(s.librespot["initial_volume"], 40)      # [speakers.librespot]
        self.assertEqual(s.librespot["mixer"], "alsa")           # built-in default
        self.assertEqual(s.agent["fastfail_hosts"], ["audio-fa.scdn.co"])

    def test_environment_overrides_a_single_speaker(self):
        env = {"LITHIFY_NAME": "Kitchen", "LITHIFY_LIBRESPOT_BITRATE": "320", "LITHIFY_AGENT_UI": "off",
               "LITHIFY_AGENT_FASTFAIL_HOSTS": "a.example, b.example"}
        s = load(TOML, env).speaker("lazienka")
        self.assertEqual((s.name, s.librespot["bitrate"], s.agent["ui"]), ("Kitchen", 320, False))
        self.assertEqual(s.agent["fastfail_hosts"], ["a.example", "b.example"])

    def test_environment_alone_is_enough(self):
        cfg = config.load(None, env={"LITHIFY_HOST": "10.0.0.5", "LITHIFY_NAME": "Office",
                                     "LITHIFY_CONFIG": "/nonexistent"})
        self.assertEqual((cfg.speaker(None).host, cfg.speaker(None).name), ("10.0.0.5", "Office"))

    def test_invalid_values_are_rejected(self):
        for bad in ('bitrate = 128', 'mixer = "pulse"', 'initial_volume = 120', 'nonsense = 1'):
            with self.subTest(bad=bad), self.assertRaises(config.ConfigError):
                load(TOML.replace("initial_volume = 40", bad))
        with self.assertRaises(config.ConfigError):
            load(TOML.replace('id = "lazienka"', 'id = "Bad Id"'))
        with self.assertRaises(config.ConfigError):
            load(TOML + '\n[[speakers]]\nid = "lazienka"\nhost = "192.168.1.109"\nname = "x"\n')

    def test_speaker_lookup(self):
        cfg = load(TOML + '\n[[speakers]]\nid = "kuchnia"\nhost = "192.168.1.109"\nname = "Kuchnia"\n')
        self.assertEqual(cfg.speaker("192.168.1.109").id, "kuchnia")
        with self.assertRaises(config.ConfigError):
            cfg.speaker(None)  # ambiguous
        with self.assertRaises(config.ConfigError):
            cfg.speaker("garage")

    def test_settings_files_for_the_speaker(self):
        s = load(TOML).speaker(None)
        values = dict(line.split("=", 1) for line in config.settings_conf(s).splitlines()
                      if "=" in line and line[0] != "#")
        self.assertEqual(set(values), set(config.OPTIONS))          # every option, nothing else
        self.assertEqual(values["name"], "Łazienka (librespot)")
        self.assertEqual((values["bitrate"], values["initial_volume"]), ("160", "40"))
        self.assertEqual(values["normalisation"], "false")
        self.assertEqual(values["fastfail_hosts"], "audio-fa.scdn.co")
        self.assertEqual(values["extra_args"], "--ap-port 443")
        patch = config.settings_patch(s, ["name", "bitrate"])
        self.assertEqual([line for line in patch.splitlines() if not line.startswith("#")],
                         ["name=Łazienka (librespot)", "bitrate=160"])
        with self.assertRaises(config.ConfigError):
            config.settings_patch(s, ["nonsense"])
        install = config.install_conf(s, "http://192.168.1.110:8095", "ls9")
        self.assertIn("speaker_id=lazienka", install)
        self.assertIn("companion_url=http://192.168.1.110:8095", install)

    def test_environment_marks_settings_to_send(self):
        s = load(TOML, {"LITHIFY_NAME": "Kitchen", "LITHIFY_LIBRESPOT_BITRATE": "320"}).speaker(None)
        self.assertEqual(s.env_keys, {"name", "bitrate"})
        self.assertEqual(load(TOML).speaker(None).env_keys, set())

    def test_line_breaks_cannot_reach_the_device_files(self):
        s = load(TOML).speaker(None)
        s.name = "two\nlines"
        with self.assertRaises(config.ConfigError):
            config.settings_conf(s)

    def test_checks_match_the_agent(self):
        # The same cases as agent/src/settings.rs (values_are_normalized_and_checked).
        n = lambda k, v: config.normalize(config.OPTIONS[k], v)  # noqa: E731
        self.assertEqual(n("normalisation", "Yes"), "true")
        self.assertEqual(n("normalisation", False), "false")
        self.assertEqual(n("bitrate", " 160 "), "160")
        self.assertEqual(n("bitrate", 320), "320")
        self.assertEqual(n("initial_volume", ""), "current")
        self.assertEqual(n("initial_volume", 40), "40")
        self.assertEqual(n("autoplay", ""), "")
        self.assertEqual(n("fastfail_hosts", "a.example, b.example\nc.example"), "a.example b.example c.example")
        self.assertEqual(n("fastfail_hosts", ["a.example", "b.example"]), "a.example b.example")
        self.assertEqual(n("ui_pin", ""), "")
        self.assertEqual(n("name", "Łazienka (librespot)"), "Łazienka (librespot)")
        for key, bad in [("bitrate", "128"), ("initial_volume", "101"), ("fastfail_hosts", "bad host;reboot"),
                         ("fastfail_routes", "10.0.0.0/33"), ("ui_pin", "12"), ("ui_pin", "12ab"), ("name", ""),
                         ("name", "two\nlines"), ("device", "plughw:0,0; reboot"), ("ui_port", "80")]:
            with self.subTest(key=key, value=bad), self.assertRaises(ValueError):
                n(key, bad)

    def test_schema_defaults_are_valid(self):
        for o in config.SCHEMA:
            with self.subTest(key=o.key):
                if o.key != "name":
                    self.assertEqual(config.normalize(o, o.default), o.default)
        self.assertEqual(config.LIBRESPOT_DEFAULTS["bitrate"], 320)
        self.assertEqual(config.AGENT_DEFAULTS["fastfail_hosts"], [])
        self.assertIs(config.AGENT_DEFAULTS["respawn_official_spotify"], True)

    def test_example_template_loads(self):
        import json
        for name in ("Living room", 'Kid\'s "den" \\ 2'):
            cfg = load(config.EXAMPLE.format(id="speaker", host="192.168.1.20", name=json.dumps(name)))
            self.assertEqual(cfg.speaker(None).name, name)

    def test_a_broken_file_is_a_configuration_error(self):
        with self.assertRaises(config.ConfigError):
            load("[[speakers]\nid = 1\n")

    def test_rules_file_parses_completely(self):
        self.assertEqual(config.RULES.bad, [])
        for o in config.SCHEMA:
            if o.kind in ("text", "list"):
                self.assertTrue(o.rule in config.RULES.text or o.rule in config.RULES.network, o.key)

    def test_shared_cases_hold(self):
        """The cases agent/src/settings.rs runs too (shared_cases_hold): same answers on both sides."""
        def unescape(v: str) -> str:
            if v == '""':
                return ""
            out, it = [], iter(v)
            for c in it:
                if c == "\\":
                    x = next(it, "")
                    out.append({"s": " ", "n": "\n"}.get(x, x))
                else:
                    out.append(c)
            return "".join(out)

        cases = (config.SCHEMA_FILE.with_name("settings-cases.tsv")).read_text(encoding="utf-8").splitlines()
        n = 0
        for line in (case for case in cases if case and not case.startswith("#")):
            f = line.split("\t")
            with self.subTest(line=line):
                if f[0] == "value" and len(f) == 4:
                    _, key, given, want = f
                    if want == "<refused>":
                        with self.assertRaises(ValueError):
                            config.normalize(config.OPTIONS[key], unescape(given))
                    else:
                        self.assertEqual(config.normalize(config.OPTIONS[key], unescape(given)), unescape(want))
                elif f[0] == "conflict" and len(f) == 3:
                    values = dict(kv.split("=", 1) for kv in unescape(f[1]).split(" "))
                    got = sorted(k for k, _ in config.conflicts(values))
                    self.assertEqual(got, sorted(k for k in unescape(f[2]).split(" ") if k))
                else:
                    self.fail(f"bad line {line!r}")
            n += 1
        self.assertGreater(n, 50)

    def test_conflicting_ports_stop_a_config(self):
        with self.assertRaisesRegex(config.ConfigError, "zeroconf_port: the web page uses this port"):
            load(TOML.replace('fastfail_hosts = ["audio-fa.scdn.co"]', 'ui_port = 4070', 1))

    def test_ids_and_hosts_must_match_whole(self):
        # (`$` alone would let a trailing line break through, into file names and URLs)
        with self.assertRaises(config.ConfigError):
            load(TOML.replace('id = "lazienka"', 'id = "lazienka\\n"'))
        with self.assertRaises(config.ConfigError):
            load(TOML.replace('host = "192.168.1.40"', 'host = "192.168.1.40\\n"'))
        with self.assertRaises(config.ConfigError):
            load(TOML.replace('host = "192.168.1.40"', 'host = 5'))
        self.assertIsNone(config.ID_RE.fullmatch("lazienka\n"))

    def test_companion_listen_and_url_are_checked(self):
        for good in ("auto", "0.0.0.0:8095", "[::]:8095", "192.168.1.10:8095", "pc.lan:8095"):
            with self.subTest(listen=good):
                load(TOML + f'\n[companion]\nlisten = "{good}"\n')
        for bad in ("8095", "192.168.1.10", "192.168.1.10:http", "192.168.1.10:99999", "bad host:80", ":8095"):
            with self.subTest(listen=bad), self.assertRaisesRegex(config.ConfigError, "companion.listen"):
                load(TOML + f'\n[companion]\nlisten = "{bad}"\n')
        self.assertEqual(config.parse_listen("[::]:8095"), ("::", 8095))
        self.assertIsNone(config.parse_listen("auto"))
        load(TOML + '\n[companion]\nurl = "http://192.168.1.10:8095/"\n')
        for bad in ("https://pc.lan:8095", "pc.lan:8095", "http://pc lan:8095"):
            with self.subTest(url=bad), self.assertRaisesRegex(config.ConfigError, "companion.url"):
                load(TOML + f'\n[companion]\nurl = "{bad}"\n')


class DiscoveryTextTest(unittest.TestCase):
    def test_device_names_cannot_carry_terminal_escapes(self):
        from lithify import discovery
        self.assertEqual(discovery.printable("Łazienka\x1b[2K\x1b]0;pwned\x07"), "Łazienka[2K]0;pwned")
        self.assertEqual(discovery.printable(None), "")


if __name__ == "__main__":
    unittest.main()
