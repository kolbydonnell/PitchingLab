#!/bin/bash
# =============================================================================
# Diamond Sports Lab — One-Click Local Stop
# Double-click this file in Finder to stop the running app.
# =============================================================================

echo ""
echo "================================================================"
echo "  Stopping Diamond Sports Lab"
echo "================================================================"
echo ""

# Stop and remove the container
if docker ps -a --format '{{.Names}}' | grep -q '^diamond-sports-lab-dev$'; then
    docker stop diamond-sports-lab-dev > /dev/null 2>&1
    docker rm   diamond-sports-lab-dev > /dev/null 2>&1
    echo "✓ Diamond Sports Lab stopped"
else
    echo "ℹ The app wasn't running. Nothing to stop."
fi

echo ""
echo "You can close this window now."
echo "Press any key to close..."
read -n 1
