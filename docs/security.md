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
  other devices can't reach on port 23;
- never forward any of the speaker's ports from the internet;
- treat the speaker like any other unmanaged IoT device.

Lithify uses the console only from your computer (to install) and from the speaker itself (the
agent restarts the official Spotify client and adds fail-fast routes through `127.0.0.1:23`).

## The web page

`http://<speaker>:8090` is served by `lithify-agent` on the local network only:

- **Read-only by default.** GET requests show status and logs; nothing changes state.
- **CSRF**: every action is a POST that must carry the header `X-Lithify: 1`. A page on another
  site cannot add it without a CORS preflight, which the agent never grants, and a request with
  a foreign `Origin` is refused.
- **DNS rebinding**: requests are served only when the `Host` header is an IP address or a local
  name (`.local`, `.lan`, `.home`, `.home.arpa`, `.internal`, `.localdomain`, single-label names).
- **PIN** (optional): with `ui_pin` set, actions (settings, update check, updates, rollback,
  restarts) need it (`X-Lithify-Pin`). The page never shows the PIN back; a forgotten one is
  removed with `lithify settings reset-pin` (through the service console). Five wrong attempts lock out the device that made them
  for a minute, twice as long after every further five (at most an hour); other devices are not
  affected. It has 4 to 12 digits; six or more are harder to guess. The PIN is compared in constant time. The page is plain HTTP,
  so the PIN protects against other people on the network clicking buttons, not against someone
  who can capture your Wi-Fi traffic.
- **Fair use**: a request must arrive within 5 seconds (at most 8 KB of headers and 32 KB of
  form data) and its reply be taken within 30; at most 8 connections per device and 24 in all
  are served. The slow requests (logs, network test, update check, build status) run at most
  two at a time and share one answer for a few seconds, so a misbehaving device cannot hold
  the page or keep the speaker busy.
- **Updates come from one place.** The agent installs only from the companion URL written into
  its configuration at install time (`lithify install`), never from an address in a request.
  Every file is verified against `SHA256SUMS` before anything is replaced (this catches broken
  transfers; the files and the sums come from the same helper, so it is not a signature), and the
  previous version is kept for rollback.
- The page's script inserts every value with `textContent` and is served with a strict
  Content-Security-Policy: scripts and styles only from the speaker's own files (no inline code),
  Trusted Types (no HTML built from strings), no framing, no form posts. Logs shown on the page
  leave out the Spotify login the official client writes into them, and shorten the account name.

## The companion (`lithify serve`)

Runs on your computer, binds to its address on the speakers' network (port 8095) and answers only
the speakers listed in `config.toml`, each for its own files; other addresses are disconnected
before their request is read. Requests from web browsers (any `Origin` header) and requests whose
`Host` is not an IP address or the configured name are refused, so a web page you visit cannot
start builds or read a speaker's settings. It builds bundles with Docker and serves the staged
files; it never accepts uploads, and its error messages and build log leave out your home path.
The files it stages for a speaker carry that speaker's settings (the web page's PIN too), so on
Linux and macOS only your user can read them. Speakers configured by name are looked up again every
minute: an address none of them has any more is no longer let in.

## The bundle

Built from source by `lithify build`: librespot from its official repository – the commit of its
development branch (`dev`) pinned in `versions.toml`, plus the local patches listed there (each
is left out once upstream has it); Rust dependencies from crates.io; alsa-lib from
alsa-project.org. The Docker image and every build step are in [`build/`](../build).
`lithify fetch` downloads a published bundle over HTTPS and checks each file against its
`SHA256SUMS`.

## Reporting

Please report security problems in Lithify privately to the maintainers before opening a public
issue.
