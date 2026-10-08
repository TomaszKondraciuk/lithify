"""Finding speakers: whatever another device on the network answers, the scan goes on."""
import json
import unittest
from unittest import mock

from lithify import discovery


class Reply:
    def __init__(self, body: bytes):
        self.body, self.asked = body, None

    def read(self, n: int = -1) -> bytes:
        self.asked = n
        return self.body if n < 0 else self.body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ProbeTest(unittest.TestCase):
    def probe(self, body: bytes):
        reply = Reply(body)
        with mock.patch.object(discovery.socket, "create_connection"), \
                mock.patch.object(discovery.urllib.request, "urlopen", return_value=reply), \
                mock.patch.object(discovery, "_greets", return_value=False):
            return discovery._probe("192.0.2.7", 0.1), reply

    def test_a_speaker_is_read_up_to_a_limit(self):
        info = {"brandDisplayName": "Lithe Audio", "modelDisplayName": "WiFi Speaker V2", "remoteName": "Kuchnia",
                "libraryVersion": "3.194.71"}
        found, reply = self.probe(json.dumps(info).encode())
        self.assertEqual((found["host"], found["name"]), ("192.0.2.7", "Kuchnia"))
        self.assertEqual(reply.asked, discovery.MAX_REPLY)
        self.assertIsNone(self.probe(b'{"x": "' + b"y" * discovery.MAX_REPLY + b'"}')[0])  # (cut off: not JSON)

    def test_a_reply_that_is_not_an_object_is_not_a_speaker(self):
        for body in (b"[1, 2]", b'"lithe"', b"null", b"42"):
            with self.subTest(body=body):
                self.assertIsNone(self.probe(body)[0])

    def test_one_odd_device_does_not_end_the_scan(self):
        def probe(host: str, timeout: float):
            if host == "192.0.2.1":
                raise TypeError("an answer nobody expected")
            return {"host": host, "name": "Kuchnia", "model": "V2", "spotify_esdk": "3.194"}

        with mock.patch.object(discovery, "_probe", side_effect=probe):
            self.assertEqual([f["host"] for f in discovery.discover("192.0.2.0/30")], ["192.0.2.2"])

    def test_a_scan_that_finds_nothing_looks_once_more_and_slower(self):
        timeouts = []

        def probe(host: str, timeout: float):
            timeouts.append(timeout)
            late = host == "192.0.2.2" and timeout > 1.0  # (it answers the second scan only)
            return {"host": host, "name": "Kuchnia", "model": "V2", "spotify_esdk": ""} if late else None

        with mock.patch.object(discovery, "_probe", side_effect=probe):
            self.assertEqual([f["host"] for f in discovery.discover("192.0.2.0/30", timeout=1.0)], ["192.0.2.2"])
        self.assertEqual(sorted(set(timeouts)), [1.0, 2.0])
        timeouts.clear()
        with mock.patch.object(discovery, "_probe", side_effect=lambda h, t: timeouts.append(t) or {"host": h}):
            discovery.discover("192.0.2.0/30", timeout=1.0)
        self.assertEqual(set(timeouts), {1.0})  # (found at once: no second scan)


class WithoutOfficialSpotifyTest(unittest.TestCase):
    """A Cast device whose official Spotify does not answer (Lithify hides it, or it crashed)."""

    def probe(self, json_by_url: dict, greets: bool, cast: type[OSError] | None = None):
        def connect(addr, timeout=None):
            if addr[1] != discovery.CAST_PORT:
                raise AssertionError(f"only Cast's port is tried first, not {addr[1]}")
            if cast:
                raise cast
            return mock.MagicMock()

        def fetch(url, timeout):
            return next((v for k, v in json_by_url.items() if k in url), None)

        with mock.patch.object(discovery.socket, "create_connection", side_effect=connect), \
                mock.patch.object(discovery, "_json", side_effect=fetch) as js, \
                mock.patch.object(discovery, "_greets", return_value=greets) as gr:
            return discovery._probe("192.0.2.7", 0.1), js, gr

    def test_lithifys_page_tells_what_it_is(self):
        status = {"speaker": {"name": "Kuchnia", "model": "Lithe Audio WiFiCeilingSpeakerV2"}}
        found, _, gr = self.probe({":8090/api/status": status}, greets=False)
        self.assertEqual((found["name"], found["found_by"], found["spotify_esdk"]), ("Kuchnia", "lithify", ""))
        gr.assert_not_called()

    def test_a_libre_cast_speaker_is_a_candidate(self):
        found, _, _ = self.probe({":8008/setup/eureka_info": {"name": "Kuchnia"}}, greets=True)
        self.assertEqual((found["name"], found["found_by"]), ("Kuchnia", "libre"))

    def test_other_devices_are_not_speakers(self):
        self.assertIsNone(self.probe({":8090/api/status": {"speaker": {"model": "Other"}}}, greets=False)[0])
        self.assertIsNone(self.probe({":8008/setup/eureka_info": {"name": "TV"}}, greets=False)[0])
        other = {"brandDisplayName": "Other", "modelDisplayName": "Box", "remoteName": "Salon"}
        found, _, gr = self.probe({":9095/zc": other}, greets=True)
        self.assertIsNone(found)  # (its official Spotify names another brand: nothing more is asked)
        gr.assert_not_called()

    def test_only_a_cast_device_is_asked_what_it_is(self):
        # A refused port as much as a silent one: Windows tries a refused connection for ~3 s, so
        # within the scan's time a speaker would look absent if its other ports were the sign.
        for error in (TimeoutError, ConnectionRefusedError, OSError):
            with self.subTest(error=error.__name__):
                found, js, gr = self.probe({}, greets=True, cast=error)
                self.assertIsNone(found)
                js.assert_not_called()
                gr.assert_not_called()


if __name__ == "__main__":
    unittest.main()
