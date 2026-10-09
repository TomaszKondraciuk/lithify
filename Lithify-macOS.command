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
INSTALL_URL=${LITHIFY_INSTALL_URL:-https://raw.githubusercontent.com/TomaszKondraciuk/lithify/main/installer/install.sh}
here=$(cd "$(dirname "$0")" 2>/dev/null && pwd -P) || here=$(pwd)

# The language of the messages: LITHIFY_LANG (pl or en), else the system's: on macOS its display
# language, elsewhere the locale (LANGUAGE, LC_ALL, LC_MESSAGES, LANG). (installer/install.sh: the same.)
lithify_lang() {
  case ${LITHIFY_LANG:-} in
    pl* | PL*) echo pl && return 0 ;;
    en* | EN*) echo en && return 0 ;;
    *) ;;
  esac
  if [ "$(uname -s)" = Darwin ]; then
    first=$(defaults read -g AppleLanguages 2>/dev/null | sed -n '2s/[^A-Za-z-]//gp')
    case $first in
      pl*) echo pl && return 0 ;;
      ?*) echo en && return 0 ;;
      *) ;;
    esac
  fi
  for v in "${LANGUAGE:-}" "${LC_ALL:-}" "${LC_MESSAGES:-}" "${LANG:-}"; do
    if [ -n "$v" ]; then
      case $v in pl*) echo pl ;; *) echo en ;; esac
      return 0
    fi
  done
  echo en
}
if [ "$(lithify_lang)" = pl ]; then PL=1; else PL=0; fi
L() { if [ "$PL" = 1 ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }  # L ENGLISH POLISH

if [ "$PL" = 1 ]; then
  cat <<'EOF'

  ==============================================================
    Lithify - Spotify Connect dla głośników Lithe Audio
  ==============================================================

  Ten program przygotuje Maca (zapyta, zanim cokolwiek
  zainstaluje), a potem otworzy w przeglądarce stronę, która
  znajdzie głośnik i zainstaluje na nim Lithify. Za pierwszym
  razem trwa to 15-40 minut; Maca i głośnik muszą być
  w tej samej sieci.

EOF
else
  cat <<'EOF'

  ==============================================================
    Lithify - Spotify Connect for your Lithe Audio speaker
  ==============================================================

  This gets your Mac ready (it asks before it installs
  anything), then opens a page in your web browser that finds
  the speaker and installs Lithify on it. The first time takes
  15-40 minutes; your Mac and the speaker must be on the
  same network.

EOF
fi

LITHIFY_LAUNCHER=1
export LITHIFY_LAUNCHER
if [ -f "$here/installer/install.sh" ]; then
  sh "$here/installer/install.sh"
  rc=$?
else
  printf '==> %s %s\n' "$(L 'downloading the installer:' 'pobieranie instalatora:')" "$INSTALL_URL"
  tmp=$(mktemp "${TMPDIR:-/tmp}/lithify-install.XXXXXX") || exit 1
  if curl -fsSL "$INSTALL_URL" -o "$tmp"; then
    sh "$tmp"
    rc=$?
  else
    printf '%s\n' "$(L 'error: could not download the installer: check the internet connection' 'błąd: nie udało się pobrać instalatora: sprawdź połączenie z internetem')" >&2
    rc=1
  fi
  rm -f "$tmp"
fi

printf '\n'
if [ "$rc" = 0 ]; then
  printf '  %s\n' "$(L 'Lithify is ready. Enjoy the music!' 'Lithify jest gotowy. Miłego słuchania!')"
elif [ "$rc" = 130 ]; then
  printf '  %s\n' "$(L 'Stopped. Run this again whenever you like: it goes on where it stopped.' 'Przerwano. Uruchom to ponownie, kiedy zechcesz: będzie kontynuować tam, gdzie skończyło.')"
else
  printf '  %s\n' "$(L 'Lithify is not installed yet: the messages above say why, and what to do.' 'Lithify nie jest jeszcze zainstalowany: komunikaty powyżej mówią dlaczego i co zrobić.')"
  printf '  %s\n' "$(L 'You can run this again any time: it skips what is already done.' 'Możesz to uruchomić ponownie w każdej chwili: pominie to, co już zrobione.')"
fi
printf '\n  %s ' "$(L 'Press Return to close this window.' 'Naciśnij Return, aby zamknąć to okno.')"
read -r _ || true
exit "$rc"
