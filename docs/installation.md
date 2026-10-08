# Installation

The [README](../README.md#install) has the short version. This page covers the questions the
installer asks on each system, the network the speaker needs, what changes on the computer, the
installer's options, and how to do the same by hand.

## What you need

- A supported speaker ([platforms.md](platforms.md)). The wizard, or `lithify detect`, tells you
  whether a speaker is supported before anything is changed.
- **Spotify Premium.** librespot does not work with free accounts.
- A computer on the same network as the speaker, running Windows 10 or 11, macOS, or Linux. You
  need it for the installation and for updates. The speaker plays without it.

## Windows

Download **`Lithify-Windows.cmd`** from the
[latest release](https://github.com/OWNER/lithify/releases/latest) and double-click it. Windows
asks once whether to run a file from the internet. Click *Run* in the "The publisher could not be
verified" dialog, or *More info → Run anyway* in the "Windows protected your PC" dialog.

The installer lists what the computer still needs and asks once:

- **Python**, installed for this user with winget. This needs no administrator rights.
- **A firewall rule**, so the speaker can download its software from this computer. Windows asks
  once for permission: *Yes*.
- **Git, Docker Desktop and WSL 2**, only when Lithify has to be built on this computer because
  no release can be downloaded. When WSL needs a restart, the installer offers it and goes on by
  itself after you sign in again.

The same in PowerShell:

```powershell
irm https://raw.githubusercontent.com/OWNER/lithify/main/installer/get.ps1 | iex
```

## macOS

Open *Terminal* (⌘ Space, "Terminal"), paste this and press Return:

```sh
curl -fsSL https://raw.githubusercontent.com/OWNER/lithify/main/installer/install.sh | sh
```

When the computer has no Python 3.11 or newer, the installer offers a private Python just for
Lithify. It needs no administrator rights and changes nothing else on the Mac. If the macOS
firewall is on, macOS asks whether Python may accept incoming network connections: *Allow*.
The speaker downloads its software from your computer.

To install with a double-click instead, open **`Lithify-macOS.zip`** from the
[latest release](https://github.com/OWNER/lithify/releases/latest) (Safari unpacks it), then
**`Lithify-macOS.command`** in it. The first time, macOS blocks it:

- **macOS 15 and later:** *System Settings → Privacy & Security → Open Anyway*.
- **macOS 14 and older:** right-click the file → *Open* → *Open*.

When macOS asks whether Terminal may access the Downloads folder: *Allow*.

## Linux

In a terminal:

```sh
wget -qO- https://raw.githubusercontent.com/OWNER/lithify/main/installer/install.sh | sh
```

Ubuntu and Debian come with wget but not curl. Where curl is installed,
`curl -fsSL https://raw.githubusercontent.com/OWNER/lithify/main/installer/install.sh | sh` does
the same. With ufw or firewalld active, the installer shows the command that opens TCP 8095 and
18096–18099 for the local network and offers to run it (with `sudo`).

To install with a double-click instead, unpack **`Lithify-Linux.zip`** from the
[latest release](https://github.com/OWNER/lithify/releases/latest) and run **`Lithify-Linux.sh`**
in it. GNOME (Ubuntu's desktop) opens it in a text editor: close the editor, right-click the
file → *Run as a Program*.

## Network requirements

On a typical home network, with the computer and the speakers on the same Wi-Fi or LAN, nothing
needs to be set up. If your speakers are on a separate network (an IoT VLAN or guest Wi-Fi), the
networks must let this traffic through:

| From | To | Port | Used for |
|---|---|---|---|
| computer | speaker | TCP 23 | installing |
| computer | speaker | TCP 8008 | finding the speaker and asking what it is (Google Cast) |
| computer or phone | speaker | TCP 8090 | the speaker's web page |
| speaker | computer | TCP 8095, 18096–18099 | downloading Lithify and its updates from the helper |
| phone | speaker | mDNS (UDP 5353) and TCP 4070 | Spotify finding librespot and connecting to it |
| speaker | internet | TCP 443 and 4070 | Spotify |

The wizard searches only the computer's own network (its /24, e.g. 192.168.1.0–255). For a speaker
elsewhere, type its address into the wizard, or set `LITHIFY_HOST=<speaker address>` when you run
the installer. Spotify finds librespot through mDNS: across networks that needs an mDNS
reflector on your router (often called "mDNS repeater" or "Bonjour gateway").

A VPN on the computer, or Wi-Fi "client isolation" ("AP isolation"), keeps the speaker from
reaching the computer. The installer checks this before it changes anything and stops with "the
speaker cannot download from this computer".

## What changes on the computer

| | Windows | macOS and Linux |
|---|---|---|
| Lithify | `%LOCALAPPDATA%\lithify\app` | `~/.local/share/lithify` |
| The `lithify` command | `%LOCALAPPDATA%\lithify\bin` | `~/.local/bin` |
| Python, if the computer had none | Python 3.12 for this user (winget) | a private Python in `~/.local/share/lithify-tools` |
| Configuration | `%APPDATA%\lithify` | `~/.config/lithify` |
| Downloaded and built bundles | `%LOCALAPPDATA%\lithify\cache` | `~/.cache/lithify` |
| The helper that keeps speakers updatable | a scheduled task | a launchd agent (macOS), a systemd user service (Linux) |

Docker and git are installed only when Lithify is built on the computer. Neither is copied to the
speaker.

The installer's window shows every step and stays open at the end. You can run it again at any
time. It skips what is done already, and updates Lithify and the speaker to the newest release.

## Options

The installers read these environment variables:

| Variable | Effect |
|---|---|
| `LITHIFY_YES=1` | yes to every question |
| `LITHIFY_LANG=pl` or `en` | the language of the messages (default: the computer's) |
| `LITHIFY_HOST=<address>` | the speaker's address: no search, and the terminal instead of the wizard |
| `LITHIFY_NAME=<name>` | its name in Spotify |
| `LITHIFY_NO_WIZARD=1` | the terminal instead of the browser |
| `LITHIFY_NO_SETUP=1` | only Lithify and the `lithify` command |
| `LITHIFY_HOME` | where Lithify goes (`LITHIFY_BIN`: the command, on macOS and Linux) |
| `LITHIFY_REPO`, `LITHIFY_ARCHIVE_URL` | the git repository, or the `.zip` (or `.tar.gz`) used instead of git |

Over SSH, on Linux without a display, or with `LITHIFY_HOST` set, the speaker part runs in the
terminal instead of the browser. From a git checkout, `./installer/install.sh` (or
`.\installer\install.ps1`) uses the checkout in place.

## By hand

With the `lithify` command installed:

```sh
lithify wizard                     # the wizard, in the browser
lithify install                    # or in the terminal: finds the speaker the first time
lithify serve --install-service    # the helper for the speaker's web page
lithify ui                         # the speaker's page address
```

Every command: [commands.md](commands.md).

## Updating

*Update everything* on the speaker's page installs the newest release. Running the installer
again, or `lithify update`, does the same. The computer downloads the release and checks every
file. The speaker installs it only when it is newer than what it runs, and keeps its settings. *Roll back* on the page, or
`lithify rollback`, returns to the previous version.

A firmware update may reset the list of programs the speaker starts, so that Lithify no longer
starts. Its files stay on the speaker: run the installer again (or `lithify update`) to add it
back.

To build the newest versions yourself (Docker and git):

```sh
lithify build --latest && lithify update
```

This moves librespot, alsa-lib and Rust to their newest versions and builds them. A downloaded
release never replaces a build made on the computer after that release. Details:
[configuration.md](configuration.md#build-pins).

## Uninstalling

1. **The speaker.** On the computer, run:

   ```sh
   lithify uninstall
   ```

   This restores the speaker's stock list of programs, deletes Lithify's folder on the speaker
   (`/lsync/lithify`), and asks before it restarts the speaker. With several speakers, add
   `--speaker <id>` and repeat for each.
2. **The helper.** Run `lithify serve --uninstall-service`.
3. **The files.** Delete the folders listed in
   [What changes on the computer](#what-changes-on-the-computer). On Windows, also remove the
   firewall rules named "Lithify" (*Windows Defender Firewall → Advanced settings → Inbound
   Rules*).

Python, and Docker and git if the installer added them, stay installed. Remove them as you would
any other program if you no longer need them.
