#!/usr/bin/env python3
"""Diamond Sports Lab — bullpen video validation harness.

Usage:
    python3 run_validation.py path/to/video.mp4 [--sport Baseball|Softball]
                                                  [--hand R|L]

Drives the same processing pipeline as Upload Video mode in the Streamlit
app, but in standalone-script form so we can rip through validation
videos without spinning up the UI.

Output:
    1. Stdout: a per-pitch table (velo, spin, break, location, etc.)
    2. <video_stem>_results.csv: same data as a CSV ready for
       side-by-side comparison with on-screen Pocket Radar / Rapsodo
       overlays.

Designed to run from the PitchingLab repo root so it can import
`pitching_lab` directly.
"""
from __future__ import annotations

import argparse
import os
import sys
import csv
from pathlib import Path

# ---- make pitching_lab importable when run from test_videos/ ----
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    import cv2
except Exception as e:
    print("ERROR: opencv-python-headless is required.", file=sys.stderr)
    print(f"  pip install opencv-python-headless --break-system-packages", file=sys.stderr)
    print(f"  ({e})", file=sys.stderr)
    sys.exit(1)


def _calibration_from_video(video_path: str) -> dict | None:
    """Sample frames from the video and try to auto-detect home plate.
    Returns a calibration dict the upload pipeline understands, or None
    if no plate is confidently found anywhere."""
    from pitching_lab import auto_detect_plate

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    plate = None
    # Try 10 evenly spaced samples
    for k in range(1, 11):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * k / 12))
        ok, frame = cap.read()
        if not ok:
            continue
        ok_enc, buf = cv2.imencode(".jpg", frame)
        if not ok_enc:
            continue
        cand = auto_detect_plate(buf.tobytes())
        if cand:
            plate = cand
            break
    cap.release()
    if not plate:
        return None
    cx, cy, w = plate
    return {
        "plate_center_x_px": int(cx),
        "plate_center_y_px": int(cy),
        "plate_width_px":    int(w),
    }


def run(video_path: str, sport: str, hand_right: bool) -> list[dict]:
    print(f"\n=== Diamond Sports Lab — Validation Run ===")
    print(f"Video:   {video_path}")
    print(f"Sport:   {sport}")
    print(f"Hand:    {'RHP' if hand_right else 'LHP'}")

    from pitching_lab import (
        auto_detect_camera_angle,
        process_uploaded_video,
        process_uploaded_video_behind_pitcher,
        classify_pitch,
        CAMERA_ANGLE_ACCURACY,
    )

    angle = auto_detect_camera_angle(video_path)
    print(f"\nAuto-detected camera angle: {angle.upper()}")
    info = CAMERA_ANGLE_ACCURACY.get(angle, {})
    if info:
        print(f"  Expected accuracy: velo {info['velo_err']}, break {info['break_err']}")
        print(f"  {info['notes']}")

    def _progress(frac):
        bar_w = 30
        filled = int(bar_w * frac)
        bar = "#" * filled + "-" * (bar_w - filled)
        sys.stdout.write(f"\r  [{bar}] {int(frac*100):>3}%")
        sys.stdout.flush()

    print("\nProcessing video...")
    pitches: list[dict] = []
    try:
        if angle == "behind_pitcher":
            pitches = process_uploaded_video_behind_pitcher(
                video_path,
                sport=sport,
                hand_is_right=hand_right,
                progress_cb=_progress,
            )
        else:
            calib = _calibration_from_video(video_path)
            if not calib:
                # Fall back to a centered guess in the lower-middle of frame
                cap = cv2.VideoCapture(video_path)
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1280)
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 720)
                cap.release()
                calib = {
                    "plate_center_x_px": w // 2,
                    "plate_center_y_px": int(h * 0.75),
                    "plate_width_px":    int(w * 0.07),
                }
                print("  (plate auto-detect failed — using centered fallback)")
            calib["sport"] = sport
            pitches = process_uploaded_video(
                video_path,
                calib,
                sport=sport,
                progress_cb=_progress,
            )
    except Exception as e:
        print(f"\n\n!! Processing failed: {e}")
        raise
    print()  # newline after progress bar

    if not pitches:
        print("\nNo pitches detected. Possible causes:")
        print("  - ball never crossed enough frames (clip too short / too tight)")
        print("  - lighting too dim for the adaptive brightness threshold")
        print("  - camera moved between shots, scrambling segmentation")
        return []

    # Auto-classify pitch type for each
    for p in pitches:
        try:
            ptype = classify_pitch(
                velocity_mph=p.get("velocity_mph"),
                useful_spin_rpm=p.get("useful_spin_rpm"),
                vert_break_in=p.get("vert_break_in"),
                horiz_break_in=p.get("horiz_break_in"),
                spin_efficiency_pct=p.get("spin_efficiency_pct"),
                tilt_clock=p.get("tilt_clock"),
                sport=sport,
                hand_is_right=hand_right,
            )
            if isinstance(ptype, tuple):
                p["pitch_type"] = ptype[0]
            else:
                p["pitch_type"] = ptype
        except Exception:
            p["pitch_type"] = None
    return pitches


