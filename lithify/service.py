"""The companion as a service of this computer, on each system: a systemd user service (Linux),
a launchd agent (macOS), a scheduled task at logon (Windows).

`lithify serve` is a small supervisor around the companion itself (`lithify serve --worker`):
when Lithify has updated itself, the worker exits with RESTART and the supervisor starts it again
with the new code. That works the same whatever started the supervisor; the service manager
only has to start it with the computer.
"""
from __future__ import annotations

import contextlib
import getpass
import os
import plistlib
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from xml.sax.saxutils import escape

from . import hostos, updates
from .device import ROOT

RESTART = 75          # a worker's exit code: start me again, the code on disk is new
UNIT = "lithify-companion"
LAUNCHD_LABEL = "com.lithify.companion"
TASK = "Lithify companion"
LOG_MAX = 1024 * 1024
# A worker that ran this long works. One that fails sooner, right after Lithify updated itself,
# gets the code that worked back (once).
HEALTHY_AFTER = 60.0


def _run(cmd: list[str], what: str, check: bool = True, timeout: float = 120) -> subprocess.CompletedProcess:
    """A service manager's command; with `check` a failure is a RuntimeError that says what the
    tool said (its exit status alone tells nobody anything)."""
    try:
        r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError(f"{what}: {cmd[0]} is not installed here") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{what}: {cmd[0]} did not answer within {timeout:g} s") from None
    if check and r.returncode:
        why = (r.stderr or r.stdout).strip() or f"exit status {r.returncode}"
        raise RuntimeError(f"{what}: {why}")
    return r


def _python(windowless: bool = False) -> str:
    """This Python; on Windows its window-less twin for a background task."""
    exe = Path(sys.executable)
    if windowless and hostos.WINDOWS and exe.with_name("pythonw.exe").exists():
        return str(exe.with_name("pythonw.exe"))
    return str(exe)


def serve_command(config: Path | None, log: Path | None = None, windowless: bool = False) -> list[str]:
    """How a service starts the companion (its supervisor)."""
    cmd = [_python(windowless), str(ROOT / "bin" / "lithify")]
    if config:
        cmd += ["--config", str(config)]
    cmd.append("serve")
    if log:
        cmd += ["--log", str(log)]
    return cmd


# ── systemd (Linux) ──────────────────────────────────────────────────────────

def _systemd_arg(arg: str) -> str:
    arg = arg.replace("%", "%%").replace("$", "$$")
    if any(c in arg for c in ' "\\\'\t'):
        arg = '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return arg


def systemd_unit(cmd: list[str]) -> str:
    return ("[Unit]\nDescription=Lithify companion (builds and serves updates for the speaker web page)\n"
            "After=network-online.target\nWants=network-online.target\n\n[Service]\n"
            f"ExecStart={' '.join(_systemd_arg(c) for c in cmd)}\n"
            f"WorkingDirectory={str(ROOT).replace('%', '%%')}\nRestart=on-failure\nRestartSec=10\n\n"
            "[Install]\nWantedBy=default.target\n")


def _systemd_path() -> Path:
    return hostos.config_dir().parent / "systemd" / "user" / f"{UNIT}.service"


def _systemd_install(config: Path | None) -> str:
    unit = _systemd_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(systemd_unit(serve_command(config)), encoding="utf-8")
    _run(["systemctl", "--user", "daemon-reload"], "systemctl daemon-reload", check=False)
    _run(["systemctl", "--user", "enable", UNIT], f"cannot enable {UNIT}")
    _run(["systemctl", "--user", "restart", UNIT], f"cannot start {UNIT}")
    # Keep it running when nobody is logged in (allowed for your own user on most systems).
    with contextlib.suppress(RuntimeError, OSError, KeyError):
        _run(["loginctl", "enable-linger", getpass.getuser()], "loginctl", check=False)
    return f"systemd user service {UNIT} (systemctl --user status {UNIT})"


