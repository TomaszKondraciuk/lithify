# Configuration

A `config.toml` with every option and its default: [config.example.toml](config.example.toml).

## Where settings live

**On the speaker.** Its web page (`http://<speaker>:8090`, section *Settings*) changes them, and so
does `lithify settings set key=value …`; librespot or the agent restarts by itself when needed.
Updates – from the page or with `lithify update` – keep them.

**config.toml** gives each speaker's values for its *first* install, and holds what the computer
needs: the speaker's address and the update helper's. `lithify update --settings` sends its values
again and replaces those set on the speaker; an environment variable (below) sends just that value.

Every option is described once, in [`agent/settings.tsv`](../agent/settings.tsv); the speaker and
the `lithify` command validate the same way.

```sh
lithify settings                         # what the speaker uses now (* = not the default)
lithify settings set name="Kuchnia" bitrate=160
lithify settings reset-pin               # forgotten PIN (uses the service console)
```

## config.toml

Lithify reads one TOML file. It looks, in order, at `--config PATH`, `$LITHIFY_CONFIG`,
`./config.toml` and the configuration directory: `%APPDATA%\lithify\config.toml` on Windows,
`~/.config/lithify/config.toml` on macOS and Linux (`$XDG_CONFIG_HOME` if set). The first
`lithify install` writes the last one.

Every option has a default, so a speaker needs only `id`, `host` and `name`. Values are applied
in this order, later ones winning:

1. the built-in defaults listed below,
2. `[defaults.librespot]` and `[defaults.agent]` (all speakers),
3. `[speakers.librespot]` / `[speakers.agent]` inside one `[[speakers]]` block,
4. environment variables (only when exactly one speaker is configured).

## Speakers

```toml
[[speakers]]
id = "kitchen"          # lowercase letters, digits, '-'; used with --speaker
host = "192.168.1.40"   # IP address or host name
name = "Kitchen"        # shown in Spotify Connect
platform = "auto"       # "auto" detects; "ls9" forces the profile

[speakers.librespot]    # per-speaker overrides
bitrate = 160
```

## `librespot` options

| Option | Default | Meaning |
|---|---|---|
| `bitrate` | `320` | Stream quality in kbit/s: `96`, `160` or `320`. |
| `device_type` | `"speaker"` | Icon in Spotify: `speaker`, `avr`, `tv`, `stb` (shown with the speaker icon), `computer`, `tablet`, `smartphone`. With `audiodongle`, `gameconsole`, `castaudio` or `castvideo` the Spotify apps hide the device from Spotify Connect, so the web page does not offer them. |
| `backend` | `"alsa"` | Audio backend. |
| `device` | `"plughw:0,0"` | ALSA output; `plughw` converts sample formats and rates as needed. |
| `format` | `"S16"` | Sample format sent to ALSA. The LS9's WM8904 takes `S16`, `S24` and `S32` natively at 44.1 kHz (Spotify's rate, so nothing is resampled), but its driver caps the audio buffer at 64 KB: 371 ms at 16 bits, 185 ms at 24 or 32 bits. With the speaker's own volume control (`mixer = "alsa"`) librespot sends full-scale samples, and 16 bits with dither lose nothing audible on this converter (96 dB SNR), so the longer buffer wins. `S32` suits `mixer = "softvol"`: librespot then turns the volume down itself, which at 16 bits costs a bit of resolution per 6 dB. |
| `mixer` | `"alsa"` | `alsa`: the Spotify slider sets the speaker's own volume control (recommended – full loudness, shared with Cast and AirPlay). `softvol`: librespot scales the samples itself. |
| `mixer_device` | `"hw:0"` | ALSA card of the volume control (`mixer = "alsa"`). |
| `mixer_control` | `"Master"` | Volume control name (`mixer = "alsa"`). |
| `volume_ctrl` | `"linear"` | Slider curve: `linear`, `log`, `cubic` or `fixed`. |
| `initial_volume` | `"current"` | `"current"` keeps the speaker's volume when librespot starts; or `0`–`100`. |
| `normalisation` | `false` | Spotify's loudness normalisation. |
| `autoplay` | `""` | Autoplay similar songs: `""` follows the Spotify app, `"on"`, `"off"`. |
| `zeroconf_port` | `4070` | Port of librespot's discovery service. |
| `extra_args` | `[]` | More librespot arguments, appended as given, e.g. `["--ap-port", "443"]`. Options Lithify already passes (name, bitrate, mixer, cache, … – see `agent/rules.tsv`) and `--help`/`--version` are refused: librespot stops when an option is given twice. |

## `agent` options

The agent runs on the speaker next to librespot.

