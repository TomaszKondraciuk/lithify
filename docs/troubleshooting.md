# Troubleshooting

Start with the speaker's web page. `lithify ui` prints its address, usually
`http://<speaker>:8090`. The banner at the top names what is wrong, and each card shows what is
running. On the computer, `lithify status` shows the same, and `lithify logs` prints the recent
log lines of librespot and the Lithify agent.

## The wizard does not find my speaker

- The computer and the speaker must be on the same network. The wizard searches only the
  computer's own network (for example 192.168.1.0 to 192.168.1.255).
- Type the speaker's address into the wizard instead. You find it in the Lithe Audio or Google
  Home app (the speaker's settings, device information) or in your router's list of devices.
- "Does not answer on the service console (TCP 23)": the speaker is not an LS9 model (see
  [platforms.md](platforms.md)), or something between the computer and the speaker blocks port 23.
- Speakers on a separate network (VLAN): see
  [Network requirements](installation.md#network-requirements).

## The new device does not appear in Spotify

- Wait a minute after an install or a restart. The speaker's page should say that librespot is
  running.
- The phone and the speaker must be on the same network. Spotify finds the device with mDNS, a way
  for devices to announce themselves on a local network. Wi-Fi "client isolation" ("AP
  isolation"), some guest networks and some mesh systems block it.
- Look for the name shown on the page under *Settings → Name in Spotify*. By default it is the
  speaker's name followed by "(librespot)".
- After you have played on it once, the speaker keeps the login and connects by itself after
  restarts. The page then shows "Spotify account: saved".

## It connects, but there is no sound

- Check *Speaker volume* on the page's *Spotify Connect* card. It may be at zero: raise it with
  the Spotify slider or in the Lithe Audio app.
- Another source may hold the speaker: Cast, AirPlay, Bluetooth or the built-in Spotify. The
  speaker has one audio output, and the card's *Now* line says when another source is playing.
  librespot pauses that source before it plays. If it cannot, pause the source in its own app.

## It is quiet

With the default setting the Spotify slider controls the speaker's own volume, over its full
range. If you changed *Volume control* to "librespot only" (`mixer = "softvol"`), librespot can
only make the sound quieter than the speaker's own volume. Switch it back on the page.

## Stutter, a long start, skipping

- Click **Check connections** on the page. Servers marked "no connection" or "slow" are the
  problem.
- Some internet providers reach a part of Spotify's content servers (CDN) badly. Spotify then
  waits 10 seconds or more before it tries another one. If the same server keeps failing, add its
  name to *Settings → Advanced → CDN servers to skip*, and the speaker will skip it at once.
- Look at the *Network* card. A signal weaker than about −70 dBm (a lower number is weaker), or the
  2.4 GHz band in a building with many networks, causes drop-outs. A 5 GHz network usually helps.
- **Run a check** on the librespot card counts buffer underruns, the moments when music ran out
  before more arrived. A few at track changes are harmless. Many during playback point to the
  network.

## The built-in Spotify device misbehaves

That device is the speaker's own Spotify client, and Lithify cannot fix it. It can only restart
it. The agent does that by itself when the client hangs or crashes, and the page has a restart
button. Use the librespot device instead, or hide the built-in one on the page.

## After a firmware update Lithify is gone

A firmware or Google Cast update may reset the list of programs the speaker starts. Lithify's
files stay on the speaker. Run the installer again, or `lithify update`: it adds Lithify back and
restarts the speaker once.

## The page's update buttons are greyed out

They need the Lithify helper on your computer. The helper is a small background service that the
installer sets up. Check that the computer is on and awake, and on the same network as the
speaker. To start the helper again:

```sh
lithify serve --install-service
```

| System | The helper is | Its log |
|---|---|---|
| Windows | the scheduled task "Lithify companion" | `%LOCALAPPDATA%\lithify\logs\companion.log` |
| macOS | a launchd agent | `~/Library/Logs/lithify/companion.log` |
| Linux | the systemd user service `lithify-companion` | `systemctl --user status lithify-companion` |

The speaker learns the computer's address when Lithify is installed. If the computer's address
has changed since, run `lithify update` once.

## The speaker cannot download from the computer

The speaker downloads Lithify from your computer on TCP 8095 and 18096–18099. The installer checks
this before it changes anything. When the check fails, it stops with "the speaker cannot download
from this computer" and says what to do on your system. Common causes:

- **Windows:** the firewall rule "Lithify" is missing, because the permission question was
  answered with *No*. Run the installer again and answer *Yes*. Or, in PowerShell opened as
  administrator:

  ```powershell
  netsh advfirewall firewall add rule name=Lithify dir=in action=allow protocol=TCP localport=8095,18096-18099 remoteip=localsubnet profile=any
  ```

  Also set the network to *Private* (*Settings → Network & internet → Properties*).
- **macOS:** the firewall is on and Python was not allowed. Open *System Settings → Network →
  Firewall → Options* and allow incoming connections for Python.
- **Linux with ufw:** open the ports for your network. Replace `192.168.1.0/24` with yours:
  `sudo ufw allow from 192.168.1.0/24 to any port 8095,18096:18099 proto tcp`
- **Any system:** a VPN on the computer, or a guest Wi-Fi that keeps devices apart.

## A setting changed on the page did not stick

If librespot cannot run with new settings (for example an extra option it does not know), the
agent puts the previous settings back after about 90 seconds and notes it in the page's events.
Correct the value and save again.

## I forgot the page's PIN

```sh
lithify settings reset-pin
```

This removes the PIN. Then set a new one on the page.

## "Update everything" failed

The page shows why, and the speaker keeps the version it runs.

- **Lithify installed from a release:** the computer could not download the newest one, because
  the internet or GitHub was out of reach. Try again later. Nothing is replaced unless every file
  arrived intact.
- **Lithify built on your computer:** the page shows the build's last lines. When newer versions
  of librespot, alsa-lib or Rust do not build, Lithify keeps the previous ones. Try again later,
  or report the error.

A failed install leaves the running version in place, and **Roll back** returns to the one before.

When the update of Lithify itself does not start, the helper goes back to the code that worked
(`git reset --keep`, which never touches uncommitted changes). If changes of yours are in the way,
it leaves everything as it is and says so. Go back yourself with `git reset --keep <commit>`, or
commit your changes first.

## Building Lithify on the computer

You only build when no ready-made release can be downloaded.

- **`Docker is required`:** install Docker. On Windows and macOS that is Docker Desktop, and it
  must be running. Or download a release instead: `lithify fetch`.
- **The build fails, or the computer slows down during it:** the build needs about 4 GB of free
  memory. Close other programs and try again; Lithify uses a lighter build mode on its own when
  memory is short. In Docker Desktop, give Docker at least 4 GB (*Settings → Resources*).
- **Network errors while building:** run `lithify build` again later. Finished steps are reused.
- **`another build is running`:** a build started from the speaker's page or from another terminal
  is still going. Wait for it to finish.
- **`local patch … does not apply`** or **`librespot commit … is not on dev`:** the librespot code
  moved on, or GitHub was out of reach during the first build. The speaker keeps its version. See
  [architecture.md](architecture.md#build-reproducibility).
- The whole build log is in `~/.cache/lithify/build.log`, on Windows
  `%LOCALAPPDATA%\lithify\cache\build.log`.

## Undo everything

```sh
lithify rollback          # the previous version of Lithify
lithify uninstall         # the speaker's stock software again
```

Removing Lithify from the computer too: [installation.md](installation.md#uninstalling).
