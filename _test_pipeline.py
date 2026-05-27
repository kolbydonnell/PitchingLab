"""End-to-end test of the data pipeline against the sample CSVs.

Stubs out streamlit and plotly so we can import the module without
the UI dependencies installed.
"""
import sys
import types
from pathlib import Path

# Stub out streamlit and plotly before importing pitching_lab
for mod_name in ["streamlit", "plotly", "plotly.express", "plotly.graph_objects"]:
    sys.modules[mod_name] = types.ModuleType(mod_name)

# Now safe to import the pure-data functions
sys.path.insert(0, str(Path(__file__).parent))
from pitching_lab import (
    parse_pitch_logic,
    parse_pulse,
    parse_proplayai_file,
    parse_proplayai_batch,
    align_pitches,
    detect_injury_flags,
    session_kpis,
    pitch_type_breakdown,
    build_action_plan,
    spin_clock_to_degrees,
)

SAMPLE_DIR = Path(__file__).parent / "sample_data"

def main():
    print("=" * 70)
    print("PITCHING LAB — PIPELINE TEST")
    print("=" * 70)

    # ---- Spin clock conversion sanity check ----
    print("\n[1] Spin clock conversion sanity")
    cases = [("12:00", 0), ("01:15", 37.5), ("03:00", 90), ("06:00", 180), ("09:15", 277.5)]
    for clock, expected in cases:
        got = spin_clock_to_degrees(clock)
        status = "✓" if abs(got - expected) < 0.01 else "✗"
        print(f"    {status} {clock} -> {got}° (expected {expected}°)")

    # ---- Parse Pitch Logic ----
    print("\n[2] Pitch Logic parser")
    with open(SAMPLE_DIR / "pitch_logic_sample.csv", "rb") as f:
        pl_df = parse_pitch_logic(f)
    print(f"    Rows parsed: {len(pl_df)}")
    print(f"    Columns: {list(pl_df.columns)[:6]}...")
    print(f"    First pitch: #{pl_df.iloc[0]['Pitch_Num']} {pl_df.iloc[0]['Pitch_Type']} @ {pl_df.iloc[0]['Velocity_mph']} mph")
    print(f"    Spin axis (deg) computed: {pl_df['Spin_Axis_Deg'].tolist()[:3]}...")

    # ---- Parse Pulse ----
    print("\n[3] Pulse parser")
    with open(SAMPLE_DIR / "pulse_sample.csv", "rb") as f:
        pulse_df = parse_pulse(f)
    print(f"    Rows parsed: {len(pulse_df)} (intentional: should be 9 because pitch 5 dropped)")

    # ---- Parse ProPlayAI batch ----
    print("\n[4] ProPlayAI batch parser")
    ppai_files = sorted(SAMPLE_DIR.glob("proplayai_pitch_*.csv"))
    print(f"    Files found on disk: {len(ppai_files)} (intentional: 9, missing pitch 8)")
    # Wrap with a fake .name attribute since the function expects it
    class FakeUpload:
        def __init__(self, path):
            self.path = path
            self.name = path.name
        def read(self):
            return self.path.read_bytes()
    fakes = [FakeUpload(p) for p in ppai_files]
    ppai_df = parse_proplayai_batch(fakes)
    print(f"    Rows after reduction: {len(ppai_df)}")
    print(f"    Pitch IDs: {ppai_df['Pitch_ID'].tolist()[:3]}...")
    print(f"    Sample release biomech: hip-shoulder sep at release = {ppai_df.iloc[0]['Release_Hip_Shoulder_Sep']}")

    # ---- Run aligner ----
    print("\n[5] Self-healing aligner")
    aligned = align_pitches(pl_df, pulse_df, ppai_df)
    print(f"    Canonical rows: {len(aligned)}")
    healed_count = aligned['Healed'].sum()
    print(f"    Healed pitches: {healed_count} (expected: 2 — pitch 5 missing Pulse, pitch 8 missing PPAI)")

    print("\n    Per-pitch alignment audit:")
    for _, r in aligned.iterrows():
        notes = r['Healed_Notes'] or "clean"
        conf = int(r['Alignment_Confidence'] * 100)
        print(f"    Pitch #{int(r['Pitch_Num']):2d} | {r['Pitch_Type']:<22s} | "
              f"Pulse={'✓' if r['Pulse_Present'] else '✗'} | "
              f"PPAI={'✓' if r['PPAI_Present'] else '✗'}  | "
              f"conf={conf}% | {notes}")

    # ---- KPIs ----
    print("\n[6] Session KPIs")
    kpis = session_kpis(aligned)
    for k, v in kpis.items():
        print(f"    {k:<20s}: {v}")

    # ---- Pitch type breakdown ----
    print("\n[7] Pitch type breakdown")
    bd = pitch_type_breakdown(aligned)
    print(bd.to_string(index=False))

    # ---- Injury flags ----
    print("\n[8] Injury flag detection")
    danger_count = 0
    warning_count = 0
    for _, r in aligned.iterrows():
        flags = detect_injury_flags(r)
        for f in flags:
            if f['severity'] == 'DANGER':
                danger_count += 1
            else:
                warning_count += 1
            print(f"    Pitch #{int(r['Pitch_Num'])}: {f['severity']} — {f['label']}")
    print(f"    Total: {danger_count} DANGER, {warning_count} WARNING")

    # ---- Action plan ----
    print("\n[9] Action plan synthesis")
    plan = build_action_plan(aligned)
    for action in plan:
        print(f"    {action['priority']} — {action['title']}")
        print(f"        Drill: {action['drill']}")
        print(f"        Why: {action['why']}")

    print("\n" + "=" * 70)
    print("PIPELINE TEST COMPLETE — all stages produced output without errors")
    print("=" * 70)


if __name__ == "__main__":
    main()
