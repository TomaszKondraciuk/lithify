# Usage: mksh rollback.sh version                 previous Lithify version (then restart the services)
#        mksh rollback.sh stock [purge [reboot]]  stock Cast service list (then reboot); purge = delete
#                                                 Lithify and undo what it changed while it ran
D=${LITHIFY_BASE:-/lsync/lithify}
OLD=${LITHIFY_LEGACY:-/lsync/cc}
PJ=${LITHIFY_PROCESS_LIST:-/system/chrome/process.json}
T=${LITHIFY_TMP:-/tmp}
# Our entries in the Cast service list (cc_: installs made before the project was named).
ours() { grep -q lithify_ $PJ || grep -q cc_librespot $PJ; }
running() { pidof lithify-agent cc-agent librespot; }
case "$1" in
version)
  [ -f $D/prev/librespot ] || { echo "no previous version in $D/prev"; exit 1; }
  # Copies of prev/ replace the files one by one, so prev/ stays usable. The console runs with
  # umask 077, but librespot and the agent run as uid 1000.
  for f in librespot lithify-agent librespot.args agent.conf VERSIONS SHA256SUMS; do
    [ -f $D/prev/$f ] || continue
    m=644; case $f in librespot|lithify-agent) m=755 ;; esac
    cp $D/prev/$f $D/$f.rb && chmod $m $D/$f.rb && mv $D/$f.rb $D/$f || { rm -f $D/$f.rb; echo "FAIL restore $f"; exit 1; }
  done
  # Without the old checksums the page would think the speaker still runs the newer files.
  [ -f $D/prev/SHA256SUMS ] || rm -f $D/SHA256SUMS
  if [ -d $D/prev/alsa ]; then
    rm -rf $D/alsa.rb $D/alsa.old
    busybox cp -R $D/prev/alsa $D/alsa.rb && busybox chmod -R a+rX $D/alsa.rb || { echo "FAIL restore alsa"; exit 1; }
    [ -d $D/alsa ] && mv $D/alsa $D/alsa.old
    mv $D/alsa.rb $D/alsa || { mv $D/alsa.old $D/alsa; echo "FAIL restore alsa"; exit 1; }
    rm -rf $D/alsa.old
  fi
  rm -f $D/.installing
  for f in $D/*; do [ -f "$f" ] && chmod 644 "$f"; done
  chmod 755 $D/librespot $D/lithify-agent
  busybox sync
  cat $D/VERSIONS
  echo VERSION_ROLLED_BACK
  ;;
stock)
  if [ "$2" = purge ] && ! ours && [ -z "$(running)" ]; then
    # (process.json.orig: an uninstall since the last boot, which a reboot still has to finish)
    n=0
    for f in $D $OLD $D.removed $OLD.removed $T/process.json.orig $T/lithify-librespot; do [ -e $f ] && n=1; done
    [ $n = 0 ] && { echo NOTHING_TO_REMOVE; exit 0; }
  fi
  if ours; then
    B=$D/backup/process.json.orig
    [ -f $B ] || B=$OLD/backup/process.json.orig
    [ -f $B ] || { echo "FAIL no stock backup in $D/backup: nothing was changed"; exit 1; }
    # A list without the Cast shell would leave the speaker without its main service.
    busybox grep -q cast_shell $B || { echo "FAIL $B is not a Cast service list: nothing was changed"; exit 1; }
    cp $B $PJ.rb || exit 1
    busybox chown 1000:1000 $PJ.rb
    chmod 600 $PJ.rb
    mv $PJ.rb $PJ || exit 1
    busybox cmp -s $B $PJ || { echo "FAIL stock list not in place"; exit 1; }
    openssl dgst -sha256 $PJ
    [ "$2" = purge ] && cp $B $T/process.json.orig
    echo "the stock Cast service list is back"
  else
    # A firmware update may have put in a list of its own: that one stays.
    echo "the Cast service list has no Lithify entries: left as it is"
  fi
  if [ "$2" != purge ]; then
    for p in $(pidof librespot) $(pidof lithify-agent); do kill $p; done
    echo "STOCK_RESTORED - reboot to finish"
    exit 0
  fi
  # The folders out of the way first: the Cast process manager starts the programs again about
  # 2 s after they end, which it cannot from a folder that is gone (nor can librespot make its
  # cache there again).
  for d in $D $OLD; do
    [ -d $d ] || continue
    rm -rf $d.removed
    mv $d $d.removed || { echo "FAIL move $d away"; exit 1; }
  done
  echo "stopping the programs of Lithify"
  # The agent first: it would stop the official Spotify again, or put its routes back.
  for n in lithify-agent cc-agent librespot; do for p in $(pidof $n); do kill $p; done; done
  n=0
  while [ -n "$(running)" ] && [ $n -lt 5 ]; do sleep 1; n=$((n + 1)); done
  for p in $(running); do kill -9 $p; done
  # What they changed while they ran (a reboot undoes it too): the fail-fast routes,
  ip route | while read -r kind dst rest; do
    [ "$kind" = unreachable ] && ip route del unreachable $dst && echo "removed the fail-fast route $dst"
  done
  # the download tmpfs of librespot,
  L=$T/lithify-librespot
  if grep -q " $L " /proc/mounts; then umount $L && echo "unmounted $L" || echo "$L stays until the reboot"; fi
  grep -q " $L " /proc/mounts || rm -rf $L
  # the official Spotify, which nothing starts again once the agent has stopped it,
  if [ -z "$(pidof spotifyhifi)" ] && [ -n "$(pidof LibreManager)" ]; then
    cd /system/bin
    LD_PRELOAD=/system/lib/libm.so ./spotifyhifi </dev/null >/dev/null 2>&1 &
    cd /
    sleep 2
    [ -n "$(pidof spotifyhifi)" ] && echo "the official Spotify runs again"
  fi
  # and the files they keep in RAM.
  for f in $T/lithify-*; do [ -f "$f" ] && rm -f "$f"; done
  cd /
  for d in $D $OLD; do
    [ -d $d.removed ] || continue
    rm -rf $d.removed || { echo "FAIL delete $d"; exit 1; }
    echo "deleted $d"
  done
  rm -f $PJ.lithify $PJ.rb
  busybox sync
  echo STOCK_RESTORED
  if [ "$3" = reboot ]; then echo "restarting the speaker"; exec reboot; fi
  ;;
*) echo "usage: rollback.sh version | stock [purge [reboot]]"; exit 2 ;;
esac
