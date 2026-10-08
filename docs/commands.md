# Commands

The installer puts the `lithify` command on the computer. With several speakers, add
`--speaker <id>` to pick one (the ids are in `config.toml`, see
[configuration.md](configuration.md#several-speakers)).

## Everyday

| Command | What it does |
|---|---|
| `lithify wizard` | set up a speaker step by step in the web browser |
| `lithify install` | install Lithify on a speaker (it finds the speaker the first time), or update it |
| `lithify update [--settings]` | the same as `install`; `--settings` also sends `config.toml`'s settings, replacing the speaker's |
| `lithify status [--json]` | versions and health |
| `lithify settings` | the speaker's settings; `lithify settings set name="Kitchen" bitrate=160` changes them, `reset-pin` removes a forgotten PIN |
| `lithify ui` | the address of the speaker's web page |
| `lithify logs` | the agent's and librespot's logs |
| `lithify rollback [version\|stock]` | the previous version of Lithify; `stock` starts only the speaker's own programs again but keeps Lithify's files |
| `lithify uninstall` | remove Lithify and restore the stock speaker |

## Speakers on the network

| Command | What it does |
|---|---|
| `lithify discover` | list the Lithe Audio speakers on this network |
| `lithify detect [--host <address>]` | model, firmware, and whether the speaker is supported |
| `lithify setup --host <address>` | write the configuration for a speaker by hand |
| `lithify reboot` | restart the speaker and wait until it is back |
| `lithify sh <command>` | run a command on the speaker as root (for experts: there is no undo) |

## The software for the speaker

| Command | What it does |
|---|---|
| `lithify fetch [--if-newer]` | download the newest release's bundle (`--if-newer`: only when it is newer than the one here) |
| `lithify build [--latest] [--force]` | build the bundle with Docker (`--latest`: newest stable librespot, alsa-lib and Rust first) |
| `lithify check-updates` | list newer versions of librespot, alsa-lib, Rust, the libraries and Lithify |
| `lithify serve [--install-service \| --uninstall-service]` | the Lithify helper for the speaker's web page: gets and serves updates |

`lithify <command> --help` shows every option of a command.
