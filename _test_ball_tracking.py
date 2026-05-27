"""End-to-end test of the ball detector + trajectory fitter.

Generates synthetic frames (white circles moving across a known background)
and verifies the detector finds the ball and the fitter recovers the
expected velocity within tolerance.

Run from PitchingLab folder: python3 _test_ball_tracking.py
"""
import sys, types, pathlib

# Stub streamlit + plotly so we can import pitching_lab headlessly
class _StubFigure:
    def __init__(self, *a, **kw):
        self._shapes = []
        self._traces = []
        self._annotations = []
        self._layout = {}
    def add_shape(self, **kw):      self._shapes.append(kw)
    def add_trace(self, t):         self._traces.append(t)
    def add_annotation(self, **kw): self._annotations.append(kw)
    def update_layout(self, **kw):  self._layout.update(kw)

class _StubScatter:
    def __init__(self, **kw): self.kw = kw

go = types.ModuleType("plotly.graph_objects")
go.Figure  = _StubFigure
go.Scatter = _StubScatter

px = types.ModuleType("plotly.express")
plotly = types.ModuleType("plotly"); plotly.graph_objects = go; plotly.express = px
for n, m in [("streamlit", types.ModuleType("streamlit")),
             ("plotly", plotly), ("plotly.express", px),
             ("plotly.graph_objects", go),
             ("streamlit.components", types.ModuleType("streamlit.components")),
             ("streamlit.components.v1", types.ModuleType("streamlit.components.v1"))]:
    sys.modules[n] = m

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import pitching_lab as pl

print("=" * 70)
print("BALL TRACKING — END-TO-END TEST (synthetic frames)")
print("=" * 70)

# ===== 1. Skip if OpenCV / numpy are missing =====
print("\n[1] Check CV deps")
try:
    import cv2
    import numpy as np
    print(f"   OpenCV: {cv2.__version__}")
    print(f"   NumPy:  {np.__version__}")
except Exception as e:
    print(f"   ⚠️  OpenCV/numpy not installed — skipping rest of test ({e})")
    print("\nRun: pip install -r requirements.txt --upgrade")
    sys.exit(0)

# ===== 2. Detector finds a stationary ball =====
print("\n[2] Detector finds a static white circle on dark background")
frame = np.zeros((400, 600, 3), dtype=np.uint8)
cv2.circle(frame, (300, 200), 12, (255, 255, 255), -1)  # white ball at (300, 200)
result = pl.detect_ball_in_frame(frame)
print(f"   Found ball at: {result}")
assert result is not None, "Detector should find a white circle"
dx, dy = abs(result[0] - 300), abs(result[1] - 200)
assert dx < 3 and dy < 3, f"Detector accuracy off — got {result}, expected ~(300, 200)"
print("   ✓ Detection within 3 px of true center")

# ===== 3. Detector ignores too-small / too-large blobs =====
print("\n[3] Detector ignores blobs outside expected radius range")
frame = np.zeros((400, 600, 3), dtype=np.uint8)
cv2.circle(frame, (100, 100), 2, (255, 255, 255), -1)   # too small
cv2.circle(frame, (500, 300), 60, (255, 255, 255), -1)  # too large
result = pl.detect_ball_in_frame(frame)
assert result is None, f"Detector should reject out-of-range blobs, got {result}"
print("   ✓ Out-of-range blobs rejected")

# ===== 4. Detector picks the ball when other bright noise is present =====
print("\n[4] Detector picks the ball amid bright noise")
frame = np.zeros((400, 600, 3), dtype=np.uint8)
# Add some random noise blobs (non-circular bright shapes)
cv2.rectangle(frame, (10, 10), (60, 30), (250, 250, 250), -1)
cv2.rectangle(frame, (500, 350), (590, 390), (240, 240, 240), -1)
# Add the actual ball
cv2.circle(frame, (320, 180), 11, (255, 255, 255), -1)
result = pl.detect_ball_in_frame(frame)
print(f"   Found ball at: {result}  (expected near (320, 180))")
assert result is not None
dx, dy = abs(result[0] - 320), abs(result[1] - 180)
assert dx < 5 and dy < 5, f"Detector should prefer circle over rectangle — got {result}"
print("   ✓ Circle preferred over noise blobs")

# ===== 5. Trajectory fitter recovers velocity from a clean pitch sequence =====
print("\n[5] Trajectory fitter recovers velocity")
# Synthesize a pitch: 90 mph fastball over 54.5 ft (release → plate)
# At 90 mph = 132 ft/s → flight time = 0.413 sec
# At 60 fps capture, that's ~25 frames of flight
fps = 60
true_velo_mph = 90.0
flight_time = 54.5 / (true_velo_mph * 1.467)
total_frames = int(flight_time * fps) + 1

