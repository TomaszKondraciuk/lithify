# Security

## The speaker's service console

Every LS9 speaker we have seen (firmware p15525.144.0 on the Wi-Fi Ceiling Speaker V2) answers
on **TCP port 23** with a service console that runs whatever it receives **as root, without a
password**. It is part of the stock firmware; Lithify did not add it and cannot remove it (the
root file system is read-only and has a single copy, so it is never modified).

What that means:

- anyone who can reach the speaker on your network can take full control of it, with or without
  Lithify;
- a compromised device on the same network can do the same.

Recommendations:

- keep the speakers on a network segment you trust – ideally a separate IoT VLAN or SSID that
  other devices can't reach on port 23. Lithify still works there, but the networks must let
  some traffic through: see [Network requirements](installation.md#network-requirements);
- never forward any of the speaker's ports from the internet;
- treat the speaker like any other unmanaged IoT device.

Lithify uses the console only from your computer (to install) and from the speaker itself (the
agent restarts the official Spotify client and adds fail-fast routes through `127.0.0.1:23`).

## Your Spotify login on the speaker

The first time you pick the speaker in the Spotify app, Spotify gives librespot a reusable login
token. librespot keeps it in `/lsync/lithify/cache/`, so it reconnects after restarts. Anyone who
can use the service console can read that file. The speaker's built-in Spotify client is worse:
it writes your user name and its own login token into the speaker's log at every login. Lithify's
page leaves that token out of the logs it shows; raw logs from the speaker still contain it.

If you suspect someone used your account, sign out of all devices in your Spotify account
settings (*Account → Sign out everywhere*). Both clients then need to be picked again in the app.

## The web page

`http://<speaker>:8090` is served by `lithify-agent` on the local network only:

- **No PIN by default.** Until you set one (*Settings → PIN*), anyone on your network can change
  the speaker's settings, install updates and roll back. GET requests only show status and logs.
- **CSRF**: every action is a POST that must carry the header `X-Lithify: 1`. A page on another
  site cannot add it without a CORS preflight, which the agent never grants, and a request with
  a foreign `Origin` is refused.
- **DNS rebinding**: requests are served only when the `Host` header is an IP address or a local
  name (`.local`, `.lan`, `.home`, `.home.arpa`, `.internal`, `.localdomain`, single-label names).
- **PIN** (optional): with `ui_pin` set, every action needs it: settings, the update check,
  updates, rollback and restarts. It has 4 to 12 digits; six or more are harder to guess. The page
  never shows the PIN back, and `lithify settings reset-pin` removes a forgotten one.
  - Five wrong attempts lock out the device that made them for a minute. The lock doubles after
    every further five, up to an hour. Other devices are not affected.
  - The PIN is compared in constant time.
  - The page is plain HTTP. The PIN stops other people on your network from clicking buttons, but
    not someone who can capture your Wi-Fi traffic.
- **Limits**: a request must arrive within 5 seconds, with at most 8 KB of headers and 32 KB of
  form data, and its reply must be taken within 30 seconds. The agent serves at most 8
  connections per device and 24 in all. Slow requests (logs, network test, update check, build
  status) run at most two at a time and share one answer for a few seconds. So a misbehaving
  device cannot block the page or keep the speaker busy.
- **Updates come from one place.** The agent installs only from the helper address written at
  install time (`lithify install`), never from an address in a request. Every file is checked
  against `SHA256SUMS` before anything is replaced. That catches broken transfers, but it is not a
  signature: the files and the sums come from the same helper. The previous version is kept for
  rollback.
- **The page's script** inserts every value with `textContent`. The page is served with a strict
  Content-Security-Policy: scripts and styles only from the speaker's own files (no inline code),
  Trusted Types (no HTML built from strings), no framing and no form posts. Logs shown on the page
  leave out the Spotify login the built-in client writes into them, and shorten the account name.

## The helper (`lithify serve`)

The Lithify helper runs on your computer and listens on its address on the speakers' network,
port 8095. (Its configuration and service names call it the companion.)

- It answers only the speakers listed in `config.toml`, each only for its own files. Other
  addresses are disconnected before their request is read.
- It refuses requests from web browsers (any `Origin` header) and requests whose `Host` is not an
  IP address or the configured name. So a web page you visit cannot start builds or read a
  speaker's settings.
- It downloads or builds bundles and serves the staged files. It never accepts uploads, and its
  error messages and build log leave out your home folder's path.
- The files it stages for a speaker carry that speaker's settings, the page's PIN too. On Linux
  and macOS only your user can read them.
- Speakers configured by name are looked up again every minute. An address that none of them has
  any more is no longer let in.

## The bundle

A release is built by the same steps on GitHub, from this repository. The release workflow signs
its `SHA256SUMS` with Lithify's Ed25519 key (`SHA256SUMS.sig`). `lithify fetch` downloads the
release over HTTPS, checks the signature against the public key in `versions.toml`, and only then
checks each file against the sums. A release whose signature is missing or wrong is refused, and
nothing on the computer is replaced. The check is the same on every system: Lithify carries its
own small Ed25519 implementation (`lithify/ed25519.py`, tested against RFC 8032's vectors).

`lithify build` builds it from source instead:

- librespot from its official repository, at the commit of its development branch (`dev`) pinned in
  `versions.toml`, plus the local patches listed there (each is left out once upstream has it);
- Rust dependencies from crates.io;
- alsa-lib from alsa-project.org.

The Docker image and every build step are in [`build/`](../build).

## Reporting

Please report security problems in Lithify privately, not in a public issue: on GitHub, open the
repository's *Security* tab and choose *Report a vulnerability*.
