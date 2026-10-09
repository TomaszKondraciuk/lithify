#!/bin/sh
# Lithify installer for Linux and macOS:
#
#   curl -fsSL https://raw.githubusercontent.com/TomaszKondraciuk/lithify/main/installer/install.sh | sh
#   ./installer/install.sh           (from a checkout: uses it in place)
#   Lithify-macOS.command, Lithify-Linux.sh: the same with a double-click
#
# (Windows: installer/install.ps1, or Lithify-Windows.cmd.)
#
# It makes this computer ready, and asks before it installs anything:
#   1. Python 3.11 or newer: the system's when it has one, otherwise a private Python 3.12 for
#      Lithify only (installed by uv; no administrator rights, nothing else changes);
#   2. Lithify itself: with git when it is there, otherwise from GitHub's archive;
#   3. the `lithify` command;
#   4. Docker and git, which build Lithify for the speaker (not needed when a prebuilt bundle is
#      already there), and the firewall ports the speaker downloads from.
# Then it opens the Lithify wizard in the web browser: find the speaker, choose its name, install.
# Without a browser (an SSH session, no display) or with LITHIFY_HOST it does the same here in
# the terminal: `lithify install --reboot`, then `lithify serve --install-service`.
#
# Environment:
#   LITHIFY_YES=1        answer yes to every question (install what is missing)
#   LITHIFY_HOST         the speaker's address: no search, and the terminal instead of the wizard
#   LITHIFY_NAME         its name in Spotify
#   LITHIFY_NO_WIZARD=1  the terminal instead of the browser wizard
#   LITHIFY_NO_SETUP=1   only steps 1-3
#   LITHIFY_HOME         Lithify's folder (~/.local/share/lithify); LITHIFY_BIN: the command's (~/.local/bin)
#   LITHIFY_REPO         git URL; LITHIFY_ARCHIVE_URL: the .tar.gz or .zip to use instead of git
#   LITHIFY_TOOLS        where the private Python goes (~/.local/share/lithify-tools)
#   LITHIFY_LANG         the language of the messages: pl or en (otherwise this computer's)
set -eu

REPO_URL=${LITHIFY_REPO:-https://github.com/TomaszKondraciuk/lithify.git}
DEST=${LITHIFY_HOME:-$HOME/.local/share/lithify}
BIN=${LITHIFY_BIN:-$HOME/.local/bin}
# uv and the private Python, when this computer has no suitable Python: never inside DEST, which
# may be a git checkout that has to stay clean.
TOOLS=${LITHIFY_TOOLS:-$HOME/.local/share/lithify-tools}
# Where `lithify build` keeps the bundle (hostos.cache_dir).
CACHE=${LITHIFY_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/lithify}
UV_RELEASES=https://github.com/astral-sh/uv/releases/latest/download
OS=$(uname -s)
PY="" CMD="" HERE="" SUDO="" FAMILY="" SG="" USE_SG=0 RELOGIN=0 STAGE="" WORK=""

# The language of the messages: LITHIFY_LANG (pl or en), else the system's: on macOS its display
# language, elsewhere the locale (LANGUAGE, LC_ALL, LC_MESSAGES, LANG).
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
# The ports the speaker downloads from, in the words of the messages.
PORTS=$(L "TCP 8095 and 18096-18099" "TCP 8095 i 18096-18099")

say() { printf '==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%s %s\n' "$(L "warning:" "uwaga:")" "$*" >&2; }
# A failure: what went wrong, then what to do next (a line each).
die() {
  printf '\n%s %s\n' "$(L "error:" "błąd:")" "$1" >&2
  shift
  pad=$(L '       ' '      ')  # (as wide as "error: " or "błąd: ")
  for line in "$@"; do printf '%s%s\n' "$pad" "$line" >&2; done
  exit 1
}
# No failure, but the install cannot go on yet (a restart, a program to start first): why, and
# what to do then.
later() {
  printf '\n==> %s\n' "$1"
  shift
  for line in "$@"; do printf '    %s\n' "$line"; done
  exit 1
}

cleanup() {
  # (an old Lithify moved aside when the new one was not in its place yet goes back)
  if [ -n "$STAGE" ] && [ -e "$STAGE/old" ] && [ ! -e "$DEST" ]; then mv "$STAGE/old" "$DEST" || true; fi
  if [ -n "$STAGE" ]; then rm -rf "$STAGE"; fi
  if [ -n "$WORK" ]; then rm -rf "$WORK"; fi
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM

# `curl | sh` has no terminal on stdin: questions (and programs that ask, like sudo) use
# /dev/tty when there is one. Without any, every question is answered no (LITHIFY_YES=1: yes).
TTY=/dev/null
if (: <>/dev/tty) 2>/dev/null; then TTY=/dev/tty; fi

ask() {  # ask QUESTION: yes (0) or no (1); Enter is yes
  if [ "${LITHIFY_YES:-0}" = 1 ]; then
    info "$1 $(L "yes (LITHIFY_YES=1)" "tak (LITHIFY_YES=1)")"
    return 0
  fi
  if [ "$TTY" = /dev/null ]; then
    info "$1 $(L "no: there is no terminal to ask in (LITHIFY_YES=1 answers yes)" \
                 "nie: nie ma terminala, w którym można zapytać (LITHIFY_YES=1 odpowiada tak)")"
    return 1
  fi
  printf '    %s %s ' "$1" "$(L "[Y/n]" "[T/n]")" >"$TTY"
  answer=""
  read -r answer <"$TTY" || answer=n
  case $answer in
    "" | [YyTt]*) return 0 ;;
    *) return 1 ;;
  esac
}

shquote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

fetch() {  # fetch URL FILE [bar]: download; "bar" shows a progress bar on a terminal
  if command -v curl >/dev/null 2>&1; then
    if [ "${3:-}" = bar ] && [ -t 2 ]; then
      curl -fL --retry 3 --connect-timeout 30 --progress-bar -o "$2" "$1"
    else
      curl -fsSL --retry 3 --connect-timeout 30 -o "$2" "$1"
    fi
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$2" "$1"
  else
    die "$(L "this computer has neither curl nor wget to download with" \
             "na tym komputerze nie ma ani curl, ani wget do pobierania plików")" \
      "$(L "install curl (Debian, Ubuntu: sudo apt-get install curl), then run this again" \
           "zainstaluj curl (Debian, Ubuntu: sudo apt-get install curl), a potem uruchom instalator ponownie")"
  fi
}

sha256() {  # the SHA-256 of a file, in lower-case hex
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print tolower($1)}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print tolower($1)}'
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$1" | awk '{print tolower($NF)}'
  else
    return 1
  fi
}

need_work() {  # WORK: one temporary folder for downloads, removed at the end
  if [ -z "$WORK" ]; then WORK=$(mktemp -d "${TMPDIR:-/tmp}/lithify.XXXXXX"); fi
}

wait_for() {  # wait_for TEXT SECONDS STEP COMMAND...: until COMMAND works, a dot every STEP seconds
  w_left=$2 w_step=$3
  printf '    %s' "$1"
  shift 3
  while ! "$@" >/dev/null 2>&1; do
    if [ "$w_left" -le 0 ]; then
      printf '\n'
      return 1
    fi
    printf '.'
    sleep "$w_step"
    w_left=$((w_left - w_step))
  done
  printf ' %s\n' "$(L "ready" "gotowe")"
}

# ── Python ──────────────────────────────────────────────────────────────────

py_ok() { "$1" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; }
py_version() { "$1" -c 'import platform; print(platform.python_version())' 2>/dev/null || echo "?"; }

# On macOS, /usr/bin/python3 and /usr/bin/git only open Apple's "install the command line
# developer tools" dialog until those tools are installed: never run them before.
dev_stub() {
  [ "$OS" = Darwin ] || return 1
  case $1 in /usr/bin/*) ;; *) return 1 ;; esac
  dev=$(xcode-select -p 2>/dev/null) || return 0
  if [ -x "$dev/usr/bin/${1##*/}" ]; then return 1; fi
  return 0
}

