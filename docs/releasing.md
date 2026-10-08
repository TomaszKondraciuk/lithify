# Releasing

The repository stays private until what Lithify found on the speakers has gone to Lithe Audio
(responsible disclosure). Going public, once:

1. `python3 .github/scripts/go-public.py <owner>/<repository>`: the launchers, the installers and
   the READMEs then download from that repository (they say `OWNER/lithify` until then), and
   `versions.toml` names the releases' bundle (`[release] url`).
2. Commit and push that, and make the repository public.
3. Tag the first release: `git tag v0.1.0 && git push origin v0.1.0`. The release workflow
   (`.github/workflows/release.yml`) builds the bundle in Docker (about 20 minutes) and publishes
   it together with `Lithify-Windows.cmd`, `Lithify-macOS.zip` and `Lithify-Linux.zip` (the last
   two in ZIPs: a browser's download is not executable, an unpacked ZIP is).

From then on every install downloads the bundle instead of building it: no Docker, git or WSL,
and a few minutes instead of up to an hour; the README's links point to the newest release. Each
later release is a new tag. When the download fails, the installer builds the bundle on the
computer as before (Docker and git are needed then).

Between releases, the speaker's page ("Update everything") builds the newest versions on the
computer, as `lithify build` does.
