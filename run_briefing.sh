#!/bin/bash
# Runs the morning briefing once per day on first wake.

STAMP="/tmp/morning_briefing_ran_$(date +%Y-%m-%d)"
LOG="/Users/prishitanahata/Documents/Morning briefing agent files/briefing.log"
ERRLOG="/Users/prishitanahata/Documents/Morning briefing agent files/briefing_error.log"
DEBUG="/tmp/jarvis_debug.log"

echo "$(date): run_briefing.sh started" >> "$DEBUG"

# Only run between 6am and 2pm
HOUR=$(date +%H)
if [ "$HOUR" -lt 6 ] || [ "$HOUR" -ge 14 ]; then
    echo "$(date): exiting — hour $HOUR outside window" >> "$DEBUG"
    exit 0
fi

# Already ran today — exit silently
if [ -f "$STAMP" ]; then
    echo "$(date): exiting — already ran today" >> "$DEBUG"
    exit 0
fi

echo "$(date): running briefing…" >> "$DEBUG"

# Mark as ran before executing (prevents double-runs)
touch "$STAMP"

PYTHON="/Users/prishitanahata/Documents/Predictive Analytics/.conda/bin/python3"
SCRIPT="/Users/prishitanahata/Documents/Morning briefing agent files/morning_briefing.py"

# Run from the project directory so .env is found
cd "/Users/prishitanahata/Documents/Morning briefing agent files"

# Run and capture output
"$PYTHON" "$SCRIPT" > "$LOG" 2> "$ERRLOG"
echo "$(date): python exited with code $?" >> "$DEBUG"
