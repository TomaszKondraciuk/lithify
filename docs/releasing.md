# Releasing

The repository stays private until what Lithify found on the speakers has gone to Lithe Audio
(responsible disclosure). Going public, once:

1. `python3 .github/scripts/go-public.py <owner>/<repository>`: the launchers, the installers and
   the READMEs then download from that repository (they say `OWNER/lithify` until then), and
   `versions.toml` names the releases' bundle (`[release] url`).
2. Commit and push that, and make the repository public. Then, in the repository's settings, turn
   on *Private vulnerability reporting* (*Security*), which [security.md](security.md#reporting)
   points to; GitHub offers it only for public repositories.
3. Check that the repository has the secret `LITHIFY_SIGNING_KEY`: the private key of the Ed25519
   key pair whose public half is `[release] public_key` in `versions.toml`. The release workflow
   signs `SHA256SUMS` with it and stops when it is missing or does not match. Keep a copy of it
   offline: without it no new release can be signed, and a new key pair means a new
   `public_key`, which older installs do not know (they then refuse new releases until Lithify on
   the computer is updated).
4. Tag the first release: `git tag v0.1.0 && git push origin v0.1.0`. The release workflow
   (`.github/workflows/release.yml`) builds the bundle in Docker (about 20 minutes) and publishes
   it together with `Lithify-Windows.cmd`, `Lithify-macOS.zip` and `Lithify-Linux.zip` (the last
   two in ZIPs: a browser's download is not executable, an unpacked ZIP is).

From then on every install downloads the bundle instead of building it: no Docker, git or WSL,
and a few minutes instead of up to an hour; the README's links point to the newest release. Each
later release is a new tag. When the download fails, the installer builds the bundle on the
computer as before (Docker and git are needed then).

Updates come the same way: "Update everything" on the speaker's page, running the installer
again, and `lithify update` download a release that is newer than the bundle on the computer (by
when each was built, so one built there later with `lithify build --latest` is kept). Building
the newest versions between releases stays `lithify build --latest` (Docker and git), then
"Install the computer's build" on the page.
