"""End-to-end test of the pitch-tunneling math + POV charts."""
import sys, types, pathlib

# Stub streamlit + plotly so we can import pitching_lab headlessly
class _StubFigure:
    def __init__(self, *a, **kw):
        self._shapes = []
        self._traces = []
        self._annotations = []
        self._layout = {}
    def add_shape(self, **kw):     self._shapes.append(kw)
    def add_trace(self, t):        self._traces.append(t)
    def add_annotation(self, **kw):self._annotations.append(kw)
    def update_layout(self, **kw): self._layout.update(kw)

class _StubScatter:
    def __init__(self, **kw): self.kw = kw

go = types.ModuleType("plotly.graph_objects")
go.Figure  = _StubFigure
go.Scatter = _StubScatter

px = types.ModuleType("plotly.express")
plotly = types.ModuleType("plotly")
plotly.graph_objects = go
plotly.express = px

for n, m in [("streamlit", types.ModuleType("streamlit")),
             ("plotly", plotly), ("plotly.express", px),
             ("plotly.graph_objects", go),
             ("streamlit.components", types.ModuleType("streamlit.components")),
             ("streamlit.components.v1", types.ModuleType("streamlit.components.v1"))]:
    sys.modules[n] = m

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import pitching_lab as pl

print("=" * 70)
print("PITCH TUNNELING — END-TO-END TEST")
print("=" * 70)

# --- 1. Constants are sane ---
print("\n[1] Sport constants")
bb = pl.TUNNEL_CONSTANTS["Baseball"]
sb = pl.TUNNEL_CONSTANTS["Softball"]
print(f"   Baseball tunnel point: {bb['tunnel_distance_ft']} ft from plate")
print(f"   Softball  tunnel point: {sb['tunnel_distance_ft']} ft from plate")
assert bb["tunnel_distance_ft"] < bb["rubber_distance_ft"]
assert sb["tunnel_distance_ft"] < sb["rubber_distance_ft"]

# --- 2. Release point ---
print("\n[2] Release-point geometry")
rxyz = pl._pitch_release_point("Baseball")
print(f"   Baseball release (x, y, z): {rxyz}")
assert rxyz[1] == bb["rubber_distance_ft"] - bb["release_extension_ft"]
assert rxyz[2] == bb["release_height_ft"]

# --- 3. Single-pitch trajectory has the right shape ---
print("\n[3] Trajectory sampler returns full release→plate path")
samples = pl._pitch_trajectory_points(
    release_xyz=rxyz, plate_xyz=(0.0, 3.0),
    velocity_mph=90, vert_break_in=18, horiz_break_in=4,
    n_samples=20,
)
print(f"   Samples returned: {len(samples)}")
print(f"   Start: y={samples[0]['y']:.1f} ft (release)  → End: y={samples[-1]['y']:.2f} ft (plate)")
assert len(samples) == 21
assert abs(samples[0]["y"] - rxyz[1]) < 1e-6, "First sample must be at release"
assert abs(samples[-1]["y"]) < 1e-6, "Last sample must be at plate (y=0)"
# Pitch must land at the target plate location
assert abs(samples[-1]["x"] - 0.0) < 0.001 and abs(samples[-1]["z"] - 3.0) < 0.001

# --- 4. Interpolation at tunnel point ---
print("\n[4] Tunnel-point interpolation")
tunnel_pt = pl._interp_at_y(samples, bb["tunnel_distance_ft"])
print(f"   At y=22ft: x={tunnel_pt['x']:+.3f} ft, z={tunnel_pt['z']:.3f} ft, "
      f"t={tunnel_pt['t']*1000:.0f} ms")
assert tunnel_pt is not None
# At tunnel point, pitch hasn't deviated fully from straight-line yet
# (break only partially accumulated)
assert tunnel_pt["t"] > 0 and tunnel_pt["t"] < (samples[-1]["t"])

# --- 5. Arsenal tunnel — fastball + slider + curveball ---
print("\n[5] Arsenal tunnel math")
arsenal = [
    {"Pitch_Type": "Four-Seam Fastball",   "Avg_Velo": 92.0, "Avg_Vert_Break": 18.0,  "Avg_Horiz_Break": 4.0},
    {"Pitch_Type": "Slider Strike-Getter", "Avg_Velo": 83.0, "Avg_Vert_Break": -2.0,  "Avg_Horiz_Break": -7.0},
    {"Pitch_Type": "Change-Up",            "Avg_Velo": 79.0, "Avg_Vert_Break": 6.0,   "Avg_Horiz_Break": 8.0},
]
tunnel_data = pl.compute_arsenal_tunnel(
    arsenal=arsenal,
    starting_pitch_type="Four-Seam Fastball",
    plate_x=0.0, plate_z=3.0,
    sport="Baseball",
)
for ptype, p in tunnel_data.items():
    print(f"   {ptype:<22s} plate=({p['plate_x']:+.2f},{p['plate_z']:.2f}) "
          f"velo={p['velocity_mph']:.0f}  tunnel_xyz=("
          f"{p['tunnel_xyz']['x']:+.2f},{p['tunnel_xyz']['z']:.2f})")
