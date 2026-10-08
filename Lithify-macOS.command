#!/bin/sh
# Lithify for macOS: double-click this file. It opens in Terminal.
#
# It runs installer/install.sh from this folder (or downloads it when this file is on its own): that gets
# the Mac ready, asking before it installs anything, then opens the Lithify wizard in the web
# browser, which finds the speaker and installs Lithify on it.
#
# The first time, macOS may refuse to open a downloaded .command ("cannot be opened because it is
# from an unidentified developer", or "Apple could not verify ..."): right-click (Control-click)
# it, choose Open, then Open again. On macOS 15 and newer: System Settings > Privacy & Security >
# "Open Anyway". If macOS says you have no permission to run it: chmod +x Lithify-macOS.command
# (or run `sh Lithify-macOS.command` in Terminal). Then macOS asks whether Terminal may access
# files in the Downloads folder: Allow (the installer reads its files from there).
set -u
INSTALL_URL=${LITHIFY_INSTALL_URL:-https://raw.githubusercontent.com/OWNER/lithify/main/installer/install.sh}
here=$(cd "$(dirname "$0")" 2>/dev/null && pwd -P) || here=$(pwd)

cat <<'EOF'

  ==============================================================
    Lithify - Spotify Connect for your Lithe Audio speaker
    Lithify - Spotify Connect dla głośników Lithe Audio
  ==============================================================

  This gets your Mac ready (it asks before it installs anything),
  then opens a page in your web browser that finds the speaker
  and installs Lithify on it. The first time takes 15-30 minutes;
  your Mac and the speaker must be on the same network.

EOF

LITHIFY_LAUNCHER=1
export LITHIFY_LAUNCHER
if [ -f "$here/installer/install.sh" ]; then
  sh "$here/installer/install.sh"
  rc=$?
else
  printf '==> downloading the installer: %s\n' "$INSTALL_URL"
  tmp=$(mktemp "${TMPDIR:-/tmp}/lithify-install.XXXXXX") || exit 1
  if curl -fsSL "$INSTALL_URL" -o "$tmp"; then
    sh "$tmp"
    rc=$?
  else
    printf 'error: could not download the installer: check the internet connection\n' >&2
    rc=1
  fi
  rm -f "$tmp"
fi

printf '\n'
if [ "$rc" = 0 ]; then
  printf '  Lithify is ready. Enjoy the music!\n'
elif [ "$rc" = 130 ]; then
  printf '  Stopped. Run this again whenever you like: it goes on where it stopped.\n'
else
  printf '  Lithify is not installed yet: the messages above say why, and what to do.\n'
  printf '  You can run this again any time: it skips what is already done.\n'
fi
printf '\n  Press Return to close this window. '
read -r _ || true
exit "$rc"
