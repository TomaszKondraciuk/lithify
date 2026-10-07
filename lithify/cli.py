"""lithify - command line interface."""
from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import tomllib
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__, bundle, companion, config, discovery, hostos, platforms, service, updates
from .device import ROOT, Device, http_get

USER_CONFIG = hostos.config_dir() / "config.toml"


def say(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def ask(question: str, default: str = "") -> str:
    if not sys.stdin.isatty():
        return default
    a = input(f"{question}{f' [{default}]' if default else ''}: ").strip()
    return a or default


def yes(question: str, assume: bool | None) -> bool:
    if assume is not None:
        return assume
    return ask(f"{question} (y/N)").lower() in ("y", "yes", "t", "tak")


# ── commands ────────────────────────────────────────────────────────────────

def slug(name: str) -> str:
    """A CLI id from a speaker name: "Łazienka" -> "lazienka"."""
    name = name.translate(str.maketrans({"ł": "l", "Ł": "L", "ß": "ss", "æ": "ae", "ø": "o", "đ": "d"}))
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:32].strip("-")
    return s if config.ID_RE.fullmatch(s) else "speaker"


def pick_speaker() -> str:
    say("looking for Lithe Audio speakers on this network…")
    found = discovery.discover()
    for i, s in enumerate(found, 1):
        print(f"  {i}. {s['host']:<15} {describe(s)}")
    if not found:
        return ask("none found; speaker IP address")
    if len(found) == 1:
        return ask("speaker IP address", found[0]["host"])
    choice = ask(f"which one (1-{len(found)} or an IP address)", "1")
    return found[int(choice) - 1]["host"] if choice.isdigit() and 0 < int(choice) <= len(found) else choice


def write_config(target: Path, host: str, name: str | None = None, sid: str | None = None,
                 interactive: bool = True, replace: bool = False) -> None:
    """config.toml for one speaker: its address and the values of its first install. A file that
    is there already is never overwritten: the speaker is added to it as a [[speakers]] block
    (`replace`, from `lithify setup --force`, starts it anew). Either way the file is replaced
    whole, and the previous one is kept as config.toml.<date>-<time>.bak (one per change, so a
    second `setup --force` never loses the original)."""
    if not config.HOST_RE.fullmatch(host):
        raise RuntimeError(f"{host!r} is not an IP address or host name")
    probe = Device(config.Speaker("probe", host, "probe"))
    if not probe.reachable():
        raise RuntimeError(f"{host} does not answer on the service console (TCP 23); is it an LS9 Lithe speaker?")
    plat = probe.platform()
    info = probe.info()
    speaker_name = info.get("speaker_name") or "Speaker"
    default = f"{speaker_name} (librespot)"
    name = name or (ask("Name in Spotify Connect", default) if interactive else default)
    sid = sid or (ask("Short id for the CLI", slug(speaker_name)) if interactive else slug(speaker_name))
    if not config.ID_RE.fullmatch(sid):
        raise RuntimeError("the id may use lowercase letters, digits and '-'")
    # A JSON string is a valid TOML basic string (quotes, backslashes, any character).
    quoted = json.dumps(name, ensure_ascii=False)
    if target.exists() and not replace:
        kept = hostos.write_atomic(target, _with_speaker(target, sid, host, quoted), backup=True, newline=None)
        done = f"added speaker {sid} to {target}"
    else:
        kept = hostos.write_atomic(target, config.EXAMPLE.format(id=sid, host=host, name=quoted), backup=True,
                                   newline=None)
        done = f"wrote {target}"
    if kept:
        done += f" (the previous file is kept as {kept.name})"
    say(f"{done}: {info.get('manufacturer') or '?'} {info.get('model') or '?'} at {host} "
        f"(platform {plat.key}), Spotify name \"{name}\"")