assert "Four-Seam Fastball" in tunnel_data
assert tunnel_data["Four-Seam Fastball"]["is_starting"]
# Slider should land BELOW & SHIFTED from the fastball due to break differential
fb = tunnel_data["Four-Seam Fastball"]
sl = tunnel_data["Slider Strike-Getter"]
assert sl["plate_z"] < fb["plate_z"], "Slider should drop more than fastball"
assert sl["plate_x"] != fb["plate_x"], "Slider should drift horizontally vs fastball"

# --- 6. Tunneling-quality metrics ---
print("\n[6] Tunneling quality grades")
quality = pl.tunnel_quality_metrics(tunnel_data)
for ptype, q in quality.items():
    print(f"   {ptype:<22s} tunnel_offset={q['tunnel_offset_in']:5.1f}\"  "
          f"plate_diff={q['plate_diff_in']:5.1f}\"  "
          f"timing={q['timing_offset_ms']:+5.0f}ms  "
          f"grade={q['tunnel_grade']}")
assert quality["Four-Seam Fastball"]["tunnel_offset_in"] == 0.0
assert quality["Slider Strike-Getter"]["tunnel_offset_in"] > 0.0
# At tunnel point, pitches should still be close-ish (the magic of tunneling)
# Even with a 7"+13" total break differential, accumulated-break is only ~35%
# of that at the tunnel point, so tunnel offset should be < plate diff.
assert quality["Slider Strike-Getter"]["tunnel_offset_in"] < quality["Slider Strike-Getter"]["plate_diff_in"]
assert quality["Change-Up"]["tunnel_offset_in"] < quality["Change-Up"]["plate_diff_in"]

# --- 7. The three POV figures build ---
print("\n[7] Build all three POV figures")
fig_b = pl._build_tunnel_batter_view(tunnel_data, sport="Baseball", hand="Right")
fig_p = pl._build_tunnel_pitcher_view(tunnel_data, sport="Baseball")
fig_s = pl._build_tunnel_side_view(tunnel_data, sport="Baseball")
print(f"   Batter POV:  {len(fig_b._traces)} traces, {len(fig_b._shapes)} shapes")
print(f"   Pitcher POV: {len(fig_p._traces)} traces, {len(fig_p._shapes)} shapes")
print(f"   Side View:   {len(fig_s._traces)} traces, {len(fig_s._shapes)} shapes")
assert len(fig_b._traces) >= 3, "Batter view should have a trace per pitch"
assert len(fig_p._traces) >= 3, "Pitcher view should have a trace per pitch"
assert len(fig_s._traces) >= 3, "Side view should have at least 3 trajectories"

# --- 8. Softball arsenal also works (different distances + tunnel point) ---
print("\n[8] Softball arsenal tunnel")
sb_arsenal = [
    {"Pitch_Type": "Softball Fastball", "Avg_Velo": 60.0, "Avg_Vert_Break": -2.0, "Avg_Horiz_Break": 0.0},
    {"Pitch_Type": "Rise Ball",         "Avg_Velo": 58.0, "Avg_Vert_Break": 8.0,  "Avg_Horiz_Break": -1.0},
    {"Pitch_Type": "Drop Ball",         "Avg_Velo": 55.0, "Avg_Vert_Break": -16.0, "Avg_Horiz_Break": 0.0},
]
sb_tunnel = pl.compute_arsenal_tunnel(
    arsenal=sb_arsenal,
    starting_pitch_type="Softball Fastball",
    plate_x=0.0, plate_z=2.5,
    sport="Softball",
)
sb_quality = pl.tunnel_quality_metrics(sb_tunnel)
print(f"   Pitches in arsenal: {list(sb_tunnel.keys())}")
for ptype, q in sb_quality.items():
    print(f"   {ptype:<22s} tunnel_offset={q['tunnel_offset_in']:5.1f}\"  "
          f"grade={q['tunnel_grade']}")
assert "Softball Fastball" in sb_tunnel
assert sb_quality["Softball Fastball"]["tunnel_offset_in"] == 0.0

print("\n" + "=" * 70)
print("PITCH TUNNELING — ALL CHECKS PASS")
print("=" * 70)
