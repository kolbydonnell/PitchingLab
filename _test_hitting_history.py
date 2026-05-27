"""End-to-end test of the Hitting Lab session-history flow:
- generate a hitting session
- save it with session_kind='hitting'
- list it back
- load_hitting_history aggregates multiple sessions
- _quality_color returns sane RGBA strings
- _build_hit_quality_zone_heatmap_figure works with mocked plotly

This mirrors _test_softball.py — stubs out Streamlit & plotly so we can run
headlessly.
"""
import sys, types, tempfile, pathlib

# --- Stub heavy UI deps so we can import pitching_lab ---
class _StubFigure:
    def __init__(self, *a, **kw):
        self._shapes = []
        self._traces = []
        self._annotations = []
        self._layout = {}
    def add_shape(self, **kw):  self._shapes.append(kw)
    def add_trace(self, t):     self._traces.append(t)
    def add_annotation(self, **kw): self._annotations.append(kw)
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

for name, mod in [
    ("streamlit", types.ModuleType("streamlit")),
    ("plotly", plotly),
    ("plotly.express", px),
    ("plotly.graph_objects", go),
    ("streamlit.components", types.ModuleType("streamlit.components")),
    ("streamlit.components.v1", types.ModuleType("streamlit.components.v1")),
]:
    sys.modules[name] = mod

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import pitching_lab as pl
import pandas as pd

# Isolated test DB
pl.DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "test_hitting.db"

print("=" * 70)
print("HITTING LAB — HISTORY + HEAT MAP END-TO-END TEST")
print("=" * 70)

# --- 1. Create a hitter ---
print("\n[1] Add hitter to roster")
pl.init_db()
hid = pl.add_athlete("Tyler Brooks", "Right", "Baseball", "2027")
print(f"   Athlete id = {hid}")

# --- 2. Generate a hitting session ---
print("\n[2] Generate Tyler's first session")
df1 = pl.generate_hitting_session("Tyler Brooks", hand="Right", sport="Baseball")
print(f"   {len(df1)} pitches faced ({(df1['Swing_Type']=='swing').sum()} swings)")
assert "Swing_Outcome" in df1.columns

# --- 3. Save with session_kind='hitting' ---
print("\n[3] Persist as session_kind='hitting'")
sid1 = pl.save_session(hid, df1, session_type="sample", session_kind="hitting")
print(f"   Saved session id = {sid1}")

# --- 4. Save a second session (simulate "next day") ---
print("\n[4] Save a second (different) session for the same hitter")
df2 = pl.generate_hitting_session("Tyler Brooks Day 2", hand="Right", sport="Baseball")
# pretend day 2 happened later by overriding the timestamps slightly
df2["Timestamp"] = pd.to_datetime(df2["Timestamp"]) + pd.Timedelta(days=2)
sid2 = pl.save_session(hid, df2, session_type="sample", session_kind="hitting")
print(f"   Saved session id = {sid2}")

# --- 5. list_sessions filtered by kind ---
print("\n[5] List sessions filtered by kind")
hitting_sessions = pl.list_sessions(hid, session_kind="hitting")
pitching_sessions = pl.list_sessions(hid, session_kind="pitching")
print(f"   hitting: {len(hitting_sessions)}, pitching: {len(pitching_sessions)}")
assert len(hitting_sessions) == 2, "Should see both hitting sessions"
assert len(pitching_sessions) == 0, "No pitching sessions yet"

# --- 6. load_hitting_history aggregates them ---
print("\n[6] load_hitting_history aggregates")
hist = pl.load_hitting_history(hid, lookback=20)
print(f"   Combined rows: {len(hist)} (expected {len(df1) + len(df2)})")
assert len(hist) == len(df1) + len(df2)
assert "Swing_Outcome" in hist.columns

# --- 7. Pitching sessions should NOT show up in hitting history ---
print("\n[7] Mixed-kind isolation check")
pitch_df = pl.generate_demo_session("Tyler Brooks", sport="Baseball")
pl.save_session(hid, pitch_df, session_type="sample", session_kind="pitching")
hist2 = pl.load_hitting_history(hid, lookback=20)
assert len(hist2) == len(df1) + len(df2), "Pitching session leaked into hitting history!"
print(f"   ✓ Pitching session ignored — hitting history still {len(hist2)} rows")

