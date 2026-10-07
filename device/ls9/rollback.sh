# Usage: mksh rollback.sh version        previous Lithify version (then restart the services)
#        mksh rollback.sh stock [purge]  stock Cast service list (then reboot); purge = delete Lithify
D=${LITHIFY_BASE:-/lsync/lithify}
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
  [ -f $D/backup/process.json.orig ] || { echo "no stock backup in $D/backup"; exit 1; }
  cp $D/backup/process.json.orig /system/chrome/process.json.rb || exit 1
  busybox chown 1000:1000 /system/chrome/process.json.rb
  chmod 600 /system/chrome/process.json.rb
  mv /system/chrome/process.json.rb /system/chrome/process.json || exit 1
  busybox cmp -s $D/backup/process.json.orig /system/chrome/process.json || { echo "FAIL stock list not in place"; exit 1; }
  openssl dgst -sha256 /system/chrome/process.json
  for p in $(pidof librespot) $(pidof lithify-agent); do kill $p; done
  if [ "$2" = "purge" ]; then cp $D/backup/process.json.orig /tmp/process.json.orig; cd /; rm -rf $D; echo "purged $D"; fi
  echo "STOCK_RESTORED - reboot to finish"
  ;;
*) echo "usage: rollback.sh version | stock [purge]"; exit 2 ;;
esac
