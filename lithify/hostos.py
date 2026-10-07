"""What differs between the computers Lithify runs on: Linux, macOS and Windows.

Where files live, locking a file between processes, removing a directory that holds read-only
files (git's objects on Windows), writing files the speaker reads (always LF line endings) and
files that must never be half-written, stopping a command together with everything it started,
and the Windows firewall rule that lets the speaker reach this computer.
"""
from __future__ import annotations

import contextlib
import errno
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WINDOWS = os.name == "nt"
MACOS = sys.platform == "darwin"
# msvcrt.locking's answers when another process holds the lock (anything else is a real error).
_LOCK_CONTENDED = {errno.EACCES, getattr(errno, "EDEADLOCK", errno.EDEADLK)}

# The speaker connects to these ports on this computer: the companion, and the temporary servers
# of `lithify install` (the bundle, the scripts), which take the first free port of TEMP_PORTS.
# (Not 8096-8097: Jellyfin and Emby use 8096.)
COMPANION_PORT = 8095
TEMP_PORTS = range(18096, 18100)
FIREWALL_RULE = "Lithify"
FIREWALL_PORTS = f"{COMPANION_PORT},{TEMP_PORTS[0]}-{TEMP_PORTS[-1]}"


def bind_server(server_class, ip: str, handler):
    """An HTTP server on the first free port of TEMP_PORTS (the ones the firewall rule opens);
    any free port when they are all taken."""
    for port in (*TEMP_PORTS, 0):
        try:
            return server_class((ip, port), handler)
        except OSError:
            if port == 0:
                raise
    raise OSError("no free port")


def _home(env) -> Path:
    return Path(env.get("USERPROFILE") or env.get("HOME") or Path.home())


def config_dir(env=None, windows: bool = WINDOWS) -> Path:
    """config.toml and versions.toml: %APPDATA%\\lithify, or ~/.config/lithify ($XDG_CONFIG_HOME)."""
    env = os.environ if env is None else env
    if windows:
        return Path(env.get("APPDATA") or _home(env) / "AppData" / "Roaming") / "lithify"
    return Path(env.get("XDG_CONFIG_HOME") or _home(env) / ".config") / "lithify"


def cache_dir(env=None, windows: bool = WINDOWS) -> Path:
    """Builds and staged bundles: $LITHIFY_CACHE, %LOCALAPPDATA%\\lithify\\cache, or ~/.cache/lithify."""
    env = os.environ if env is None else env
    if env.get("LITHIFY_CACHE"):
        return Path(env["LITHIFY_CACHE"])
    if windows:
        return Path(env.get("LOCALAPPDATA") or _home(env) / "AppData" / "Local") / "lithify" / "cache"
    return Path(env.get("XDG_CACHE_HOME") or _home(env) / ".cache") / "lithify"


def log_dir(env=None, windows: bool = WINDOWS, macos: bool = MACOS) -> Path:
    """The companion's log where no service manager keeps one (macOS, Windows)."""
    env = os.environ if env is None else env
    if windows:
        return Path(env.get("LOCALAPPDATA") or _home(env) / "AppData" / "Local") / "lithify" / "logs"
    if macos:
        return _home(env) / "Library" / "Logs" / "lithify"
    return cache_dir(env, windows) / "logs"


def write_lf(path: Path, text: str, private: bool = False) -> None:
    """A text file for the speaker: UTF-8 with LF line endings on every computer (Windows would
    write CRLF, which the speaker's shell scripts would read as part of each file name).
    `private`: readable by this user only (Linux, macOS), for files that carry the speaker's
    settings (its web page's PIN among them)."""
    if not private:
        path.write_text(text, encoding="utf-8", newline="\n")
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    with open(fd, "w", encoding="utf-8", newline="\n") as f:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)  # (a file that was there before keeps its mode otherwise)
        f.write(text)


