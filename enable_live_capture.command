#!/bin/bash
# =============================================================================
# Diamond Sports Lab — Enable Live Capture (one-time install)
# =============================================================================
# Double-click this to install the camera/CV packages needed for the
# Live Capture tab. After it finishes, quit and re-open the app.
#
# What this installs (about 3-5 minutes total):
#   - streamlit-webrtc  → camera access in the browser
#   - opencv-python     → ball detection in video frames
#   - av (PyAV)         → video frame I/O
#   - numpy             → math backbone
#
# It does NOT install MediaPipe (the skeleton/pose extractor) — that
# requires Python 3.12. Run enable_full_pose.command for that later.
# =============================================================================

cd "$(dirname "$0")" || exit 1

echo ""
echo "==================================================================="
echo "  Diamond Sports Lab — installing Live Capture dependencies..."
echo "==================================================================="
echo ""
echo "This will take 3-5 minutes. Lots of text will scroll — that's normal."
echo ""

# Use whichever python3 the user has (3.13, 3.14, etc.). All four packages
# below have wheels for every Python 3.10+, so this works on any modern Mac.
PY=$(which python3)
if [ -z "$PY" ]; then
    echo "ERROR: python3 not found on your Mac. Install it from python.org first."
    echo ""
    echo "Press any key to close this window..."
    read -n 1
    exit 1
fi
echo "Using Python at: $PY"
echo ""

# Upgrade pip first to avoid wheel-build errors on newer Python versions
"$PY" -m pip install --upgrade pip --user
echo ""

# Install the Live Capture + Upload Video packages
"$PY" -m pip install --upgrade --user \
    streamlit-webrtc \
    opencv-python-headless \
    av \
    numpy \
    streamlit-image-coordinates

INSTALL_RESULT=$?
echo ""
echo "==================================================================="

if [ $INSTALL_RESULT -eq 0 ]; then
    echo "  SUCCESS — Live Capture dependencies installed."
    echo "==================================================================="
    echo ""
    echo "Next steps:"
    echo "  1. Close the running app (Ctrl+C in the start.command Terminal,"
    echo "     or close that Terminal window entirely)."
    echo "  2. Double-click start.command to relaunch the app."
    echo "  3. Toggle Live Capture (Beta) in the sidebar."
    echo "  4. The dependency error message should be gone."
    echo ""
    echo "If you still see the error after restarting:"
    echo "  - Try running this command in Terminal:"
    echo "      $PY -m pip list | grep -E 'webrtc|opencv|^av '"
    echo "  - You should see all three packages listed. If not, send the"
    echo "    output to Claude and we'll debug it."
    echo ""
else
    echo "  ERROR — one or more packages failed to install."
    echo "==================================================================="
    echo ""
    echo "Scroll up to find the red error text. Copy it and send to Claude."
    echo ""
fi

echo "Press any key to close this window..."
read -n 1
