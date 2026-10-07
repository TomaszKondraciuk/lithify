"""Operations on one speaker: probes over HTTP, scripts over the service console."""
from __future__ import annotations

import http.client
import json
import plistlib
import socket
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import console, platforms
from .config import Speaker

ROOT = Path(__file__).resolve().parent.parent


def http_get(url: str, timeout: float = 5.0) -> bytes | None:
    """The body at `url` (a speaker's service, plain HTTP on the LAN); None when it does not answer."""
    if not url.startswith("http://"):
        raise ValueError(f"not an http:// address: {url}")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - http:// checked above
            return r.read()
    except (OSError, ValueError, http.client.HTTPException):
        return None


def http_json(url: str, timeout: float = 5.0) -> dict:
    raw = http_get(url, timeout)
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


class Device:
    def __init__(self, speaker: Speaker):
        self.s = speaker
        self.host = speaker.host
        self._platform: platforms.Platform | None = None

    # ── discovery ──
    def reachable(self) -> bool:
        try:
            socket.create_connection((self.host, 23), timeout=3).close()
            return True
        except OSError:
            return False

    def platform(self) -> platforms.Platform:
        if self._platform:
            return self._platform
        if self.s.platform != "auto":
            self._platform = platforms.PLATFORMS[self.s.platform]
            return self._platform
        # A console that cannot be reached is that error, not "unsupported".
        with console.Console(self.host) as c:
            build_prop = c.exec("cat /system/build.prop", wait=15, idle=5)
        found = platforms.detect(build_prop, banner_ok=True)
        if not found:
            raise RuntimeError(
                f"{self.host}: not a supported Lithe Audio platform (no LS9 service console). "
                "LS10 models (WiFi Speaker V3, PRO 2, iO1) are not supported yet - see docs/platforms.md")
        self._platform = found
        return found

    def info(self) -> dict:
        """What the speaker reports about itself (no console needed). The services are asked at
        the same time: an offline speaker costs one timeout, not four."""
        out: dict = {"host": self.host}
        port = self.s.librespot.get("zeroconf_port", 4070)
        with ThreadPoolExecutor(max_workers=4) as pool:
            raw_f = pool.submit(http_get, f"http://{self.host}:7000/info")
            esdk_f = pool.submit(http_json, f"http://{self.host}:9095/zc?action=getInfo")
            lr_f = pool.submit(http_json, f"http://{self.host}:{port}/?action=getInfo")
            cast_f = pool.submit(http_json, f"http://{self.host}:8008/setup/eureka_info")
            raw, esdk, lr, cast = raw_f.result(), esdk_f.result(), lr_f.result(), cast_f.result()
        if raw:
            try:
                p = plistlib.loads(raw)
                out.update(model=p.get("model"), manufacturer=p.get("manufacturer"),
                           firmware=p.get("firmwareRevision"), firmware_date=p.get("firmwareBuildDate"),
                           airplay=p.get("sdk"), speaker_name=p.get("name"))
            except (plistlib.InvalidFileException, ValueError):
                pass
        out["official_spotify"] = esdk.get("libraryVersion")
        # (librespot's zeroconf "activeUser" stays empty with a saved login; the agent reports that)
        out["librespot"] = {"version": lr.get("libraryVersion"), "name": lr.get("remoteName")} if lr else None
        out["cast"] = cast.get("cast_build_revision")
        return out

    # ── console helpers ──
    def exec(self, command: str, wait: float = 20.0) -> str:
        return console.exec_one(self.host, command, wait=wait)

    def can_download(self, url: str) -> tuple[bool, str]:
        """Can the speaker fetch `url` from this computer? A firewall here, a VPN or a guest network
        in between would let the install time out half-way, so this asks first: (True, "") or
        (False, what curl said)."""
        out = self.exec(f"curl -sS -m 8 -o /dev/null -w HTTP%{{http_code}} {url}", wait=25)
        return "HTTP200" in out, out.strip()[-300:]

    def script(self, name: str, args: str = "", wait: float = 120.0, idle: float = console.STALL) -> str:
        """Run device/<platform>/<name>.sh as root, with the platform's install paths set."""
        plat = self.platform()
        path = ROOT / "device" / plat.scripts / f"{name}.sh"
        env = f"LITHIFY_BASE={plat.base}\n" + "".join(f"LITHIFY_LEGACY={b}\n" for b in plat.legacy_bases[:1])
        return console.run(self.host, env + path.read_text(encoding="utf-8"), args, wait=wait, idle=idle)

    def shell(self, lines: list[str], wait: float = 60.0) -> str:
        return console.run(self.host, "\n".join(lines) + "\n", wait=wait)

    def read(self, path: str) -> str:
        return self.exec(f"cat {path}", wait=20)

    # ── lifecycle ──
    def stock_process_list(self) -> list[dict]:
        """The stock Cast service list (live file, or the backup when ours is installed)."""
        p = self.platform()
        live = self.read(p.process_list)
        try:
            for base in (p.base, *p.legacy_bases):
                if "lithify_" in live or "cc_librespot" in live:
                    backup = self.read(f"{base}/backup/process.json.orig")
                    if "[" in backup:
                        return platforms.strip_services(backup)
            return platforms.strip_services(live)
        except ValueError as e:
            raise RuntimeError(f"could not read the speaker's service list ({e}); nothing was changed - "
                               "try again") from None

    def install(self, base_url: str) -> str:
        out = self.script("install", base_url, wait=600)
        if "INSTALL_OK" not in out:
            raise RuntimeError("install failed on the speaker:\n" + out[-2000:])
        return out

    def persist(self) -> bool:
        out = self.script("persist", wait=60)
        if "PERSIST_CHANGED" in out:
            return True
        if "PERSIST_UNCHANGED" in out:
            return False
        raise RuntimeError("could not register the services:\n" + out[-2000:])

    def restart_services(self) -> str:
        return self.script("restart-services", wait=40)

    def rollback(self, mode: str, purge: bool = False) -> str:
        out = self.script("rollback", f"{mode} purge" if purge else mode, wait=120)
        if ("VERSION_ROLLED_BACK" if mode == "version" else "STOCK_RESTORED") not in out:
            raise RuntimeError(f"the rollback did not finish on the speaker:\n{out[-2000:]}")
        return out

    def reboot(self, wait_for_librespot: bool = True, timeout: float = 420) -> float:
        try:
            with console.Console(self.host) as c:
                c._send("exec reboot")
        except console.ConsoleError:
            pass
        start = time.time()
        time.sleep(25)
        port = self.s.librespot.get("zeroconf_port", 4070)
        while time.time() - start < timeout:
            ok = http_json(f"http://{self.host}:{port}/?action=getInfo") if wait_for_librespot else self.reachable()
            if ok:
                return time.time() - start
            time.sleep(5)
        part = "librespot does not answer" if wait_for_librespot and self.reachable() else "the speaker does not answer"
        raise RuntimeError(f"{part} {int(timeout)} s after the reboot")

    def wait_for_agent(self, timeout: float = 45) -> dict:
        """The agent's status once it answers again (after a restart); {} when it does not."""
        end = time.time() + timeout
        while time.time() < end:
            st = self.agent_status()
            if st:
                return st
            time.sleep(3)
        return {}

    def agent_status(self) -> dict:
        port = self.s.agent.get("ui_port", 8090)
        return http_json(f"http://{self.host}:{port}/api/status", timeout=8)
