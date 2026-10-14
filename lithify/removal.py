"""Removing Lithify from this computer (`lithify uninstall --computer`): the helper's service, the
Windows firewall rules, the build caches in Docker, the configuration, downloads and logs.

Lithify's own program files (the program, its private Python, on Windows also the `lithify`
command) are in use while it runs: they are named at the end, to be deleted once the command has
finished. Python, Docker and git stay: other programs may use them.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import bundle, hostos, service
from .device import ROOT


@dataclass
class Part:
    """One thing to remove: what it is, in words (`key` and `detail` for pages in other languages);
    its files and folders are deleted, and `remove` does the rest (it returns what went wrong, ""
    when nothing did)."""
    what: str
    paths: list[Path] = field(default_factory=list)
    remove: Callable[[], str] | None = None
    key: str = ""
    detail: str = ""


# The kinds of parts there are (the wizard has a text for each).
PART_KEYS = ("helper", "docker", "firewall", "path", "runonce", "temp_installer", "config", "cache", "logs", "command")


@dataclass
class Plan:
    parts: list[Part]
    later: list[Path]   # Lithify's program files: deleted by hand once the command has finished
    notes: list[str]    # what stays, and why


def _home(env) -> Path:
    return Path(env.get("USERPROFILE") or env.get("HOME") or Path.home())


def program_places(env=None, windows: bool = hostos.WINDOWS) -> dict[str, Path]:
    """Where the installers put Lithify, its private Python and the `lithify` command: their
    defaults, or what LITHIFY_HOME, LITHIFY_BIN and LITHIFY_TOOLS chose (installer/install.sh,
    installer/install.ps1)."""
    env = os.environ if env is None else env
    if windows:
        base = Path(env.get("LOCALAPPDATA") or _home(env) / "AppData" / "Local") / "lithify"
        return {"base": base, "app": Path(env.get("LITHIFY_HOME") or base / "app"), "bin": base / "bin",
                "tools": base / "tools"}
    home = _home(env)
    bin_dir = Path(env.get("LITHIFY_BIN") or home / ".local" / "bin")
    return {"app": Path(env.get("LITHIFY_HOME") or home / ".local" / "share" / "lithify"), "bin": bin_dir,
            "tools": Path(env.get("LITHIFY_TOOLS") or home / ".local" / "share" / "lithify-tools"),
            "command": bin_dir / "lithify"}


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def made_by_installer(command: Path, root: Path) -> bool:
    """Is `command` the `lithify` command an installer wrote for Lithify in `root`? (A command of
    another copy of Lithify, or someone's own script of that name, stays.)"""
    try:
        if command.is_symlink():  # (an earlier installer linked bin/lithify)
            return _same(command, root / "bin" / "lithify")
        text = command.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return str(root / "bin" / "lithify").casefold() in text.casefold()


def deletable(path: Path, env=None) -> bool:
    """A folder or file of Lithify's (its name says so), never the home folder, one above it, or a
    drive: a variable such as LITHIFY_CACHE set to somewhere else must not empty that."""
    env = os.environ if env is None else env
    p, home = Path(os.path.realpath(path)), Path(os.path.realpath(_home(env)))
    if p == Path(p.anchor) or p == home or p in home.parents:
        return False
    return any("lithify" in part.casefold() for part in p.parts[-2:])


def without_entry(path_value: str, entry: str) -> str | None:
    """A Windows PATH value without `entry` (compared as Windows does: case and a closing
    backslash aside); None when `entry` is not in it. The other entries stay as they were."""
    def norm(p: str) -> str:
        return p.strip().replace("/", "\\").rstrip("\\").casefold()

    parts = path_value.split(";")
    keep = [p for p in parts if not p.strip() or norm(p) != norm(entry)]
    return None if len(keep) == len(parts) else ";".join(keep)


def _registry_parts(entry: Path) -> list[Part]:
    """Windows: what the installer put into this user's registry."""
    return [p for p in (_user_path_part(entry), _runonce_part()) if p]


def _runonce_part() -> Part | None:
    """Windows: the installer's step for after a restart, should one still wait (RunOnce)."""
    import winreg  # (Windows only)
    key = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            winreg.QueryValueEx(k, "Lithify")
    except OSError:
        return None

    def remove() -> str:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, "Lithify")
        except OSError as e:
            return f"the installer's step for after a restart stays (RunOnce \"Lithify\"): {e}"
        return ""

    return Part("the installer's step for after a restart (it would start the installer again)", remove=remove,
                key="runonce")


