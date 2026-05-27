#!/bin/bash
# ============================================================
# PITCHING LAB — UPDATER
# Double-click this file to copy the latest version of the app
# from this Cowork session to your Desktop folder.
# ============================================================

set -e
cd "$(dirname "$0")"

DEST="$HOME/Desktop/PitchingLab"
SRC_DIR="$(pwd)"

echo ""
echo "============================================================"
echo "  PITCHING LAB UPDATER"
echo "============================================================"
echo ""
echo "Source folder (this session's outputs):"
echo "  $SRC_DIR"
echo ""
echo "Destination:"
echo "  $DEST"
echo ""

if [[ ! -d "$DEST" ]]; then
    echo "Destination folder doesn't exist yet — creating it..."
    mkdir -p "$DEST"
fi

# Show before
if [[ -f "$DEST/pitching_lab.py" ]]; then
    BEFORE=$(wc -l < "$DEST/pitching_lab.py" | tr -d ' ')
    echo "BEFORE: pitching_lab.py = $BEFORE lines"
else
    echo "BEFORE: pitching_lab.py = (does not exist yet)"
fi

echo ""
echo "Copying files..."
cp -R "$SRC_DIR/." "$DEST/"

# Show after
AFTER=$(wc -l < "$DEST/pitching_lab.py" | tr -d ' ')
echo "AFTER:  pitching_lab.py = $AFTER lines"

echo ""
if [[ "$AFTER" -gt 1400 ]]; then
    echo "============================================================"
    echo "  ✅  UPDATE SUCCESSFUL"
    echo ""
    echo "  You have the latest version with:"
    echo "    • Demo Mode (toggle in sidebar)"
    echo "    • Expanded drill library (today + week plan)"
    echo "    • Auto-detecting parsers"
    echo "    • Rapsodo CSV support (alongside Pitch Logic)"
    echo ""
    echo "  Next: close the Terminal running the app (if any),"
    echo "        then double-click start.command to launch."
    echo "============================================================"
else
    echo "============================================================"
    echo "  ⚠️  Something looks off — file is only $AFTER lines."
    echo "  Expected 1400+ lines. Send Kolby's last screen to Claude."
    echo "============================================================"
fi

echo ""
echo "You can close this window."
echo ""
