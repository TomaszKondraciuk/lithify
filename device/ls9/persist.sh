# Register librespot + lithify-agent with the Cast process_manager (active from the next boot).
# Changes only /system/chrome/process.json; the stock file is kept in $LITHIFY_BASE/backup.
# Usage: mksh persist.sh
D=${LITHIFY_BASE:-/lsync/lithify}
OLD=${LITHIFY_LEGACY:-/lsync/cc}
PJ=/system/chrome/process.json
[ -f $D/process.json ] || { echo "no $D/process.json"; exit 1; }
# A list without the Cast shell would leave the speaker without its main service after a reboot.
busybox grep -q cast_shell $D/process.json || { echo "FAIL $D/process.json is not a Cast service list"; exit 1; }
if ! grep -q lithify_ $PJ && ! grep -q cc_librespot $PJ; then
  # The live file is stock (first install, or a Cast update replaced it): refresh the backup.
  cp $PJ $D/backup/process.json.orig || exit 1
  echo "stock backup: $(openssl dgst -sha256 $D/backup/process.json.orig)"
elif [ ! -f $D/backup/process.json.orig ] && [ -f $OLD/backup/process.json.orig ]; then
  cp $OLD/backup/process.json.orig $D/backup/process.json.orig || exit 1
fi
# (toolbox cmp has no -s)
if busybox cmp -s $D/process.json $PJ; then echo PERSIST_UNCHANGED; exit 0; fi
cp $D/process.json $PJ.lithify || exit 1
busybox chown 1000:1000 $PJ.lithify || exit 1
chmod 600 $PJ.lithify
mv $PJ.lithify $PJ || exit 1
openssl dgst -sha256 $PJ
echo PERSIST_CHANGED
