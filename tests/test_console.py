import re
import socket
import subprocess
import threading
import time
import unittest
import urllib.request

from lithify import console


class FakeSpeaker:
    """A console that behaves like the speaker's: a banner, then every line runs on its own and
    the connection stays open (so a client has to know when a command is done)."""

    def __init__(self, can_download: bool = True):
        self.can_download = can_download
        self.files: dict[str, str] = {}
        self.downloads = 0
        self.runs = 0
        self.srv = socket.create_server(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def close(self):
        self.srv.close()

    def _serve(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    def _session(self, conn: socket.socket):
        conn.sendall(b"CONNECTED!!\r\n")
        buf = b""
        with conn:
            while True:
                data = conn.recv(4096)
                if not data:
                    return
                buf += data
                while b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    if self._line(conn, line.decode()):
                        return

    def _line(self, conn, line: str) -> bool:
        if m := re.fullmatch(r"echo '(.*)' (>>?) (\S+)", line):
            self.files[m[3]] = (self.files.get(m[3], "") if m[2] == ">>" else "") + m[1] + "\n"
        elif m := re.fullmatch(r'exec mksh -c "curl -fsS -m 20 -o (\S+) (http://\S+) && exec mksh (\S+)(.*)"', line):
            if not self.can_download:
                conn.sendall(b"curl: (7) Failed to connect\r\n")
                return False
            self.files[m[1]] = urllib.request.urlopen(m[2], timeout=5).read().decode()  # noqa: S310 - http:// only (the regex)
            self.downloads += 1
            self._run(conn, self.files[m[3]], m[4])
        elif m := re.fullmatch(r'exec mksh -c "(.*)"', line):
            self._run(conn, m[1], "")
        elif m := re.fullmatch(r"exec mksh (\S+)(.*)", line):
            self._run(conn, self.files[m[1]], m[2])
        return False

    def _run(self, conn, script: str, args: str):
        self.runs += 1
        out = subprocess.run(["sh", "-c", script, "sh", *args.split()], capture_output=True).stdout
        conn.sendall(out.replace(b"\n", b"\r\n"))


class ConsoleTest(unittest.TestCase):
    def test_clean_drops_prompt_and_carriage_returns(self):
        self.assertEqual(console.clean(">line one\r\n>line two\r\n"), "line one\nline two")

    def test_telnet_negotiation_is_stripped(self):
        raw = bytes([255, 251, 1]) + b"CONNECTED!!" + bytes([255, 250, 24, 1, 255, 240]) + b"\r\n"
        self.assertEqual(console._strip_telnet(raw), b"CONNECTED!!\r\n")

    def test_scripts_are_downloaded_in_one_line(self):
        speaker = FakeSpeaker()
        self.addCleanup(speaker.close)
        t = time.monotonic()
        out = console.run("127.0.0.1", "echo hello $1\necho done\nexit 3\n", "world", wait=30, idle=20,
                          port=speaker.port)
        self.assertEqual(out, "hello world\ndone")
        self.assertEqual((speaker.downloads, speaker.runs), (1, 1))
        self.assertLess(time.monotonic() - t, 5, "reading must stop at the end marker, not wait for silence")

    def test_scripts_are_typed_in_when_the_speaker_cannot_download(self):
        speaker = FakeSpeaker(can_download=False)
        self.addCleanup(speaker.close)
        t = time.monotonic()
        out = console.run("127.0.0.1", "echo hello $1\n", "world", wait=30, idle=20, port=speaker.port)
        self.assertEqual(out, "hello world")
        self.assertEqual((speaker.downloads, speaker.runs), (0, 1))
        self.assertLess(time.monotonic() - t, 8)

    def test_single_commands_end_at_the_marker(self):
        speaker = FakeSpeaker()
        self.addCleanup(speaker.close)
        t = time.monotonic()
        with console.Console("127.0.0.1", speaker.port) as c:
            self.assertEqual(c.exec("echo one", wait=30, idle=20), "one")
        self.assertLess(time.monotonic() - t, 3)

    def test_the_script_server_answers_only_the_speaker(self):
        try:
            console.local_ip_for("192.0.2.7")
        except OSError:
            self.skipTest("no network route on this machine")
        with console.serve_script("echo x\n", "192.0.2.7") as (url, served):
            self.assertTrue(url.startswith("http://"))
            with self.assertRaises(OSError):  # from this machine, not the speaker
                urllib.request.urlopen(url, timeout=2).read()  # noqa: S310 - http:// asserted above
        self.assertFalse(served.is_set())

    def test_arguments_stay_plain(self):
        for bad in ('a"b', "a;b", "$(reboot)", "a`b`", "x\ny"):
            with self.subTest(args=bad), self.assertRaises(console.ConsoleError):
                console.run("127.0.0.1", "true\n", bad, port=1)


if __name__ == "__main__":
    unittest.main()