def _with_speaker(target: Path, sid: str, host: str, quoted_name: str) -> str:
    """The text of config.toml `target` with one more [[speakers]] block; ConfigError (and the
    file left as it is) when that cannot be done cleanly."""
    text = target.read_text(encoding="utf-8")
    try:
        before = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise config.ConfigError(f"{target} is not valid TOML ({e}); it was left as it is - fix it, or add the "
                                 "speaker to it by hand") from None
    speakers = before.get("speakers", [])
    known = [s for s in speakers if isinstance(s, dict)] if isinstance(speakers, list) else []
    for s in known:
        if s.get("id") == sid or s.get("host") == host:
            raise config.ConfigError(f"{target} already has a speaker {s.get('id')!r} at {s.get('host')}; it was "
                                     "left as it is")
    block = f'[[speakers]]\nid = "{sid}"\nhost = "{host}"\nname = {quoted_name}  # name shown in Spotify Connect\n'
    new = (text.rstrip("\n") + "\n\n" if text.strip() else "") + block
    try:
        after = tomllib.loads(new)
    except tomllib.TOMLDecodeError:
        after = {}
    if not isinstance(after.get("speakers"), list) or len(after["speakers"]) != len(known) + 1:
        raise config.ConfigError(f"cannot add a [[speakers]] block to {target} (its `speakers` is written another "
                                 "way); it was left as it is - add the speaker to it by hand")
    return new


def auto_setup(a, cfg: config.Config | None = None) -> config.Config:
    """No speaker configured yet: find the speaker and add it with defaults (nothing to answer
    when there is exactly one speaker; settings can be changed later on its web page). It goes
    into the configuration that was read (or the user's), never replacing what is in it."""
    target = Path(a.config).expanduser() if a.config else (cfg.path if cfg and cfg.path else USER_CONFIG)
    host = getattr(a, "host", None)
    if not host:
        say("looking for Lithe Audio speakers on this network…")
        found = discovery.discover()
        if len(found) == 1:
            host = found[0]["host"]
            say(f"found {found[0]['name']} ({found[0]['model']}) at {host}")
        elif found and sys.stdin.isatty():
            host = pick_from(found)
        elif found:
            raise config.ConfigError("several speakers found: "
                                     + ", ".join(f["host"] for f in found) + " - run `lithify install --host IP`")
        else:
            raise config.ConfigError("no Lithe Audio speaker found on this network - run `lithify install --host IP`")
    # (a name for Spotify from the environment, as config.py reads it: `lithify wizard` sets it)
    write_config(target, host, name=os.environ.get("LITHIFY_NAME") or None, interactive=False)
    return config.load(str(target))


def describe(s: dict) -> str:
    """A found speaker in one line: its name, model and official Spotify (when that answered)."""
    esdk = f", Spotify eSDK {s['spotify_esdk']}" if s.get("spotify_esdk") else ""
    return f"{s['name']}  ({s['model']}{esdk})"


def pick_from(found: list[dict]) -> str:
    for i, s in enumerate(found, 1):
        print(f"  {i}. {s['host']:<15} {s['name']}  ({s['model']})")
    choice = ask(f"which one (1-{len(found)} or an IP address)", "1")
    return found[int(choice) - 1]["host"] if choice.isdigit() and 0 < int(choice) <= len(found) else choice


def cmd_setup(a, _cfg_unused) -> int:
    target = Path(a.config or USER_CONFIG).expanduser()
    if target.exists() and not a.force:
        print(f"{target} already exists (use --force to start it anew - the old one is kept as "
              f"{target.name}.<date>-<time>.bak - or edit it)")
        return 1
    host = a.host or pick_speaker()
    if not host:
        print("a speaker IP address is required")
        return 2
    write_config(target, host, a.name, a.id, replace=a.force)
    print("next: lithify install")
    return 0


def cmd_discover(_a, _cfg) -> int:
    found = discovery.discover()
    if not found:
        print("no Lithe Audio speaker answered on this network")
        return 1
    for s in found:
        print(f"{s['host']:<15} {describe(s)}")
    return 0


def cmd_fetch(a, _cfg) -> int:
    url = a.url or bundle.load_pins().get("release", {}).get("url", "")
    if not url:
        print("no release URL: pass --url, or set [release] url in versions.toml")
        return 2
    d = bundle.fetch(url)
    print((d / "VERSIONS").read_text(encoding="utf-8"))
    return 0


def cmd_detect(a, cfg) -> int:
    s = cfg.speaker(a.speaker) if not a.host else config.Speaker("probe", a.host, "probe")
    d = Device(s)
    info = d.info()
    try:
        plat = d.platform()
        support = f"{plat.key} (supported)"
    except RuntimeError as e:
        support = str(e)
    print(json.dumps({**info, "platform": support}, indent=2, ensure_ascii=False))
    return 0