def render_table(pitches: list[dict]) -> None:
    if not pitches:
        return
    headers = ["#", "Type", "Velo", "Spin", "V Brk", "H Brk", "Eff%",
                "Tilt", "Plate X", "Plate Z", "N"]
    rows = []
    for i, p in enumerate(pitches, 1):
        rows.append([
            str(i),
            (p.get("pitch_type") or "—")[:14],
            f"{p.get('velocity_mph', 0):.1f}" if p.get('velocity_mph') is not None else "—",
            f"{int(p['useful_spin_rpm'])}" if p.get('useful_spin_rpm') is not None else "—",
            f"{p.get('vert_break_in', 0):+.1f}" if p.get('vert_break_in') is not None else "—",
            f"{p.get('horiz_break_in', 0):+.1f}" if p.get('horiz_break_in') is not None else "—",
            f"{int(p['spin_efficiency_pct'])}" if p.get('spin_efficiency_pct') is not None else "—",
            f"{p.get('tilt_clock', '—')}" if p.get('tilt_clock') else "—",
            f"{p.get('plate_x_ft', 0):+.2f}" if p.get('plate_x_ft') is not None else "—",
            f"{p.get('plate_z_ft', 0):.2f}" if p.get('plate_z_ft') is not None else "—",
            str(p.get('n_samples', '—')),
        ])
    widths = [max(len(h), max((len(r[i]) for r in rows), default=0))
              for i, h in enumerate(headers)]

    def fmt(cells):
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

    print(f"\n=== {len(pitches)} pitches detected ===")
    print(fmt(headers))
    print(fmt(["-" * w for w in widths]))
    for r in rows:
        print(fmt(r))


def write_csv(pitches: list[dict], video_path: str) -> str:
    if not pitches:
        return ""
    stem = Path(video_path).stem
    out_path = Path(video_path).parent / f"{stem}_results.csv"
    fieldnames = [
        "Pitch_Num", "Pitch_Type", "Velocity_mph", "Total_Spin_rpm",
        "Useful_Spin_rpm", "Spin_Efficiency_Pct", "Tilt_Clock",
        "Vert_Break_in", "Horiz_Break_in",
        "Plate_X_ft", "Plate_Z_ft", "Flight_Time_sec", "N_Samples",
        # ground-truth columns for you to fill in by hand from the video
        "GT_Velo_mph", "GT_Total_Spin_rpm", "GT_Vert_Break_in",
        "GT_Horiz_Break_in", "Notes",
    ]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for i, p in enumerate(pitches, 1):
            w.writerow({
                "Pitch_Num":          i,
                "Pitch_Type":         p.get("pitch_type"),
                "Velocity_mph":       p.get("velocity_mph"),
                "Total_Spin_rpm":     p.get("total_spin_rpm")
                                       or p.get("useful_spin_rpm"),
                "Useful_Spin_rpm":    p.get("useful_spin_rpm"),
                "Spin_Efficiency_Pct": p.get("spin_efficiency_pct"),
                "Tilt_Clock":         p.get("tilt_clock"),
                "Vert_Break_in":      p.get("vert_break_in"),
                "Horiz_Break_in":     p.get("horiz_break_in"),
                "Plate_X_ft":         p.get("plate_x_ft"),
                "Plate_Z_ft":         p.get("plate_z_ft"),
                "Flight_Time_sec":    p.get("flight_time_sec"),
                "N_Samples":          p.get("n_samples"),
                "GT_Velo_mph":        "",
                "GT_Total_Spin_rpm":  "",
                "GT_Vert_Break_in":   "",
                "GT_Horiz_Break_in":  "",
                "Notes":              "",
            })
    return str(out_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                       formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", help="Path to the bullpen video file")
    parser.add_argument("--sport", default="Baseball",
                        choices=["Baseball", "Softball"])
    parser.add_argument("--hand", default="R", choices=["R", "L"],
                        help="Throwing hand of the pitcher (R or L)")
    args = parser.parse_args()

    video_path = os.path.abspath(args.video)
    if not os.path.exists(video_path):
        print(f"ERROR: video not found: {video_path}", file=sys.stderr)
        sys.exit(1)

    pitches = run(video_path, sport=args.sport,
                    hand_right=(args.hand == "R"))
    render_table(pitches)
    csv_path = write_csv(pitches, video_path)
    if csv_path:
        print(f"\nCSV written: {csv_path}")
        print("Fill in the GT_* columns from the on-screen radar/Rapsodo "
              "readings to build your accuracy report.")


if __name__ == "__main__":
    main()
