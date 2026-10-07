"""Platform profiles. Lithe Audio speakers are built on Libre Wireless modules:

  LS9  - WiFi Speaker V2, WiFi PRO, Micro Subwoofer (Marvell Berlin, Cast 1.52) - supported
  LS10 - WiFi Speaker V3, WiFi PRO 2, iO1 - different SoC/firmware, not supported yet
"""
from __future__ import annotations

import json
from dataclasses import dataclass

SERVICE_PREFIXES = ("lithify_", "cc_")  # cc_ = installs made before the project was named


@dataclass(frozen=True)
class Platform:
    key: str
    supported: bool
    base: str = ""              # install directory on the speaker
    process_list: str = ""      # Cast process_manager service list
    scripts: str = ""           # device/<scripts>/ in this repository
    legacy_bases: tuple = ()    # older install locations to migrate from


LS9 = Platform("ls9", True, base="/lsync/lithify", process_list="/system/chrome/process.json",
               scripts="ls9", legacy_bases=("/lsync/cc",))
LS10 = Platform("ls10", False)
PLATFORMS = {"ls9": LS9, "ls10": LS10}


def strip_services(process_list: str) -> list[dict]:
    """Return the stock Cast service list: drop entries added by Lithify (or its predecessor)."""
    raw = process_list[process_list.index("["):process_list.rindex("]") + 1]
    entries = json.loads(raw)
    if not isinstance(entries, list) or not all(isinstance(e, dict) and "name" in e for e in entries):
        raise ValueError("process list has an unexpected shape")
    if "cast_shell" not in [e["name"] for e in entries]:
        raise ValueError("process list has no cast_shell entry - refusing to touch it")
    return [e for e in entries if not str(e["name"]).startswith(SERVICE_PREFIXES)]


def render_process_list(stock: list[dict], base: str) -> str:
    """Stock list + librespot and the agent. Positional arguments only: the Cast
    process_manager (Chromium base::CommandLine) moves `--switch` arguments to the front."""
    out = [
        *stock,
        {"name": "lithify_librespot",
         "command": [f"{base}/lithify-agent", "exec-file", f"{base}/librespot.args", f"{base}/librespot"]},
        {"name": "lithify_agent", "command": [f"{base}/lithify-agent", "run"]},
    ]
    text = json.dumps(out, ensure_ascii=False, indent=2) + "\n"
    json.loads(text)
    return text


def detect(build_prop: str, banner_ok: bool) -> Platform | None:
    """Pick a profile from what the speaker reports (console banner + /system/build.prop)."""
    if banner_ok and "chickentikka" in build_prop:
        return LS9
    return None
