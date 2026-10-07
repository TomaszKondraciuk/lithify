# Install or update Lithify into $LITHIFY_BASE (default /lsync/lithify, persistent yaffs2).
# Touches nothing else.
# Every file in SHA256SUMS is downloaded into new/ and verified before anything is replaced;
# the current version is kept in prev/ for a one-step rollback. Usage: mksh install.sh BASE_URL
B=$1
[ -n "$B" ] || { echo "usage: install.sh BASE_URL"; exit 2; }
D=${LITHIFY_BASE:-/lsync/lithify}
OLD=${LITHIFY_LEGACY:-/lsync/cc}
FILES="librespot lithify-agent librespot.args agent.conf VERSIONS SHA256SUMS"
# A failed step leaves the running version as it is and frees what the attempt used.
fail() { echo "FAIL $*"; cd /; rm -rf $D/new; exit 1; }
# After a failed replace: the files of prev/ (the old version) go back in place.
restore() {
  for r in $FILES; do [ -f $D/prev/$r ] && busybox ln -f $D/prev/$r $D/$r; done
  if [ ! -d $D/alsa ]; then
    if [ -d $D/alsa.old ]; then mv $D/alsa.old $D/alsa; elif [ -d $D/prev/alsa ]; then busybox cp -R $D/prev/alsa $D/alsa; fi
  fi
}
mkdir -p $D/cache $D/backup || fail "create $D"
# Keep the Spotify login of an install made before the project was named ($OLD).
if [ -d $OLD/cache ] && [ ! -f $D/cache/credentials.json ]; then cp $OLD/cache/* $D/cache/ 2>/dev/null; fi
# Room for the new files next to the current ones (prev/ holds hard links: no extra space).
used=0
for k in $(busybox du -sk $D/librespot $D/lithify-agent $D/alsa 2>/dev/null | busybox cut -f1); do used=$((used + k)); done
set -- $(busybox df -Pk $D | busybox tail -n 1)
[ "${4:-0}" -ge $((used + 16384)) ] || fail "space: ${4:-?} KB free in $D, $((used + 16384)) KB needed"
rm -rf $D/new; mkdir -p $D/new || fail "create $D/new"
cd $D/new || fail "enter $D/new"
get() { curl -fsS --retry 2 --connect-timeout 10 --max-time 600 -o "$1" "$B/$1"; }
get SHA256SUMS || fail "download SHA256SUMS"
while read -r sum f; do
  get "$f" || fail "download $f"
  got=$(openssl dgst -sha256 "$f"); got=${got##*= }
  [ "$got" = "$sum" ] || fail "checksum $f"
  echo "ok $f"
done < SHA256SUMS
tar -xf alsa.tar || fail "untar alsa.tar"
rm -f alsa.tar
# The speaker keeps its own settings: settings.default is used only when it has none yet, and
# settings.patch carries values sent on purpose. The new agent checks them before anything moves.
if [ -f settings.default ]; then
  chmod 700 lithify-agent
  ./lithify-agent settings-apply $D || fail "settings"
  rm -f settings.default settings.patch install.conf
fi
# The console runs with umask 077, but librespot and the agent run as uid 1000. The files get
# their modes and reach the flash before they replace anything.
for f in *; do [ -f "$f" ] && chmod 644 "$f"; done
chmod 755 librespot lithify-agent
busybox chmod -R a+rX alsa
busybox fsync * 2>/dev/null
# The running version goes to prev/ as hard links (a verified copy where links fail). When an
# earlier install was cut off, the files here are a mix: prev/ still has the last complete version.
if [ -f $D/.installing ] && [ -f $D/prev/librespot ]; then
  echo "an earlier install did not finish: $D/prev keeps the last complete version"
else
  rm -rf $D/prev; mkdir -p $D/prev || fail "create $D/prev"
  for f in $FILES; do
    [ -f $D/$f ] || continue
    busybox ln $D/$f $D/prev/$f 2>/dev/null && continue
    cp $D/$f $D/prev/$f && busybox cmp -s $D/$f $D/prev/$f && continue
    rm -rf $D/prev; fail "keep $f in $D/prev"
  done
fi
touch $D/.installing
# Each mv replaces one file in a single step (toolbox mv has no -f; it overwrites anyway). The
# version files go last: until then the speaker still says it runs the old version.
for f in *; do
  case $f in VERSIONS|SHA256SUMS) continue ;; esac
  [ -f "$f" ] || continue
  mv "$f" $D/$f || { restore; fail "install $f (the previous version is back)"; }
done
rm -rf $D/alsa.old
if [ -d $D/alsa ]; then
  if [ -d $D/prev/alsa ]; then mv $D/alsa $D/alsa.old; else mv $D/alsa $D/prev/alsa; fi || { restore; fail "keep alsa"; }
fi
mv alsa $D/alsa || { restore; fail "install alsa (the previous version is back)"; }
rm -rf $D/alsa.old
for f in VERSIONS SHA256SUMS; do
  [ -f $f ] || continue
  mv $f $D/$f || { restore; fail "install $f (the previous version is back)"; }
done
cd $D; rmdir $D/new; rm -f $D/.installing
for f in $D/prev/*; do [ -f "$f" ] && chmod 644 "$f"; done
chmod 755 $D
[ -f $D/prev/librespot ] && chmod 755 $D/prev $D/prev/librespot $D/prev/lithify-agent
busybox chown -R 1000:1000 $D/cache
busybox sync
cat $D/VERSIONS
busybox df -Pk $D
echo INSTALL_OK
