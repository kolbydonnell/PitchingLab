"""Test the new today/week drill recommender."""
import sys, types
from pathlib import Path
for m in ["streamlit", "plotly", "plotly.express", "plotly.graph_objects"]:
    sys.modules[m] = types.ModuleType(m)
sys.path.insert(0, str(Path(__file__).parent))

from pitching_lab import generate_demo_session, recommend_drills, _format_plan_text

print("=" * 72)
print("DRILL RECOMMENDER TEST — DEMO SESSION (Marcus Vance)")
print("=" * 72)

df = generate_demo_session("Marcus Vance")
plan = recommend_drills(df)

print(f"\n>>> TODAY plan: {len(plan['today'])} drill(s)")
for d in plan["today"]:
    print(f"   • [P{d['priority']}] [{d['category']}] {d['label']}")
    print(f"       Drill:    {d['drill']}")
    print(f"       Protocol: {d['protocol']}")
    print(f"       Trigger:  {d['trigger']}")
    print()

print(f">>> THIS WEEK plan: {len(plan['week'])} drill(s)")
for d in plan["week"]:
    print(f"   • [P{d['priority']}] [{d['category']}] {d['label']}")
    print(f"       Drill:    {d['drill']}")
    print(f"       Protocol: {d['protocol']}")
    print(f"       Trigger:  {d['trigger']}")
    print()

print("=" * 72)
print("PLAIN-TEXT EXPORT (what gets downloaded for SMS/email):")
print("=" * 72)
print(_format_plan_text("Marcus Vance", plan))

# Try a few different pitchers to see variety
print("\n" + "=" * 72)
print("VARIETY CHECK — different pitchers should get different plans:")
print("=" * 72)
for name in ["Jake Smith", "Sara Johnson", "Tyler Rodriguez"]:
    df2 = generate_demo_session(name)
    plan2 = recommend_drills(df2)
    print(f"\n{name}:")
    print(f"  Today:  {[d['label'] for d in plan2['today']]}")
    print(f"  Week:   {[d['label'] for d in plan2['week']]}")
