# Platforms

Lithe Audio's Wi-Fi speakers are built on modules from Libre Wireless. Two generations exist,
with different hardware and firmware.

## LS9 – supported

| | |
|---|---|
| Models | Wi-Fi Ceiling Speaker V2 (single and pair), WiFi PRO, Micro Subwoofer |
| SoC | Marvell Berlin, 2 × Cortex-A7 1.3 GHz (ARMv7, NEON), 462 MB RAM |
| Wi-Fi | Marvell SD8887 (2.4 and 5 GHz) |
| Audio | WM8904 codec, ALSA card 0 `marvell-wm8904`, control `Master` 0–100 |
| Firmware | Libre p15525.144.0 (2024-07-16), Cast 1.52, AirPlay SDK 2.0.10, Spotify eSDK 3.194.71 |
| Kernel | Linux 3.8.13 (no `SO_REUSEPORT` – librespot's mDNS needs a small patch) |
| Storage | read-only squashfs root (single copy, never touched); `/lsync` (yaffs2, ~200 MB free) and `/system/chrome` (Cast data) are writable and persistent |
| Lithify files | `/lsync/lithify` |
| Autostart | two entries in `/system/chrome/process.json`, the Cast process manager's service list (stock copy kept in `/lsync/lithify/backup`) |
| Console | TCP 23, root, no password (see [security.md](security.md)) |

`lithify detect --host <ip>` identifies the platform from `/system/build.prop` (`chickentikka`)
before anything is changed.

### Things that matter on LS9

- The Cast process manager parses commands like Chromium's `base::CommandLine`: it moves
  `--switch` arguments in front of positional ones. Lithify's entries therefore use only
  positional arguments (`lithify-agent exec-file ARGS_FILE PROGRAM`), and librespot's options
  live in `librespot.args`.
- The console's shell inherits `SIGCHLD=SIG_IGN` and spins forever after any forked command;
  every line is `exec <command>` or `exec mksh <script>`. The firmware's own event hooks
  (`sh -c LUCI_local …`) hit the same bug and can keep a core busy for days; the agent stops them.
- The console drops the last two bytes of each line (it expects CRLF) and accepts about 180
  characters per line. `lithify` therefore sends a single line that has the speaker download the
  script from your computer (a short-lived HTTP server that answers only the speaker); where a
  firewall prevents that, scripts are typed in line by line (no single quotes, about a second per
  line).
- Only one PCM substream exists: Cast, AirPlay, Bluetooth, the official Spotify client and
  librespot take turns. The agent pauses a Libre source before librespot plays, and stops
  librespot when a Libre source starts.
- Never change the ALSA `Master` control or its playback switch directly (amixer): the firmware
  mirrors every change into Cast's system volume and mute state, and a mute set that way sticks
  (and briefly set off a volume-sync loop between the Libre services). Change the volume through
  Spotify, Cast or the Lithe app; librespot's `--mixer alsa` path is fine.
- A Cast firmware update may replace `/system/chrome/process.json`; `lithify update` puts the
  entries back. `/lsync` survives reboots, factory resets and firmware updates.

## LS10 – not supported yet

Wi-Fi Speaker V3, WiFi PRO 2 and iO1 use the newer LS10 module with different firmware (its
control protocol runs over TLS). It needs its own profile; contributions with a device to test on
are welcome.

## Adding a platform

1. A `Platform` profile in [`lithify/platforms.py`](../lithify/platforms.py): install directory,
   service list, script directory, detection rule.
2. Device scripts in `device/<platform>/` (`install.sh`, `persist.sh`, `rollback.sh`,
   `restart-services.sh`), written for the shell the device has.
3. Agent support for the platform's audio device, control protocol and process names
   ([`agent/src`](../agent/src)).
4. Build settings if the CPU differs (`build/Dockerfile`, `RUSTFLAGS` in `lithify/bundle.py`).