def _interrupt(signum, _frame):
    signal.signal(signum, signal.SIG_IGN)  # once: the way out is not interrupted again
    raise KeyboardInterrupt


def stop_like_ctrl_c() -> None:
    """SIGTERM (the companion stopping a build that hangs, a service manager) and SIGHUP (a closed
    terminal) end a command the way Ctrl-C does: an exception, so a build stops its containers and
    commands on the way out (they run in process groups of their own, which these signals miss).
    A signal set to be ignored (`nohup`) stays ignored."""
    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is not None and signal.getsignal(sig) == signal.SIG_DFL:
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, _interrupt)


def cmd_build(a, _cfg) -> int:
    if a.exit_with_parent:  # started by the companion: never outlive it
        service.exit_with_parent(on_exit=bundle.stop_all)
    if a.latest and not a.no_self_update and updates.self_update(ROOT):
        say("Lithify updated itself: building with the new version")
        # (a child process, not exec: Windows has no exec, and its "execv" detaches)
        flags = ["--latest", "--no-self-update", *(["--force"] if a.force else []),
                 *(["--exit-with-parent"] if a.exit_with_parent else [])]
        return service.call([sys.executable, str(ROOT / "bin" / "lithify"), "build", *flags])
    bundle.build(force=a.force, latest=a.latest)
    print((bundle.CACHE / "bundle" / "VERSIONS").read_text(encoding="utf-8"))
    return 0


def get_bundle(force_build: bool = False) -> None:
    """The bundle to install: a published one when versions.toml names a release (no Docker needed),
    otherwise - or when it cannot be downloaded - built here."""
    url = bundle.load_pins().get("release", {}).get("url", "")
    if url and not force_build:
        try:
            bundle.fetch(url)
            return
        except (bundle.BuildError, OSError, ValueError) as e:
            say(f"the published bundle could not be downloaded ({e}): building it on this computer instead")
    bundle.build()


def cmd_install(a, cfg) -> int:
    host = getattr(a, "host", None)
    # `--host` names the speaker: one not configured yet is added first (never another one installed).
    if not a.speaker and (not cfg.speakers or (host and all(sp.host != host for sp in cfg.speakers))):
        cfg = auto_setup(a, cfg)
    s = cfg.speaker(a.speaker or host)
    d = Device(s)
    plat = d.platform()
    b = bundle.CACHE / "bundle"
    bundle.recover_bundle()  # (a build the computer stopped while it replaced the bundle)
    if not (b / "VERSIONS").exists() or a.build:
        get_bundle(force_build=a.build)
    say(f"installing {bundle.read_versions(b).get('librespot')} on {s.id} ({s.host}, {plat.key})")
    stock = d.stock_process_list()
    legacy = [base for base in plat.legacy_bases if "VERSIONS" in d.exec(f"ls {base}")]
    # The speaker keeps its own settings; these are sent only on purpose.
    push = sorted(config.OPTIONS) if a.settings else sorted(s.env_keys)
    if push:
        say(f"sending settings to the speaker: {', '.join(push) if not a.settings else 'all from config.toml'}")
    if not hostos.ensure_firewall_rule():
        print(f"warning: the Windows firewall may keep the speaker from downloading the update; allow "
              f"TCP {hostos.FIREWALL_PORTS} from your local network", file=sys.stderr)
    with tempfile.TemporaryDirectory(prefix="lithify-stage-") as tmp:
        with bundle.bundle_lock(bundle.CACHE):
            staged = bundle.stage(b, s, plat, stock, companion.public_url(cfg), Path(tmp) / s.id, push)
        with bundle.serve(staged, s.host) as url:
            reachable, said = d.can_download(f"{url}/VERSIONS")
            if not reachable:
                raise RuntimeError(f"the speaker cannot download from this computer ({url}: {said or 'no answer'}). "
                                   + hostos.firewall_advice(urllib.parse.urlsplit(url).hostname or ""))
            out = d.install(url)
    print("\n".join(line for line in out.splitlines() if not line.startswith("ok ")))
    changed = d.persist()
    if changed:
        say("the Cast service list changed: a reboot activates it (~1 min without sound)")
        if yes("reboot the speaker now?", a.reboot):
            secs = d.reboot()
            say(f"speaker back after {int(secs)} s")
            for base in legacy:
                d.shell([f"rm -rf {base}", f"echo removed {base}"])
                say(f"removed the old install in {base} (its Spotify login and backup were migrated)")
        else:
            print("run `lithify reboot` (or power-cycle the speaker) to activate")
            return 0
    else:
        say("restarting librespot and the agent")
        d.restart_services()
    if s.agent.get("ui", True) and not d.wait_for_agent():
        print(f"warning: the agent's web page does not answer on port {s.agent['ui_port']} after the install; "
              "if the speaker misbehaves, `lithify rollback` returns to the previous version", file=sys.stderr)
    return cmd_status(a, cfg)


