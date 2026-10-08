# Contributing to Lithify

Thanks for helping. Bug reports, fixes, translations and support for more speakers are all welcome.

## Reporting a bug

Open an issue with the bug report form. Before you do, please:

- look through [docs/troubleshooting.md](../docs/troubleshooting.md);
- run `lithify status` and paste its output into the report;
- if you attach logs, check them first. The speaker's own Spotify client writes your Spotify login
  into its logs. The page's logs leave it out, but raw logs from the speaker may contain it.

## Changing the code

The code has three parts:

| Part | Where | Language |
|---|---|---|
| The `lithify` command, wizard and helper | `lithify/`, `installer/` | Python 3.11+, POSIX sh, PowerShell 5.1 |
| The agent and the speaker's web page | `agent/` | Rust, plain JavaScript |
| Scripts that run on the speaker | `device/` | the speaker's `mksh` |

Run the same checks as CI before you open a pull request:

```sh
python -m unittest discover -s tests -t .
ruff check lithify tests bin/lithify .github/scripts
(cd agent && cargo test)
```

Guidelines:

- Keep each pull request to one change, with tests for it.
- Every check and message lives once, in `agent/rules.tsv` and `agent/settings.tsv`. The Rust
  agent and the Python side both read them.
- Texts shown to people exist in English and Polish. The tests fail when one language is missing.
- The computer side must work on Windows, macOS and Linux.
- Never touch the speaker's ALSA `Master` control directly, and send only `exec …` lines to its
  service console. Both rules are explained in [docs/platforms.md](../docs/platforms.md).

## Supporting another speaker

[docs/platforms.md](../docs/platforms.md#adding-a-platform) describes what a new platform needs.
LS10 speakers (Wi-Fi Speaker V3, WiFi PRO 2, iO1) need someone with a device to test on.

## License

By contributing you agree that your work is published under the [MIT License](../LICENSE).