| Option | Default | Meaning |
|---|---|---|
| `ui` | `true` | Serve the web page. |
| `ui_port` | `8090` | Its port. When it cannot be used (another service has it), the page falls back to 8090. |
| `ui_pin` | `""` | When set, every action on the page (settings, update check, updates, rollback, restarts) asks for this PIN: 4 to 12 digits, six or more are harder to guess. Status, logs and tests stay readable. |
| `fastfail_hosts` | `[]` | CDN host names your network cannot reach reliably. Their IPv4 addresses (re-resolved every 30 min, each kept for a day after it was last seen) get `unreachable` routes, so both Spotify clients skip to the next CDN at once instead of waiting 10 s or more. Use the page's connection test to find them. |
| `fastfail_routes` | `[]` | The same for fixed prefixes, e.g. `["199.232.0.0/16"]`. Networks larger than /8, private, loopback and multicast ranges are refused, and the speaker's own network and gateway are never blocked. |
| `spin_threshold_pct` | `85` | The official Spotify client is restarted when one of its threads uses this much of a core… |
| `spin_seconds` | `60` | …for this long. |
| `spin_cooldown_seconds` | `600` | At most one such restart per this many seconds. |
| `respawn_official_spotify` | `true` | Start the official client again when it has crashed (the firmware never does). |
| `respawn_grace_seconds` | `120` | How long it must be gone first. |
| `hide_official_spotify` | `false` | Keep the official client stopped while librespot works, so the Spotify apps list one device for this speaker. When librespot has not worked for `respawn_grace_seconds`, the official client is started again as a fallback (and hidden again once librespot is back and nothing plays). |
| `silent_start_seconds` | `8` | The official client "started" a track but has fed no audio for this long… |
| `silent_start_action` | `"log"` | …then: `log`, `pause_resume`, `next` or `seek0`. |
| `conflict_stop_librespot` | `true` | Stop librespot when Cast, AirPlay, Bluetooth or the official client starts playing, so they can open the single audio device. |
| `wifi_scancfg` | `""` | Wi-Fi driver scan tuning passed to `mlanutl wlan0 scancfg` (experts only). |

## `companion` options

`lithify serve` is the helper on your computer that the speaker's web page uses to build and
install updates.

| Option | Default | Meaning |
|---|---|---|
| `listen` | `"auto"` | `auto`: this computer's address on the speakers' network, port 8095. Or `"IP:PORT"`; `"0.0.0.0:8095"` (or `"[::]:8095"`) listens on every address, and the speakers are given this computer's address on their network. |
| `url` | `""` | How the speakers reach the helper, when it differs from `listen` (e.g. behind NAT). |

The address is written into each speaker at install time, so after changing it run
`lithify update`.

## Environment variables

| Variable | Effect |
|---|---|
| `LITHIFY_CONFIG` | Path of the configuration file. |
| `LITHIFY_HOST`, `LITHIFY_NAME` | Host and Spotify name of the (single) speaker; with no file at all they are the whole configuration. |
| `LITHIFY_LIBRESPOT_<OPTION>` | Any `librespot` option, e.g. `LITHIFY_LIBRESPOT_BITRATE=160`. |
| `LITHIFY_AGENT_<OPTION>` | Any `agent` option, e.g. `LITHIFY_AGENT_UI_PIN=2468`. |
| `LITHIFY_CACHE` | Build cache directory (default `%LOCALAPPDATA%\lithify\cache` on Windows, `~/.cache/lithify` elsewhere). The compiler's caches live in Docker volumes (`lithify-cargo`, `lithify-target-*`). |

Booleans accept `1/true/yes/on` and `0/false/no/off`; lists are comma-separated:
`LITHIFY_AGENT_FASTFAIL_HOSTS=audio-fa.scdn.co,heads-fa.scdn.co`.

```sh
LITHIFY_HOST=192.168.1.40 LITHIFY_NAME="Kitchen" lithify install
LITHIFY_LIBRESPOT_BITRATE=160 lithify update     # sends just this value to the speaker
```

## Several speakers

```toml
[defaults.librespot]
bitrate = 320

[[speakers]]
id = "kitchen"
host = "192.168.1.40"
name = "Kitchen"

[[speakers]]
id = "bathroom"
host = "192.168.1.41"
name = "Bathroom"

[speakers.agent]
ui_pin = "2468"
```

`lithify install --speaker kitchen`, `lithify status --speaker bathroom`, and so on.

## Build pins

[`versions.toml`](../versions.toml) pins the tested baseline: the commit of librespot's development
branch and the local patches on top of it, alsa-lib and the Rust toolchain. *Update everything* (or
`lithify build --latest`) builds the branch's newest commit with the newest alsa-lib and Rust
releases and, once the bundle built with them is in
place, remembers them in `versions.toml` in the configuration directory (a build that fails or is
stopped leaves that file as it was); a version there older than the repository's is ignored, and
deleting the file goes back to the baseline. `lithify check-updates` lists what is newer.
