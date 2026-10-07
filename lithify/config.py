"""Lithify configuration: config.toml + LITHIFY_* environment overrides.

Search order: --config PATH, $LITHIFY_CONFIG, ./config.toml, then the user's configuration
directory (~/.config/lithify on Linux and macOS, %APPDATA%\\lithify on Windows).

Every option is described once, in agent/settings.tsv, and every check with its message in
agent/rules.tsv; the agent on the speaker reads the same two files, so both accept and refuse
alike (both test suites run agent/settings-cases.tsv).
After the first install a speaker owns its settings (its web page changes them); config.toml
gives the values for a first install, and `lithify update --settings` (or an environment
override) sends them again on purpose.
"""
from __future__ import annotations

import copy
import ipaddress
import os
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import hostos

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "agent" / "settings.tsv"
RULES_FILE = SCHEMA_FILE.with_name("rules.tsv")


@dataclass(frozen=True)
class Option:
    key: str
    section: str     # speaker | librespot | agent
    kind: str        # text | int | bool | choice | volume | list
    default: str     # canonical form ("" when empty)
    rule: str
    group: str
    restart: str

    def choices(self) -> list[str]:
        return ["" if c == "-" else c for c in self.rule.split("|")] if self.kind == "choice" else []

    def range(self) -> tuple[int, int] | None:
        if self.kind not in ("int", "volume") or "-" not in self.rule:
            return None
        lo, hi = self.rule.split("-", 1)
        return int(lo), int(hi)


def _load_schema(path: Path = SCHEMA_FILE) -> list[Option]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = line.split()
        if len(f) != 7:
            raise ValueError(f"{path.name}: bad line {line!r}")
        out.append(Option(f[0], f[1], f[2], "" if f[3] == "-" else f[3], f[4], f[5], f[6]))
    return out


SCHEMA = _load_schema()
OPTIONS = {o.key: o for o in SCHEMA}


def _typed(o: Option, canonical: str):
    """The Python/TOML value of a canonical string."""
    if o.kind == "bool":
        return canonical == "true"
    if o.kind == "int" or (o.kind == "choice" and canonical.isdigit()):
        return int(canonical)
    if o.kind == "volume":
        return canonical if canonical == "current" else int(canonical)
    if o.kind == "list":
        return canonical.split()
    return canonical


LIBRESPOT_DEFAULTS: dict = {o.key: _typed(o, o.default) for o in SCHEMA if o.section == "librespot"}
AGENT_DEFAULTS: dict = {o.key: _typed(o, o.default) for o in SCHEMA if o.section == "agent"}

COMPANION_DEFAULTS: dict = {
    "listen": "auto",   # "auto" = this computer's LAN address, port 8095; or "IP:PORT"
    "url": "",          # how speakers reach the companion; derived from `listen` when empty
}

# Use with fullmatch(): `$` would also let a trailing line break through (into file names, URLs).
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
HOST_RE = re.compile(r"[A-Za-z0-9.:-]{1,253}")
# What the agent accepts as the companion's address (agent/src/web.rs, safe_url).
URL_RE = re.compile(r"http://[A-Za-z0-9.:/\-_\[\]]{1,192}")


class ConfigError(ValueError):
    pass


def parse_listen(value) -> tuple[str, int] | None:
    """[companion] listen: None for "auto", else (address, port); "0.0.0.0" or "::" listens on
    every address of this computer."""
    if value == "auto":
        return None
    text = str(value).strip()
    host, sep, port = text.rpartition(":")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    ok = bool(sep) and port.isascii() and port.isdigit() and 0 < int(port) < 65536
    if ok:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            ok = bool(HOST_RE.fullmatch(host)) and ":" not in host  # a name of this computer
    if not ok:
        raise ConfigError(f"companion.listen {value!r}: use \"auto\" or \"IP:PORT\", e.g. \"192.168.1.10:8095\" "
                          "(\"0.0.0.0:8095\": every address)")
    return host, int(port)


