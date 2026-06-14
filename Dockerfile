# =============================================================================
# Diamond Sports Lab — Production Container
# =============================================================================
# Build:   docker build -t diamond-sports-lab .
# Run:     docker run -p 8501:8501 diamond-sports-lab
# Deploy:  Push to GitHub → Render auto-builds and deploys from this file.
#
# Why Docker:
#   Streamlit Cloud's environment kept producing PermissionError on the
#   bundled MediaPipe .tflite files. With Docker we control the entire
#   environment — Python version, system packages, file permissions,
#   user identity. MediaPipe (and everything else) installs identically
#   every time.
# =============================================================================
FROM python:3.12-slim

# Set timezone for consistent log timestamps and date math
ENV TZ=UTC \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# -----------------------------------------------------------------------------
# System dependencies
# -----------------------------------------------------------------------------
# ffmpeg     : video processing (av, motion interpolation upsample)
# libsm6     : OpenCV X11 dependency
# libxext6   : OpenCV X11 dependency
# libgl1     : OpenCV OpenGL dependency
# libgomp1   : MediaPipe runtime
# libglib2.0 : MediaPipe runtime
# curl       : for HEALTHCHECK
# -----------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsm6 \
        libxext6 \
        libgl1 \
        libgomp1 \
        libglib2.0-0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# -----------------------------------------------------------------------------
# Python dependencies — copy requirements FIRST so this layer caches when
# only application code changes.
# -----------------------------------------------------------------------------
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# -----------------------------------------------------------------------------
# Fix MediaPipe model file permissions — the issue that kept biting us on
# Streamlit Cloud. We're root in this layer so chmod always works.
# -----------------------------------------------------------------------------
RUN find /usr/local/lib/python3.12/site-packages/mediapipe \
        \( -name "*.tflite" -o -name "*.binarypb" -o -name "*.pbtxt" \) \
        -exec chmod 644 {} \; 2>/dev/null || true

# -----------------------------------------------------------------------------
# Application code (last layer — changes most often)
# -----------------------------------------------------------------------------
COPY . .

# Make sure the entrypoint script is executable
RUN chmod +x /app/entrypoint.sh

# Render injects PORT at runtime. Default 8501 for local docker run.
EXPOSE 8501

# Streamlit's built-in health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl --fail http://localhost:${PORT:-8501}/_stcore/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
