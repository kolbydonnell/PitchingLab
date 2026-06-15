#!/bin/bash
# =============================================================================
# Diamond Sports Lab — One-Click Local Start
# Double-click this file in Finder to start the app on your Mac.
# =============================================================================
set -e

# Pretty banner
echo ""
echo "================================================================"
echo "  Diamond Sports Lab — starting on your Mac"
echo "================================================================"
echo ""

# Move to the script's directory (where the Dockerfile lives)
cd "$(dirname "$0")"

# ---- Check Docker Desktop is running ----
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker Desktop isn't running."
    echo ""
    echo "Please open Docker Desktop from your Applications folder,"
    echo "wait ~60 seconds for it to start (the whale icon in the"
    echo "menu bar should be solid, not animated), then double-click"
    echo "this file again."
    echo ""
    echo "Press any key to close this window..."
    read -n 1
    exit 1
fi

echo "✓ Docker Desktop is running"

# ---- Stop any existing instance ----
docker stop diamond-sports-lab-dev > /dev/null 2>&1 || true
docker rm   diamond-sports-lab-dev > /dev/null 2>&1 || true

# ---- Build the image if it doesn't exist yet ----
if ! docker image inspect diamond-sports-lab > /dev/null 2>&1; then
    echo ""
    echo "Building Docker image (first run — takes ~5 minutes)..."
    echo "Future starts will be instant."
    echo ""
    docker build -t diamond-sports-lab .
    echo ""
    echo "✓ Build complete"
fi

# ---- Run the container ----
echo ""
echo "Starting Diamond Sports Lab..."

# Set up volume mounts: code is hot-editable, secrets and test videos
# are accessible from the container.
VOLUMES=()
VOLUMES+=("-v" "$(pwd)/pitching_lab.py:/app/pitching_lab.py")
if [ -d "$(pwd)/.streamlit" ]; then
    VOLUMES+=("-v" "$(pwd)/.streamlit:/app/.streamlit")
fi
if [ -d "$(pwd)/test_videos" ]; then
    VOLUMES+=("-v" "$(pwd)/test_videos:/app/test_videos")
fi

docker run -d \
    --name diamond-sports-lab-dev \
    -p 8501:8501 \
    "${VOLUMES[@]}" \
    diamond-sports-lab > /dev/null

# ---- Wait for the app to be ready ----
echo ""
echo "Waiting for the app to be ready..."
for i in {1..60}; do
    if curl -s http://localhost:8501/_stcore/health > /dev/null 2>&1; then
        echo "✓ App is up"
        break
    fi
    sleep 1
done

# ---- Open the browser ----
echo ""
echo "Opening the app in your browser..."
sleep 1
open http://localhost:8501

# ---- Friendly closing message ----
echo ""
echo "================================================================"
echo "  Diamond Sports Lab is running at: http://localhost:8501"
echo "================================================================"
echo ""
echo "  • To stop: double-click Stop_App.command"
echo "  • To see logs live: open a terminal and run:"
echo "      docker logs -f diamond-sports-lab-dev"
echo "  • Code changes to pitching_lab.py are picked up automatically"
echo "    (hot reload — just save the file and refresh the browser)"
echo ""
echo "You can close this window now."
echo "Press any key to close..."
read -n 1
