"""Test the new Demo Mode synthetic data generator."""
import sys, types
from pathlib import Path
for m in ["streamlit", "plotly", "plotly.express", "plotly.graph_objects"]:
    sys.modules[m] = types.ModuleType(m)
sys.path.insert(0, str(Path(__file__).parent))

from pitching_lab import (
    generate_demo_session, session_kpis, detect_injury_flags,
    pitch_type_breakdown, build_action_plan,
)

print("=" * 70)
print("DEMO MODE TEST")
print("=" * 70)

# Test 1: generate session for a pitcher
df = generate_demo_session("Marcus Vance", hand="Right")
print(f"\n[1] Generated session for Marcus Vance — {len(df)} pitches")
print(f"    Columns: {len(df.columns)} canonical columns")
print(f"    Healed pitches: {int(df['Healed'].sum())} (expected: 2)")

# Test 2: same name → same data
df2 = generate_demo_session("Marcus Vance", hand="Right")
print(f"\n[2] Reproducibility check (same name should give same data):")
print(f"    Pitch 1 velocity match: {df.iloc[0]['Velocity_mph'] == df2.iloc[0]['Velocity_mph']}")

# Test 3: different name → different data
df3 = generate_demo_session("Jake Smith", hand="Right")
print(f"\n[3] Different pitcher → different data:")
print(f"    Marcus pitch 1 velo: {df.iloc[0]['Velocity_mph']}")
print(f"    Jake   pitch 1 velo: {df3.iloc[0]['Velocity_mph']}")

# Test 4: KPIs work on synthetic data
print(f"\n[4] KPIs on synthetic session:")
for k, v in session_kpis(df).items():
    print(f"    {k}: {v}")

# Test 5: Action plan on synthetic data
print(f"\n[5] Action plan on synthetic session:")
plan = build_action_plan(df)
for action in plan:
    print(f"    {action['priority']} — {action['title']}")

# Test 6: Pitch breakdown
print(f"\n[6] Pitch type breakdown:")
print(pitch_type_breakdown(df).to_string(index=False))

print("\n" + "=" * 70)
print("DEMO MODE TEST COMPLETE")
print("=" * 70)