def _check_companion(companion: dict) -> None:
    parse_listen(companion.get("listen", "auto"))
    url = companion.get("url", "")
    if not isinstance(url, str) or (url and not URL_RE.fullmatch(url.rstrip("/"))):
        raise ConfigError(f"companion.url {url!r}: the speakers need an address like \"http://192.168.1.10:8095\"")


# ── checks (agent/rules.tsv, read by the agent too) ─────────────────────────

@dataclass(frozen=True)
class TextRule:
    name: str
    min: int
    max: int
    chars: list[tuple[str, str]] | None   # allowed ranges; None: anything but a control character
    flags: frozenset
    message: str


def _char_class(spec: str) -> list[tuple[str, str]] | None:
    """A class like "A-Za-z0-9_-" as ranges (\\s is a space, \\x the character x); "*": None."""
    if spec == "*":
        return None
    tokens: list[tuple[str, bool]] = []  # (character, escaped)
    it = iter(spec)
    for c in it:
        if c == "\\":
            x = next(it, None)
            if x is not None:
                tokens.append((" " if x == "s" else x, True))
        else:
            tokens.append((c, False))
    out, i = [], 0
    while i < len(tokens):
        if i + 2 < len(tokens) and tokens[i + 1] == ("-", False):
            out.append((tokens[i][0], tokens[i + 2][0]))
            i += 3
        else:
            out.append((tokens[i][0], tokens[i][0]))
            i += 1
    return out


class Rules:
    def __init__(self, path: Path = RULES_FILE):
        self.messages: dict[str, str] = {}
        self.text: dict[str, TextRule] = {}
        self.network: dict[str, int] = {}
        self.never: list[ipaddress.IPv4Network] = []
        self.reserved: list[tuple[str, str]] = []
        self.ports: set[int] = set()
        self.list_max = 0
        self.bad: list[str] = []  # lines that did not parse (a test keeps this empty)
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.rstrip("\r")
            if not line or line.startswith("#"):
                continue
            f = line.split("\t")
            try:
                if f[0] == "message" and len(f) == 3:
                    self.messages[f[1]] = f[2]
                elif f[0] == "list" and len(f) == 2:
                    self.list_max = int(f[1])
                elif f[0] == "text" and len(f) == 7:
                    self.text[f[1]] = TextRule(f[1], int(f[2]), int(f[3]), _char_class(f[4]),
                                               frozenset(f[5].split(",")), f[6])
                elif f[0] == "network" and len(f) == 3:
                    self.network[f[1]] = int(f[2])
                elif f[0] == "never" and len(f) == 2:
                    self.never.append(ipaddress.IPv4Network(f[1], strict=False))
                elif f[0] == "reserved" and len(f) == 3:
                    self.reserved.append((f[1], f[2]))
                elif f[0] == "port" and len(f) == 3:
                    self.ports.add(int(f[1]))
                else:
                    self.bad.append(line)
            except ValueError:
                self.bad.append(line)

    def message(self, name: str, **fill) -> str:
        """Message `name` with its {placeholders} filled in."""
        text = self.messages.get(name, name)
        for k, v in fill.items():
            text = text.replace("{" + k + "}", str(v))
        return text


RULES = Rules()


def _check(rule: str, v: str, entry: bool = False) -> None:
    """`v` against rule `rule` (a text value, or one entry of a list); ValueError when refused."""
    if rule in RULES.network:
        check_network(v)
        return
    r = RULES.text.get(rule)
    if r is None:
        raise ValueError(RULES.message("unknown"))
    if not v and "empty-ok" in r.flags:
        return

    def allowed(c: str) -> bool:
        if r.chars is None:
            return unicodedata.category(c) != "Cc"
        return any(a <= c <= b for a, b in r.chars)

    ok = (r.min <= len(v) <= r.max and all(allowed(c) for c in v)
          and not ("no-space" in r.flags and any(c.isspace() for c in v))
          and not ("no-leading-dash" in r.flags and v.startswith("-")))
    if not ok:
        raise ValueError(RULES.message("entry", value=v, message=r.message) if entry else r.message)
    if "not-reserved" in r.flags and (long := _reserved_arg(v)):
        raise ValueError(RULES.message("reserved", value=v, option=long))


