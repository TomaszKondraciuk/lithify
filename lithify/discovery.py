"""Find Lithe Audio speakers on the local network.

Every LS9 speaker is a Google Cast device: probing the local /24 for Cast's port (TCP 8008) needs
no multicast, works across most home routers and is quick on every system, since an open port
accepts at once. (A closed one is no quick sign: Windows tries a refused connection again for
about 3 seconds.) A Cast device is then asked what it is. The official Spotify eSDK's zeroconf
endpoint (TCP 9095) names the brand; a speaker whose official client does not run (Lithify hides
it, or it crashed: the firmware never starts it again) is found by Lithify's own page, or as a
Libre Cast speaker: its service console greets on TCP 23 and Cast names it (nothing is sent to
the console).
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def local_ipv4() -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # no packet is sent; this only picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def printable(text) -> str:
    """Device-supplied names are printed on a terminal: drop control and escape characters."""
    return "".join(c for c in str(text or "") if c.isprintable())[:80]


MAX_REPLY = 65536  # a getInfo reply is about 1 KB: a device that sends more is not a speaker
CAST_PORT = 8008


def _json(url: str, timeout: float) -> object:
    """A small JSON reply from the speaker, or None."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - http:// to this LAN, built here
            return json.loads(r.read(MAX_REPLY).decode("utf-8", "replace"))
    except (OSError, ValueError, http.client.HTTPException):
        return None


def _greets(host: str, port: int, greeting: bytes, timeout: float) -> bool:
    """Does `host` say `greeting` first on `port`? (Only read: nothing is sent.)"""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(1.5)
            return greeting in s.recv(64)
    except OSError:
        return False


def _probe(host: str, timeout: float) -> dict | None:
    try:
        socket.create_connection((host, CAST_PORT), timeout=timeout).close()
    except OSError:
        return None  # no Cast device there
    info = _json(f"http://{host}:9095/zc?action=getInfo", 3)
    if not isinstance(info, dict):
        return _probe_without_esdk(host, timeout)  # a Cast device whose official Spotify does not answer
    brand = f"{info.get('brandDisplayName', '')} {info.get('modelDisplayName', '')}"
    if "lithe" not in brand.lower():
        return None
    return {"host": host, "name": printable(info.get("remoteName")), "model": printable(info.get("modelDisplayName")),
            "spotify_esdk": printable(info.get("libraryVersion")), "found_by": "spotify"}


def _probe_without_esdk(host: str, timeout: float) -> dict | None:
    """A speaker whose official Spotify does not answer: Lithify's page says what it is; otherwise a
    Libre Cast speaker is a candidate that `lithify detect` and the install confirm."""
    status = _json(f"http://{host}:8090/api/status", 2)
    speaker = status.get("speaker") if isinstance(status, dict) else None
    if isinstance(speaker, dict) and "lithe" in str(speaker.get("model", "")).lower():
        return {"host": host, "name": printable(speaker.get("name")), "model": printable(speaker.get("model")),
                "spotify_esdk": "", "found_by": "lithify"}
    if not _greets(host, 23, b"CONNECTED", timeout):
        return None
    cast = _json(f"http://{host}:{CAST_PORT}/setup/eureka_info?params=name", 2)
    if not isinstance(cast, dict):
        return None
    return {"host": host, "name": printable(cast.get("name")), "model": "Libre Cast speaker", "spotify_esdk": "",
            "found_by": "libre"}


def discover(network: str | None = None, timeout: float = 1.0) -> list[dict]:
    """Lithe speakers in `network` (default: this computer's /24)."""
    if network is None:
        ip = local_ipv4()
        if not ip:
            return []
        network = str(ipaddress.ip_network(f"{ip}/24", strict=False))
    hosts = [str(h) for h in ipaddress.ip_network(network, strict=False).hosts()]
    found = _scan(hosts, timeout)
    if not found:  # (once more and slower: a first scan has missed a speaker that answered the next one)
        found = _scan(hosts, timeout * 2)
    return sorted(found, key=lambda r: ipaddress.ip_address(r["host"]))


def _scan(hosts: list[str], timeout: float) -> list[dict]:
    with ThreadPoolExecutor(max_workers=64) as ex:
        return [r for r in ex.map(lambda h: _probe_one(h, timeout), hosts) if r]


def _probe_one(host: str, timeout: float) -> dict | None:
    """One odd device on the network (a reply nobody expected) must not end the whole scan."""
    try:
        return _probe(host, timeout)
    except Exception:  # noqa: BLE001 - that host is simply not a speaker
        return None