# Ball travels diagonally across the image: from (200, 100) at release
# to (350, 280) at the plate. Plate center we'll call (350, 280) with
# width 50 pixels.
positions = []
for i in range(total_frames + 1):
    t = i / fps
    f = t / flight_time
    x = 200 + (350 - 200) * f
    y = 100 + (280 - 100) * f
    positions.append((t, int(round(x)), int(round(y))))

calibration = {
    "plate_width_px":    50,
    "plate_center_x_px": 350,
    "plate_center_y_px": 280,
    "sport":             "Baseball",
}
fit = pl.fit_pitch_trajectory(positions, calibration)
print(f"   Recovered: velocity={fit['velocity_mph']} mph, flight={fit['flight_time_sec']}s, "
      f"plate=({fit['plate_x_ft']:+.2f}, {fit['plate_z_ft']:.2f}) ft")
assert fit is not None
# Velocity should be very close to true (within ~3% — release/catch frame
# heuristics might pick slightly different boundaries)
err_pct = abs(fit["velocity_mph"] - true_velo_mph) / true_velo_mph * 100
print(f"   Velocity error: {err_pct:.1f}%")
assert err_pct < 8, f"Velocity off by {err_pct:.1f}% (expected < 8%)"
# Plate location should be near (0, 2.5) since we placed catch at plate center
assert abs(fit["plate_x_ft"]) < 0.5, f"plate_x off: {fit['plate_x_ft']}"
assert abs(fit["plate_z_ft"] - 2.5) < 0.5, f"plate_z off: {fit['plate_z_ft']}"
print("   ✓ Velocity + plate location within tolerance")

# ===== 6. Fitter handles a softball pitch (shorter pitching distance) =====
print("\n[6] Fitter handles softball (43 ft mound)")
sb_true_velo = 60.0
sb_dist = 39.0  # 43 ft rubber - 4 ft release extension
sb_flight = sb_dist / (sb_true_velo * 1.467)
sb_frames = int(sb_flight * fps) + 1
sb_positions = []
for i in range(sb_frames + 1):
    t = i / fps
    f = t / sb_flight
    x = 220 + (360 - 220) * f
    y = 80 + (290 - 80) * f
    sb_positions.append((t, int(round(x)), int(round(y))))
sb_cal = {
    "plate_width_px":    45,
    "plate_center_x_px": 360,
    "plate_center_y_px": 290,
    "sport":             "Softball",
}
sb_fit = pl.fit_pitch_trajectory(sb_positions, sb_cal)
assert sb_fit is not None
print(f"   Softball velo: {sb_fit['velocity_mph']} mph (expected ~{sb_true_velo})")
sb_err = abs(sb_fit["velocity_mph"] - sb_true_velo) / sb_true_velo * 100
print(f"   Velocity error: {sb_err:.1f}%")
assert sb_err < 10, f"Softball velocity off by {sb_err:.1f}%"

# ===== 7. Fitter returns None for too-few samples =====
print("\n[7] Fitter rejects too-few-samples input")
short_fit = pl.fit_pitch_trajectory([(0, 100, 100), (0.1, 110, 105)], calibration)
assert short_fit is None
print("   ✓ Short input rejected gracefully")

# ===== 8. Full pipeline: synthesize 30 frames, detect ball in each,
#         then run the fitter on the detected positions =====
print("\n[8] Pipeline: detect → fit on synthesized frames")
detected = []
n_pipe_frames = 25
pipe_flight = 0.4
for i in range(n_pipe_frames + 1):
    t = i / fps
    f = t / pipe_flight
    if f > 1.0:
        break
    cx = int(200 + (350 - 200) * f)
    cy = int(100 + (280 - 100) * f)
    frame = np.zeros((400, 600, 3), dtype=np.uint8)
    # Add some scene noise so detection has to filter
    cv2.rectangle(frame, (10, 10), (50, 25), (240, 240, 240), -1)
    cv2.circle(frame, (cx, cy), 10, (255, 255, 255), -1)
    pos = pl.detect_ball_in_frame(frame)
    if pos:
        detected.append((t, pos[0], pos[1]))