def cmd_status(a, cfg) -> int:
    s = cfg.speaker(a.speaker)
    d = Device(s)
    info = d.info()
    agent = d.agent_status()
    if getattr(a, "json", False):
        print(json.dumps({"info": info, "agent": agent}, indent=2, ensure_ascii=False))
        return 0
    lr = info.get("librespot") or {}
    saved = (agent.get("librespot") or {}).get("credentials_saved") if agent else None
    login = {True: "account saved", False: "no account linked yet", None: ""}[saved]
    print(f"Speaker {s.id} ({s.host})  {info.get('manufacturer') or ''} {info.get('model') or ''}".rstrip())
    print(f"  firmware   Libre {info.get('firmware')} ({info.get('firmware_date')}), Cast {info.get('cast')}, "
          f"AirPlay {info.get('airplay')}")
    print(f"  spotify    official {info.get('official_spotify')} | librespot {lr.get('version')} "
          f"\"{lr.get('name')}\" {login}".rstrip())
    if agent:
        for k, v in agent.get("versions", {}).items():
            print(f"  bundle     {k}={v}")
        net = agent.get("network", {})
        print(f"  wifi       {net.get('ssid')} {net.get('freq_mhz')} MHz {net.get('signal_dbm')} dBm")
        print(f"  fail-fast  {', '.join(net.get('fastfail_routes', [])) or 'none'}")
        print(f"  web page   http://{s.host}:{s.agent['ui_port']}/")
    else:
        print("  agent      not answering (not installed yet, or the web page is disabled)")
    return 0


def cmd_logs(a, cfg) -> int:
    s = cfg.speaker(a.speaker)
    n = int(a.n)
    # The agent keeps librespot's and its own lines (the speaker's logcat buffer covers minutes).
    text = http_get(f"http://{s.host}:{s.agent['ui_port']}/api/logs?n={n}", timeout=10)
    if text:
        print(text.decode("utf-8", "replace"))
        return 0
    d = Device(s)
    raw = d.exec("logcat -d -v time -s lithify-agent:*", wait=40)
    print("\n".join(raw.splitlines()[-n:]) or d.exec(f"tail -n {n} /tmp/lithify-agent.log", wait=20))
    return 0


def cmd_rollback(a, cfg) -> int:
    d = Device(cfg.speaker(a.speaker))
    print(d.rollback(a.mode, a.purge))
    if a.mode == "version":
        d.restart_services()
    else:
        print("reboot the speaker to finish: lithify reboot")
    return 0


def cmd_uninstall(a, cfg) -> int:
    d = Device(cfg.speaker(a.speaker))
    print(d.rollback("stock", purge=True))
    if yes("reboot the speaker now to finish?", a.reboot):
        d.reboot(wait_for_librespot=False)
        say("done - the speaker runs its stock software again")
    return 0


def cmd_reboot(a, cfg) -> int:
    say(f"speaker back after {int(Device(cfg.speaker(a.speaker)).reboot())} s")
    return 0


def agent_url(s: config.Speaker, path: str) -> str:
    return f"http://{s.host}:{s.agent['ui_port']}{path}"


