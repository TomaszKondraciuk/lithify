# Installation

The [README](../README.md#install) has the short version. This page covers the prompts the
installer meets on each system, what it changes on the computer, its options, and how to do the
same by hand.

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

## What changes on the computer

| | Windows | macOS and Linux |
|---|---|---|
| Lithify | `%LOCALAPPDATA%\lithify\app` | `~/.local/share/lithify` |
| The `lithify` command | `%LOCALAPPDATA%\lithify\bin` | `~/.local/bin` |
| Python, if the computer had none | Python 3.12 for this user (winget) | a private Python in `~/.local/share/lithify-tools` |
| Configuration | `%APPDATA%\lithify` | `~/.config/lithify` |
| Downloaded and built bundles | `%LOCALAPPDATA%\lithify\cache` | `~/.cache/lithify` |
| The helper that keeps speakers updatable | a scheduled task | a launchd agent (macOS), a systemd user service (Linux) |

Docker and git are installed only when Lithify is built on the computer. Nothing of them goes
onto the speaker.

The installer's window shows every step and stays open at the end. You can run it again at any
time. It skips what is done already and updates Lithify and the speaker to the newest release.

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

*Update everything* on the speaker's page installs the newest release. So do running the
installer again and `lithify update`. The computer downloads the release and checks every file
against the release's checksums. The speaker installs it only when it is newer than what it runs,
and keeps its settings. *Roll back* on the page, or `lithify rollback`, returns to the previous
version.

To build the newest versions yourself (Docker and git):

```sh
lithify build --latest && lithify update
```

This moves librespot, alsa-lib and Rust to their newest stable releases and builds them. A release
never replaces a bundle built on the computer after it. Details:
[configuration.md](configuration.md#build-pins).

## Uninstalling

On the speaker:

```sh
lithify uninstall
```

This restores the speaker's stock service list, deletes `/lsync/lithify` and restarts the speaker.

On the computer, remove the helper with `lithify serve --uninstall-service`, then delete the
folders listed in [What changes on the computer](#what-changes-on-the-computer).
