#!/bin/bash
# =============================================================================
# Container startup — wires up secrets and starts Streamlit.
# =============================================================================
set -e

# -----------------------------------------------------------------------------
# Secrets handling
# -----------------------------------------------------------------------------
# Render's "Secret Files" feature lets you upload a file via the dashboard
# that gets mounted at /etc/secrets/ at runtime. We point Streamlit at it
# by copying to the path it expects.
#
# To set this up in Render dashboard:
#   1. Service → Environment → Secret Files → Add Secret File
#   2. Filename: secrets.toml
#   3. Contents: paste your local .streamlit/secrets.toml content
# -----------------------------------------------------------------------------
if [ -f "/etc/secrets/secrets.toml" ]; then
    echo "[entrypoint] Found Render secret file — copying to .streamlit/"
    mkdir -p /app/.streamlit
    cp /etc/secrets/secrets.toml /app/.streamlit/secrets.toml
    chmod 600 /app/.streamlit/secrets.toml
else
    echo "[entrypoint] No Render secret file found at /etc/secrets/secrets.toml"
    echo "[entrypoint] Stripe + email features will be unavailable until secrets are configured."
fi

# -----------------------------------------------------------------------------
# Verify MediaPipe loads correctly (the original reason we moved here)
# -----------------------------------------------------------------------------
python -c "
import mediapipe as mp
pose = mp.solutions.pose.Pose(model_complexity=0)
pose.close()
print('[entrypoint] MediaPipe loaded successfully')
" || echo "[entrypoint] WARNING: MediaPipe smoke test failed"

# -----------------------------------------------------------------------------
# Start Streamlit
# -----------------------------------------------------------------------------
# Render injects PORT at runtime. Default to 8501 for local docker testing.
PORT="${PORT:-8501}"

echo "[entrypoint] Starting Streamlit on port ${PORT}"

exec streamlit run pitching_lab.py \
    --server.port="${PORT}" \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.fileWatcherType=none \
    --server.enableCORS=false \
    --server.enableXsrfProtection=true \
    --browser.gatherUsageStats=false