def private_dir(path: Path) -> Path:
    """A directory only this user can open (Linux, macOS): staged files carry settings."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not WINDOWS:
        os.chmod(path, 0o700)
    return path


def make_private(path: Path, files: bool = True) -> None:
    """Directory `path` (with `files`, the files in it too) this user's only, on Linux and macOS:
    for what an earlier Lithify left with wider modes. A mode is changed only where it differs."""
    if WINDOWS:
        return
    wanted = [(path, 0o700)]
    if files:
        with os.scandir(path) as entries:
            wanted += [(Path(e.path), 0o600) for e in entries if e.is_file(follow_symlinks=False)]
    for p, mode in wanted:
        if stat.S_IMODE(os.lstat(p).st_mode) != mode:
            os.chmod(p, mode)


def replace(src, dst, attempts: int = 8) -> None:
    """os.replace that rides out Windows' short refusals: a virus scanner or the search indexer
    that still holds a file just written (PermissionError) gets a moment, a few times."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if not WINDOWS or i == attempts - 1:
                raise
            time.sleep(min(0.05 * 2 ** i, 1.0))


def _backup_name(path: Path) -> Path:
    """<name>.<date>-<time>.bak, never one that is there already: each replaced file is kept."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for n in range(1, 1000):
        candidate = path.with_name(f"{path.name}.{stamp}{'' if n == 1 else f'-{n}'}.bak")
        if not os.path.lexists(candidate):
            return candidate
    raise OSError(f"no free backup name for {path}")


def write_atomic(path: Path, text: str, backup: bool = False, newline: str | None = "\n") -> Path | None:
    """Replace `path` as a whole: readers, and a computer that stops midway, see the old file or
    the new one, never half of one. The temporary file has a name of its own (two writers never
    share one). A link stays a link (its target is replaced) and a file keeps its mode; a new
    file is this user's only. With `backup` the old file is kept as <name>.<date>-<time>.bak,
    which is returned."""
    path = Path(os.path.realpath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = None
    kept = None
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8", newline=newline) as f:
            if mode is not None and hasattr(os, "fchmod"):
                os.fchmod(f.fileno(), mode)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if backup and path.is_file():
            kept = _backup_name(path)
            shutil.copy2(path, kept)
        replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return kept


def _is_link(p) -> bool:
    """A symbolic link, or a Windows junction (which rmtree must not descend into either)."""
    return os.path.islink(p) or bool(getattr(os.path, "isjunction", lambda _: False)(p))


def _unlink(p) -> None:
    """Remove a file or a link (on Windows a link to a directory, and a junction, go with rmdir)."""
    if WINDOWS and os.path.isdir(p):
        os.rmdir(p)
    else:
        os.unlink(p)


def _let_remove(p: str, inside: bool) -> None:
    """Permissions that let `p` go: its directory writable when that lies inside the tree (on
    Linux and macOS the entries of a read-only directory cannot be removed), a directory of its
    own readable, and on Windows no read-only flag. A link's target is never touched."""
    if inside:
        parent = os.path.dirname(p)
        os.chmod(parent, stat.S_IMODE(os.lstat(parent).st_mode) | stat.S_IRWXU)
    if _is_link(p):
        return
    st = os.lstat(p)
    if stat.S_ISDIR(st.st_mode):
        os.chmod(p, stat.S_IMODE(st.st_mode) | stat.S_IRWXU)
    elif WINDOWS:
        os.chmod(p, stat.S_IMODE(st.st_mode) | stat.S_IWRITE)