def _temp_installer(env) -> Part | None:
    """Windows: the copy of the installer that get.ps1 and Lithify-Windows.cmd download into the
    temporary folder (after a restart the installer may go on from it, so it stays until now)."""
    temp = env.get("TEMP") or env.get("TMP")
    f = Path(temp) / "lithify-install.ps1" if temp else None
    if not (f and f.is_file()):
        return None
    return Part(f"the installer's copy in the temporary folder: {f}", paths=[f], key="temp_installer", detail=str(f))


def _user_path_part(entry: Path) -> Part | None:
    """Windows: `entry` (the folder of the `lithify` command) out of this user's PATH."""
    import winreg  # (Windows only)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            value, _kind = winreg.QueryValueEx(k, "Path")
    except OSError:
        return None
    if not isinstance(value, str) or without_entry(value, str(entry)) is None:
        return None

    def remove() -> str:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                                winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as k:
                value, kind = winreg.QueryValueEx(k, "Path")
                new = without_entry(value, str(entry))
                if new is not None:
                    winreg.SetValueEx(k, "Path", 0, kind, new)
        except OSError as e:
            return f"your PATH still names {entry}: {e}"
        return ""

    return Part(f"{entry} in your PATH (new windows then no longer know `lithify`)", remove=remove, key="path",
                detail=str(entry))


def _docker(args: list[str], timeout: float = 120) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL, timeout=timeout, **hostos.no_window())
    except (OSError, subprocess.SubprocessError):
        return None


def docker_objects() -> dict[str, list[str]] | None:
    """Lithify's build containers, volumes and builder images in Docker, found by their label and
    names only (nothing else of Docker's is touched); None when Docker does not answer."""
    vols = _docker(["volume", "ls", "--format", "{{.Name}}"])
    if vols is None or vols.returncode:
        return None
    imgs = _docker(["images", "--format", "{{.Repository}}:{{.Tag}}", "lithify-builder"])
    ctrs = _docker(["ps", "-a", "-q", "--filter", f"label={bundle.BUILD_LABEL}"])
    images = imgs.stdout.split() if imgs and imgs.returncode == 0 else []
    return {
        "containers": ctrs.stdout.split() if ctrs and ctrs.returncode == 0 else [],
        "volumes": [v for v in vols.stdout.split()
                    if v == bundle.CARGO_VOLUME or v.startswith(bundle.TARGET_VOLUME_PREFIX)],
        "images": [i for i in images if i.startswith("lithify-builder:") and not i.endswith(":<none>")],
    }


def builder_base(root: Path = ROOT) -> str:
    """The image the builder image starts from (versions.toml), when Docker has it; "" otherwise.
    Not Lithify's by its name, so it is only named, never removed."""
    try:
        base = bundle.load_pins(root)["rust"]["builder_base"]
    except (OSError, ValueError, KeyError):
        return ""
    r = _docker(["image", "inspect", "--format", "{{.Id}}", base])
    return base if r is not None and r.returncode == 0 else ""


def _docker_part(found: dict[str, list[str]]) -> Part | None:
    if not any(found.values()):
        return None
    names = [*found["volumes"], *found["images"]]
    if found["containers"]:
        names.append(f"{len(found['containers'])} build container(s)")

    def remove() -> str:
        problems = []
        for kind, cmd in (("containers", ["rm", "-f"]), ("volumes", ["volume", "rm"]), ("images", ["image", "rm"])):
            if not found[kind]:
                continue
            r = _docker([*cmd, *found[kind]], timeout=600)
            if r is None or r.returncode:
                problems.append((r.stderr or r.stdout).strip() if r else f"docker {' '.join(cmd)} did not answer")
        return "; ".join(problems)

    return Part(f"the build caches in Docker: {', '.join(names)}", remove=remove, key="docker", detail=", ".join(names))