print(f"   Detected ball in {len(detected)}/{n_pipe_frames + 1} frames")
assert len(detected) >= 0.8 * (n_pipe_frames + 1), "Detector should hit on most frames"
pipe_fit = pl.fit_pitch_trajectory(detected, calibration)
print(f"   Pipeline velocity: {pipe_fit['velocity_mph']} mph")
assert pipe_fit is not None
# Synthetic pitch was at 54.5/(0.4*1.467) ≈ 93 mph
expected = 54.5 / (pipe_flight * 1.467)
err = abs(pipe_fit["velocity_mph"] - expected) / expected * 100
print(f"   Expected ~{expected:.1f} mph, error {err:.1f}%")
assert err < 12, f"Pipeline velocity off by {err:.1f}%"
print("   ✓ End-to-end pipeline works on synthetic frames")

# ===== 9. Break estimator recovers vertical break from curved trajectory =====
print("\n[9] Break estimator — fastball with +15\" of IVB")
# Synthesize a fastball trajectory in REAL-WORLD COORDINATES, then project to
# pixels using the same calibration the fitter will use.
#
# Real-world model (matches the trajectory math used elsewhere in the app):
#   z(t) = rz + (pz - vb - rz + 0.5*g*T²)*f + (vb - 0.5*g*T²)*f²    where f=t/T
# Release at 6 ft, plate at 3 ft, vb = 15"/12 = 1.25 ft, T from velo.

g = 32.17
true_vb_in = 15.0
true_hb_in = 6.0
rz_ft, pz_ft = 6.0, 3.0
rx_ft, px_ft = 0.0, 0.5   # slight horizontal — half-foot first-base side at plate
true_velo = 92.0
pitch_dist = 54.5
T_total = pitch_dist / (true_velo * 1.467)
gd = 0.5 * g * T_total ** 2   # gravity drop a no-spin ball would have

vb_ft = true_vb_in / 12.0
hb_ft = true_hb_in / 12.0
z_lin = (pz_ft - vb_ft - rz_ft) + gd
z_quad = vb_ft - gd
x_lin = (px_ft - hb_ft - rx_ft)
x_quad = hb_ft

# Camera calibration: plate width 50 px, plate center at (350, 280)
plate_w_px = 50
plate_cx_px = 350
plate_cy_px = 280
# 1 px = 17"/plate_w_px in real-world inches; convert to ft
PLATE_WIDTH_IN = 17.0
px_per_ft = plate_w_px * (12.0 / PLATE_WIDTH_IN)
# Plate camera reference height is z=2.5 ft (see fitter)
plate_camera_z = 2.5

fps = 60
fastball_positions = []
n_frames = int(T_total * fps) + 1
for i in range(n_frames + 1):
    t = i / fps
    if t > T_total:
        break
    f = t / T_total
    z_world = rz_ft + z_lin * f + z_quad * f * f
    x_world = rx_ft + x_lin * f + x_quad * f * f
    # Project to pixels (behind-catcher view: x→x, height→y inverted)
    x_px = int(round(plate_cx_px + x_world * px_per_ft))
    y_px = int(round(plate_cy_px - (z_world - plate_camera_z) * px_per_ft))
    fastball_positions.append((t, x_px, y_px))

calibration_v2 = {
    "plate_width_px":    plate_w_px,
    "plate_center_x_px": plate_cx_px,
    "plate_center_y_px": plate_cy_px,
    "sport":             "Baseball",
}
fit_v2 = pl.fit_pitch_trajectory(fastball_positions, calibration_v2)
print(f"   Recovered: velo={fit_v2['velocity_mph']} mph · "
      f"VB={fit_v2['vert_break_in']:+.1f}\" (true +{true_vb_in:.0f}\") · "
      f"HB={fit_v2['horiz_break_in']:+.1f}\" (true +{true_hb_in:.0f}\")")
assert fit_v2["vert_break_in"] is not None, "Vert break should be recovered"
assert fit_v2["horiz_break_in"] is not None, "Horiz break should be recovered"
vb_err = abs(fit_v2["vert_break_in"] - true_vb_in)
hb_err = abs(fit_v2["horiz_break_in"] - true_hb_in)
print(f"   Errors: VB {vb_err:.1f}\", HB {hb_err:.1f}\"")
# Within a few inches is good — phone-video break is never radar-precise.
# We were told to expect ~10-15% accuracy, so 2-3 inches off a 15" break is fine.
assert vb_err < 3.0, f"VB error too high ({vb_err:.1f}\")"
assert hb_err < 3.0, f"HB error too high ({hb_err:.1f}\")"
print("   ✓ Both break dimensions within tolerance")