find_python() {  # a Python 3.11+ of this computer's own
  for c in python3 python3.14 python3.13 python3.12 python3.11 python \
    /opt/homebrew/bin/python3 /usr/local/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
    p=$(command -v "$c" 2>/dev/null) || continue
    if dev_stub "$p"; then continue; fi
    if py_ok "$p"; then
      PY=$p
      return 0
    fi
  done
  return 1
}

python_found() {  # what this computer has instead, in words
  p=$(command -v python3 2>/dev/null) || {
    L "this computer has no Python" "ten komputer nie ma Pythona"
    return 0
  }
  if dev_stub "$p"; then
    L "macOS's python3 is only a placeholder for Apple's developer tools" \
      "python3 w macOS działa dopiero po zainstalowaniu narzędzi programistycznych Apple"
  else
    echo "$(L "this computer has Python" "ten komputer ma Pythona") $(py_version "$p") ($p)"
  fi
}

uv_target() {  # the name of uv's build for this computer
  case $OS in
    Darwin)
      # (the chip, not the shell: under Rosetta uname -m says x86_64 on Apple Silicon too)
      if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = 1 ]; then echo aarch64-apple-darwin; else echo x86_64-apple-darwin; fi
      ;;
    Linux)
      bits=$(getconf LONG_BIT 2>/dev/null || echo 64)
      case $(uname -m) in
        x86_64 | amd64) if [ "$bits" = 32 ]; then a=i686; else a=x86_64; fi ;;
        aarch64 | arm64) if [ "$bits" = 32 ]; then a=armv7; else a=aarch64; fi ;;  # (32-bit Raspberry Pi OS)
        armv7* | armv8l) a=armv7 ;;
        armv6*) a=arm ;;
        i?86) a=i686 ;;
        ppc64le) a=powerpc64le ;;
        s390x) a=s390x ;;
        riscv64) a=riscv64gc ;;
        *) return 1 ;;
      esac
      libc=gnu
      for f in /lib/ld-musl-*; do if [ -e "$f" ]; then libc=musl; fi; done
      # (ARMv6: uv's static musl build is the only one, and runs with glibc too)
      if [ "$a" = arm ]; then libc=musl; fi
      case $a in
        armv7 | arm) echo "$a-unknown-linux-${libc}eabihf" ;;
        *) echo "$a-unknown-linux-$libc" ;;
      esac
      ;;
    *) return 1 ;;
  esac
}

uv_run() {  # uv with Lithify's own folders, and none of the user's uv settings or virtualenv
  (
    unset VIRTUAL_ENV CONDA_PREFIX UV_PYTHON UV_OFFLINE UV_PYTHON_DOWNLOADS UV_MANAGED_PYTHON \
      UV_NO_MANAGED_PYTHON UV_PYTHON_PREFERENCE UV_SYSTEM_PYTHON
    UV_PYTHON_INSTALL_DIR="$TOOLS/python" UV_CACHE_DIR="$TOOLS/cache" UV_NO_CONFIG=1
    export UV_PYTHON_INSTALL_DIR UV_CACHE_DIR UV_NO_CONFIG
    exec "$TOOLS/bin/uv" "$@"
  )
}

private_python() {  # Lithify's private Python, from an earlier run
  [ -x "$TOOLS/bin/uv" ] || return 1
  p=$(uv_run python find --managed-python --no-project 3.12 2>/dev/null </dev/null) || return 1
  [ -n "$p" ] || return 1
  py_ok "$p" || return 1
  PY=$p
}

get_uv() {  # get_uv TARGET: uv's standalone build from its GitHub release, checked against its .sha256
  need_work
  d="$WORK/uv"
  mkdir -p "$d/x"
  file="uv-$1.tar.gz"
  say "$(L "downloading uv (Astral's Python installer, about 20 MB) for $1" \
           "pobieranie uv (instalatora Pythona od Astral, ok. 20 MB) dla $1")"
  for attempt in 1 2; do
    fetch "$UV_RELEASES/$file" "$d/$file" bar \
      || die "$(L "could not download uv from GitHub ($UV_RELEASES/$file)" \
                  "nie udało się pobrać uv z GitHuba ($UV_RELEASES/$file)")" \
        "$(L "check the internet connection, then run this again" \
             "sprawdź połączenie z internetem, a potem uruchom instalator ponownie")"
    fetch "$UV_RELEASES/$file.sha256" "$d/$file.sha256" \
      || die "$(L "could not download uv's checksum from GitHub" "nie udało się pobrać sumy kontrolnej uv z GitHuba")" \
        "$(L "check the internet connection, then run this again" \
             "sprawdź połączenie z internetem, a potem uruchom instalator ponownie")"
    want=$(awk '{print tolower($1); exit}' "$d/$file.sha256")
    got=$(sha256 "$d/$file") || die "$(L "there is no sha256sum, shasum or openssl to check the download with" \
                                         "brak sha256sum, shasum i openssl do sprawdzenia pobranego pliku")" \
      "$(L "install one of them (coreutils, perl or openssl), then run this again" \
           "zainstaluj jeden z nich (coreutils, perl albo openssl), a potem uruchom instalator ponownie")"
    if [ -n "$want" ] && [ "$want" = "$got" ]; then break; fi
    if [ "$attempt" = 2 ]; then
      die "$(L "the uv download does not match its checksum" "pobrany uv nie zgadza się ze swoją sumą kontrolną")" \
        "$(L "run this again in a few minutes (a new uv release may have come out meanwhile)" \
             "uruchom instalator ponownie za kilka minut (w międzyczasie mogła się ukazać nowa wersja uv)")"
    fi
    info "$(L "the download does not match its checksum: once more" \
              "pobrany plik nie zgadza się z sumą kontrolną: pobieranie jeszcze raz")"
  done
  info "$(L "checksum ok" "suma kontrolna się zgadza")"
  tar -xzf "$d/$file" -C "$d/x" || die "$(L "cannot unpack $file" "nie można rozpakować $file")"
  [ -f "$d/x/uv-$1/uv" ] || die "$(L "$file holds no uv" "w $file nie ma uv")"
  mkdir -p "$TOOLS/bin"
  cp "$d/x/uv-$1/uv" "$TOOLS/bin/uv.new"
  chmod 755 "$TOOLS/bin/uv.new"
  mv -f "$TOOLS/bin/uv.new" "$TOOLS/bin/uv"
}

install_private_python() {
  say "$(L "Lithify needs Python 3.11 or newer, and" "Lithify potrzebuje Pythona 3.11 lub nowszego, a") $(python_found)."
  if ! target=$(uv_target); then
    die "$(L "there is no private Python for this computer" "dla tego komputera nie ma prywatnego Pythona") ($OS $(uname -m))" \
      "$(L "install Python 3.11 or newer yourself (https://www.python.org/downloads/), then run this again" \
           "zainstaluj samodzielnie Pythona 3.11 lub nowszego (https://www.python.org/downloads/), a potem uruchom instalator ponownie")"
  fi
  info "$(L "It can install a private Python 3.12 just for Lithify, in $TOOLS:" \
            "Można zainstalować prywatnego Pythona 3.12 tylko dla Lithify, w $TOOLS:")"
  info "$(L "no administrator rights, nothing else on this computer changes (about 50 MB to download)." \
            "bez uprawnień administratora, nic innego na tym komputerze się nie zmieni (ok. 50 MB do pobrania).")"
  if ! ask "$(L "Install the private Python?" "Zainstalować prywatnego Pythona?")"; then
    die "$(L "Python 3.11 or newer is needed" "potrzebny jest Python 3.11 lub nowszy")" \
      "$(L "install it (https://www.python.org/downloads/; macOS: brew install python; Ubuntu 22.04: sudo apt install python3.11)," \
           "zainstaluj go (https://www.python.org/downloads/; macOS: brew install python; Ubuntu 22.04: sudo apt install python3.11),")" \
      "$(L "then run this again" "a potem uruchom instalator ponownie")"
  fi
  if [ ! -x "$TOOLS/bin/uv" ]; then get_uv "$target"; fi
  say "$(L "installing Python 3.12 (uv python install 3.12)" "instalowanie Pythona 3.12 (uv python install 3.12)")"
  uv_run python install --no-bin 3.12 </dev/null \
    || die "$(L "uv could not install Python 3.12 (see above)" "uv nie zdołał zainstalować Pythona 3.12 (patrz wyżej)")" \
      "$(L "check the internet connection, then run this again" \
           "sprawdź połączenie z internetem, a potem uruchom instalator ponownie")"
  private_python || die "$(L "uv installed Python 3.12, but it does not start" \
                             "uv zainstalował Pythona 3.12, ale nie da się go uruchomić")" \
    "$(L "remove $TOOLS, then run this again" "usuń $TOOLS, a potem uruchom instalator ponownie")"
  # (uv's download cache: not needed any more)
  rm -rf "$TOOLS/cache"
}

get_python() {
  if find_python; then
    say "Python $(py_version "$PY"): $PY"
  elif private_python; then
    say "Python $(py_version "$PY"): $PY $(L "(Lithify's private Python)" "(prywatny Python Lithify)")"
  else
    install_private_python
    say "Python $(py_version "$PY"): $PY $(L "(Lithify's private Python)" "(prywatny Python Lithify)")"
  fi
}

# ── Lithify itself ──────────────────────────────────────────────────────────

is_lithify() { [ -f "$1/lithify/cli.py" ] && [ -f "$1/bin/lithify" ]; }

have_git() {
  g=$(command -v git 2>/dev/null) || return 1
  if dev_stub "$g"; then return 1; fi
  return 0
}

archive_url() {  # GitHub's archive of the main branch, for computers without git
  if [ -n "${LITHIFY_ARCHIVE_URL:-}" ]; then
    printf '%s\n' "$LITHIFY_ARCHIVE_URL"
    return 0
  fi
  u=${REPO_URL%/}
  u=${u%.git}
  case $u in
    git@github.com:*) u="https://github.com/${u#git@github.com:}" ;;
    ssh://git@github.com/*) u="https://github.com/${u#ssh://git@github.com/}" ;;
    *) ;;
  esac
  case $u in
    https://github.com/*/*) printf '%s/archive/refs/heads/main.tar.gz\n' "$u" ;;
    *) return 1 ;;
  esac
}