def check_network(v: str) -> None:
    """May `v` (an IPv4 address or network) get a fail-fast route? ValueError when not."""
    min_prefix = next(iter(RULES.network.values()), 8)
    ip, slash, length = v.partition("/")
    try:
        if slash and not (length.isascii() and length.isdigit()):
            raise ValueError
        net = ipaddress.IPv4Network(f"{ip}/{length or 32}", strict=False)
    except ValueError:
        raise ValueError(RULES.message("network", value=v)) from None
    if net.prefixlen < min_prefix:
        raise ValueError(RULES.message("prefix", value=v, min=min_prefix))
    if any(net.overlaps(n) for n in RULES.never):
        raise ValueError(RULES.message("never", value=v))


def _reserved_arg(v: str) -> str | None:
    """The librespot option (long name) an extra argument would repeat, if any."""
    if v.startswith("--"):
        name = v[2:].split("=", 1)[0]
        return next((long for long, _ in RULES.reserved if long == name), None)
    if v.startswith("-"):
        letters = re.match(r"[A-Za-z]*", v[1:]).group(0)
        return next((long for long, short in RULES.reserved if short and short in letters), None)
    return None


def conflicts(values: dict[str, str]) -> list[tuple[str, str]]:
    """Settings (canonical strings) that are fine one by one but not together: ports the
    speaker's own services use, or the page and librespot on the same port."""
    def port(k: str) -> int:
        try:
            return int(values.get(k, OPTIONS[k].default))
        except ValueError:
            return 0
    out = [(k, RULES.message("port", port=port(k))) for k in ("ui_port", "zeroconf_port") if port(k) in RULES.ports]
    if port("ui_port") == port("zeroconf_port"):
        out.append(("zeroconf_port", RULES.message("same-port")))
    return out


def normalize(o: Option, value) -> str:
    """The canonical (settings.conf) form of `value`, or ValueError with the reason."""
    if isinstance(value, bool):
        raw = "true" if value else "false"
    elif isinstance(value, (list, tuple)):
        raw = " ".join(str(v) for v in value)
    else:
        raw = str(value)
    v = raw.strip()
    if o.kind == "bool":
        if v.lower() in ("1", "true", "yes", "on"):
            return "true"
        if v.lower() in ("0", "false", "no", "off", ""):
            return "false"
        raise ValueError(RULES.message("bool"))
    if o.kind in ("int", "volume"):
        if o.kind == "volume" and v.lower() in ("", "current"):
            return "current"
        # (int() would take "٤٠" or "1_000"; the agent takes ASCII digits only)
        if not re.fullmatch(r"[+-]?[0-9]+", v):
            raise ValueError(RULES.message("number"))
        n = int(v)
        lo, hi = o.range() or (-(2 ** 63), 2 ** 63)
        if not lo <= n <= hi:
            raise ValueError(RULES.message("range", min=lo, max=hi))
        return str(n)
    if o.kind == "choice":
        for c in o.choices():
            if c.lower() == v.lower():
                return c
        none = RULES.message("none")
        raise ValueError(RULES.message("choice", choices=", ".join(c or none for c in o.choices())))
    if o.kind == "text":
        _check(o.rule, v)
        return v
    items = [i for i in re.split(r"[\s,]+", v) if i]
    if len(items) > RULES.list_max:
        raise ValueError(RULES.message("entries", max=RULES.list_max))
    for i in items:
        _check(o.rule, i, entry=True)
    return " ".join(items)


@dataclass
class Speaker:
    id: str
    host: str
    name: str
    platform: str = "auto"
    librespot: dict = field(default_factory=dict)
    agent: dict = field(default_factory=dict)
    env_keys: set = field(default_factory=set)   # settings given through the environment

    def value(self, key: str):
        if key == "name":
            return self.name
        o = OPTIONS[key]
        section = self.librespot if o.section == "librespot" else self.agent
        return section.get(key, _typed(o, o.default))


