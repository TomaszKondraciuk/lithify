"""Client for the Libre LS9 service console (TCP 23).

The console answers "CONNECTED!!" with no login and runs every received line as `sh -c <line>`
as root. Two quirks shape everything here:
  * its `sh` (old Android ash) inherits SIGCHLD=SIG_IGN and spins forever after any forked
    command, so a line must be `exec <cmd>` or a builtin; scripts run as `exec mksh <file>`
    (mksh reaps children properly);
  * it strips the last two bytes of each line - lines end with CRLF.

Scripts reach the speaker in one line: it downloads them from a short-lived HTTP server on this
computer that answers only the speaker. Where that is not possible (a firewall on this computer),
they are typed in line by line, which takes about a second per line. The console keeps the
connection open after a command, so every command ends by printing a marker: reading stops there
instead of waiting for silence.
"""
from __future__ import annotations

import contextlib
import http.server
import re
import secrets
import socket
import threading
import time

from . import hostos

IAC = 255
MAX_LINE = 180
START = "LITHIFY_SCRIPT_START"
END = "LITHIFY_SCRIPT_END"
# A command that prints nothing for this long is taken as stuck (normally the end marker comes).
STALL = 120.0
# Script arguments are copied into a console line (inside double quotes): plain words only.
ARGS_RE = re.compile(r"[A-Za-z0-9_./:=@%+, -]*")


class ConsoleError(RuntimeError):
    pass


def _strip_telnet(data: bytes) -> bytes:
    out, i = bytearray(), 0
    while i < len(data):
        if data[i] == IAC and i + 1 < len(data):
            cmd = data[i + 1]
            if cmd in (251, 252, 253, 254):
                i += 3
                continue
            if cmd == 250:
                j = data.find(bytes([IAC, 240]), i)
                i = j + 2 if j != -1 else len(data)
                continue
            i += 2
            continue
        out.append(data[i])
        i += 1
    return bytes(out)


def clean(text: str) -> str:
    """Drop the console's '>' prompt and CR characters."""
    lines = [line[1:] if line.startswith(">") else line for line in text.replace("\r", "").split("\n")]
    return "\n".join(lines).strip("\n")