# --- 8. _quality_color sanity ---
print("\n[8] _quality_color edge cases")
# strong barrel (+2.0)
red = pl._quality_color(2.0)
# strong whiff (-2.0)
blue = pl._quality_color(-2.0)
# neutral
neutral = pl._quality_color(0.0)
none_color = pl._quality_color(None)
print(f"   +2.0 → {red}")
print(f"   -2.0 → {blue}")
print(f"    0.0 → {neutral}")
print(f"   None → {none_color}")
assert "rgba" in red and "rgba" in blue and "rgba" in neutral

# --- 9. Build the new heat map figure ---
print("\n[9] Build heat-map figure with history")
fig = pl._build_hit_quality_zone_heatmap_figure(df1, history_df=hist)
print(f"   Shapes drawn: {len(fig._shapes)}")
print(f"   Traces drawn: {len(fig._traces)}")
print(f"   Annotations: {len(fig._annotations)}")
# 25 grid cells + strike-zone box + 2 vertical + 2 horizontal grid lines + plate = 30 shapes minimum
assert len(fig._shapes) >= 25, f"Expected ≥25 shapes (5x5 grid), got {len(fig._shapes)}"
# Should have at least one trace per outcome present in today's data
assert len(fig._traces) > 0, "Heat map should have at least one outcome trace"

# --- 10. Build heat map with NO history (single session fallback) ---
print("\n[10] Heat map with no history")
fig2 = pl._build_hit_quality_zone_heatmap_figure(df1, history_df=pd.DataFrame())
assert len(fig2._shapes) >= 25
print(f"   ✓ Renders with empty history: {len(fig2._shapes)} shapes")

# --- 11. Spray chart still renders ---
print("\n[11] Spray chart figure still builds")
spray = pl._build_spray_chart_figure(df1, sport="Baseball", hand="Right")
print(f"   Spray shapes: {len(spray._shapes)}, traces: {len(spray._traces)}")
assert len(spray._traces) > 0

# --- 12. Swing detail panel function exists (we can't render Streamlit) ---
print("\n[12] _render_swing_detail_panel exists")
assert callable(pl._render_swing_detail_panel)
print("   ✓ Callable")

# --- 13. Mechanics critique builds and returns the expected shape ---
print("\n[13] analyze_hitting_mechanics")
critique = pl.analyze_hitting_mechanics(df1, sport="Baseball")
print(f"   Strengths: {[s['label'] for s in critique['strengths']]}")
print(f"   Weaknesses: {[w['label'] for w in critique['weaknesses']]}")
assert "strengths" in critique and "weaknesses" in critique
for s in critique["strengths"]:
    assert {"label", "detail", "gain", "tag"} <= set(s.keys())
for w in critique["weaknesses"]:
    assert {"label", "detail", "gain", "fix"} <= set(w.keys())

# --- 14. Hitting drill recommender ---
print("\n[14] recommend_hitting_drills")
hplan = pl.recommend_hitting_drills(df1, sport="Baseball", athlete_level="HS-Varsity")
print(f"   Today: {[d['label'] for d in hplan['today']]}")
print(f"   Week:  {[d['label'] for d in hplan['week']]}")
assert hplan["today"], "Should always have at least the default cooldown"
for d in hplan["today"] + hplan["week"]:
    assert {"label", "drill", "protocol", "why", "category", "trigger"} <= set(d.keys())

# --- 15. Softball hitter still gets a coherent plan ---
print("\n[15] Softball hitter plan")
df_sb = pl.generate_hitting_session("Lily Softball", hand="Right", sport="Softball")
crit_sb = pl.analyze_hitting_mechanics(df_sb, sport="Softball")
plan_sb = pl.recommend_hitting_drills(df_sb, sport="Softball")
print(f"   Softball strengths: {[s['label'] for s in crit_sb['strengths']]}")
print(f"   Softball today: {[d['label'] for d in plan_sb['today']]}")
assert plan_sb["today"], "Softball plan should still produce a cooldown"

# --- 16. Post-Swing Report PDF generates valid bytes ---
print("\n[16] generate_post_swing_pdf produces real PDF")
pdf_bytes = pl.generate_post_swing_pdf(df1, athlete_name="Tyler Brooks",
                                          athlete_hand="Right", athlete_class="2027",
                                          sport="Baseball", athlete_level="HS-Varsity")