@dataclass
class Config:
    path: Path | None
    speakers: list[Speaker]
    companion: dict

    def speaker(self, sid: str | None) -> Speaker:
        if not self.speakers:
            raise ConfigError("no speaker configured yet - run `lithify install` (it finds the speaker)")
        if sid is None:
            if len(self.speakers) == 1:
                return self.speakers[0]
            ids = ", ".join(s.id for s in self.speakers)
            raise ConfigError(f"several speakers configured ({ids}); pick one with --speaker")
        for s in self.speakers:
            if s.id == sid or s.host == sid:
                return s
        raise ConfigError(f"unknown speaker {sid!r}")


def find_config(explicit: str | None = None, env: dict | None = None) -> Path | None:
    env = os.environ if env is None else env
    candidates = [explicit, env.get("LITHIFY_CONFIG"), "config.toml", str(hostos.config_dir(env) / "config.toml"),
                  str(Path.home() / ".config" / "lithify" / "config.toml")]
    for c in candidates:
        if c and Path(c).expanduser().is_file():
            return Path(c).expanduser()
    if explicit:
        raise ConfigError(f"config file {explicit} not found")
    return None


def _coerce_env(value: str, like):
    if isinstance(like, bool):
        if value.lower() in ("1", "true", "yes", "on"):
            return True
        if value.lower() in ("0", "false", "no", "off"):
            return False
        raise ConfigError(f"expected a boolean, got {value!r}")
    if isinstance(like, int):
        return int(value)
    if isinstance(like, list):
        return [v.strip() for v in value.split(",") if v.strip()]
    return value


def _env_overrides(section: str, defaults: dict, env: dict) -> dict:
    """LITHIFY_LIBRESPOT_BITRATE=160, LITHIFY_AGENT_UI_PORT=8091, ..."""
    out = {}
    for key, default in defaults.items():
        name = f"LITHIFY_{section.upper()}_{key.upper()}"
        if name in env:
            try:
                out[key] = _coerce_env(env[name], default)
            except ValueError as e:
                raise ConfigError(f"{name}: {e}") from None
    return out


def _validate(section: str, values: dict, sid: str) -> None:
    for key, value in values.items():
        o = OPTIONS.get(key)
        if o is None or o.section != section:
            raise ConfigError(f"speaker {sid}: unknown {section} option {key!r}")
        try:
            normalize(o, value)
        except ValueError as e:
            raise ConfigError(f"speaker {sid}: {section}.{key}: {e}") from None