class Console:
    def __init__(self, host: str, port: int = 23, timeout: float = 5.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.sock: socket.socket | None = None

    def __enter__(self):
        for attempt in range(3):
            try:
                self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
                break
            except OSError as e:
                if attempt == 2:
                    raise ConsoleError(f"cannot reach the speaker console {self.host}:{self.port}: {e}") from None
                time.sleep(1)
        # The banner can be slow while the speaker is busy (a spinning official client).
        banner, found = self._read_until("CONNECTED", stall=8.0, total=10.0)
        if not found:
            self.sock.close()
            raise ConsoleError(f"unexpected console banner from {self.host}: {banner[:60]!r}")
        return self

    def __exit__(self, *exc):
        if self.sock:
            self.sock.close()

    def _connected(self) -> socket.socket:
        """The open connection (ConsoleError outside `with Console(...)`: there is none)."""
        if self.sock is None:
            raise ConsoleError(f"not connected to the speaker console {self.host}:{self.port}")
        return self.sock

    def _read(self, idle: float, total: float) -> str:
        sock = self._connected()
        buf, end = b"", time.time() + total
        while time.time() < end:
            sock.settimeout(max(0.05, min(idle, end - time.time())))
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                break
            if not chunk:
                break
            buf += chunk
        return _strip_telnet(buf).decode("utf-8", "replace")

    def _read_until(self, marker: str, stall: float, total: float) -> tuple[str, bool]:
        """Read until `marker` arrives (True), the speaker is silent for `stall` seconds or closes
        the connection, or `total` seconds pass (False)."""
        sock = self._connected()
        buf, end, want = b"", time.time() + total, marker.encode()
        while time.time() < end:
            sock.settimeout(max(0.05, min(stall, end - time.time())))
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                break
            if not chunk:
                break
            buf += chunk
            if want in buf:
                text = _strip_telnet(buf).decode("utf-8", "replace")
                return text.split(marker, 1)[0], True
        return _strip_telnet(buf).decode("utf-8", "replace"), False

    def _poll(self, timeout: float) -> tuple[bytes, bool]:
        """What arrives within `timeout`, and whether the speaker closed the session."""
        sock = self._connected()
        sock.settimeout(timeout)
        try:
            chunk = sock.recv(65536)
        except TimeoutError:
            return b"", False
        return chunk, not chunk

    def _send(self, line: str) -> None:
        self._connected().sendall((line + "\r\n").encode("utf-8"))

    def exec(self, command: str, wait: float = 20.0, idle: float = 10.0) -> str:
        """Run one command (no pipes, no `;`) and return its output (`idle`: how long it may stay
        silent)."""
        if re.search(r'[;&|`"$\\\n]', command):
            raise ConsoleError("exec() takes a single command; use run_script() for more")
        self._send(f'exec mksh -c "{command}; echo {END}"')
        return clean(self._read_until(END, stall=idle, total=wait)[0])

    def put(self, remote: str, text: str) -> None:
        """Write a small text file with the shell's builtin echo, one short line at a time."""
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", remote):
            raise ConsoleError(f"bad remote path {remote!r}")
        lines = text.splitlines()
        for n, line in enumerate(lines):
            if "'" in line or len(line) > MAX_LINE:
                raise ConsoleError(f"{remote} line {n + 1}: single quote or longer than {MAX_LINE}")
            self._send(f"echo '{line}' {'>' if n == 0 else '>>'} {remote}")
            self._read(idle=0.3, total=1.0)

    def run_script(self, text: str, args: str = "", wait: float = 120.0, idle: float = STALL,
                   name: str = "") -> str:
        """Type `text` into a file line by line and run it with mksh; returns the output."""
        _check_args(args)
        name = name or script_name()
        self.put(name, wrap(text, name))
        self._send(f"exec mksh {name} {args}".rstrip())
        return clean(self._read_until(END, stall=idle, total=wait)[0])

    def run_downloaded(self, url: str, served: threading.Event, args: str = "", wait: float = 120.0,
                       idle: float = STALL, name: str = "") -> str | None:
        """Have the speaker download the script at `url` (made with `wrap`) and run it; None when
        it never fetched it (then the script did not run, so another way may be tried)."""
        _check_args(args)
        name = name or script_name()
        run_it = f"exec mksh {name} {args}".rstrip()
        line = f'exec mksh -c "curl -fsS -m 20 -o {name} {url} && {run_it}"'
        if len(line) > MAX_LINE:
            return None
        self._send(line)
        # Wait for the download itself: a reply that is merely slow must not make the script run
        # twice (curl gives up after 20 s, and the server is gone by then).
        early, closed, end = b"", False, time.time() + 25
        while not served.is_set() and not closed and b"curl:" not in early and time.time() < end:
            chunk, closed = self._poll(0.25)
            early += chunk
        if not served.is_set():
            return None
        rest = "" if END.encode() in early or closed else self._read_until(END, stall=idle, total=wait)[0]
        text = _strip_telnet(early).decode("utf-8", "replace") + rest
        return clean(text.split(END, 1)[0])


def script_name() -> str:
    """A fresh name for a script in the speaker's /tmp (two runs at once must not share one)."""
    return f"/tmp/lithify_{secrets.token_hex(4)}.sh"


def wrap(script: str, name: str) -> str:
    """`script` that removes itself and prints the end marker however it ends."""
    return f'trap "rm -f {name}; echo {END}" EXIT\n{script}'


def _check_args(args: str) -> None:
    if not ARGS_RE.fullmatch(args):
        raise ConsoleError(f"script arguments must be plain words: {args!r}")


def local_ip_for(host: str) -> str:
    """This computer's address on the way to `host` (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 9))
        return s.getsockname()[0]
    finally:
        s.close()


@contextlib.contextmanager
def serve_script(text: str, client_ip: str):
    """Serve `text` over HTTP to `client_ip` only, for the duration of the block, under a random
    path; yields (url, event set once it was downloaded)."""
    body, path, served = text.encode("utf-8"), f"/{secrets.token_hex(6)}.sh", threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        timeout = 15

        def do_GET(self):
            if self.path != path:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            served.set()

        def log_message(self, *args):
            pass

    class Server(http.server.ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = not hostos.WINDOWS  # (Windows: a second server would share the port)

        def verify_request(self, request, client_address) -> bool:
            return client_address[0] == client_ip

    # A known port, so a firewall rule can let the speaker in (Windows); should the speaker still
    # not get through, the script is typed in.
    httpd = hostos.bind_server(Server, local_ip_for(client_ip), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://{httpd.server_address[0]}:{httpd.server_address[1]}{path}", served
    finally:
        httpd.shutdown()
        httpd.server_close()


def run(host: str, script: str, args: str = "", wait: float = 120.0, idle: float = STALL, port: int = 23) -> str:
    """Run `script` with mksh as root on the speaker and return its output (`idle`: how long the
    script may print nothing before it counts as stuck)."""
    _check_args(args)
    name = script_name()
    with contextlib.ExitStack() as stack:
        try:
            text = wrap(f"echo {START}\n{script}", name)
            url, served = stack.enter_context(serve_script(text, socket.gethostbyname(host)))
        except OSError:
            url = None  # no way to serve it from here: type it in
        if url:
            with Console(host, port) as c:
                out = c.run_downloaded(url, served, args, wait=wait, idle=idle, name=name)
            if out is not None:  # it ran: never run it a second time
                return out.split(START, 1)[1].lstrip("\n") if START in out else out
    with Console(host, port) as c:
        return c.run_script(script, args, wait=wait, idle=idle)


def exec_one(host: str, command: str, wait: float = 20.0, idle: float = 10.0) -> str:
    with Console(host) as c:
        return c.exec(command, wait=wait, idle=idle)