unpack_archive() {  # unpack_archive URL INTO: download a .tar.gz or .zip of Lithify; INTO is the Lithify folder
  case $1 in
    *.zip | *.zip\?*) f="$STAGE/lithify.zip" ;;
    *) f="$STAGE/lithify.tar.gz" ;;
  esac
  fetch "$1" "$f" bar || die "$(L "could not download $1" "nie udało się pobrać $1")" \
    "$(L "check the internet connection, then run this again" \
         "sprawdź połączenie z internetem, a potem uruchom instalator ponownie")"
  x="$STAGE/x"
  mkdir -p "$x"
  # The files byte for byte as published (LF line endings: the speaker runs some of them).
  case $f in
    *.zip)
      if command -v unzip >/dev/null 2>&1; then
        unzip -q "$f" -d "$x" || die "$(L "cannot unpack $1" "nie można rozpakować $1")"
      else
        "$PY" -m zipfile -e "$f" "$x" || die "$(L "cannot unpack $1" "nie można rozpakować $1")"
      fi
      ;;
    *) tar -xzf "$f" -C "$x" || die "$(L "cannot unpack $1" "nie można rozpakować $1")" ;;
  esac
  # GitHub's archives hold one folder (lithify-main/); a .zip of the files themselves works too.
  top=""
  if is_lithify "$x"; then
    top=$x
  else
    for d in "$x"/*; do
      if is_lithify "$d"; then top=$d; fi
    done
  fi
  [ -n "$top" ] || die "$(L "$1 does not hold Lithify" "w $1 nie ma Lithify")" \
    "$(L "set LITHIFY_ARCHIVE_URL to a .tar.gz or .zip of Lithify, or install git, then run this again" \
         "ustaw LITHIFY_ARCHIVE_URL na plik .tar.gz lub .zip z Lithify albo zainstaluj git, a potem uruchom instalator ponownie")"
  mv "$top" "$2"
}

replace_tree() {  # replace_tree NEW: NEW takes DEST's place, the old DEST is removed afterwards
  if [ -e "$DEST" ] || [ -L "$DEST" ]; then
    mv "$DEST" "$STAGE/old" || die "$(L "cannot move the old $DEST aside" "nie można przenieść starego folderu $DEST w inne miejsce")"
    if ! mv "$1" "$DEST"; then
      mv "$STAGE/old" "$DEST" || true
      die "$(L "cannot put the new Lithify into $DEST" "nie można umieścić nowego Lithify w $DEST")"
    fi
  else
    mv "$1" "$DEST" || die "$(L "cannot create $DEST" "nie można utworzyć $DEST")"
  fi
}

get_lithify() {
  copy_from=""
  if [ -n "$HERE" ] && is_lithify "$HERE"; then
    # A checkout run directly is used where it is; a downloaded folder (not a git checkout) that a
    # double-click launcher runs is copied into DEST, so it can be deleted afterwards.
    if [ -e "$HERE/.git" ] || [ "${LITHIFY_LAUNCHER:-0}" != 1 ]; then
      DEST=$HERE
      say "$(L "using this checkout: $DEST" "używany jest ten folder Lithify: $DEST")"
      return 0
    fi
    copy_from=$HERE
  fi
  if [ -e "$DEST/.git" ]; then
    have_git || die "$(L "$DEST is a git checkout, but git is not installed" \
                         "$DEST to kopia z git (checkout), ale git nie jest zainstalowany")" \
      "$(L "install git, or remove $DEST to get a fresh copy, then run this again" \
           "zainstaluj git albo usuń $DEST, żeby pobrać świeżą kopię, a potem uruchom instalator ponownie")"
    say "$(L "updating Lithify in $DEST (git pull)" "aktualizowanie Lithify w $DEST (git pull)")"
    GIT_TERMINAL_PROMPT=0 git -C "$DEST" pull --ff-only -q </dev/null \
      || die "$(L "git could not update $DEST (see above)" "git nie zdołał zaktualizować $DEST (patrz wyżej)")" \
        "$(L "check the internet connection; if you changed files there, commit or undo the changes; then run this again" \
             "sprawdź połączenie z internetem; jeśli pliki w nim były zmieniane, zatwierdź (commit) albo cofnij zmiany; potem uruchom instalator ponownie")"
    return 0
  fi
  if [ -d "$DEST" ] && ! is_lithify "$DEST" && [ -n "$(ls -A "$DEST" 2>/dev/null)" ]; then
    die "$(L "$DEST is not empty, and it is not Lithify" "folder $DEST nie jest pusty i nie jest to Lithify")" \
      "$(L "set LITHIFY_HOME to another folder (or empty this one), then run this again" \
           "ustaw LITHIFY_HOME na inny folder (albo opróżnij ten), a potem uruchom instalator ponownie")"
  fi
  parent=$(dirname "$DEST")
  mkdir -p "$parent"
  # Next to DEST: the new copy takes its place with a rename.
  STAGE=$(mktemp -d "$parent/.lithify-new.XXXXXX")
  new="$STAGE/lithify"
  if [ -n "$copy_from" ]; then
    say "$(L "copying Lithify from $copy_from to $DEST" "kopiowanie Lithify z $copy_from do $DEST")"
    mkdir "$new"
    (cd "$copy_from" && tar -cf - .) | (cd "$new" && tar -xf -) \
      || die "$(L "cannot copy $copy_from to $DEST" "nie można skopiować $copy_from do $DEST")"
  else
    got=0
    if [ -z "${LITHIFY_ARCHIVE_URL:-}" ] && have_git; then  # (an archive named is the one to use)
      say "$(L "downloading Lithify with git into $DEST" "pobieranie Lithify przez git do $DEST")"
      if GIT_TERMINAL_PROMPT=0 git clone -q --depth 1 --config core.autocrlf=false "$REPO_URL" "$new" </dev/null; then
        got=1
      else
        warn "$(L "git could not download $REPO_URL" "git nie zdołał pobrać $REPO_URL")"
        rm -rf "$new"
      fi
    fi
    if [ "$got" = 0 ]; then
      url=$(archive_url) \
        || die "$(L "git is not installed, and $REPO_URL is not a GitHub address to download an archive from" \
                    "git nie jest zainstalowany, a $REPO_URL nie jest adresem na GitHubie, z którego można pobrać archiwum")" \
          "$(L "install git, or set LITHIFY_ARCHIVE_URL to a .tar.gz or .zip of Lithify, then run this again" \
               "zainstaluj git albo ustaw LITHIFY_ARCHIVE_URL na plik .tar.gz lub .zip z Lithify, a potem uruchom instalator ponownie")"
      say "$(L "downloading Lithify into $DEST ($url)" "pobieranie Lithify do $DEST ($url)")"
      unpack_archive "$url" "$new"
    fi
  fi
  replace_tree "$new"
  rm -rf "$STAGE"
  STAGE=""
}

install_command() {
  mkdir -p "$BIN"
  chmod +x "$DEST/bin/lithify" 2>/dev/null || true
  CMD="$BIN/lithify"
  # A small script that runs Lithify with the Python found above (an earlier installer linked
  # bin/lithify here: the link itself is replaced, never what it points to).
  tmp="$BIN/.lithify.$$"
  {
    printf '#!/bin/sh\n'
    printf '# The lithify command, written by Lithify'"'"'s installer: Lithify in %s with this Python.\n' "$DEST"
    printf 'exec %s %s "$@"\n' "$(shquote "$PY")" "$(shquote "$DEST/bin/lithify")"
  } >"$tmp"
  chmod 755 "$tmp"
  mv -f "$tmp" "$CMD"
  version=$("$CMD" --version 2>&1) || die "$(L "the lithify command does not start:" "polecenie lithify się nie uruchamia:")" \
    "$version" "$(L "remove $DEST and run this again (or report it)" "usuń $DEST i uruchom instalator ponownie (albo zgłoś problem)")"
  say "$(L "installed the command: $CMD ($version)" "zainstalowano polecenie: $CMD ($version)")"
}

path_tip() {
  case ":$PATH:" in
    *":$BIN:"*) ;;
    *)
      # shellcheck disable=SC2016 # printed for the user, expanded by their shell
      info "$(L "to type \`lithify\` in new terminal windows, add $BIN to your PATH:" \
                "aby wpisywać \`lithify\` w nowych oknach terminala, dodaj $BIN do zmiennej PATH:")"
      if [ "$OS" = Darwin ]; then
        info "  echo 'export PATH=\"$BIN:\$PATH\"' >> ~/.zprofile"
      else
        info "  echo 'export PATH=\"$BIN:\$PATH\"' >> ~/.profile"
      fi
      ;;
  esac
}

# ── Docker and git: they build Lithify for the speaker ──────────────────────

can_root() { [ "$(id -u)" = 0 ] || command -v sudo >/dev/null 2>&1; }
as_root() {
  if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi
}

docker_path() {  # a docker that is installed, but not on PATH (Docker Desktop links it on its first start)
  if command -v docker >/dev/null 2>&1; then return 0; fi
  for d in /usr/local/bin "$HOME/.docker/bin" /Applications/Docker.app/Contents/Resources/bin \
    "$HOME/.orbstack/bin" /opt/homebrew/bin; do
    if [ -x "$d/docker" ]; then
      PATH="$d:$PATH"
      export PATH
      return 0
    fi
  done
  return 0
}

docker_ok() {
  docker_path
  command -v docker >/dev/null 2>&1 || return 1
  if [ "$USE_SG" = 1 ]; then
    "$SG" docker -c 'docker info' >/dev/null 2>&1
  else
    docker info >/dev/null 2>&1
  fi
}

bundle_ready() { [ -f "$CACHE/bundle/VERSIONS" ]; }

release_url() {  # the published bundle versions.toml names (`lithify install` downloads it first)
  "$PY" -I -c 'import sys, tomllib
with open(sys.argv[1], "rb") as f:
    print(tomllib.load(f).get("release", {}).get("url") or "")' "$DEST/versions.toml" 2>/dev/null || true
}

# Without published releases, "Update everything" on the speaker's page builds on this computer,
# with Docker and git: the ones missing, when the install itself needs neither.
updates_tip() {
  docker_path
  tip_docker=0 tip_git=0
  if command -v docker >/dev/null 2>&1; then tip_docker=1; fi
  if have_git; then tip_git=1; fi
  case $tip_docker$tip_git in
    11) return 0 ;;
    10) tip_en="git" tip_pl="musi być git" ;;
    01) tip_en="Docker" tip_pl="musi być Docker" ;;
    *) tip_en="Docker and git" tip_pl="muszą być Docker i git" ;;
  esac
  warn "$(L "to build updates later (\"Update everything\" on the speaker's page), this computer needs $tip_en" \
            "aby później budować aktualizacje (\"Zaktualizuj wszystko\" na stronie głośnika), na tym komputerze $tip_pl")"
}

build_tools() {
  if bundle_ready; then
    say "$(L "a prebuilt Lithify bundle is already on this computer ($CACHE/bundle)" \
             "gotowa paczka Lithify dla głośnika jest już na tym komputerze ($CACHE/bundle)")"
    info "$(L "Docker and git are not needed for this install." "Docker i git nie są potrzebne do tej instalacji.")"
    case $(release_url) in https://*) ;; *) updates_tip ;; esac
    return 0
  fi
  release=$(release_url)
  case $release in
    https://*)
      say "$(L "Lithify's prebuilt bundle is published: the install downloads it ($release)" \
               "gotowa paczka Lithify jest opublikowana: instalacja ją pobierze ($release)")"
      info "$(L "Docker and git are needed only when that download fails (Lithify is built here then)." \
                "Docker i git są potrzebne tylko wtedy, gdy to pobieranie się nie uda (wtedy Lithify zostanie zbudowany na tym komputerze).")"
      # ("Update everything" downloads the next release too: no tip about building)
      return 0
      ;;
    *) ;;
  esac
  say "$(L "checking Docker and git: they build Lithify for the speaker on this computer (10-30 minutes the first time)" \
           "sprawdzanie, czy są Docker i git: budują one na tym komputerze Lithify dla głośnika (za pierwszym razem 10-30 minut)")"
  if [ "$OS" = Darwin ]; then
    tools_macos
  else
    tools_linux
  fi
}

tools_macos() {
  if ! have_git; then
    say "$(L "git is missing: it comes with Apple's free Command Line Developer Tools" \
             "git nie jest zainstalowany: jest w bezpłatnych narzędziach programistycznych Apple (Command Line Developer Tools)")"
    if ! ask "$(L "Install them now? (a window from Apple opens: click Install there)" \
                  "Zainstalować je teraz? (otworzy się okno Apple: kliknij w nim Instaluj)")"; then
      die "$(L "git is needed to build Lithify" "git jest potrzebny do zbudowania Lithify")" \
        "$(L "install Apple's tools (xcode-select --install) or Homebrew's git, then run this again" \
             "zainstaluj narzędzia Apple (xcode-select --install) albo git z Homebrew, a potem uruchom instalator ponownie")"
    fi
    xcode-select --install >/dev/null 2>&1 || true
    wait_for "$(L "waiting until they are installed (5-15 minutes)" "czekanie, aż się zainstalują (5-15 minut)")" 1800 20 have_git \
      || later "$(L "the Command Line Developer Tools are not installed yet" \
                    "narzędzia programistyczne Apple (Command Line Developer Tools) nie są jeszcze zainstalowane")" \
        "$(L "finish their installation in Apple's window, then run this installer again: it skips what is done" \
             "dokończ ich instalację w oknie Apple, a potem uruchom instalator ponownie: pominie to, co już zrobione")"
  fi
  if docker_ok; then
    say "$(L "Docker is running" "Docker działa")"
    return 0
  fi
  app=""
  for a in /Applications/Docker.app "$HOME/Applications/Docker.app"; do
    if [ -d "$a" ]; then app=$a; fi
  done
  if [ -z "$app" ] && ! command -v docker >/dev/null 2>&1; then
    if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = 1 ]; then
      chip=$(L "Apple Silicon (M1 or newer)" "Apple Silicon (M1 lub nowszy)") link=https://desktop.docker.com/mac/main/arm64/Docker.dmg
    else
      chip="Intel" link=https://desktop.docker.com/mac/main/amd64/Docker.dmg
    fi
    say "$(L "Docker Desktop is not installed: Lithify is built for the speaker inside it (free for personal use)" \
             "Docker Desktop (bezpłatny do użytku osobistego) nie jest zainstalowany: w nim budowany jest Lithify dla głośnika")"
    major=$(sw_vers -productVersion 2>/dev/null | cut -d. -f1)
    if [ -n "$major" ] && [ "$major" -lt 14 ] 2>/dev/null; then
      warn "$(L "the current Docker Desktop needs macOS 14 or newer; this Mac has macOS" \
                "aktualna wersja Docker Desktop wymaga macOS 14 lub nowszego; ten Mac ma macOS") $(sw_vers -productVersion)"
    fi
    if command -v brew >/dev/null 2>&1; then
      info "$(L "Homebrew can install it: brew install --cask docker-desktop (it may ask for your password)" \
                "Homebrew może go zainstalować: brew install --cask docker-desktop (może zapytać o hasło)")"
      if ! ask "$(L "Install Docker Desktop with Homebrew now?" "Zainstalować teraz Docker Desktop przez Homebrew?")"; then
        die "$(L "Docker Desktop is needed to build Lithify" "Docker Desktop jest potrzebny do zbudowania Lithify")" \
          "$(L "install it (brew install --cask docker-desktop)," "zainstaluj go (brew install --cask docker-desktop),")" \
          "$(L "or download it for this Mac ($chip): $link" "albo pobierz wersję dla tego Maca ($chip): $link")" \
          "$(L "then run this installer again" "a potem uruchom instalator ponownie")"
      fi
      # (the cask's old name, for a Homebrew from before it was renamed)
      brew install --cask docker-desktop <"$TTY" || brew install --cask docker <"$TTY" \
        || die "$(L "Homebrew could not install Docker Desktop (see above)" \
                    "Homebrew nie zdołał zainstalować Docker Desktop (patrz wyżej)")" \
          "$(L "download it for this Mac ($chip): $link" "pobierz wersję dla tego Maca ($chip): $link")" \
          "$(L "open the .dmg and drag Docker to Applications, then run this installer again" \
               "otwórz plik .dmg i przeciągnij ikonę Docker do folderu Programy, a potem uruchom instalator ponownie")"
      for a in /Applications/Docker.app "$HOME/Applications/Docker.app"; do
        if [ -d "$a" ]; then app=$a; fi
      done
    else
      info "$(L "Download Docker Desktop for this Mac ($chip): $link" "Pobierz Docker Desktop dla tego Maca ($chip): $link")"
      info "$(L "Open the .dmg and drag Docker to Applications; then run this installer again." \
                "Otwórz plik .dmg i przeciągnij ikonę Docker do folderu Programy; potem uruchom instalator ponownie.")"
      if ask "$(L "Open the download in your web browser now?" "Otworzyć teraz link do pobrania w przeglądarce?")"; then
        open "$link" || true
      fi
      later "$(L "Docker Desktop is needed to build Lithify for the speaker" \
                 "Docker Desktop jest potrzebny do zbudowania Lithify dla głośnika")" \
        "$(L "once it is in Applications, run this installer again: it skips what is already done" \
             "gdy będzie w folderze Programy, uruchom instalator ponownie: pominie to, co już zrobione")"
    fi
  fi
  if [ -z "$app" ]; then
    later "$(L "docker is installed, but its engine does not answer" "docker jest zainstalowany, ale jego silnik nie odpowiada")" \
      "$(L "start it (Docker Desktop, OrbStack, or: colima start), then run this installer again" \
           "uruchom go (Docker Desktop, OrbStack albo: colima start), a potem jeszcze raz uruchom instalator")"
  fi
  say "$(L "starting Docker Desktop" "uruchamianie Docker Desktop")"
  info "$(L "The first time, it asks you to accept its terms (the Docker Subscription Service Agreement):" \
            "Za pierwszym razem poprosi o zaakceptowanie warunków (Docker Subscription Service Agreement):")"
  info "$(L "click Accept. It may ask for your Mac's password to finish its setup, and offer to sign in" \
            "kliknij Accept. Może zapytać o hasło do Maca, żeby dokończyć konfigurację, i zaproponować logowanie")"
  info "$(L "(not needed: Skip)." "(niepotrzebne: Skip).")"
  open -a "$app" 2>/dev/null || open -a Docker 2>/dev/null || true
  wait_for "$(L "waiting for Docker Desktop to start (up to 3 minutes)" "czekanie na uruchomienie Docker Desktop (do 3 minut)")" 180 3 docker_ok \
    || later "$(L "Docker Desktop has not finished starting" "Docker Desktop nie zakończył jeszcze uruchamiania")" \
      "$(L "look at its window: accept its terms if it asks, and wait until the whale icon in the menu bar" \
           "zajrzyj do jego okna: zaakceptuj warunki, jeśli o to poprosi, i poczekaj, aż ikona wieloryba na pasku menu")" \
      "$(L "stops moving (\"Docker Desktop is running\"); then run this installer again: it skips what is done" \
           "przestanie się poruszać (\"Docker Desktop is running\"); potem uruchom instalator ponownie: pominie to, co już zrobione")"
}

distro() {  # FAMILY (debian, fedora, arch or nothing) and its PRETTY name, from /etc/os-release
  FAMILY="" PRETTY=$(L "this Linux" "tego Linuksa")
  [ -r /etc/os-release ] || return 0
  # shellcheck source=/dev/null
  ids=$(. /etc/os-release && printf '%s %s' "${ID:-}" "${ID_LIKE:-}") || ids=""
  # shellcheck source=/dev/null
  pretty=$(. /etc/os-release && printf '%s' "${PRETTY_NAME:-}") || pretty=""
  if [ -n "$pretty" ]; then PRETTY=$pretty; fi
  first=${ids%% *}
  for i in $ids; do
    case $i in
      debian | ubuntu) FAMILY=debian && return 0 ;;
      arch | archlinux) FAMILY=arch && return 0 ;;
      *) ;;
    esac
  done
  # (Fedora itself: RHEL and its relatives have no `docker` package of their own)
  if [ "$first" = fedora ]; then FAMILY=fedora; fi
  return 0
}

linux_install() {  # linux_install docker git: with the system's package manager, after asking
  pkgs=""
  for x in "$@"; do
    if [ "$x" = docker ] && [ "$FAMILY" = debian ]; then x=docker.io; fi
    pkgs="${pkgs:+$pkgs }$x"
  done
  case $FAMILY in
    debian) c1="apt-get update" c2="apt-get install -y $pkgs" ;;
    fedora) c1="" c2="dnf install -y $pkgs" ;;
    arch) c1="" c2="pacman -S --needed --noconfirm $pkgs" ;;
    *)
      die "$(L "please install $* yourself, then run this installer again" \
               "zainstaluj samodzielnie $*, a potem uruchom instalator ponownie")" \
        "Docker: https://docs.docker.com/engine/install/" \
        "$(L "        (and https://docs.docker.com/engine/install/linux-postinstall/ to use it without sudo)" \
             "        (oraz https://docs.docker.com/engine/install/linux-postinstall/, żeby używać go bez sudo)")" \
        "$(L "git: from your distribution's packages" "git: z pakietów Twojej dystrybucji")"
      ;;
  esac
  info "$(L "the commands for $PRETTY:" "polecenia dla $PRETTY:")"
  if [ -n "$c1" ]; then info "  $SUDO$c1"; fi
  info "  $SUDO$c2"
  if ! can_root; then
    die "$(L "there is no sudo here" "nie ma tu sudo")" \
      "$(L "run these commands as root (su -), then run this installer again" \
           "wykonaj te polecenia jako root (su -), a potem uruchom instalator ponownie")"
  fi
  if ! ask "$(L "Run them now (sudo asks for your password)?" "Uruchomić je teraz (sudo zapyta o hasło)?")"; then
    die "$(L "Docker and git are needed to build Lithify for the speaker" \
             "Docker i git są potrzebne do zbudowania Lithify dla głośnika")" \
      "$(L "run the commands above, then run this installer again" \
           "wykonaj powyższe polecenia, a potem uruchom instalator ponownie")"
  fi
  # shellcheck disable=SC2086 # the words of the command
  if [ -n "$c1" ]; then
    as_root $c1 <"$TTY" || die "$(L "\"$SUDO$c1\" did not work (see above)" "polecenie \"$SUDO$c1\" nie zadziałało (patrz wyżej)")"
  fi
  # shellcheck disable=SC2086
  as_root $c2 <"$TTY" || die "$(L "the installation did not work (see above)" "instalacja się nie udała (patrz wyżej)")" \
    "$(L "run the commands yourself, then run this installer again" \
         "wykonaj te polecenia samodzielnie, a potem uruchom instalator ponownie")"
}

docker_error() {
  if [ "$USE_SG" = 1 ]; then
    { "$SG" docker -c 'docker info' >/dev/null; } 2>&1 || true
  else
    { docker info >/dev/null; } 2>&1 || true
  fi
}

find_sg() {  # SG: the sg command (in /usr/sbin, off a user's PATH, on some systems)
  for s in sg /usr/bin/sg /usr/sbin/sg; do
    if SG=$(command -v "$s" 2>/dev/null); then return 0; fi
  done
  SG=""
  return 1
}

docker_service() {  # Docker installed, its service not running
  say "$(L "the Docker service is not running" "usługa Dockera nie działa")"
  if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
    set -- systemctl enable --now docker
    info "  ${SUDO}systemctl enable --now docker   $(L "(starts it now, and with the computer)" \
                                                       "(uruchamia ją teraz i razem z komputerem)")"
  elif command -v rc-service >/dev/null 2>&1; then
    set -- rc-service docker start
    info "  ${SUDO}rc-service docker start"
  else
    set -- service docker start
    info "  ${SUDO}service docker start"
  fi
  can_root || die "$(L "there is no sudo here" "nie ma tu sudo")" \
    "$(L "run that command as root (su -), then run this installer again" \
         "wykonaj to polecenie jako root (su -), a potem uruchom instalator ponownie")"
  ask "$(L "Start it now?" "Uruchomić ją teraz?")" \
    || die "$(L "Docker must run to build Lithify" "Docker musi działać, żeby zbudować Lithify")" \
      "$(L "start it with the command above, then run this installer again" \
           "uruchom go powyższym poleceniem, a potem jeszcze raz uruchom instalator")"
  as_root "$@" <"$TTY" || die "$(L "Docker did not start (see above)" "Docker się nie uruchomił (patrz wyżej)")" \
    "$(L "look at: ${SUDO}systemctl status docker   (or: ${SUDO}journalctl -u docker)" \
         "sprawdź: ${SUDO}systemctl status docker   (albo: ${SUDO}journalctl -u docker)")"
  sleep 2
}

docker_group() {  # Docker refuses this user: the docker group
  me=$(id -un)
  if ! { getent group docker || grep '^docker:' /etc/group; } >/dev/null 2>&1; then
    die "$(L "Docker refuses $me, and there is no docker group to join" \
             "Docker odmawia dostępu użytkownikowi $me, a nie ma grupy docker, do której można by go dodać")" \
      "$(L "see https://docs.docker.com/engine/install/linux-postinstall/, then run this installer again" \
           "zobacz https://docs.docker.com/engine/install/linux-postinstall/, a potem uruchom instalator ponownie")"
  fi
  if ! id -nG "$me" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
    say "$(L "Docker lets only members of the docker group use it, and $me is not one yet" \
             "Dockera mogą używać tylko członkowie grupy docker, a użytkownik $me jeszcze do niej nie należy")"
    info "  ${SUDO}usermod -aG docker $me"
    info "$(L "(Members of the docker group control Docker, which can do anything on this computer.)" \
              "(Członkowie grupy docker sterują Dockerem, który może zrobić na tym komputerze wszystko.)")"
    can_root || die "$(L "there is no sudo here" "nie ma tu sudo")" \
      "$(L "run that command as root (su -), log out and back in, then run this installer again" \
           "wykonaj to polecenie jako root (su -), wyloguj się i zaloguj ponownie, a potem jeszcze raz uruchom instalator")"
    ask "$(L "Add $me to the docker group now?" "Dodać teraz użytkownika $me do grupy docker?")" \
      || die "$(L "Docker must be usable by $me to build Lithify" \
                  "aby zbudować Lithify, użytkownik $me musi mieć dostęp do Dockera")" \
        "$(L "run the command above, log out and back in, then run this installer again" \
             "wykonaj powyższe polecenie, wyloguj się i zaloguj ponownie, a potem jeszcze raz uruchom instalator")"
    as_root usermod -aG docker "$me" <"$TTY" || die "$(L "usermod did not work (see above)" "usermod nie zadziałał (patrz wyżej)")"
  fi
  # The new group counts from the next login; `sg docker` has it right away.
  if find_sg && "$SG" docker -c 'docker info' >/dev/null 2>&1; then
    USE_SG=1 RELOGIN=1
    info "$(L "this login session does not have the docker group yet: the rest runs through \`sg docker\`" \
              "ta sesja logowania nie ma jeszcze grupy docker: dalsze kroki zostaną wykonane przez \`sg docker\`")"
    return 0
  fi
  later "$(L "$me is in the docker group now, but this login session does not know it yet" \
             "użytkownik $me jest już w grupie docker, ale ta sesja logowania jeszcze o tym nie wie")" \
    "$(L "log out and back in (or restart the computer), then run this installer again: it skips what is done" \
         "wyloguj się i zaloguj ponownie (albo uruchom ponownie komputer), a potem jeszcze raz uruchom instalator: pominie to, co już zrobione")"
}

tools_linux() {
  distro
  missing=""
  if ! command -v docker >/dev/null 2>&1; then missing="docker"; fi
  if ! have_git; then missing="${missing:+$missing }git"; fi
  if [ -n "$missing" ]; then
    say "$(L "missing: $missing (Docker builds Lithify for the speaker; git downloads librespot's source for it)" \
             "brakuje: $missing (Docker buduje Lithify dla głośnika; git pobiera do tego źródła librespot)")"
    # shellcheck disable=SC2086 # one word each
    linux_install $missing
  fi
  for round in 1 2 3; do
    if docker_ok; then
      say "$(L "Docker is ready" "Docker jest gotowy")"
      return 0
    fi
    err=$(docker_error)
    case $err in
      *"ermission denied"*) docker_group ;;
      *"/.docker/desktop/"*)
        later "$(L "Docker Desktop is not running" "Docker Desktop nie działa")" \
          "$(L "start it (or: systemctl --user start docker-desktop), wait until it runs, then run this installer again" \
               "uruchom go (albo: systemctl --user start docker-desktop), poczekaj, aż zacznie działać, a potem jeszcze raz uruchom instalator")"
        ;;
      *"/run/user/"*)
        later "$(L "Docker (rootless) is not running" "Docker (rootless) nie działa")" \
          "$(L "start it: systemctl --user start docker; then run this installer again" \
               "uruchom go: systemctl --user start docker; potem jeszcze raz uruchom instalator")"
        ;;
      *) if [ "$round" = 1 ]; then docker_service; fi ;;
    esac
  done
  die "$(L "Docker still does not work:" "Docker nadal nie działa:")" "$err" \
    "$(L "fix that (https://docs.docker.com/engine/install/linux-postinstall/), then run this installer again" \
         "napraw to (https://docs.docker.com/engine/install/linux-postinstall/), a potem uruchom instalator ponownie")"
}

# ── the firewall: the speaker downloads from this computer ──────────────────

firewall_macos() {
  fw=/usr/libexec/ApplicationFirewall/socketfilterfw
  [ -x "$fw" ] || return 0
  case $("$fw" --getglobalstate 2>/dev/null | tr '[:upper:]' '[:lower:]') in
    *enabled* | *"state = 1"* | *"state = 2"*) ;;
    *) return 0 ;;
  esac
  say "$(L "the macOS firewall is on" "zapora macOS jest włączona")"
  info "$(L "When macOS asks whether \"Python\" (or \"python3\") may accept incoming network connections," \
            "Gdy macOS zapyta, czy \"Python\" (albo \"python3\") może przyjmować przychodzące połączenia sieciowe,")"
  info "$(L "click Allow: the speaker downloads Lithify from this computer ($PORTS)." \
            "kliknij Pozwalaj: głośnik pobiera Lithify z tego komputera ($PORTS).")"
  case $("$fw" --getblockall 2>/dev/null | tr '[:upper:]' '[:lower:]') in
    *disabled*) ;;
    *enabled*)
      warn "$(L "the firewall blocks all incoming connections, so the speaker cannot download from this computer" \
                "zapora blokuje wszystkie połączenia przychodzące, więc głośnik nie może niczego pobrać z tego komputera")"
      info "$(L "turn that off: System Settings > Network > Firewall > Options > \"Block all incoming connections\"" \
                "wyłącz to: Ustawienia systemowe > Sieć > Zapora > Opcje > \"Blokuj wszystkie połączenia przychodzące\"")"
      ;;
    *) ;;
  esac
}

lan_net() {  # the local network of the interface the default route uses, e.g. 192.168.1.0/24
  command -v ip >/dev/null 2>&1 || return 1
  dev=$(ip -4 route show default 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "dev") {print $(i + 1); exit}}')
  [ -n "$dev" ] || return 1
  net=$(ip -4 route show dev "$dev" scope link 2>/dev/null | awk '$1 ~ /\// {print $1; exit}')
  [ -n "$net" ] || return 1
  printf '%s\n' "$net"
}

ufw_status() {  # as root, or with sudo when it needs no password now; nothing otherwise
  if [ "$(id -u)" = 0 ]; then ufw status 2>/dev/null || true; else sudo -n ufw status 2>/dev/null || true; fi
}

ufw_on() {
  command -v ufw >/dev/null 2>&1 || [ -x /usr/sbin/ufw ] || return 1
  # `ufw status` wants root; its settings file says whether it is enabled. (Not
  # `systemctl is-active ufw`: that service is "active" also while ufw is disabled.)
  case $(ufw_status) in
    *"Status: active"*) return 0 ;;
    *"Status: inactive"*) return 1 ;;
    *) ;;
  esac
  grep -qs '^ENABLED=yes' /etc/ufw/ufw.conf
}

firewalld_on() {
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet firewalld 2>/dev/null; then return 0; fi
  command -v firewall-cmd >/dev/null 2>&1 && [ "$(firewall-cmd --state 2>/dev/null)" = running ]
}

firewalld_open() {  # the default zone already lets 8095 and 18096-18099 in (Fedora Workstation: 1025-65535)
  ports=$(firewall-cmd --list-ports 2>/dev/null) || return 1
  open1=0 open2=0
  for p in $ports; do
    case $p in */tcp) ;; *) continue ;; esac
    r=${p%/tcp}
    lo=${r%-*} hi=${r#*-}
    if [ "$lo" -le 8095 ] 2>/dev/null && [ "$hi" -ge 8095 ] 2>/dev/null; then open1=1; fi
    if [ "$lo" -le 18096 ] 2>/dev/null && [ "$hi" -ge 18099 ] 2>/dev/null; then open2=1; fi
  done
  [ "$open1" = 1 ] && [ "$open2" = 1 ]
}

