"""End-to-end test of softball mode."""
import sys, types, tempfile, pathlib
for m in ["streamlit", "plotly", "plotly.express", "plotly.graph_objects",
          "streamlit.components", "streamlit.components.v1"]:
    sys.modules[m] = types.ModuleType(m)
sys.path.insert(0, str(pathlib.Path(__file__).parent))

import pitching_lab as pl

# Isolated test DB so we don't touch the user's actual data
pl.DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "test_softball.db"

print("=" * 70)
print("SOFTBALL MODE — END-TO-END TEST")
print("=" * 70)

# --- 1. Sport-aware archetypes ---
print("\n[1] Sport archetype lookup")
bb_arch = pl.get_sport_archetypes("Baseball")
sb_arch = pl.get_sport_archetypes("Softball")
print(f"   Baseball pitch types: {list(bb_arch.keys())[:3]}...")
print(f"   Softball pitch types: {list(sb_arch.keys())}")
assert "Rise Ball" in sb_arch
assert "Four-Seam Fastball" in bb_arch
assert sb_arch != bb_arch

# --- 2. Demo session generator with sport ---
print("\n[2] Generating softball demo session...")
sb_df = pl.generate_demo_session("Sara Johnson", sport="Softball")
print(f"   Pitches: {len(sb_df)}")
print(f"   Pitch types thrown: {sorted(sb_df['Pitch_Type'].unique())}")
assert "Rise Ball" in sb_df["Pitch_Type"].values
assert "Softball Fastball" in sb_df["Pitch_Type"].values
assert "Four-Seam Fastball" not in sb_df["Pitch_Type"].values  # no baseball pitches in softball session

# Reproducibility: same name → same data
sb_df2 = pl.generate_demo_session("Sara Johnson", sport="Softball")
assert sb_df.iloc[0]["Velocity_mph"] == sb_df2.iloc[0]["Velocity_mph"]
print("   Same-name reproducibility: ✓")

# --- 3. Sport database column ---
print("\n[3] Sport persists in DB")
pl.init_db()
aid = pl.add_athlete("Sara Johnson", "Right", "Softball", "2026")
roster = pl.list_athletes()
assert roster[0]["sport"] == "Softball"
print(f"   Roster shows: {roster[0]['name']} | sport={roster[0]['sport']}")

# Add a baseball athlete for variety
bid = pl.add_athlete("Marcus Vance", "Right", "Baseball", "2027")
all_a = pl.list_athletes()
sports_in_roster = sorted({a["sport"] for a in all_a})
print(f"   Sports in roster: {sports_in_roster}")
assert "Baseball" in sports_in_roster and "Softball" in sports_in_roster

# --- 4. Grip recommendation for softball pitches (sport-aware) ---
print("\n[4] Grip recommendations for softball pitches (sport='Softball')")
for ptype in ["Rise Ball", "Drop Ball", "Softball Fastball", "Screwball",
              "Curveball", "Change-Up"]:
    grip = pl._grip_for_pitch_type(ptype, sport="Softball")
    print(f"   {ptype:<22s} → {grip}")
    assert grip is not None, f"No grip for {ptype}"
    # Generic names should route to softball variants
    if ptype == "Curveball":
        assert grip == "softball_curve", f"Expected softball_curve, got {grip}"
    if ptype == "Change-Up":
        assert grip == "softball_change", f"Expected softball_change, got {grip}"

# --- 5. Drill recommender for softball (sport-aware) ---
print("\n[5] Drill recommender on softball session (sport='Softball')")
plan = pl.recommend_drills(sb_df, sport="Softball")
print(f"   Today: {[d['label'] for d in plan['today']]}")
print(f"   Week:  {[d['label'] for d in plan['week']]}")
# Should NOT fire the baseball-specific 'low extension' drill
week_labels = [d["label"] for d in plan["week"]]
assert "Short Release Extension" not in week_labels, \
    "Extension check should be skipped for softball"

# --- 6. PDF generation for softball ---
print("\n[6] Softball PDF generation")
pdf_bytes = pl.generate_pbr_pdf(sb_df, "Sara Johnson", "Right", "2026", sport="Softball")
assert pdf_bytes[:4] == b"%PDF"
print(f"   Generated PDF: {len(pdf_bytes):,} bytes ✓")

# Save for visual inspection
out = pathlib.Path(__file__).parent / "sample_softball_pbr.pdf"
out.write_bytes(pdf_bytes)
print(f"   Saved: {out}")

# --- 7. Baseball PDF still works ---
print("\n[7] Baseball PDF still works (regression check)")
bb_df = pl.generate_demo_session("Marcus Vance", sport="Baseball")
bb_pdf = pl.generate_pbr_pdf(bb_df, "Marcus Vance", "Right", "2027", sport="Baseball")
assert bb_pdf[:4] == b"%PDF"
print(f"   Baseball PDF: {len(bb_pdf):,} bytes ✓")

print("\n" + "=" * 70)
print("SOFTBALL MODE — ALL CHECKS PASS")
print("=" * 70)