def cmd_settings(a, cfg) -> int:
    s = cfg.speaker(a.speaker)
    if a.action == "reset-pin":
        out = Device(s).shell([
            f"F={platforms.LS9.base}/settings/settings.conf",
            "[ -f $F ] || { echo no settings on this speaker; exit 1; }",
            'busybox sed -i -e "s/^ui_pin=.*/ui_pin=/" $F',
            "for p in $(pidof lithify-agent); do kill $p; done",
            "echo PIN_RESET"])
        print("PIN removed; the web page restarts" if "PIN_RESET" in out else out)
        return 0 if "PIN_RESET" in out else 1
    if a.action == "set":
        pairs = [tuple(p.split("=", 1)) for p in a.values]
        if not pairs or any(len(p) != 2 for p in pairs):
            print("usage: lithify settings set key=value [key=value ...]")
            return 2
        body = urllib.parse.urlencode(pairs).encode()
        headers = {"X-Lithify": "1", "Content-Type": "application/x-www-form-urlencoded"}
        pin = a.pin or os.environ.get("LITHIFY_PIN")
        if pin:
            headers["X-Lithify-Pin"] = pin
        url = agent_url(s, "/api/settings")
        if not url.startswith("http://"):  # (the speaker's page: plain HTTP on the LAN)
            raise ValueError(f"not an http:// address: {url}")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310 - http:// checked above
        try:
            with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 - http:// checked above
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            res = json.loads(e.read() or b"{}")
            print(f"error: {res.get('error', e.reason)}")
            for k, msg in (res.get("errors") or {}).items():
                print(f"  {k}: {msg}")
            return 1
        changed = res.get("changed", [])
        print(f"changed: {', '.join(changed)}; restarting {', '.join(res.get('restart', []))}" if changed
              else "nothing changed")
        return 0
    data = http_get(agent_url(s, "/api/settings"), timeout=10)
    if not data:
        print(f"the speaker's web page does not answer at {agent_url(s, '/')}")
        return 1
    info = json.loads(data)
    if not info.get("available"):
        print("this speaker gets its settings from the computer until its next `lithify update`")
        return 1
    print(f"settings of {s.id} (changed {info.get('updated') or '?'} by {info.get('by') or '?'})")
    for o in info["schema"]:
        if o["group"] == "hidden":
            continue
        v = info["values"].get(o["key"], "")
        if o["key"] == "ui_pin":
            v = "(set)" if info.get("pin_set") else ""
        mark = "" if v == o["default"] or (o["key"] == "ui_pin" and not v) else "   *"
        print(f"  {o['key']:<26} {v}{mark}")
    print("(* = not the default; change with: lithify settings set key=value)")
    return 0


def cmd_check_updates(_a, _cfg) -> int:
    print(updates.format_report(updates.report()))
    return 0


def cmd_serve(a, cfg) -> int:
    conf = cfg.path.resolve() if cfg.path else None
    if a.uninstall_service:
        service.uninstall()
        say("companion service removed")
        return 0
    if a.install_service:
        hostos.ensure_firewall_rule()
        how = service.install(conf or USER_CONFIG)
        say(f"companion running on {companion.public_url(cfg)}: {how}")
        return 0
    if a.worker:
        service.exit_with_parent(on_exit=companion.stop_build)  # (a build must not outlive the worker)
        return companion.serve_forever(cfg)
    # The supervisor: it starts the companion again after Lithify updated itself.
    worker = [sys.executable, str(ROOT / "bin" / "lithify"), *(["--config", str(conf)] if conf else []),
              "serve", "--worker"]
    return service.supervise(worker, Path(a.log) if a.log else None)


def cmd_ui(a, cfg) -> int:
    s = cfg.speaker(a.speaker)
    print(f"http://{s.host}:{s.agent['ui_port']}/")
    return 0


def cmd_wizard(a, _cfg) -> int:
    from . import wizard  # (the setup page and its server: for this command only)
    return wizard.run(lang=a.lang, open_browser=not a.no_browser, port=a.port, config_path=a.config)


