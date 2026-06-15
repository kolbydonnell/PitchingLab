#!/bin/bash
# =============================================================================
# Diamond Sports Lab — Rebuild from Scratch
# Use this when you've changed requirements.txt or Dockerfile and need a
# fresh container build. For day-to-day code changes you don't need this —
# pitching_lab.py edits are picked up automatically via hot reload.
# =============================================================================
set -e

echo ""
echo "================================================================"
echo "  Diamond Sports Lab — rebuilding Docker image"
echo "================================================================"
echo ""

cd "$(dirname "$0")"

# Check Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker Desktop isn't running. Please start it first."
    echo "Press any key to close..."
    read -n 1
    exit 1
fi

# Stop the running container (rebuild requires no container running on the image)
docker stop diamond-sports-lab-dev > /dev/null 2>&1 || true
docker rm   diamond-sports-lab-dev > /dev/null 2>&1 || true

# Rebuild the image (no cache so dependencies refresh)
echo "Rebuilding from scratch (this takes a few minutes)..."
echo ""
docker build --no-cache -t diamond-sports-lab .

echo ""
echo "✓ Rebuild complete."
echo ""
echo "Double-click Start_App.command to launch the rebuilt version."
echo ""
echo "Press any key to close..."
read -n 1
