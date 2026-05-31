#!/bin/bash
# ============================================================================
# DIAMOND SPORTS LAB — DEMO LAUNCHER
# Double-click this BEFORE a demo to start the app AND open a connection
# guide that shows BOTH local-Wi-Fi and cloud URLs in giant text + QR codes.
# Hold your phone up to the laptop screen, scan whichever QR works, and
# the app loads on the phone. Use the phone as the tripod-mounted camera.
# ============================================================================

cd "$(dirname "$0")" || exit 1

# Make sure brew/python paths are loaded
if [[ -d /opt/homebrew/bin ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
elif [[ -d /usr/local/bin ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
fi

echo ""
echo "============================================================"
echo "  DIAMOND SPORTS LAB — DEMO LAUNCHER"
echo "============================================================"
echo ""

# -----------------------------------------------------------------
# Detect local network IP — try Wi-Fi first, then Ethernet
# -----------------------------------------------------------------
LOCAL_IP=""
for IF in en0 en1 en2 en3; do
    IP=$(ipconfig getifaddr "$IF" 2>/dev/null)
    if [ -n "$IP" ]; then
        LOCAL_IP="$IP"
        echo "Found local IP on $IF: $LOCAL_IP"
        break
    fi
done
if [ -z "$LOCAL_IP" ]; then
    LOCAL_IP="(no Wi-Fi detected)"
    echo "WARNING: No Wi-Fi connection — local URL won't work."
    echo "Connect to Wi-Fi or create a phone hotspot, then re-run this script."
fi
echo ""

LOCAL_URL="http://${LOCAL_IP}:8501"
CLOUD_URL="https://diamond-sports-lab.streamlit.app"

# -----------------------------------------------------------------
# Generate the on-screen connection guide HTML
# Includes both URLs in massive text + QR codes (via inline JS lib)
# so the phone can scan from across the room.
# -----------------------------------------------------------------
GUIDE_PATH="/tmp/diamond_sports_lab_connect.html"
cat > "$GUIDE_PATH" <<HTML
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Diamond Sports Lab — Connect Your Phone</title>
<style>
  body {
    background: #0f172a; color: #f1f5f9;
    font-family: -apple-system, "Segoe UI", Inter, sans-serif;
    margin: 0; padding: 32px;
    display: flex; flex-direction: column; align-items: center;
    min-height: 100vh; box-sizing: border-box;
  }
  h1 { color: #d4a634; letter-spacing: 0.08em; text-transform: uppercase;
       font-size: 14px; margin: 0; font-weight: 700; }
  h2 { color: #f1f5f9; font-size: 28px; margin: 6px 0 32px 0; font-weight: 800; }
  .cards { display: flex; gap: 24px; flex-wrap: wrap; justify-content: center; max-width: 1100px; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 14px;
          padding: 28px; width: 480px; box-sizing: border-box; }
  .card.cloud { border-left: 6px solid #3b82f6; }
  .card.local { border-left: 6px solid #22c55e; }
  .badge { font-size: 11px; letter-spacing: 0.12em; font-weight: 700;
           text-transform: uppercase; }
  .badge.cloud { color: #3b82f6; }
  .badge.local { color: #22c55e; }
  .title { font-size: 22px; font-weight: 800; color: #f1f5f9; margin: 6px 0 4px 0; }
  .desc { font-size: 14px; color: #94a3b8; margin-bottom: 18px; line-height: 1.5; }
  .url { background: #0f172a; border: 1px solid #334155; border-radius: 8px;
         padding: 14px 18px; font-family: "JetBrains Mono", Menlo, monospace;
         font-size: 18px; color: #f1f5f9; word-break: break-all; margin-bottom: 18px;
         text-align: center; font-weight: 600; }
  .qr-box { background: white; padding: 16px; border-radius: 10px;
            display: flex; justify-content: center; }
  .step { font-size: 13px; color: #cbd5e1; line-height: 1.6; margin-top: 16px; }
  .hint { font-size: 12px; color: #94a3b8; margin-top: 24px;
          text-align: center; max-width: 900px; }
</style>
</head>
<body>
<h1>Diamond Sports Lab</h1>
<h2>Connect Your Phone — Pick a Path</h2>
<div class="cards">

  <div class="card local">
    <div class="badge local">Option A · Local Wi-Fi (faster)</div>
    <div class="title">Both devices on same Wi-Fi</div>
    <div class="desc">Phone connects to your laptop directly over the local network.
    30 fps, no internet lag. Works without internet as long as both devices share a Wi-Fi router (or laptop joined your phone's hotspot).</div>
    <div class="url">${LOCAL_URL}</div>
    <div class="qr-box"><div id="qr-local"></div></div>
    <div class="step"><b>How to connect:</b><br>
    1. Make sure phone is on the same Wi-Fi as this laptop.<br>
    2. Scan the QR with the phone's camera, OR type the URL above into Safari.<br>
    3. Allow camera permission when asked.</div>
  </div>

  <div class="card cloud">
    <div class="badge cloud">Option B · Cloud (anywhere)</div>
    <div class="title">Internet-hosted version</div>
    <div class="desc">Works on any device with internet (cellular or Wi-Fi).
    No laptop needed for the app to run — Streamlit Cloud handles it.
    Slower frame rate (5-15 fps) and slight delay because video travels over the internet.</div>
    <div class="url">${CLOUD_URL}</div>
    <div class="qr-box"><div id="qr-cloud"></div></div>
    <div class="step"><b>How to connect:</b><br>
    1. Open Safari on the phone, scan the QR with the camera.<br>
    2. (One-time) Tap Share → Add to Home Screen for a clean icon next time.<br>
    3. Allow camera permission when asked.</div>
  </div>

</div>

<div class="hint">
  TIP: try Option A first — it's faster. If the local URL doesn't load on the
  phone (shows "can't reach" or "page didn't open"), fall back to Option B.
  Most failures of A are because the phone and laptop are on different Wi-Fi
  networks (e.g. one's on guest, the other's on main).
</div>

<!-- Inline QR code generator (qrcode-generator by Kazuhiko Arase — public domain) -->
<script src="https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.min.js"></script>
<script>
  function makeQR(elId, text) {
    const el = document.getElementById(elId);
    try {
      const qr = qrcode(0, 'M');
      qr.addData(text);
      qr.make();
      el.innerHTML = qr.createImgTag(7);
    } catch (e) {
      el.innerHTML = '<div style="color:#dc2626;padding:20px;">QR generator failed to load. Type the URL above by hand on your phone.</div>';
    }
  }
  // CDN may fail without internet — generate QRs only if the lib loaded.
  window.addEventListener('load', () => {
    if (typeof qrcode === 'function') {
      makeQR('qr-local', '${LOCAL_URL}');
      makeQR('qr-cloud', '${CLOUD_URL}');
    } else {
      ['qr-local','qr-cloud'].forEach(id => {
        document.getElementById(id).innerHTML =
          '<div style="color:#475569;padding:20px;text-align:center;">'+
          'QR generator needs internet to load. Type the URL above by hand on your phone.</div>';
      });
    }
  });
</script>

</body>
</html>
HTML

echo "============================================================"
echo "  Connection guide written to: $GUIDE_PATH"
echo ""
echo "  LOCAL  (fast — same Wi-Fi):   $LOCAL_URL"
echo "  CLOUD  (anywhere internet):   $CLOUD_URL"
echo "============================================================"
echo ""
echo "Opening connection guide in your browser..."

# Open the guide in the default browser BEFORE Streamlit starts so it's
# visible immediately (Streamlit will open its own tab as well)
open "$GUIDE_PATH"

# Give the guide a moment to render before Streamlit opens its own tab
sleep 2

echo ""
echo "============================================================"
echo "  STARTING DIAMOND SPORTS LAB..."
echo "  Keep this Terminal window OPEN during the demo."
echo "  To stop the app, close this window or press Ctrl+C."
echo "============================================================"
echo ""

# Run streamlit. --server.address=0.0.0.0 binds to all interfaces so phones
# on the local Wi-Fi can connect. Default --server.port=8501.
streamlit run pitching_lab.py --server.address=0.0.0.0
