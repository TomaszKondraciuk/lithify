# Restart librespot and lithify-agent; the Cast process_manager starts them again with the new files.
# Usage: mksh restart-services.sh
for n in librespot lithify-agent cc-agent; do for p in $(pidof $n); do kill $p; done; done
sleep 8
echo "librespot=$(pidof librespot) lithify-agent=$(pidof lithify-agent)"
