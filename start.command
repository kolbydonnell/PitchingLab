#!/bin/bash
# ============================================================
# PITCHING LAB — LAUNCHER
# Double-click this file to start the app.
# Your browser will open automatically.
# To stop the app, close this Terminal window.
# ============================================================

cd "$(dirname "$0")"

# Make sure brew/python paths are loaded
if [[ -d /opt/homebrew/bin ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
elif [[ -d /usr/local/bin ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
fi

echo ""
echo "============================================================"
echo "  STARTING PITCHING LAB"
echo "  Your browser will open in a few seconds..."
echo "============================================================"
echo ""

streamlit run pitching_lab.py