def _systemd_uninstall() -> None:
    with contextlib.suppress(RuntimeError):  # (no systemd: nothing to remove there)
        _run(["systemctl", "--user", "disable", "--now", UNIT], "systemctl disable", check=False)
    _systemd_path().unlink(missing_ok=True)
    with contextlib.suppress(RuntimeError):
        _run(["systemctl", "--user", "daemon-reload"], "systemctl daemon-reload", check=False)


# ── launchd (macOS) ──────────────────────────────────────────────────────────

def launchd_plist(cmd: list[str], early_log: Path) -> bytes:
    return plistlib.dumps({
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": cmd,
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        # (the supervisor writes its own log; this one only catches a Python that cannot start)
        "StandardErrorPath": str(early_log),
        # launchd starts programs with a bare PATH: Docker and git live elsewhere.
        "EnvironmentVariables": {"PATH": "/usr/local/bin:/opt/homebrew/bin:"
                                         "/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
    })


def _launchd_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _launchd_install(config: Path | None) -> str:
    logs = hostos.log_dir()
    logs.mkdir(parents=True, exist_ok=True)
    plist = _launchd_path()
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(launchd_plist(serve_command(config, logs / "companion.log"), logs / "launchd.log"))
    domain = f"gui/{os.getuid()}"
    _run(["launchctl", "bootout", f"{domain}/{LAUNCHD_LABEL}"], "launchctl bootout", check=False)
    if _run(["launchctl", "bootstrap", domain, str(plist)], "launchctl bootstrap", check=False).returncode:
        _run(["launchctl", "load", "-w", str(plist)], f"cannot load {plist}")
    return f"launchd agent {LAUNCHD_LABEL} (log: {logs / 'companion.log'})"


def _launchd_uninstall() -> None:
    _run(["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"], "launchctl bootout", check=False)
    _launchd_path().unlink(missing_ok=True)


# ── Task Scheduler (Windows) ─────────────────────────────────────────────────

def task_xml(cmd: list[str], user: str) -> str:
    """A task that starts the companion at logon, hidden, without a time limit, again a minute
    after it stops on an error."""
    args = subprocess.list2cmdline(cmd[1:])
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Lithify companion: builds and serves updates for the Lithe Audio speaker's web page</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(cmd[0])}</Command>
      <Arguments>{escape(args)}</Arguments>
      <WorkingDirectory>{escape(str(ROOT))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _task_install(config: Path | None) -> str:
    logs = hostos.log_dir()
    logs.mkdir(parents=True, exist_ok=True)
    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".lstrip("\\")
    cmd = serve_command(config, logs / "companion.log", windowless=True)
    with tempfile.TemporaryDirectory() as d:
        xml = Path(d) / "task.xml"
        xml.write_text(task_xml(cmd, user), encoding="utf-16")  # schtasks wants UTF-16
        _run(["schtasks", "/Create", "/TN", TASK, "/XML", str(xml), "/F"], f"cannot create the task \"{TASK}\"")
    _run(["schtasks", "/End", "/TN", TASK], "schtasks /End", check=False)  # an instance from before
    _run(["schtasks", "/Run", "/TN", TASK], f"cannot start the task \"{TASK}\"")
    return f"scheduled task \"{TASK}\" (starts at logon; log: {logs / 'companion.log'})"


def _task_uninstall() -> None:
    _run(["schtasks", "/End", "/TN", TASK], "schtasks /End", check=False)
    _run(["schtasks", "/Delete", "/TN", TASK, "/F"], "schtasks /Delete", check=False)


# ── install / uninstall ─────────────────────────────────────────────────────

NO_SYSTEMD = "no systemd user session here: run `lithify serve` yourself (e.g. from your desktop's autostart)"


def install(config: Path | None) -> str:
    """Start the companion now and with the computer from now on; says how."""
    if hostos.WINDOWS:
        return _task_install(config)
    if hostos.MACOS:
        return _launchd_install(config)
    try:
        session = _run(["systemctl", "--user", "show-environment"], "systemctl", check=False).returncode == 0
    except RuntimeError:  # no systemctl at all
        session = False
    if session:
        return _systemd_install(config)
    raise RuntimeError(NO_SYSTEMD)


def uninstall() -> None:
    if hostos.WINDOWS:
        _task_uninstall()
    elif hostos.MACOS:
        _launchd_uninstall()
    else:
        _systemd_uninstall()


# ── the supervisor ──────────────────────────────────────────────────────────

def _open_log(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.exists() and path.stat().st_size > LOG_MAX:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass  # (Windows: another process still has it open)
    return path.open("a", encoding="utf-8", buffering=1)


def call(cmd: list[str], grace: float = 45, **kw) -> int:
    """subprocess.call, except that Ctrl-C (or SIGTERM made an exception) does not kill the child
    at once: it got the same signal (one process group) and has `grace` seconds to stop what it
    started - a build its containers, a worker its build."""
    p = subprocess.Popen(cmd, **kw)
    try:
        return p.wait()
    except BaseException:
        try:
            p.wait(timeout=grace)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):  # the time is up, or a second Ctrl-C
            p.kill()
            p.wait()
        raise


def supervise(worker: list[str], log: Path | None = None, root: Path = ROOT,
              healthy_after: float = HEALTHY_AFTER, retry_after: float | None = None) -> int:
    """Run the companion worker; again whenever it asks for it (exit code RESTART). A worker that
    fails within `healthy_after` seconds of Lithify updating itself gets the code that last ran
    well back, once (`git reset --keep`: uncommitted changes are never lost), and is started
    again; a broken update never loops. With `retry_after` (Windows, whose Task Scheduler does not
    restart a program that exits with an error) a failed worker is started again after that many
    seconds, twice as long after each failure in a row, at most five minutes."""
    out = _open_log(log) if log else None
    if out:
        sys.stdout = sys.stderr = out
    good = ""        # the code a worker last ran well with (healthy_after seconds or more)
    updated = False  # the worker runs code Lithify has just updated itself to
    failures = 0     # failed runs in a row (retry_after)
    try:
        while True:
            code = updates.head(root)
            started = time.monotonic()
            rc = call(worker, stdout=out, stderr=out)
            if time.monotonic() - started >= healthy_after:
                good, updated, failures = code, False, 0
            if rc == RESTART:
                updated = True
                print("the companion starts again with Lithify's new code", flush=True)
                continue
            if rc != 0 and updated and good and code and code != good:
                updated = False
                undone, why = updates.undo_update(root, good)
                if undone:
                    print(f"the companion fails with Lithify's new code (exit code {rc}): it starts again with "
                          f"{good[:12]}, the code that worked", flush=True)
                    continue
                print(f"the companion fails with Lithify's new code (exit code {rc}), and going back to "
                      f"{good[:12]} failed: {why}", flush=True)
            if rc not in (0, 130) and retry_after:
                delay = min(retry_after * 2 ** failures, 300.0)
                failures += 1
                print(f"the companion stopped (exit code {rc}): starting it again in {delay:g} s", flush=True)
                time.sleep(delay)
                continue
            return rc
    except KeyboardInterrupt:
        return 130


def exit_with_parent(on_exit: Callable[[], None] | None = None) -> None:
    """End this process with its parent (Windows does not end a stopped program's children: a
    stray worker would keep the companion's port, a stray build its containers). `on_exit` runs
    first, to stop what this process started."""
    ppid = os.getppid()

    def leave() -> None:
        if on_exit:
            try:
                on_exit()
            except Exception as e:  # noqa: BLE001 - leaving anyway
                print(f"while stopping: {e}", file=sys.stderr, flush=True)
        os._exit(0)

    if hostos.WINDOWS:
        k = hostos.kernel32()
        handle = k.OpenProcess(0x00100000, False, ppid)  # SYNCHRONIZE
        if not handle:
            return

        def wait():
            k.WaitForSingleObject(handle, 0xFFFFFFFF)  # INFINITE
            leave()
    else:
        def wait():
            while os.getppid() == ppid:
                time.sleep(2)
            leave()
    threading.Thread(target=wait, daemon=True).start()