firewall_linux() {
  if ufw_on; then
    if ufw_status | grep -q '8095'; then return 0; fi
    net=$(lan_net) || net=192.168.0.0/16
    say "$(L "the firewall (ufw) is on: the speaker downloads Lithify from this computer on $PORTS" \
             "zapora (ufw) jest włączona: głośnik pobiera Lithify z tego komputera na portach $PORTS")"
    info "$(L "this opens them for your local network ($net; for a speaker on another network, use its):" \
              "to polecenie otwiera je dla Twojej sieci lokalnej ($net; jeśli głośnik jest w innej sieci, użyj jej adresu):")"
    info "  ${SUDO}ufw allow from $net to any port 8095,18096:18099 proto tcp comment Lithify"
    if can_root && ask "$(L "Open them now?" "Otworzyć je teraz?")"; then
      as_root ufw allow from "$net" to any port 8095,18096:18099 proto tcp comment Lithify <"$TTY" \
        || warn "$(L "ufw did not take the rule (see above): the speaker may not reach this computer" \
                     "ufw nie przyjął reguły (patrz wyżej): głośnik może nie mieć dostępu do tego komputera")"
    else
      warn "$(L "until these ports are open, the speaker cannot download from this computer" \
                "dopóki te porty nie zostaną otwarte, głośnik nie może niczego pobrać z tego komputera")"
    fi
  elif firewalld_on; then
    if firewalld_open; then return 0; fi
    say "$(L "the firewall (firewalld) is on: the speaker downloads Lithify from this computer on $PORTS" \
             "zapora (firewalld) jest włączona: głośnik pobiera Lithify z tego komputera na portach $PORTS")"
    info "$(L "these open them (in the default zone, the one of your local network):" \
              "te polecenia je otwierają (w strefie domyślnej, czyli strefie Twojej sieci lokalnej):")"
    info "  ${SUDO}firewall-cmd --permanent --add-port=8095/tcp --add-port=18096-18099/tcp"
    info "  ${SUDO}firewall-cmd --reload"
    if can_root && ask "$(L "Open them now?" "Otworzyć je teraz?")"; then
      { as_root firewall-cmd --permanent --add-port=8095/tcp --add-port=18096-18099/tcp <"$TTY" \
        && as_root firewall-cmd --reload <"$TTY"; } \
        || warn "$(L "firewalld did not take the ports (see above): the speaker may not reach this computer" \
                     "firewalld nie przyjął portów (patrz wyżej): głośnik może nie mieć dostępu do tego komputera")"
    else
      warn "$(L "until these ports are open, the speaker cannot download from this computer" \
                "dopóki te porty nie zostaną otwarte, głośnik nie może niczego pobrać z tego komputera")"
    fi
  fi
}