def rmtree(path: Path, quiet: bool = True) -> None:
    """Remove a directory tree, read-only files and directories too (git makes its objects
    read-only on Windows; on Linux and macOS a read-only directory keeps its entries). A link is
    removed itself, never what it points to, and permissions change only where removing met a
    PermissionError. What cannot be removed (a file another process has open, on Windows) stays
    and is named on stderr; without `quiet` it is an OSError instead."""
    top = os.fspath(path)
    left: list[str] = []
    tried: set[str] = set()

    def again(_func, p, exc) -> None:
        if not isinstance(exc, BaseException):  # (onerror, before Python 3.12, passes exc_info)
            exc = exc[1]
        if isinstance(exc, FileNotFoundError):
            return  # gone meanwhile
        if isinstance(exc, PermissionError) and p not in tried:
            tried.add(p)
            try:
                _let_remove(p, inside=p != top)
                if os.path.isdir(p) and not _is_link(p):
                    _rmtree(p)  # (its entries too: a directory that could not be listed still has them)
                else:
                    _unlink(p)
                return
            except FileNotFoundError:
                return
            except OSError as e:
                exc = e
        left.append(f"{p} ({getattr(exc, 'strerror', None) or exc})")

    def _rmtree(p: str) -> None:
        if sys.version_info >= (3, 12):
            shutil.rmtree(p, onexc=again)
        else:
            shutil.rmtree(p, onerror=again)

    if not os.path.lexists(top):
        return
    if _is_link(top) or not os.path.isdir(top):
        try:
            _unlink(top)
        except OSError as e:
            again(_unlink, top, e)
    else:
        _rmtree(top)
    if not left:
        return
    what = "; ".join(left[:3]) + (f" and {len(left) - 3} more" if len(left) > 3 else "")
    if not quiet:
        raise OSError(f"cannot remove {what}")
    print(f"warning: could not remove {what}", file=sys.stderr, flush=True)


@contextlib.contextmanager
def file_lock(path: Path, shared: bool = False, wait: bool = True, timeout: float | None = 60.0):
    """Hold a lock on `path` for the block, between processes too. Without `wait`, a lock held
    elsewhere raises BlockingIOError at once; with it, TimeoutError once `timeout` seconds have
    passed (None: no limit). Windows knows exclusive locks only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = None if timeout is None else time.monotonic() + timeout

    def wait_more() -> None:
        if not wait:
            raise BlockingIOError(f"{path} is locked")
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError(f"{path} is still locked after {timeout:g} s")
        time.sleep(0.1)

    with path.open("a+b") as f:
        if WINDOWS:
            import msvcrt
            while True:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as e:
                    if e.errno not in _LOCK_CONTENDED:
                        raise  # not another holder: waiting would not help
                    wait_more()
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            if wait and deadline is None:
                fcntl.flock(f, mode)
            else:
                while True:
                    try:
                        fcntl.flock(f, mode | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        wait_more()
            yield


# ── commands and what they start ────────────────────────────────────────────

# (Windows' values, for the tests elsewhere: the subprocess module names them on Windows only)
_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def no_window() -> dict:
    """Popen arguments for a console program started from the companion: under the scheduled
    task (pythonw, no console) Windows would open a window for each one."""
    return {"creationflags": _NO_WINDOW} if WINDOWS else {}


def own_group() -> dict:
    """Popen arguments that start a command in a process group of its own, so kill_tree() can end
    everything it started: a grandchild that keeps the output pipe open would otherwise keep a
    reader waiting forever after the command itself was killed. (On Windows: and no window.)"""
    if WINDOWS:
        return {"creationflags": _NEW_PROCESS_GROUP | _NO_WINDOW}
    return {"start_new_session": True}


def kill_tree(p: subprocess.Popen) -> None:
    """End `p` (started with own_group()) and everything it started: its process group, on
    Windows its process tree."""
    if WINDOWS:
        if p.poll() is None:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, timeout=30,
                               **no_window())
        with contextlib.suppress(OSError):
            p.kill()
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(p.pid, signal.SIGKILL)


def run(cmd: list[str], timeout: float, **kw) -> subprocess.CompletedProcess:
    """subprocess.run with the output captured and no input, in a process group of its own: when
    the time is up, everything the command started ends with it (git's network helpers too) and
    TimeoutExpired comes at once, instead of after the helpers let go of the output."""
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         **own_group(), **kw)
    try:
        out, err = p.communicate(timeout=timeout)
    except BaseException:
        kill_tree(p)
        with contextlib.suppress(Exception):
            p.communicate(timeout=10)
        raise
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def terminate_tree(p: subprocess.Popen, grace: float) -> None:
    """Ask `p` (started with own_group()) and what it started to stop - SIGTERM: a build stops its
    containers on the way out - and end them all after `grace` seconds. (Windows cannot ask a
    program that has no window: its tree ends at once.)"""
    if not WINDOWS:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(p.pid, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            p.wait(timeout=grace)
    kill_tree(p)
    with contextlib.suppress(subprocess.TimeoutExpired):
        p.wait(timeout=10)


def kernel32():
    """Windows' kernel32 with the types of the calls used here (without them ctypes passes and
    returns 32-bit ints, which cuts a 64-bit process HANDLE in half)."""
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    k.GetExitCodeProcess.restype = wintypes.BOOL
    k.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k.CloseHandle.restype = wintypes.BOOL
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    return k


def pid_alive(pid: int) -> bool:
    """Is process `pid` running on this computer? (Asked without a signal: on Windows os.kill
    would end it.)"""
    if pid <= 0:
        return False
    if WINDOWS:
        import ctypes
        from ctypes import wintypes
        k = kernel32()
        handle = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: it exists, someone else's
        try:
            code = wintypes.DWORD()
            return bool(k.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # another user's
    return True


def firewall_rule_command(ports: str = FIREWALL_PORTS) -> list[str]:
    """netsh arguments for the Windows firewall rule: speakers on the local network may reach
    Lithify's ports on this computer (any network profile: home networks are often "public")."""
    return ["advfirewall", "firewall", "add", "rule", f"name={FIREWALL_RULE}", "dir=in", "action=allow",
            "protocol=TCP", f"localport={ports}", "remoteip=localsubnet", "profile=any"]