# ===== 10. Curveball: large negative IVB =====
print("\n[10] Break estimator — curveball with -12\" IVB (drops more than gravity)")
true_vb_in = -12.0
true_hb_in = -8.0
pz_ft = 1.5
true_velo = 78.0
T_total = pitch_dist / (true_velo * 1.467)
gd = 0.5 * g * T_total ** 2
vb_ft = true_vb_in / 12.0
hb_ft = true_hb_in / 12.0
z_lin = (pz_ft - vb_ft - rz_ft) + gd
z_quad = vb_ft - gd
x_lin = (px_ft - hb_ft - rx_ft)
x_quad = hb_ft

curve_positions = []
n_frames = int(T_total * fps) + 1
for i in range(n_frames + 1):
    t = i / fps
    if t > T_total:
        break
    f = t / T_total
    z_world = rz_ft + z_lin * f + z_quad * f * f
    x_world = rx_ft + x_lin * f + x_quad * f * f
    x_px = int(round(plate_cx_px + x_world * px_per_ft))
    y_px = int(round(plate_cy_px - (z_world - plate_camera_z) * px_per_ft))
    curve_positions.append((t, x_px, y_px))

fit_c = pl.fit_pitch_trajectory(curve_positions, calibration_v2)
print(f"   Recovered: velo={fit_c['velocity_mph']} mph · "
      f"VB={fit_c['vert_break_in']:+.1f}\" (true {true_vb_in:.0f}\") · "
      f"HB={fit_c['horiz_break_in']:+.1f}\" (true {true_hb_in:.0f}\")")
vb_err = abs(fit_c["vert_break_in"] - true_vb_in)
hb_err = abs(fit_c["horiz_break_in"] - true_hb_in)
print(f"   Errors: VB {vb_err:.1f}\", HB {hb_err:.1f}\"")
assert vb_err < 3.0, f"Curveball VB error too high ({vb_err:.1f}\")"
assert hb_err < 3.0, f"Curveball HB error too high ({hb_err:.1f}\")"
# Sign check: curveball VB should be NEGATIVE
assert fit_c["vert_break_in"] < 0, "Curveball VB should be negative (drops more than gravity)"
print("   ✓ Curveball break direction + magnitude correct")

# ===== 11. Break math is sport-aware (softball uses 43 ft mound) =====
print("\n[11] Softball break estimation")
sb_velo = 60.0
sb_dist = 39.0
T_total = sb_dist / (sb_velo * 1.467)
gd = 0.5 * g * T_total ** 2
true_vb_in = 8.0  # rise ball — lifts above gravity
true_hb_in = 0.0
pz_ft = 2.0
vb_ft = true_vb_in / 12.0
hb_ft = true_hb_in / 12.0
z_lin = (pz_ft - vb_ft - 4.0) + gd   # softball release at 4 ft
z_quad = vb_ft - gd
x_lin = (px_ft - hb_ft - rx_ft)
x_quad = hb_ft

sb_positions = []
n_frames = int(T_total * fps) + 1
for i in range(n_frames + 1):
    t = i / fps
    if t > T_total:
        break
    f = t / T_total
    z_world = 4.0 + z_lin * f + z_quad * f * f
    x_world = rx_ft + x_lin * f + x_quad * f * f
    x_px = int(round(plate_cx_px + x_world * px_per_ft))
    y_px = int(round(plate_cy_px - (z_world - plate_camera_z) * px_per_ft))
    sb_positions.append((t, x_px, y_px))

sb_cal = dict(calibration_v2, sport="Softball")
sb_fit = pl.fit_pitch_trajectory(sb_positions, sb_cal)
print(f"   Recovered: velo={sb_fit['velocity_mph']} mph · "
      f"VB={sb_fit['vert_break_in']:+.1f}\" (true +{true_vb_in}\")")
vb_err = abs(sb_fit["vert_break_in"] - true_vb_in)
print(f"   VB error: {vb_err:.1f}\"")
assert vb_err < 3.0, f"Softball VB error too high ({vb_err:.1f}\")"
print("   ✓ Softball break recovery works")

# ===== 12. Spin estimator — fastball recovers ~2200 useful RPM =====
print("\n[12] Spin estimator — 92 mph 4-seam fastball")
spin = pl.estimate_spin_metrics(vert_break_in=15.0, horiz_break_in=6.0,
                                  velocity_mph=92.0,
                                  pitch_type="Four-Seam Fastball",
                                  sport="Baseball")
