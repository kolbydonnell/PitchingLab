#!/bin/bash
# ============================================================
# PITCHING LAB — ONE-TIME SETUP
# Double-click this file. Enter your Mac password when asked.
# This installs Python and everything the app needs.
# Takes about 5 minutes the first time.
# ============================================================

set -e
cd "$(dirname "$0")"

echo ""
echo "============================================================"
echo "  PITCHING LAB SETUP"
echo "  One-time install. Takes ~5 minutes."
echo "============================================================"
echo ""

# Step 1: Install Homebrew if missing
if ! command -v brew >/dev/null 2>&1; then
    echo ">>> Installing Homebrew (the tool that installs other tools)..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for this session
    if [[ -d /opt/homebrew/bin ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -d /usr/local/bin ]]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
else
    echo ">>> Homebrew already installed. Skipping."
fi

# Step 2: Install Python if missing
if ! command -v python3 >/dev/null 2>&1; then
    echo ""
    echo ">>> Installing Python..."
    brew install python
else
    echo ">>> Python already installed. Skipping."
fi

# Step 3: Install the three app packages
echo ""
echo ">>> Installing app dependencies (streamlit, pandas, plotly)..."
pip3 install --quiet --upgrade pip
pip3 install --quiet streamlit pandas plotly

echo ""
echo "============================================================"
echo "  ✅  SETUP COMPLETE"
echo ""
echo "  Now double-click 'start.command' to launch the app."
echo "============================================================"
echo ""
echo "You can close this window."
echo ""