def firewall_rule_present() -> bool:
    if not WINDOWS:
        return True
    try:
        r = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={FIREWALL_RULE}"],
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError):  # (a firewall service that does not answer)
        return False
    return r.returncode == 0


def firewall_advice(lan_ip: str = "") -> str:
    """What to do when the speaker cannot download from this computer (whose address on the speakers'
    network is `lan_ip`), for this system."""
    ports = FIREWALL_PORTS
    parts = lan_ip.split(".")
    net = ".".join(parts[:3]) + ".0/24" if len(parts) == 4 and all(p.isdigit() for p in parts) else "192.168.0.0/16"
    if WINDOWS:
        fix = (f"Windows Defender Firewall blocks it: allow the rule \"{FIREWALL_RULE}\" (TCP {ports}) - Windows asks "
               "once, answer Allow - and set this network to Private (Settings > Network > Properties), not Public.")
    elif MACOS:
        fix = ("the macOS firewall blocks it: answer Allow when macOS asks whether Python may accept incoming "
               "connections, or allow it in System Settings > Network > Firewall > Options.")
    else:
        dash = ports.replace(",", " and ")
        fix = (f"a firewall on this computer blocks it: open TCP {dash} for your local network, e.g. "
               f"`sudo ufw allow proto tcp from {net} to any port {ports.replace('-', ':')}` (ufw) or "
               f"`sudo firewall-cmd --permanent --add-port={ports.split(',')[1]}/tcp "
               f"--add-port={ports.split(',')[0]}/tcp && sudo firewall-cmd --reload` (firewalld).")
    return (f"{fix} Also check that this computer and the speaker are on the same network: no VPN, and no guest "
            "Wi-Fi that keeps devices apart.")


def ensure_firewall_rule() -> bool:
    """On Windows, add the rule once (Windows asks for an administrator's consent). True when the
    speaker can reach this computer, as far as the firewall goes."""
    if firewall_rule_present():
        return True
    args = " ".join(firewall_rule_command())
    # Elevated (Windows asks), hidden, and waited for, so the result can be checked.
    ps = f"Start-Process netsh -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList '{args}'"
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return False
    return firewall_rule_present()