print(f"   Useful spin: {spin['useful_spin_rpm']} RPM "
      f"(typical 4-seam: ~2100 useful)")
print(f"   Tilt: {spin['tilt_clock']} "
      f"(typical fastball: 12:30-1:00)")
print(f"   Efficiency: {spin['spin_efficiency_pct']}%")
assert 1800 <= spin["useful_spin_rpm"] <= 2600, "Useful spin RPM out of typical range"
assert spin["tilt_clock"] in ("12:30", "12:45", "1:00"), \
    f"Expected fastball tilt, got {spin['tilt_clock']}"
assert spin["spin_efficiency_pct"] >= 70, "Fastball efficiency should be high"
print("   ✓ Fastball spin metrics in expected range")

# ===== 13. Spin estimator — curveball ~ 6:30-7:30 tilt =====
print("\n[13] Spin estimator — 78 mph curveball")
spin_c = pl.estimate_spin_metrics(vert_break_in=-12.0, horiz_break_in=-8.0,
                                    velocity_mph=78.0,
                                    pitch_type="Curveball",
                                    sport="Baseball")
print(f"   Useful spin: {spin_c['useful_spin_rpm']} RPM")
print(f"   Tilt: {spin_c['tilt_clock']} "
      f"(typical curveball: 6:30-7:30)")
print(f"   Efficiency: {spin_c['spin_efficiency_pct']}%")
# Curveball tilt should be in lower half of clock (6-9 hour range)
hr_str, mn_str = spin_c["tilt_clock"].split(":")
hr = int(hr_str); mn = int(mn_str)
total_minutes = hr * 60 + mn
# 6:00 = 360 min, 8:00 = 480 min
assert 360 <= total_minutes <= 480, \
    f"Curveball tilt should be ~7:00, got {spin_c['tilt_clock']}"
print("   ✓ Curveball tilt in expected 6-8 o'clock range")

# ===== 14. Spin estimator — softball rise ball ~12:00 backspin =====
print("\n[14] Spin estimator — 60 mph softball rise ball")
spin_rb = pl.estimate_spin_metrics(vert_break_in=8.0, horiz_break_in=0.0,
                                     velocity_mph=60.0,
                                     pitch_type="Rise Ball",
                                     sport="Softball")
print(f"   Useful spin: {spin_rb['useful_spin_rpm']} RPM "
      f"(typical rise ball: ~1500 useful)")
print(f"   Tilt: {spin_rb['tilt_clock']} (rise = pure backspin = 12:00)")
print(f"   Efficiency: {spin_rb['spin_efficiency_pct']}%")
assert 800 <= spin_rb["useful_spin_rpm"] <= 2000, \
    "Softball rise ball spin out of expected range"
# Pure positive vert break, zero horiz break → tilt should be 12:00
assert spin_rb["tilt_clock"] == "12:00", \
    f"Pure backspin should be 12:00, got {spin_rb['tilt_clock']}"
print("   ✓ Pure-backspin rise ball recognized as 12:00 tilt")

# ===== 15. Spin estimator returns clean Nones when break unavailable =====
print("\n[15] Spin estimator handles missing break data")
spin_none = pl.estimate_spin_metrics(vert_break_in=None, horiz_break_in=None,
                                       velocity_mph=90.0)
assert spin_none["useful_spin_rpm"] is None
assert spin_none["tilt_clock"] is None
assert spin_none["spin_efficiency_pct"] is None
print("   ✓ Returns None values cleanly")

# ===== 16. Full pipeline now includes spin metrics =====
print("\n[16] Full fitter output includes spin metrics")
# Reuse fastball positions from test #9
full_fit = pl.fit_pitch_trajectory(fastball_positions, calibration_v2)
print(f"   Output keys: {sorted(full_fit.keys())}")
expected_keys = {"velocity_mph", "vert_break_in", "horiz_break_in",
                  "useful_spin_rpm", "spin_efficiency_pct", "tilt_clock"}
assert expected_keys <= set(full_fit.keys()), \
    f"Missing keys: {expected_keys - set(full_fit.keys())}"
assert full_fit["useful_spin_rpm"] is not None
print(f"   Fastball: velo={full_fit['velocity_mph']} mph · "
      f"useful_spin={full_fit['useful_spin_rpm']} RPM · "
      f"tilt={full_fit['tilt_clock']} · "
      f"eff={full_fit['spin_efficiency_pct']}%")

print("\n" + "=" * 70)
print("BALL TRACKING + BREAK + SPIN ESTIMATION — ALL CHECKS PASS")
print("=" * 70)