# ── the speaker ─────────────────────────────────────────────────────────────

lithify_run() {  # the lithify command; through `sg docker` when this session lacks the docker group
  if [ "$USE_SG" = 1 ]; then
    line=$(shquote "$CMD")
    for arg in "$@"; do line="$line $(shquote "$arg")"; done
    "$SG" docker -c "$line"
  else
    "$CMD" "$@"
  fi
}

want_wizard() {  # the browser wizard, unless told otherwise or there is no screen to show it on
  [ -z "${LITHIFY_HOST:-}" ] || return 1
  [ "${LITHIFY_NO_WIZARD:-0}" != 1 ] || return 1
  [ -z "${SSH_CONNECTION:-}" ] || return 1
  if [ "$OS" != Darwin ] && [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then return 1; fi
  # (a Lithify from before the wizard: argparse says "invalid choice")
  if out=$("$CMD" wizard --help 2>&1 </dev/null); then return 0; fi
  case $out in
    *"invalid choice"*)
      info "$(L "this Lithify has no browser wizard yet: on in the terminal" \
                "ta wersja Lithify nie ma jeszcze kreatora w przeglądarce: dalej w terminalu")"
      ;;
    *) warn "$(L "\`lithify wizard\` does not start: on in the terminal" "\`lithify wizard\` się nie uruchamia: dalej w terminalu")" ;;
  esac
  return 1
}

speaker() {
  if want_wizard; then
    # (after installing, the wizard also sets up the helper that keeps the speaker updatable)
    say "$(L "opening the Lithify wizard in your web browser: it finds the speaker, asks for its name, installs" \
             "otwieranie kreatora Lithify w przeglądarce: znajdzie głośnik, zapyta o jego nazwę i zainstaluje Lithify")"
    rc=0
    # (a language set on purpose is the page's too; otherwise the browser's)
    if [ -n "${LITHIFY_LANG:-}" ]; then
      lithify_run wizard --lang "$(lithify_lang)" <"$TTY" || rc=$?
    else
      lithify_run wizard <"$TTY" || rc=$?
    fi
    case $rc in
      0) ;;
      130) later "$(L "stopped" "przerwano")" \
        "$(L "run this installer again any time, or: $CMD wizard" "uruchom instalator ponownie, kiedy zechcesz, albo: $CMD wizard")" ;;
      *) die "$(L "Lithify was not installed on the speaker (the wizard ended before that)" \
                  "Lithify nie został zainstalowany na głośniku (kreator zakończył się wcześniej)")" \
        "$(L "start it again: $CMD wizard   (or in the terminal: $CMD install)" \
             "uruchom go ponownie: $CMD wizard   (albo w terminalu: $CMD install)")" ;;
    esac
  else
    say "$(L "installing Lithify on the speaker (in this terminal)" "instalowanie Lithify na głośniku (w tym terminalu)")"
    rc=0
    if [ -n "${LITHIFY_HOST:-}" ]; then
      lithify_run install --reboot --host "$LITHIFY_HOST" <"$TTY" || rc=$?
    else
      lithify_run install --reboot <"$TTY" || rc=$?
    fi
    case $rc in
      0) ;;
      130) later "$(L "stopped" "przerwano")" \
        "$(L "run this installer again any time, or: $CMD install" "uruchom instalator ponownie, kiedy zechcesz, albo: $CMD install")" ;;
      *) die "$(L "the install did not finish (see above)" "instalacja nie dobiegła końca (patrz wyżej)")" \
        "$(L "fix what it says, then run this installer again (or: $CMD install)" \
             "napraw to, co wskazuje komunikat powyżej, a potem uruchom instalator ponownie (albo: $CMD install)")" ;;
    esac
    # The helper starts with the computer (a systemd user service, a launchd agent): the
    # speaker's web page installs updates through it.
    lithify_run serve --install-service </dev/null \
      || warn "$(L "to install updates from the speaker's web page, keep \`lithify serve\` running on this computer" \
                   "aby instalować aktualizacje ze strony głośnika, \`lithify serve\` musi stale działać na tym komputerze")"
  fi
  page=$("$CMD" ui 2>/dev/null </dev/null || true)
  printf '\n'
  say "$(L "done! Open Spotify and pick the speaker in its list of devices (the Spotify Connect icon)." \
           "gotowe! Otwórz Spotify i wybierz głośnik z listy urządzeń (ikona Spotify Connect).")"
  if [ -n "$page" ]; then
    say "$(L "its page (settings, tests, updates): $page" "strona głośnika (ustawienia, testy, aktualizacje): $page")"
  fi
  if [ "$RELOGIN" = 1 ]; then
    warn "$(L "log out and back in once: until then the helper cannot use Docker to build updates" \
              "wyloguj się i zaloguj ponownie (wystarczy raz): do tego czasu pomocnik Lithify nie może używać Dockera do budowania aktualizacji")"
  fi
}

# Windows, in Git Bash or a similar shell (Git for Windows runs .sh files on a double-click):
# the Windows installer does the work, as a double-click on Lithify-Windows.cmd would.
windows_installer() {
  if [ -n "$HERE" ] && [ -f "$HERE/installer/install.ps1" ] && command -v powershell.exe >/dev/null 2>&1; then
    say "$(L "this is Windows: starting the Windows installer (install.ps1)" \
             "to jest Windows: uruchamianie instalatora dla Windows (install.ps1)")"
    ps1=$HERE/installer/install.ps1
    if command -v cygpath >/dev/null 2>&1; then ps1=$(cygpath -w "$ps1"); fi
    exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ps1"
  fi
  die "$(L "this is Windows ($OS), not Linux or macOS" "to jest Windows ($OS), a nie Linux ani macOS")" \
    "$(L "double-click Lithify-Windows (Lithify-Windows.cmd) in Lithify's folder instead" \
         "zamiast tego kliknij dwukrotnie Lithify-Windows (Lithify-Windows.cmd) w folderze Lithify")"
}

main() {
  if [ "${LITHIFY_LAUNCHER:-0}" != 1 ]; then
    printf '%s\n\n' "$(L "Lithify installer: Spotify Connect (librespot) for Lithe Audio speakers" \
                         "Instalator Lithify: Spotify Connect (librespot) dla głośników Lithe Audio")"
  fi
  # HERE: the Lithify folder this script is in (installer/ is in it); none when piped into sh
  if [ -f "$0" ]; then HERE=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd -P) || HERE=""; fi
  case $OS in
    Linux | Darwin) ;;
    MINGW* | MSYS* | CYGWIN*) windows_installer ;;
    *) warn "$(L "Lithify knows Linux, macOS and Windows; this is $OS: trying anyway" \
                 "Lithify zna Linuksa, macOS i Windows; to jest $OS: mimo to próbujemy dalej")" ;;
  esac
  if [ "$(id -u)" = 0 ]; then
    if [ -n "${SUDO_USER:-}" ]; then
      die "$(L "please run this installer as yourself, not with sudo" \
               "uruchom ten instalator ze swojego konta, nie przez sudo")" \
        "$(L "it asks for sudo by itself when something needs it" "sam poprosi o sudo, gdy coś będzie tego wymagać")"
    fi
    warn "$(L "running as root: Lithify is installed for root" \
              "uruchomiono jako root: Lithify zostanie zainstalowany dla użytkownika root")"
  else
    SUDO="sudo "
  fi

  get_python
  get_lithify
  install_command
  if [ "${LITHIFY_NO_SETUP:-0}" = 1 ]; then
    path_tip
    return 0
  fi
  build_tools
  if [ "$OS" = Darwin ]; then firewall_macos; else firewall_linux; fi
  speaker
  path_tip
}

# (all of it in a function: `curl | sh` runs nothing before the whole script is there)
main "$@"
