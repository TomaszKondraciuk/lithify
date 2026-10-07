import json
import unittest

from lithify import platforms

STOCK = [
    {"name": "cast_shell", "command": ["/system/chrome/cast_shell"]},
    {"name": "spotify_helper", "command": ["/system/bin/x"]},
]


class PlatformsTest(unittest.TestCase):
    def test_render_adds_positional_only_entries(self):
        text = platforms.render_process_list(STOCK, "/lsync/lithify")
        entries = json.loads(text)
        names = [e["name"] for e in entries]
        self.assertEqual(names, ["cast_shell", "spotify_helper", "lithify_librespot", "lithify_agent"])
        for e in entries[2:]:
            # The Cast process manager reorders --switch arguments; ours must be positional.
            self.assertFalse(any(a.startswith("-") for a in e["command"]), e)
        self.assertEqual(entries[2]["command"][1:3], ["exec-file", "/lsync/lithify/librespot.args"])

    def test_strip_removes_ours_and_the_predecessors_entries(self):
        live = "garbage before " + json.dumps([
            *STOCK, {"name": "lithify_agent", "command": []}, {"name": "cc_librespot", "command": []}]) + " after"
        self.assertEqual(platforms.strip_services(live), STOCK)

    def test_strip_refuses_unknown_shapes(self):
        with self.assertRaises(ValueError):
            platforms.strip_services(json.dumps([{"name": "something_else"}]))
        with self.assertRaises(ValueError):
            platforms.strip_services(json.dumps([{"no_name": 1}]))

    def test_detect(self):
        self.assertIs(platforms.detect("ro.build.product=chickentikka\n", True), platforms.LS9)
        self.assertIsNone(platforms.detect("ro.build.product=other\n", True))
        self.assertFalse(platforms.LS10.supported)


if __name__ == "__main__":
    unittest.main()
