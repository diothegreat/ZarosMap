#!/bin/sh
# Εκκίνηση του NAS API (TAXIS Workbench) στην πόρτα 8787.
# Ασφαλές να τρέχει όσες φορές θέλει: αν ο server τρέχει ήδη, δεν κάνει τίποτα.
# Προορίζεται για DSM Task Scheduler (Boot-up + ωριαίος έλεγχος), ως root.

DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$DIR/nas_api.log"
PORT="${NAS_API_PORT:-8787}"

# Τρέχει ήδη κάποιος στην πόρτα; Τότε τέλος.
if netstat -tln 2>/dev/null | grep -q ":$PORT "; then
    exit 0
fi

# Στο boot το Entware (/opt) μπορεί να αργήσει να φορτώσει — περίμενε έως 3 λεπτά.
PY=""
i=0
while [ $i -lt 36 ]; do
    for c in /opt/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
        if [ -x "$c" ]; then
            PY="$c"
            break 2
        fi
    done
    sleep 5
    i=$((i+1))
done

if [ -z "$PY" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') ΣΦΑΛΜΑ: δεν βρέθηκε python3" >> "$LOG"
    exit 1
fi

# Κράτα το log κάτω από ~5MB.
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 5242880 ]; then
    : > "$LOG"
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') Εκκίνηση NAS API με $PY" >> "$LOG"
nohup "$PY" "$DIR/nas_api.py" >> "$LOG" 2>&1 &