def load(explicit: str | None = None, env: dict | None = None) -> Config:
    env = dict(os.environ) if env is None else env
    path = find_config(explicit, env)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8")) if path else {}
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None
    defaults = data.get("defaults", {})
    base_librespot = {**LIBRESPOT_DEFAULTS, **defaults.get("librespot", {})}
    base_agent = {**AGENT_DEFAULTS, **defaults.get("agent", {})}
    raw_speakers = data.get("speakers", [])
    # A configuration can also come entirely from the environment (single speaker).
    if not raw_speakers and env.get("LITHIFY_HOST"):
        raw_speakers = [{"id": "speaker", "host": env["LITHIFY_HOST"], "name": env.get("LITHIFY_NAME", "")}]
    speakers = []
    for i, raw in enumerate(raw_speakers):
        sid = str(raw.get("id") or f"speaker{i + 1}")
        if not ID_RE.fullmatch(sid):
            raise ConfigError(f"speaker id {sid!r}: use lowercase letters, digits and '-'")
        single = len(raw_speakers) == 1
        host = env.get("LITHIFY_HOST") if single and env.get("LITHIFY_HOST") else raw.get("host", "")
        name = env.get("LITHIFY_NAME") if single and env.get("LITHIFY_NAME") else raw.get("name", "")
        if not isinstance(host, str) or not HOST_RE.fullmatch(host):
            raise ConfigError(f"speaker {sid}: `host` (IP address or hostname) is required")
        try:
            normalize(OPTIONS["name"], name)
        except ValueError:
            raise ConfigError(f"speaker {sid}: `name` (shown in Spotify) is required, 1-64 characters") from None
        librespot = {**copy.deepcopy(base_librespot), **raw.get("librespot", {})}
        agent = {**copy.deepcopy(base_agent), **raw.get("agent", {})}
        env_keys: set = set()
        if single:
            for section, values, defaults_ in (("librespot", librespot, LIBRESPOT_DEFAULTS),
                                               ("agent", agent, AGENT_DEFAULTS)):
                overrides = _env_overrides(section, defaults_, env)
                values.update(overrides)
                env_keys |= set(overrides)
            if env.get("LITHIFY_NAME"):
                env_keys.add("name")
        _validate("librespot", librespot, sid)
        _validate("agent", agent, sid)
        speaker = Speaker(sid, host, name, str(raw.get("platform", "auto")), librespot, agent, env_keys)
        for key, why in conflicts(settings_values(speaker)):
            raise ConfigError(f"speaker {sid}: {key}: {why}")
        speakers.append(speaker)
    ids = [s.id for s in speakers]
    if len(set(ids)) != len(ids):
        raise ConfigError("speaker ids must be unique")
    companion = {**COMPANION_DEFAULTS, **data.get("companion", {})}
    _check_companion(companion)
    return Config(path, speakers, companion)


# ── files sent to the speaker ───────────────────────────────────────────────

def settings_values(s: Speaker) -> dict[str, str]:
    """Every setting of a speaker in its canonical form."""
    out = {}
    for o in SCHEMA:
        try:
            out[o.key] = normalize(o, s.value(o.key))
        except ValueError as e:
            raise ConfigError(f"speaker {s.id}: {o.key}: {e}") from None
    return out


def _lines(values: dict[str, str]) -> str:
    for k, v in values.items():
        if "\n" in v or "\r" in v:
            raise ConfigError(f"{k} must not contain line breaks")
    return "".join(f"{k}={v}\n" for k, v in values.items())


def settings_conf(s: Speaker) -> str:
    """The settings a speaker starts with when it has none yet (settings.default)."""
    return ("# Settings from the computer's config.toml, used when the speaker has none yet.\n"
            + _lines(settings_values(s)))


def settings_patch(s: Speaker, keys) -> str:
    """Values sent on purpose; they replace the speaker's own (settings.patch)."""
    values = settings_values(s)
    unknown = set(keys) - set(values)
    if unknown:
        raise ConfigError(f"unknown settings: {', '.join(sorted(unknown))}")
    return "# Settings sent on purpose by `lithify`.\n" + _lines({k: values[k] for k in values if k in set(keys)})


def install_conf(s: Speaker, companion_url: str, platform: str) -> str:
    """What the speaker needs to know about this computer (install.conf)."""
    return "# Written by `lithify` at install time.\n" + _lines(
        {"companion_url": companion_url, "speaker_id": s.id, "platform": platform})


EXAMPLE = '''# Lithify configuration - see docs/configuration.md
# These values are used when Lithify is first installed on a speaker. After that the speaker
# keeps its own settings: change them on its web page (http://<speaker>:8090), or send these
# again with `lithify update --settings`.

[defaults.librespot]
bitrate = 320                 # 96 | 160 | 320
mixer = "alsa"                # "alsa" = Spotify slider drives the speaker volume (recommended)
volume_ctrl = "linear"
initial_volume = "current"    # keep the speaker volume when librespot (re)starts

[defaults.agent]
ui_pin = ""                   # optional PIN (6+ digits) for actions on the web page
fastfail_hosts = []           # CDN hosts your ISP cannot reach, see docs/troubleshooting.md

[[speakers]]
id = "{id}"
host = "{host}"
name = {name}               # name shown in Spotify Connect
'''