assert pdf_bytes[:4] == b"%PDF", "Output must start with %PDF magic bytes"
# With 4 embedded charts (spray, outcomes bar, EV/LA quadrant, zone heat map)
# the PDF should be ~90-150kb. Under 60kb means multiple charts silently failed.
assert len(pdf_bytes) > 60_000, f"PDF suspiciously small ({len(pdf_bytes):,} bytes) — chart embeds likely failed"
print(f"   Generated {len(pdf_bytes):,} bytes ✓ ({pdf_bytes[:4]!r})")
out = pathlib.Path(__file__).parent / "sample_post_swing.pdf"
out.write_bytes(pdf_bytes)
print(f"   Saved: {out}")

# --- 17. Softball Post-Swing PDF also works ---
print("\n[17] Softball Post-Swing PDF")
sb_pdf = pl.generate_post_swing_pdf(df_sb, athlete_name="Lily Softball",
                                      athlete_hand="Right", athlete_class="2026",
                                      sport="Softball", athlete_level="HS-Varsity")
assert sb_pdf[:4] == b"%PDF"
print(f"   Softball PDF: {len(sb_pdf):,} bytes ✓")
(pathlib.Path(__file__).parent / "sample_post_swing_softball.pdf").write_bytes(sb_pdf)

# --- 18. Expanded drill library — multiple drills per issue ---
print("\n[18] Drill library has multiple drills per issue")
print(f"   Total drill entries: {len(pl.HITTING_DRILL_LIBRARY)}")
print(f"   Issue → drill count:")
for issue, keys in pl.HITTING_ISSUE_TO_DRILLS.items():
    print(f"     {issue:<20s} {len(keys)} drills")
    # Every key in the issue map must exist in the library
    for k in keys:
        assert k in pl.HITTING_DRILL_LIBRARY, f"Issue {issue} references missing drill {k}"
    # Every drill in an issue must carry the correct issue tag
    for k in keys:
        assert pl.HITTING_DRILL_LIBRARY[k]["issue"] == issue, \
            f"Drill {k} is in issue '{issue}' but tagged as '{pl.HITTING_DRILL_LIBRARY[k]['issue']}'"
# Each issue should have at least 3 drills (the whole point of this exercise)
for issue, keys in pl.HITTING_ISSUE_TO_DRILLS.items():
    assert len(keys) >= 3, f"Issue {issue} has only {len(keys)} drills — need at least 3"

# --- 19. Recommender produces MULTIPLE drills when an issue fires ---
print("\n[19] Recommender pulls multi-drill packages")
# Force-fire a bunch of issues by building a synthetic df with bad metrics
import pandas as pd
def _bad_session():
    base = pl.generate_hitting_session("Force Fire", hand="Right", sport="Baseball").copy()
    # Crank the metrics into trigger territory
    base["Bat_Speed_mph"]                = 50.0   # below 60 — fires bat_speed
    base["Peak_Hip_Shoulder_Sep_deg"]    = 20.0   # below 32 — fires hip_separation
    base["Attack_Angle_deg"]             = -2.0   # below 2 — fires flat_swing
    base["On_Plane_Eff_pct"]             = 40.0   # below 60 — fires off_plane
    base["Time_to_Contact_sec"]          = 0.21   # above 0.18 — fires slow_ttc
    return base
bad_df = _bad_session()
bad_plan = pl.recommend_hitting_drills(bad_df, sport="Baseball", athlete_level="HS-Varsity")
print(f"   Today drills: {len(bad_plan['today'])}")
print(f"   Week drills:  {len(bad_plan['week'])}")
print(f"   Categories pulled: {sorted({d['category'] for d in bad_plan['week']})}")
# With 5 issues firing, we expect at minimum 5 distinct issues × 3 drills each = 15-ish
assert len(bad_plan["week"]) >= 12, \
    f"Expected ≥12 weekly drills with 5 issues firing, got {len(bad_plan['week'])}"
# Each fired issue should produce its expected drill count
for issue in ("bat_speed", "hip_separation", "flat_swing", "off_plane", "slow_ttc"):
    drills_for_issue = [d for d in bad_plan["week"]
                         if pl.HITTING_DRILL_LIBRARY.get(d["key"], {}).get("issue") == issue]
    assert len(drills_for_issue) >= 2, \
        f"Issue {issue} only produced {len(drills_for_issue)} drills"
    print(f"     {issue:<20s} {len(drills_for_issue)} drills queued")

# --- 20. Every drill in the library has a SPECIFIC video entry ---
print("\n[20] Every hitting drill has a specific YouTube link")
missing_video = []
search_url_count = 0
for key, d in pl.HITTING_DRILL_LIBRARY.items():
    v = pl.pick_video(key, severity="any", level="any")
    if v is None:
        missing_video.append(key)
    elif "results?search_query" in v["url"]:
        search_url_count += 1
