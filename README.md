# Lithify

**A modern Spotify Connect client for Lithe Audio Wi-Fi speakers.**

Lithify puts [librespot](https://github.com/librespot-org/librespot) – the open-source Spotify
Connect client – on Lithe Audio ceiling speakers, next to the speaker's own firmware. You get a
second Spotify Connect device that starts faster, keeps playing, and can be updated whenever
librespot is, plus a small web page on the speaker for status, tests, updates and rollback.

Nothing is flashed. The speaker keeps its firmware, Google Cast, AirPlay and its official Spotify
client; Lithify adds files in one directory of the speaker's writable storage and two entries to
its service list, and `lithify uninstall` removes both.

## Why

On the LS9 platform (WiFi Speaker V2 and relatives) the firmware's Spotify client is
eSDK 3.194 from 2021, and the last firmware is from 2024. It starts tracks and then plays
nothing, lags on pause/skip and can spin a CPU core for days. Lithe can't fix it without a new
firmware; Lithify replaces it in practice with a current, maintained client.

## Features

- **librespot 0.8** with upstream fixes back-ported, built statically for the speaker's
  Cortex-A7 (NEON), every Rust dependency refreshed to its newest compatible release.
- **Hardware volume**: the Spotify slider drives the speaker's own master volume, shared with
  Cast and AirPlay, without jumps when librespot restarts.
- **Watchdog** for the official client: restarts it when it spins or vanishes, frees the audio
  device when another source starts, optional fail-fast routes for CDNs your ISP can't reach.
- **Web page on the speaker** (`http://<speaker>:8090`): every setting, status, librespot and
  connection tests, logs, **one button to update everything**, rollback. English and Polish.
- **Settings live on the speaker**: change the name, quality, volume behaviour or anything else
  on its page; updates keep them. `config.toml` and environment variables give the values for
  the first install (and can be sent again on purpose).
- **Safe updates**: every file is checksummed before anything is replaced, the previous version
  is kept for a one-step rollback, and the stock service list is backed up.

## Supported speakers

| Platform | Lithe Audio models | Status |
|---|---|---|
| Libre **LS9** (Marvell Berlin, Cast 1.52) | Wi-Fi Ceiling Speaker V2 (single, pair), WiFi PRO, Micro Subwoofer | **supported** |
| Libre **LS10** | Wi-Fi Speaker V3, WiFi PRO 2, iO1 | not yet – see [docs/platforms.md](docs/platforms.md) |

`lithify discover` lists the speakers on your network, and `lithify detect` tells you whether one
is supported before anything is changed.

## Quick start

You need a computer on the same network as the speaker – **Windows, macOS or Linux**. Nothing
has to be installed first: the installer gets the computer ready and **asks once before it
installs anything**. Then it opens the **Lithify wizard** in the web browser, which finds the
speaker and installs Lithify on it (the speaker restarts once). Both show their texts in Polish or
English, as the computer is set up.

### Windows

1. Download **`Lithify-Windows.cmd`** from the [latest release](https://github.com/OWNER/lithify/releases/latest)
   (or all of Lithify: *Code → Download ZIP*, unpacked – the file is at its top).
2. Double-click it. Windows asks once whether to run it ("The publisher could not be verified"):
   click *Run* (if it says "Windows protected your PC" instead: *More info → Run anyway*).

The installer lists what this computer still needs (Python, Git, Docker Desktop, WSL, a firewall
rule) and asks once; Windows then asks once for permission (*Yes*). When WSL needs a restart, it
offers to restart and goes on by itself after you sign in again. The first time takes up to an
hour, most of it Docker Desktop and the build.

In PowerShell the same is one line:
`irm https://raw.githubusercontent.com/OWNER/lithify/main/installer/get.ps1 | iex`

### macOS

Open *Terminal* (⌘ Space, "Terminal"), paste this and press Return:

```sh
curl -fsSL https://raw.githubusercontent.com/OWNER/lithify/main/installer/install.sh | sh
```

This way macOS asks nothing about the file. With a double-click instead: **`Lithify-macOS.zip`**
from the [latest release](https://github.com/OWNER/lithify/releases/latest) (Safari unpacks it),
then `Lithify-macOS.command` in it – the first time macOS blocks it: *System Settings → Privacy &
Security → Open Anyway* (macOS 14 and older: right-click it → *Open* → *Open*), and *Allow* when
it asks whether Terminal may access the Downloads folder.
When macOS asks whether Python may accept incoming network connections: *Allow*.

### Linux

In a terminal:

```sh
wget -qO- https://raw.githubusercontent.com/OWNER/lithify/main/installer/install.sh | sh
```

(Ubuntu and Debian come with wget, not curl; where curl is there,
`curl -fsSL https://raw.githubusercontent.com/OWNER/lithify/main/installer/install.sh | sh` does
the same.) With a double-click instead: **`Lithify-Linux.zip`** from the
[latest release](https://github.com/OWNER/lithify/releases/latest), unpacked, then `Lithify-Linux.sh`
in it – Ubuntu and other GNOME systems open it in a text editor: close that, right-click the file →
*Run as a Program*.

### What the installer does

- **Python 3.11 or newer** – the one the computer has; otherwise Python 3.12 for this user (on
  Windows with winget) or a private one just for Lithify (by [uv](https://docs.astral.sh/uv/)),
  without administrator rights.
- **Docker and git**, which build Lithify for the speaker (nothing of them goes onto the speaker):
  Docker Desktop and WSL 2 on Windows, Docker Desktop on macOS, the distribution's packages on
  Linux. They are not needed when a prebuilt bundle is already on the computer, or published
  (`[release]` in `versions.toml`): the install then takes a few minutes.
- **The firewall** – the speaker downloads from the computer on TCP 8095 and 18096–18099: a rule
  on Windows, ufw or firewalld on Linux (when one is on); on macOS, *Allow* when it asks about Python.

The terminal window shows every step and stays open at the end. Run it again any time: it skips
what is already done. Lithify is copied into its own folder, so the download can be deleted
afterwards. Open Spotify, pick the speaker in its list of devices – done; the name and everything
else can be changed on the speaker's web page later.

From a git checkout, `.\installer\install.ps1` or `./installer/install.sh` uses it in place. Over SSH, on a Linux
computer without a display, or with `LITHIFY_HOST` set, the speaker part runs in the terminal
instead of the browser. Lithify comes with git when it is installed (it then updates itself),
otherwise as GitHub's archive.

Options (environment variables): `LITHIFY_YES=1` – yes to every question; `LITHIFY_LANG=pl` or
`en` – the language of the messages; `LITHIFY_HOST=<ip>` –
the speaker's address; `LITHIFY_NAME` – its name in Spotify; `LITHIFY_NO_WIZARD=1` – the terminal
instead of the browser; `LITHIFY_NO_SETUP=1` – only the `lithify` command; `LITHIFY_HOME`,
`LITHIFY_REPO`, `LITHIFY_ARCHIVE_URL` – where Lithify goes and where it comes from.

The same by hand: `lithify wizard`, or in the terminal `lithify install` (finds the speaker the
first time), `lithify serve --install-service` (a scheduled task on Windows, a launchd agent on
macOS, a systemd user service on Linux), `lithify ui`.

## The web page

Open `http://<speaker-ip>:8090` (or run `lithify ui`). It shows librespot and the official
client, Wi-Fi band and signal, the audio output and the installed versions, and it can:

- **change every setting** – name in Spotify, audio quality, volume behaviour, fail-fast CDN
  servers, the page's PIN and the advanced options – and restart only what uses them;
- **update everything**: one button builds the newest stable librespot, Rust, alsa-lib and
  libraries on your computer and installs them (nothing is reinstalled when nothing changed),
  with a list of what is new and a rollback button;
- test librespot (discovery, recent underruns and errors) and the connections to Spotify's
  access points and CDNs; restart librespot, the official client or the agent; show the logs.

Builds and update files come only from Lithify on your computer (`lithify serve`), never from the
internet directly. Set `ui_pin` to require a PIN for actions; see
[docs/security.md](docs/security.md).

## Settings

The speaker keeps its settings and its web page changes them; so does
`lithify settings set name="Kitchen" bitrate=160`. Updates never overwrite them.

`config.toml` (written by the first `lithify install` into `%APPDATA%\lithify` on Windows,
`~/.config/lithify` elsewhere) and environment variables
(`LITHIFY_NAME`, `LITHIFY_LIBRESPOT_BITRATE`, `LITHIFY_AGENT_UI_PIN`, …) give the values for the
first install; `lithify update --settings` sends config.toml's values again on purpose:

```toml
[[speakers]]
id = "kitchen"
host = "192.168.1.40"
name = "Kitchen"         # shown in Spotify Connect

[speakers.librespot]
bitrate = 320            # 96 | 160 | 320
```

Every option and its default: [docs/configuration.md](docs/configuration.md).

## Commands

| Command | What it does |
|---|---|
| `lithify discover` | list Lithe Audio speakers on this network |
| `lithify install` | first install (finds the speaker) or update |
| `lithify settings [set k=v …]` | show or change the speaker's settings |
| `lithify setup` | create the configuration for a speaker by hand |
| `lithify detect` | show model, firmware and whether it is supported |
| `lithify build` / `fetch` | build the bundle with Docker / download a published one |
| `lithify update [--settings]` | update (reboots only when the service list changes) |
| `lithify status [--json]` | versions and health |
| `lithify logs` | agent and librespot logs |
| `lithify check-updates` | newer librespot, alsa-lib, Rust, libraries, Lithify? |
| `lithify build [--latest]` | build the bundle (`--latest`: newest stable versions first) |
| `lithify rollback [version\|stock]` | previous version, or the stock service list |
| `lithify uninstall` | remove Lithify and restore the stock speaker |
| `lithify serve [--install-service]` | update helper for the speaker's web page |
| `lithify reboot`, `ui`, `sh` | reboot and wait, print the page address, run a root command |

With several speakers, add `--speaker <id>`.

## Updating

**Update everything** on the web page (or `lithify build --latest && lithify update`) moves
librespot, alsa-lib and Rust to their newest stable releases, refreshes every library to its
newest compatible version, builds, and installs – only when something changed. A newer choice
is remembered in `~/.config/lithify/versions.toml`; if it does not build, the previous one stays.
[`versions.toml`](versions.toml) holds the tested baseline, and the upstream fixes back-ported
onto it are used only for that release.

## How it works

The speaker's Cast process manager starts two extra entries: `lithify-agent exec-file … librespot`
(librespot with its arguments from a file) and `lithify-agent run` (watchdog and web page). The
files live in `/lsync/lithify`, which survives reboots, factory resets and firmware updates. A
Cast firmware update can replace the service list; `lithify update` restores the entries. Details,
including the build pipeline, in [docs/architecture.md](docs/architecture.md).

## Troubleshooting

See [docs/troubleshooting.md](docs/troubleshooting.md): device not visible in Spotify, no sound,
stutter (with the network test), quiet volume, a firmware update removed Lithify, build problems.

## Security

LS9 speakers ship with an unauthenticated root service console on TCP port 23. Lithify did not
create it, but it uses it to install, and anyone on your network can use it too. Keep these
speakers on a network you trust, ideally an IoT VLAN. Read [docs/security.md](docs/security.md)
before exposing them anywhere.

## Legal

Lithify is an independent project, not affiliated with or endorsed by Lithe Audio, Libre
Wireless, Google or Spotify. Spotify is a trademark of Spotify AB. librespot is licensed under the
MIT license; alsa-lib under the LGPL 2.1 (it is linked statically; its source is at
https://www.alsa-project.org and the build script reproduces the binary). Lithify itself is
MIT-licensed – see [LICENSE](LICENSE).

Polski opis: [docs/pl/README.md](docs/pl/README.md).
