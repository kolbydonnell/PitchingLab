#!/bin/bash
# =============================================================================
# Diamond Sports Lab — Enable Full Pose Biomechanics (one-time install)
# =============================================================================
# This installs Python 3.12 + MediaPipe so the Live Capture tab can show
# the skeleton overlay on the video and extract hip-shoulder separation,
# arm slot, lead-knee flex, and elbow-stress estimate from pose alone.
#
# Required first: enable_live_capture.command (the basic version).
# Time required: about 10-15 minutes.
# Requires: Homebrew (this script installs it if missing).
# =============================================================================

cd "$(dirname "$0")" || exit 1

echo ""
echo "==================================================================="
echo "  Diamond Sports Lab — installing Python 3.12 + MediaPipe Pose..."
echo "==================================================================="
echo ""
echo "This will take 10-15 minutes. You will be asked for your Mac"
echo "password at least once (you won't see letters as you type — normal)."
echo ""

# Step 1 — Homebrew
if ! command -v brew &>/dev/null; then
    echo "Homebrew not found. Installing it first..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for this session
    if [ -x "/opt/homebrew/bin/brew" ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
else
    echo "Homebrew already installed: $(which brew)"
fi
echo ""

# Step 2 — Python 3.12 via Homebrew
echo "Installing Python 3.12 (skips if already installed)..."
brew install python@3.12
echo ""

# Find Python 3.12
PY312=""
for path in /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12; do
    if [ -x "$path" ]; then PY312="$path"; break; fi
done
if [ -z "$PY312" ]; then
    echo "ERROR: Could not find python3.12 after install."
    echo "Try: which python3.12"
    echo ""
    echo "Press any key to close..."
    read -n 1
    exit 1
fi
echo "Found Python 3.12 at: $PY312"
echo ""

# Step 3 — Install all app dependencies into Python 3.12
echo "Installing all app dependencies into Python 3.12..."
"$PY312" -m pip install --upgrade pip
"$PY312" -m pip install --upgrade \
    streamlit pandas plotly kaleido pillow reportlab matplotlib pypdf \
    streamlit-webrtc opencv-python-headless av numpy \
    mediapipe
INSTALL_RESULT=$?
echo ""

if [ $INSTALL_RESULT -ne 0 ]; then
    echo "==================================================================="
    echo "  ERROR — one or more packages failed."
    echo "==================================================================="
    echo ""
    echo "Scroll up for the red text. Copy and send to Claude."
    echo ""
    echo "Press any key to close..."
    read -n 1
    exit 1
fi

# Step 4 — Update start.command to use Python 3.12
echo "Updating start.command to use Python 3.12..."
if [ -f "start.command" ]; then
    cp start.command start.command.backup
    # Replace any "python3" or "/usr/bin/python3" with the 3.12 path
    sed -i.bak "s|python3 |$PY312 |g" start.command
    sed -i.bak "s|/usr/bin/python3 |$PY312 |g" start.command
    sed -i.bak "s|/usr/local/bin/python3 |$PY312 |g" start.command
    rm -f start.command.bak
    chmod +x start.command
    echo "Done. (Original saved as start.command.backup.)"
fi
echo ""

echo "==================================================================="
echo "  SUCCESS — Full pose biomechanics enabled."
echo "==================================================================="
echo ""
echo "Next steps:"
echo "  1. Close any running app windows."
echo "  2. Double-click start.command to relaunch."
echo "  3. Open Live Capture. The pre-flight self-test should now show"
echo "     MediaPipe as installed (green checkmark)."
echo "  4. When you snap a pitch, you'll see a skeleton overlay drawn"
echo "     on the video AND the Mechanics Analysis panel will fill in"
echo "     all the biomech metrics."
echo ""
echo "Press any key to close..."
read -n 1
