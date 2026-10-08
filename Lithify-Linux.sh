#!/bin/sh
# Lithify for Linux.
#
# You see this text in an editor? Close it (in vim: type :q and press Enter), then right-click
# Lithify-Linux.sh in the file manager and choose "Run as a Program" - or run it in a terminal:
#   sh Lithify-Linux.sh
# Widzisz ten tekst w edytorze? Zamknij go (w vimie: wpisz :q i naciśnij Enter), kliknij
# Lithify-Linux.sh prawym przyciskiem w menedżerze plików i wybierz "Uruchom jako program".
#
# It runs install.sh from this folder (or downloads it when this file is on its own): that gets
# the computer ready, asking before it installs anything, then opens the Lithify wizard in the
# web browser, which finds the speaker and installs Lithify on it.
#
# (GNOME's Files opens scripts in an editor on a double-click. When "Run as a Program" is
# missing, allow it first: Properties > Permissions > "Allow executing file as program", or
# chmod +x Lithify-Linux.sh in a terminal.)
set -u
INSTALL_URL=${LITHIFY_INSTALL_URL:-https://raw.githubusercontent.com/OWNER/lithify/main/install.sh}
here=$(cd "$(dirname "$0")" 2>/dev/null && pwd -P) || here=$(pwd)
self="$here/$(basename "$0")"

# Started from a file manager, there is no terminal to show the progress and ask the questions
# in: open one, and run this file there.
if [ ! -t 0 ] && [ ! -t 1 ] && [ "${LITHIFY_IN_TERMINAL:-0}" != 1 ]; then
  LITHIFY_IN_TERMINAL=1
  export LITHIFY_IN_TERMINAL
  for t in x-terminal-emulator gnome-terminal ptyxis kgx konsole xfce4-terminal mate-terminal \
    alacritty kitty foot wezterm xterm; do
    command -v "$t" >/dev/null 2>&1 || continue
    case $t in
      gnome-terminal | kgx) exec "$t" -- sh "$self" ;;
      ptyxis) exec "$t" --new-window -- sh "$self" ;;
      xfce4-terminal | mate-terminal) exec "$t" -x sh "$self" ;;
      wezterm) exec "$t" start -- sh "$self" ;;
      kitty | foot) exec "$t" sh "$self" ;;
      *) exec "$t" -e sh "$self" ;;
    esac
  done
  msg="Lithify needs a terminal window. Open one (Terminal, Konsole, ...) and run:  sh '$self'"
  if command -v zenity >/dev/null 2>&1; then
    zenity --error --title=Lithify --text="$msg"
  elif command -v kdialog >/dev/null 2>&1; then
    kdialog --title Lithify --error "$msg"
  elif command -v notify-send >/dev/null 2>&1; then
    notify-send Lithify "$msg"
  fi
  printf '%s\n' "$msg" >&2
  exit 1
fi

cat <<'EOF'

  ==============================================================
    Lithify - Spotify Connect for your Lithe Audio speaker
    Lithify - Spotify Connect dla głośników Lithe Audio
  ==============================================================

  This gets your computer ready (it asks before it installs
  anything), then opens a page in your web browser that finds
  the speaker and installs Lithify on it. The first time takes
  15-30 minutes; your computer and the speaker must be on the
  same network.

EOF

LITHIFY_LAUNCHER=1
export LITHIFY_LAUNCHER
if [ -f "$here/install.sh" ]; then
  sh "$here/install.sh"
  rc=$?
else
  printf '==> downloading the installer: %s\n' "$INSTALL_URL"
  tmp=$(mktemp "${TMPDIR:-/tmp}/lithify-install.XXXXXX") || exit 1
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$INSTALL_URL" -o "$tmp"
    rc=$?
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$tmp" "$INSTALL_URL"
    rc=$?
  else
    printf 'error: there is neither curl nor wget to download the installer with\n' >&2
    rc=1
  fi
  if [ "$rc" = 0 ]; then
    sh "$tmp"
    rc=$?
  else
    printf 'error: could not download the installer: check the internet connection\n' >&2
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
printf '\n  Press Enter to close this window. '
read -r _ || true
exit "$rc"