def cmd_sh(a, cfg) -> int:
    print(Device(cfg.speaker(a.speaker)).shell(a.lines, wait=a.wait))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lithify", description="Spotify Connect (librespot) for Lithe Audio speakers")
    p.add_argument("--version", action="version", version=f"lithify {__version__}")
    p.add_argument("--config", help="config.toml (default: $LITHIFY_CONFIG, ./config.toml, ~/.config/lithify/)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, speaker=True):
        sp = sub.add_parser(name, help=help_)
        if speaker:
            sp.add_argument("--speaker", help="speaker id or host from config.toml")
        sp.set_defaults(fn=fn)
        return sp

    sp = add("setup", cmd_setup, "create config.toml for a speaker", speaker=False)
    sp.add_argument("--host")
    sp.add_argument("--name")
    sp.add_argument("--id")
    sp.add_argument("--force", action="store_true")
    add("discover", cmd_discover, "list the Lithe Audio speakers on this network", speaker=False)
    add("detect", cmd_detect, "show what a speaker is and whether it is supported").add_argument("--host")
    sp = add("build", cmd_build, "build the bundle with Docker", speaker=False)
    sp.add_argument("--force", action="store_true", help="recompile even when nothing changed")
    sp.add_argument("--latest", action="store_true", help="first move to the newest stable librespot, alsa-lib, Rust")
    sp.add_argument("--no-self-update", action="store_true", help=argparse.SUPPRESS)
    sp.add_argument("--exit-with-parent", action="store_true", help=argparse.SUPPRESS)
    add("fetch", cmd_fetch, "download a prebuilt bundle instead of building", speaker=False).add_argument("--url")
    for name in ("install", "update"):
        sp = add(name, cmd_install, "install or update Lithify on a speaker (finds it the first time)")
        sp.add_argument("--host", help="the speaker's address, when it is not found automatically")
        sp.add_argument("--build", action="store_true", help="rebuild the bundle first")
        sp.add_argument("--settings", action="store_true",
                        help="send config.toml's settings, replacing those changed on the speaker")
        g = sp.add_mutually_exclusive_group()
        g.add_argument("--reboot", dest="reboot", action="store_true", default=None)
        g.add_argument("--no-reboot", dest="reboot", action="store_false")
    add("status", cmd_status, "versions and health").add_argument("--json", action="store_true")
    add("logs", cmd_logs, "agent and librespot logs").add_argument("-n", default=40)
    sp = add("rollback", cmd_rollback, "previous version, or the stock service list")
    sp.add_argument("mode", nargs="?", default="version", choices=["version", "stock"])
    sp.add_argument("--purge", action="store_true")
    sp = add("uninstall", cmd_uninstall, "remove Lithify and restore the stock speaker")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--reboot", dest="reboot", action="store_true", default=None)
    g.add_argument("--no-reboot", dest="reboot", action="store_false")
    add("reboot", cmd_reboot, "reboot the speaker and wait for it")
    add("check-updates", cmd_check_updates, "newer upstream versions than versions.toml?", speaker=False)
    sp = add("serve", cmd_serve, "companion for the speaker web page (builds, serves updates)", speaker=False)
    sp.add_argument("--install-service", action="store_true",
                    help="start it with the computer (systemd user service, launchd agent or scheduled task)")
    sp.add_argument("--uninstall-service", action="store_true")
    sp.add_argument("--log", help="write its output to this file (for services without a journal)")
    sp.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    sp = add("settings", cmd_settings, "show or change the speaker's settings")
    sp.add_argument("action", nargs="?", default="show", choices=["show", "set", "reset-pin"])
    sp.add_argument("values", nargs="*", metavar="key=value")
    sp.add_argument("--pin", help="the web page PIN, when one is set (or $LITHIFY_PIN)")
    add("ui", cmd_ui, "print the speaker web page address")
    sp = add("wizard", cmd_wizard, "set up a speaker step by step in your web browser", speaker=False)
    sp.add_argument("--no-browser", action="store_true", help="open no browser; print the address to open")
    sp.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: a free one)")
    sp.add_argument("--lang", choices=["pl", "en"], help="the page's language (default: the browser's)")
    sp = add("sh", cmd_sh, "run commands on the speaker as root (mksh)")
    sp.add_argument("lines", nargs="+")
    sp.add_argument("--wait", type=float, default=60)

    a = p.parse_args(argv)
    if a.cmd != "serve":  # (the companion and its supervisor stop through their own ways)
        stop_like_ctrl_c()
    # A Windows console or pipe may not take every character (speaker names): never crash on one.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            with contextlib.suppress(ValueError):
                stream.reconfigure(errors="replace")
    try:
        # (setup writes the configuration, and the wizard shows a broken one on its page)
        cfg = config.load(a.config) if a.cmd not in ("setup", "wizard") else None
        return a.fn(a, cfg)
    except (config.ConfigError, bundle.BuildError, RuntimeError, OSError, ValueError, subprocess.SubprocessError,
            http.client.HTTPException) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
