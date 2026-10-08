# Architecture

```
 your computer                                    the speaker (LS9)
 ─────────────                                    ─────────────────
 lithify (CLI)                                    Cast process manager
   setup · install · update · status …              ├─ stock services (Cast, LibreManager, official Spotify …)
   │                                                ├─ lithify_librespot:
   ├─ build ── Docker builder image                 │     lithify-agent exec-file librespot.args librespot
   │           (armv7 musl, alsa-lib, Rust)         │        └─ exec → librespot  ── Spotify Connect, ALSA, mixer
   │             └─ bundle: librespot, agent,       └─ lithify_agent:
   │                alsa.tar, VERSIONS, SHA256SUMS        lithify-agent run
   │                                                        ├─ watchdog (official Spotify, PCM conflicts,
   ├─ stage ── + librespot.args, agent.conf,               │   silent starts, fail-fast routes)
   │            process.json (from config.toml)            └─ web page :8090 ── status · tests · updates
   │                                                                 │
   └─ install ── HTTP (LAN) ──────────────► install.sh (root console)│
                                            /lsync/lithify ◄──────────┘ update: companion → install.sh
 lithify serve (companion :8095) ◄──────── web page: check · build · install
```

## Files on the speaker

```
/lsync/lithify/
  librespot            static armv7 binary
  lithify-agent        static armv7 binary (this repository's agent/)
  settings/            settings.conf (the speaker's settings), install.conf, settings.good.conf
                       (the last settings librespot ran well with)
  VERSIONS             what this bundle contains
  SHA256SUMS           checksums of the installed files
  alsa/                alsa-lib configuration for the static librespot (ALSA_CONFIG_DIR)
  cache/               librespot's credentials (Spotify login)
  prev/                the previous version, for rollback
  backup/              the stock /system/chrome/process.json
```

Everything is relative to the directory the agent runs from; `lithify-agent` sets
`ALSA_CONFIG_DIR` for librespot.

librespot downloads every track it plays (and the next one) whole into
`/tmp/lithify-librespot`, a tmpfs of its own (192 MB, emptied whenever librespot starts): the
speaker's `/tmp` is a 32 MB tmpfs that a long track or podcast would fill.

## Settings

`agent/settings.tsv` lists every option (type, default, check, where the page shows it, what
restarts) and `agent/rules.tsv` every check with its message (allowed characters and lengths,
networks that never get a fail-fast route, librespot options extra arguments must not repeat,
ports the speaker's services use). The agent embeds both and serves the page's form from them;
the `lithify` command reads the same files, so the speaker and the computer accept and refuse
alike. Both test suites run the cases in `agent/settings-cases.tsv`; a rule changes in one place. The speaker's values live in `settings/settings.conf` (owned by the services' user,
so the page can write it); `settings/install.conf` holds the computer's address and the speaker's
id. librespot's command line and the agent's options are derived from them at start, so a change
on the page needs only a restart of librespot or the agent.