print(f"   Drills checked: {len(pl.HITTING_DRILL_LIBRARY)}")
print(f"   Missing video: {len(missing_video)}")
print(f"   Search-URL fallbacks (should be 0): {search_url_count}")
assert not missing_video, f"Drills missing videos: {missing_video}"
assert search_url_count == 0, "Some drills still use search URLs instead of specific videos"

# --- 21. Hitting trend KPIs computable across multiple sessions ---
print("\n[21] Trend KPIs across multiple sessions")
# Generate 3 distinct sessions for Tyler, save all, then verify we can compute
# a real trend dataframe like the History tab does.
for seed in ["Tyler Day 3", "Tyler Day 4", "Tyler Day 5"]:
    extra = pl.generate_hitting_session(seed, hand="Right", sport="Baseball")
    pl.save_session(hid, extra, session_type="sample", session_kind="hitting")
all_h = pl.list_sessions(hid, limit=50, session_kind="hitting")
print(f"   Hitting sessions on file: {len(all_h)}")
assert len(all_h) >= 4, "Should have at least 4 hitting sessions for trend analysis"
trend_rows = []
for s in all_h:
    sdf = pl.load_session_df(s["id"])
    if len(sdf) == 0:
        continue
    k = pl.hitting_session_kpis(sdf)
    trend_rows.append({
        "session_id":     s["id"],
        "avg_bat_speed":  k["Avg Bat Speed"],
        "avg_exit_velo":  k["Avg Exit Velo"],
        "barrel_pct":     k["Barrel %"],
        "whiff_pct":      k["Whiff %"],
        "on_plane_pct":   k["On-Plane %"],
        "total_swings":   k["Total Swings"],
    })
import pandas as _pd
trend_df_t = _pd.DataFrame(trend_rows)
print(f"   Trend rows built: {len(trend_df_t)}")
print(f"   Bat speeds: {[round(x,1) if x else None for x in trend_df_t['avg_bat_speed']]}")
print(f"   Barrel %:   {list(trend_df_t['barrel_pct'])}")
assert len(trend_df_t) == len(all_h)
# Each session should produce real KPIs (not all None)
non_null = trend_df_t["avg_bat_speed"].notna().sum()
assert non_null >= len(trend_df_t) // 2, "Most sessions should have a bat speed KPI"

# --- 22. Sell-sheet PDF (now covers Pitching + Hitting) ---
print("\n[22] Sell-sheet PDF generates correctly")
sell = pl.generate_sell_sheet_pdf(contact_name="Kolby Donnell",
                                    contact_email="kolbydonnell@gmail.com")
assert sell[:4] == b"%PDF"
# Sell sheet is text+tables (no embedded images) — 4kb+ is fine
assert len(sell) > 4_000, f"Sell sheet too small ({len(sell)} bytes)"
out = pathlib.Path(__file__).parent / "sample_sell_sheet.pdf"
out.write_bytes(sell)
print(f"   Sell sheet: {len(sell):,} bytes ✓")
print(f"   Saved: {out}")
# Verify both products are referenced — decompress text streams and search
try:
    from pypdf import PdfReader
    text_all = ""
    for p in PdfReader(out).pages:
        text_all += p.extract_text() or ""
    assert "Hitting" in text_all and "Pitching" in text_all, \
        "Sell sheet should mention both Pitching AND Hitting"
    print("   Both 'Pitching' + 'Hitting' present in extracted text ✓")
except ImportError:
    print("   (pypdf not installed — skipping text content check)")

# --- 23. Compare-two-swings columns all present in df ---
print("\n[23] Swing-compare needs all expected columns present")
needed_cols = {"Swing_Num", "Pitch_Type_Faced", "Pitch_Velocity_mph",
               "Plate_X_ft", "Plate_Z_ft", "Bat_Speed_mph", "Attack_Angle_deg",
               "On_Plane_Eff_pct", "Time_to_Contact_sec",
               "Exit_Velocity_mph", "Launch_Angle_deg", "Distance_ft",
               "Peak_Hip_Shoulder_Sep_deg", "Stride_Length_in", "Lead_Knee_Flex_deg"}
missing = needed_cols - set(df1.columns)
assert not missing, f"Compare view needs missing columns: {missing}"
print(f"   All {len(needed_cols)} compare columns present ✓")

print("\n" + "=" * 70)
print("HITTING LAB — HISTORY + COMPARE + SELL SHEET — ALL CHECKS PASS")
print("=" * 70)
