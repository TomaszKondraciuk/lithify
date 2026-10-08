# Troubleshooting

Start with the speaker's web page (`lithify ui` prints its address) or `lithify status`; both
show what is running. `lithify logs` prints the agent's and librespot's recent log lines.

## `lithify setup` finds no speaker

- The computer and the speaker must be on the same network (and VLAN). `lithify discover` scans
  your computer's /24 for Google Cast devices (TCP 8008), then asks each one what it is; give the
  address directly with `lithify setup --host <ip>` otherwise (in the wizard: type it in). The
  Lithe app or your router shows it.
- "does not answer on the service console (TCP 23)": the speaker is not an LS9 model (see
  [platforms.md](platforms.md)), or something filters port 23 between you and it.

## The build fails, or the computer is very slow during it

- The first build compiles librespot on this computer. Its last step needs about 2.2 GB of free
  memory at once; with less (a computer with 4 GB and a browser open), Lithify uses thin LTO,
  which needs about 1.2 GB, and compiles once more that way when the system killed the compiler
  for memory. If it still runs out, the wizard says so: close other programs and try again.
  Docker Desktop (Windows, macOS) has a memory limit of its own: give it 4 GB or more.
- `~/.cache/lithify/build.log` (Windows: `%LOCALAPPDATA%\lithify\cache\build.log`) has the
  whole build.

## The new device does not appear in Spotify

- Wait a minute after an install or reboot; `lithify status` should show librespot's version.
- Spotify finds librespot with mDNS (zeroconf). Phone and speaker must be on the same network;
  "client isolation" or "AP isolation" on the Wi-Fi blocks it, so do some mesh/guest networks.
- Once you have played on it, librespot stores the login (`cache/credentials.json`) and connects
  by itself after restarts; the web page shows "Spotify account: saved".
- Look for the name set on the page (*Settings → Name in Spotify*).

## It connects, but there is no sound

- Check the *Audio* card on the web page: the speaker volume may be at zero or muted; raise it in
  the Lithe app or with the Spotify slider.
- Another source (Cast, AirPlay, Bluetooth, the official client) may hold the audio device; the
  page's *Audio* card shows the Libre source and whether the output is playing. librespot pauses
  that source before it plays; if it can't, pause it in its own app.

## It's quiet

`mixer = "alsa"` (the default) makes the Spotify slider the speaker's own volume, with the full
range. With `mixer = "softvol"` librespot only scales samples below the speaker's volume.

## Stutter, long start, skipping

- Run **Test connections** on the web page. Hosts with "no connection" or "slow" are the
  problem. When a CDN host keeps failing (on some ISPs Fastly – `*.scdn.co`), add it to
  `fastfail_hosts` and run `lithify update`; Spotify then skips it immediately.
- Look at the Wi-Fi card: a signal below about −70 dBm, or the 2.4 GHz band in a busy area,
  causes drop-outs. A 5 GHz network usually helps.
- The librespot test counts buffer underruns in the recent log; a few at track changes are
  harmless, many during playback point to the network.

## The official Spotify device misbehaves

That is the firmware's client (eSDK 3.194); Lithify can only restart it. The agent does that
automatically when it spins or crashes, and the web page has a restart button. Prefer the
librespot device.

## After a firmware or Cast update Lithify is gone

The files in `/lsync/lithify` survive, but a Cast update may replace the service list. Run
`lithify update`: it detects the stock list, re-adds the two entries, and reboots once.

## The web page's update buttons are greyed out

They need `lithify serve` on your computer (the installer starts it), reachable from the speaker
on port 8095:

```sh
lithify serve --install-service      # starts it with the computer, and now
```

It runs as a scheduled task "Lithify companion" on Windows (log in
`%LOCALAPPDATA%\lithify\logs\companion.log`), a launchd agent on macOS
(`~/Library/Logs/lithify/companion.log`) and a systemd user service on Linux
(`systemctl --user status lithify-companion`).

The speaker learns the helper's address at install time; if your computer's address has changed
since, run `lithify update`. On a laptop that sleeps, the buttons work while it is awake.

## The speaker cannot download from the computer (firewall)

The speaker connects to the computer on TCP 8095 (the helper) and 18096–18099 (the temporary
servers of `lithify install`). On Windows, Lithify adds the firewall rule "Lithify" for these ports from the
local network – Windows asks for consent once; if you said no:

```powershell
netsh advfirewall firewall add rule name=Lithify dir=in action=allow protocol=TCP localport=8095,18096-18099 remoteip=localsubnet profile=any
```

(in a PowerShell opened as administrator). With ufw on Linux:
`sudo ufw allow from 192.168.0.0/16 to any port 8095,18096:18099 proto tcp`; macOS asks on its own
when its firewall is on.

`lithify install` checks this before it changes anything: the speaker downloads a small file from
the computer first, and when it cannot, the install stops with "the speaker cannot download from
this computer" and what to do on this system (it names this computer's network for ufw). The
same happens when the computer and the speaker are not really on one network: a VPN on the
computer, or a guest Wi-Fi that keeps devices apart.

## Build problems

- `Docker is required`: install Docker (Docker Desktop on Windows and macOS – it must be
  running), or download a published bundle with `lithify fetch`. When `versions.toml` names a
  release (`[release] url`), `lithify install` downloads that bundle first and builds only when
  it cannot.
- Macs with Apple chips and ARM Linux computers (a Raspberry Pi) build too: Docker runs the
  builder image's arm64 variant, and the agent's tests run on that CPU.
- Network errors while building: the build keeps a local mirror of librespot in the cache
  directory and the downloaded crates in a Docker volume; retry later, or run `lithify build`
  again – finished steps are reused.
- `another build is running`: a build started from the speaker's page (or another terminal) is
  still going; wait for it. A build from the page that runs longer than two hours is stopped,
  and so is one whose companion stops; build containers left behind by a build that was killed
  are removed when the companion starts.
- `local patch … does not apply`: librespot changed the code the patch touches (after "update
  everything" moved to a newer commit); the previous version keeps running. Refresh the patch in
  `build/patches/`, or drop it from `local_patches` in `versions.toml`.
- `librespot commit … is not on dev`: the commit is not in the local mirror (GitHub was
  unreachable during the first build); retry when the network is back.

## I forgot the web page's PIN

```sh
lithify settings reset-pin
```

removes it through the service console; then set a new one on the page.

## A setting changed on the page "did not stick"

When librespot cannot keep running with new options (for example an extra argument it does not
know), the agent restores the previous settings after about 90 seconds and says so in the
page's events. Correct the value and save again.

## "Update everything" failed

With releases, the computer downloads the newest one: when it cannot (no internet, GitHub out of
reach), the page says so and the speaker keeps what it runs; try again later. Every file is checked
against the release's checksums, and nothing is replaced unless all of them arrived intact.

Without releases, the page shows the build's last lines. When newer librespot, alsa-lib or Rust
releases do not build, Lithify keeps the previous versions, so the speaker is unchanged; try again
later or report the error. A failed install leaves the running version in place, and *Roll back*
returns to the one before.

When the update of Lithify itself does not start, the companion goes back to the code that
worked (`git reset --keep`, which never touches uncommitted changes in the checkout). If changes
of yours are in the way, it leaves everything as it is and says so (on the page, or in the
companion's log); go back yourself with `git reset --keep <commit>`, or commit your changes first.

## Undo everything

```sh
lithify rollback          # previous version
lithify uninstall         # stock service list, delete /lsync/lithify, reboot
```