A bundle carries `settings.default` (config.toml's values) and, only when sent on purpose,
`settings.patch`. `install.sh` has the new agent apply them before anything is replaced:
`settings.default` only when the speaker has no settings yet, the patch on top.

## Install and update

1. `lithify build` creates the bundle: librespot at the pinned tag plus back-ported upstream
   commits, every dependency refreshed (`cargo update`), libmdns patched for Linux 3.8, compiled
   for Cortex-A7 with NEON; the agent's unit tests run, then it is cross-compiled. Everything
   compiles in the Docker builder image; the crates and the compiler's output stay in Docker
   volumes, so the build is the same – and as fast – on Windows, macOS and Linux.
2. `lithify install` stages the bundle for one speaker – adding `librespot.args`, `agent.conf` and
   the service list rendered from `config.toml` – and serves it over HTTP on the LAN for the
   duration of the install.
3. On the speaker, `install.sh` checks the free space, downloads every file into `new/`, checks
   it against `SHA256SUMS`, gives it its final modes and flushes it, keeps the current version in
   `prev/` (hard links: no extra space) and only then moves the new files in, one rename each. A
   failed step leaves the running version untouched and removes the download.
4. `persist.sh` writes the service list; the speaker reboots only when it changed. Otherwise
   librespot and the agent are restarted.

From the web page the flow is the same, with `lithify serve` staging the files and the agent
running `install.sh` (embedded in the agent at build time) through the local root console. The
page never changes the service list, so it never needs a reboot.

## The agent

`lithify-agent` is a dependency-free Rust program (about 700 KB):

- `exec-file` restores default signal handling (the process manager starts children with
  `SIGCHLD` ignored, which breaks librespot's event hook), pins librespot's initial volume to the
  current hardware level, points alsa-lib at its configuration, prepares librespot's download
  tmpfs (mounted through the root console when it is missing, resized when an older version made
  it, emptied), closes the descriptors the process manager left open and `exec`s librespot;
- `onevent` is librespot's event hook: before librespot opens the audio device it pauses the
  Libre source that still holds it (LUCI protocol, TCP 7777);
- `run` is the watchdog plus the web page. It talks LUCI to follow the speaker's sources and uses
  the root console only for the two actions that need root (restarting the official client and
  adding routes).

What keeps it light and self-healing:

- **One logcat reader** for everything it reads (librespot's and its own lines for the page,
  official Spotify's errors for the silent-start watch). Each reader receives the speaker's whole
  log from logd, and the kernel stops it together with the agent (`PR_SET_PDEATHSIG`); readers an
  older agent left behind are stopped at start.
- **Polling reads one file, not all of /proc**: the last pid of a process is checked first, and
  official Spotify's per-thread counters are read only when the whole process uses enough CPU to
  hold a spinning thread. Routes come from `/proc/net/route`, the address from a UDP socket, slow
  answers (Cast, librespot) are cached for a few seconds: the page's status costs no processes.
- **Settings on trial**: librespot options changed on the page must let librespot run for 20 s
  within 90 s; otherwise librespot's options from the last settings it ran well with
  (`settings/settings.good.conf`, kept once it has run a minute with them) come back by
  themselves – the agent's own (PIN, port) stay. Files are replaced atomically and flushed.
- **librespot that keeps exiting** is slowed down (the wrapper waits longer the more often it
  started lately), its last good settings come back, and when a new version was installed just
  before, the agent rolls back to the previous one.
- **librespot health**: a librespot that stops answering on its zeroconf port for three minutes
  (and is not playing) is restarted, then again after 10, 30 and 60 minutes at most. One whose
  connection to Spotify closed (`Connection to server closed.`, a dealer that stopped answering)
  and that has not signed in again within 20 s is restarted too, never while it plays and at
  most every 10 minutes: idle, librespot would otherwise stay on the dead session until the next
  command from an app fails. A loss reported within 10 s of a new login is the replaced
  session's and does not count.
- **Nothing it starts can hang it**: tools (amixer, iwconfig, df) get a few seconds and are then
  killed, and a call that is stuck in a driver is not made again until it returns (the page gets
  the last answer meanwhile); the volume is read straight from the kernel (one ioctl), amixer is
  the fallback; the watchdog runs at a lower priority than audio (`nice 10`).
- **Firmware shells that spin**: the firmware runs event hooks through `sh -c` (e.g.
  `sh -c LUCI_local 494 StationConnected` after a Wi-Fi reconnect); with `SIGCHLD` ignored that
  shell keeps a whole core busy until the next reboot once its command has finished. The agent
  stops an orphaned, childless `sh -c` that holds a core for 30 s (the page counts them).
- **The page stays reachable**: when its port cannot be used, it falls back to 8090.
- **Fail-fast routes expire** a day after their address was last resolved, and go away when the
  settings no longer ask for them.

## Build reproducibility

`versions.toml` pins librespot, Lithify's own changes to it (patch files in `build/patches/`),
alsa-lib, the Rust release and the cross-compiler image. librespot is followed on its development
branch: its releases come about once a year, its fixes every few weeks. The pin is one commit of
that branch, the one this Lithify was built and tested with; `lithify build --latest` ("update
everything" without releases) moves on to the branch's newest commit, and a patch librespot has
taken in by then is skipped by itself. (A release tag in `ref`, without `commit`, works too.) The
build log, the resolved `Cargo.lock`, the librespot commit and the key crate versions recorded in
`VERSIONS` describe exactly what was built.