def plan(env=None, windows: bool = hostos.WINDOWS, root: Path = ROOT) -> Plan:
    """What there is to remove from this computer."""
    env = os.environ if env is None else env
    places = program_places(env, windows)
    parts: list[Part] = []
    notes: list[str] = []
    how = service.installed()
    if how:
        parts.append(Part(f"the helper that keeps the speakers updatable ({how})",
                          remove=lambda: service.uninstall() or "", key="helper", detail=how))
    if shutil.which("docker"):
        found = docker_objects()
        if found is None:
            notes.append("Docker does not answer: Lithify's build caches in it stay (volumes lithify-cargo and "
                         "lithify-target-*, images lithify-builder). Start Docker and run this again, or remove "
                         "them in Docker Desktop.")
        else:
            if part := _docker_part(found):
                parts.append(part)
            if base := builder_base(root):
                notes.append(f"Docker keeps {base}, the image Lithify's builder starts from: `docker image rm "
                             f"{base}` removes it, if nothing else of yours uses it.")
    if windows and hostos.firewall_rules_exist():
        parts.append(Part("the Windows firewall rules \"Lithify\" (Windows asks for permission)",
                          remove=lambda: "" if hostos.remove_firewall_rules() else
                          "the firewall rules \"Lithify\" are still there: remove them in Windows Defender "
                          "Firewall > Advanced settings > Inbound Rules", key="firewall"))
    if windows:
        parts += _registry_parts(places["bin"])
        if temp := _temp_installer(env):
            parts.append(temp)
    folders = (("config", "the configuration (speakers, the web page's PIN)", hostos.config_dir(env, windows)),
               ("cache", "downloads, builds and the helper's files", hostos.cache_dir(env, windows)),
               ("logs", "the helper's logs", hostos.log_dir(env, windows)))
    for key, what, path in folders:
        if not path.exists() or any(path.is_relative_to(p) for q in parts for p in q.paths):
            continue
        if deletable(path, env):
            parts.append(Part(f"{what}: {path}", paths=[path], key=key, detail=str(path)))
        else:
            notes.append(f"{path} stays: its name does not say it is Lithify's ({what}). Delete it yourself.")
    installed_copy = _same(root, places["app"])
    later = []
    if windows:
        if places["base"].exists():
            later.append(places["base"])
    else:
        command = places["command"]
        if command.exists() and made_by_installer(command, root):
            parts.append(Part(f"the lithify command: {command}", paths=[command], key="command", detail=str(command)))
        app, tools = places["app"], places["tools"]
        if app.exists() and (app / "bin" / "lithify").exists() and deletable(app, env):
            later.append(app)
        if tools.exists() and deletable(tools, env):
            later.append(tools)
    if not installed_copy:
        notes.append(f"Lithify itself stays in {root}: a copy of your own, not one an installer made. Delete "
                     "that folder when you no longer need it.")
    if not windows and (shutil.which("ufw") or shutil.which("firewall-cmd")):
        notes.append("If the installer opened TCP 8095 and 18096-18099 in your firewall, close them again: "
                     "`sudo ufw status numbered`, then `sudo ufw delete <number>` for the rule marked Lithify "
                     "(ufw), or `sudo firewall-cmd --permanent --remove-port=8095/tcp "
                     "--remove-port=18096-18099/tcp && sudo firewall-cmd --reload` (firewalld).")
    return Plan(parts, later, notes)


def delete_command(paths: list[Path], windows: bool = hostos.WINDOWS) -> str:
    """The command that deletes `paths` (folders and files), for this system's terminal."""
    if windows:  # (cmd's rmdir: in PowerShell, rmdir is another command)
        return " & ".join(f'cmd /c rmdir /s /q "{p}"' for p in paths)
    return "rm -rf -- " + " ".join(shlex.quote(str(p)) for p in paths)


def remove(parts: list[Part]) -> list[str]:
    """Remove `parts` in their order (the helper first: it may be using the files); what went wrong."""
    problems = []
    for part in parts:
        if part.remove and (why := part.remove()):
            problems.append(why)
        for path in part.paths:
            try:
                if path.is_dir() and not path.is_symlink():
                    hostos.rmtree(path, quiet=False)
                else:
                    path.unlink(missing_ok=True)
            except OSError as e:
                problems.append(str(e))
    return problems
