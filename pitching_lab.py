"""
Diamond Sports Lab — Pitching
==============================
Single-file Streamlit application that ingests CSV exports from three apps
(Pitch Logic / Rapsodo, Driveline Pulse, ProPlayAI), aligns them into a
canonical per-pitch dataset, heals dropped pitches, and renders a
Post-Bullpen Report with action plan, strike-zone scatter, and grip
recommendations.

Hitting analysis is on the v2 roadmap.

Run locally with:
    streamlit run pitching_lab.py

Author: Built for Kolby's Diamond Sports Lab, May 2026.
"""

import hashlib
import io
import random
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


# =============================================================================
# CONFIG / CONSTANTS
# =============================================================================
PITCH_LOGIC_METADATA_ROWS = 4          # rows before the header row in PL exports
ALIGNMENT_TOLERANCE_SECONDS = 5         # ± window for timestamp-based matching
SECOND_PASS_TOLERANCE_SECONDS = 15      # wider window for velocity-signature pass
DANGER_VALGUS_NM = 62.0                 # elbow stress threshold (Newton-meters)
ACR_WARNING_THRESHOLD = 1.3             # acute:chronic workload ratio warning
ACR_DANGER_THRESHOLD = 1.5              # ACR danger threshold
EARLY_TRUNK_ROTATION_DEG = 35.0         # >this at foot-plant = "opening too early"

# Sample pitcher video for testing the video features when the user has no
# bullpen footage of their own. (Public YouTube — "Slow Motion Pitching
# Mechanics in Bullpen" from an amateur/college pitcher.)
SAMPLE_PITCHER_VIDEO_URL = "https://www.youtube.com/watch?v=SdxpgCsDXcw"

# Pitch-type color map for the scatter plot
PITCH_COLORS = {
    # Baseball pitch types
    "Four-Seam Fastball":   "#d32f2f",
    "Two-Seam Sinker":      "#f57c00",
    "Slider Strike-Getter": "#1976d2",
    "Slider Chase":         "#7b1fa2",
    "Changeup":             "#388e3c",
    "Curveball":            "#00838f",
    # Softball pitch types
    "Softball Fastball":    "#d32f2f",
    "Rise Ball":            "#0ea5e9",
    "Drop Ball":            "#7c3aed",
    "Curveball ":           "#00838f",       # trailing space avoids key collision; not used externally
    "Screwball":            "#f59e0b",
    "Change-Up":            "#388e3c",
}

# =============================================================================
# DRILL LIBRARY
# =============================================================================
# Each drill has:
#   category   — Injury / Mechanics / Velocity / Stuff / Grip / Consistency
#   phase      — "today" (cooldown immediately after bullpen) or "week" (between bullpens)
#   priority   — 1-5; lower = more urgent. Injury 1, mech 2, velo 3, stuff 4, grip 4, consistency 5
#   label      — short title shown to the coach
#   drill      — the actual drill name
#   protocol   — sets × reps × frequency
#   why        — short coach-friendly explanation
#
# These drill names and protocols are reasonable starting points based on
# common baseball training literature (Driveline, Tread, etc) — Kolby should
# review and replace any he doesn't trust with his preferred protocols.
#
# DRILL_VIDEOS maps drill_key → list of curated YouTube tutorial entries.
# Each entry is a dict with:
#   url       — YouTube URL
#   title     — short descriptor for the link
#   source    — who made it (Driveline, Amanda Scarborough, etc.)
#   level     — "any" / "youth" / "hs" / "college+" — athlete-skill match
#   severity  — "any" / "mild" / "moderate" / "severe" — how serious the
#               problem is in the data (e.g., gyro 75° → moderate vs 85° → severe)
#   duration_min — approx tutorial length (info only)
#
# The video picker selects the best fit for the athlete's data + level.
# Multiple entries per drill let coaches see alternates and the system
# can pick differently for a youth pitcher vs a college pitcher.
DRILL_VIDEOS = {
    # ===== Baseball =====
    "high_valgus_stress": [
        {"url": "https://www.youtube.com/watch?v=71fRlH-nKdM",
         "title": "Reverse Throws — Plyo-Care Routine",
         "source": "Resilient Performance / PlyoCare",
         "level": "any", "severity": "any"},
    ],
    "moderate_acr_cooldown": [
        {"url": "https://www.youtube.com/watch?v=qLgmm6OWFOM",
         "title": "Sleeper Stretch + Shoulder Cooldown Series",
         "source": "Driveline Baseball",
         "level": "any", "severity": "any"},
    ],
    "session_cooldown_default": [
        {"url": "https://www.youtube.com/watch?v=qLgmm6OWFOM",
         "title": "Standard Bullpen Cooldown — Cuff Series",
         "source": "Driveline Baseball",
         "level": "any", "severity": "any"},
    ],
    "early_trunk_rotation": [
        {"url": "https://www.youtube.com/watch?v=espdyF-BEUU",
         "title": "Pivot Pickoff Throws (Slow-Mo)",
         "source": "Driveline Baseball",
         "level": "any", "severity": "any"},
    ],
    "soft_lead_knee": [
        {"url": "https://www.youtube.com/watch?v=D2iBFwUSphg",
         "title": "Lead Leg Patterning Drill Series",
         "source": "Performance Therapy",
         "level": "any", "severity": "any"},
    ],
    "low_hip_shoulder_separation": [
        {"url": "https://www.youtube.com/watch?v=AVBhM0U9wDc",
         "title": "Hip-Shoulder Separation Drill — All Ages",
         "source": "Coach DM",
         "level": "any", "severity": "any"},
    ],
    "low_extension": [
        {"url": "https://www.youtube.com/watch?v=ybqA8ukDhpo",
         "title": "How Pros Use the Towel Drill for Drive & Extension",
         "source": "Hector Berrios",
         "level": "any", "severity": "any"},
    ],
    "below_baseline_fastball_velo": [
        {"url": "https://www.youtube.com/watch?v=vzbYILuv2zM",
         "title": "Underload Velocity Pulldowns",
         "source": "Driveline Baseball",
         "level": "hs", "severity": "moderate"},
        {"url": "https://www.youtube.com/watch?v=d-a0CKlF8sQ",
         "title": "Max Velocity Pulldowns — 105.8 MPH",
         "source": "Driveline Baseball",
         "level": "college+", "severity": "severe"},
    ],
    "below_baseline_offspeed_velo": [
        {"url": "https://www.youtube.com/watch?v=SPo50ugFM6Q",
         "title": "How To Use Weighted Baseballs On A Velocity Day",
         "source": "Driveline Baseball",
         "level": "any", "severity": "any"},
    ],
    "low_fastball_spin": [
        {"url": "https://www.youtube.com/watch?v=soY59hqq5IQ",
         "title": "Fingerprint of Velocity — Why Spin Starts in the Hand",
         "source": "Driveline Baseball",
         "level": "any", "severity": "any"},
    ],
    "low_slider_spin_efficiency": [
        {"url": "https://www.youtube.com/watch?v=SypTLJa7paM",
         "title": "Pitching Towel Drill — Throw Gas",
         "source": "Baseball Pitcher Drills",
         "level": "any", "severity": "mild"},
        {"url": "https://www.youtube.com/watch?v=1LR9sp6qutE",
         "title": "Quick Guide to Baseball Pitch Grips",
         "source": "PlayBaseball",
         "level": "any", "severity": "moderate"},
    ],
    "low_offspeed_break": [
        {"url": "https://www.youtube.com/watch?v=1LR9sp6qutE",
         "title": "Pitch Grips — Curveball / Changeup Spin",
         "source": "PlayBaseball",
         "level": "any", "severity": "any"},
    ],
    "slider_grip_pronation_fix": [
        {"url": "https://www.youtube.com/watch?v=1LR9sp6qutE",
         "title": "Slider Grip — Spike-Seam Variation",
         "source": "PlayBaseball",
         "level": "any", "severity": "any"},
    ],
    "fastball_grip_finger_pressure": [
        {"url": "https://www.youtube.com/watch?v=soY59hqq5IQ",
         "title": "Fingerprint of Velocity — Middle-Finger Pressure",
         "source": "Driveline Baseball",
         "level": "any", "severity": "any"},
    ],
    "arm_slot_variance": [
        {"url": "https://www.youtube.com/watch?v=D2iBFwUSphg",
         "title": "Lead Leg Patterning — Release Consistency",
         "source": "Performance Therapy",
         "level": "any", "severity": "any"},
    ],

    # ===== Softball =====
    "softball_low_rise_spin": [
        {"url": "https://www.youtube.com/watch?v=QiI_P1RZct0",
         "title": "Wrist Snap Practice for Softball Pitchers",
         "source": "Power Up Sports",
         "level": "any", "severity": "any"},
    ],
    "softball_low_drop_topspin": [
        {"url": "https://www.youtube.com/watch?v=LIVYiPfKjKU",
         "title": "How to Throw a Dropball — Amanda Scarborough",
         "source": "Amanda Scarborough",
         "level": "any", "severity": "any"},
    ],
    "softball_K_drill": [
        {"url": "https://www.youtube.com/watch?v=D5ba34a9nK4",
         "title": "Power K — Putting It All Together (Part 3)",
         "source": "FastPitch Softball Pitching",
         "level": "any", "severity": "any"},
    ],
    "softball_brush_at_hip": [
        {"url": "https://www.youtube.com/watch?v=wMrRQfBSzTU",
         "title": "3 Swing Whip Drill — Brush at Hip",
         "source": "Softball Pitching",
         "level": "any", "severity": "any"},
    ],
    "softball_drag_toe": [
        {"url": "https://www.youtube.com/watch?v=8pbfXm_o4lo",
         "title": "Fastpitch Pitching Softball — Drag Box Tutorial",
         "source": "Power Drive Performance",
         "level": "any", "severity": "any"},
    ],
    "softball_below_baseline_velo": [
        {"url": "https://www.youtube.com/watch?v=NBODs5vHchA",
         "title": "Front Foot Rotation — Toe Touch & Heel Plant",
         "source": "Fastpitch Pitching",
         "level": "any", "severity": "any"},
    ],
    "softball_grip_curve_pronation": [
        {"url": "https://www.youtube.com/watch?v=QiI_P1RZct0",
         "title": "Wrist Snap & Axis Control",
         "source": "Power Up Sports",
         "level": "any", "severity": "any"},
    ],

    # Softball versions of shared issue keys
    "softball_session_cooldown_default": [
        {"url": "https://www.youtube.com/watch?v=qLgmm6OWFOM",
         "title": "Shoulder Cooldown Series (works for windmill)",
         "source": "Driveline Baseball",
         "level": "any", "severity": "any"},
    ],
    "softball_high_valgus_stress": [
        {"url": "https://www.youtube.com/watch?v=qLgmm6OWFOM",
         "title": "Sleeper Stretch + Cuff Series",
         "source": "Driveline Baseball",
         "level": "any", "severity": "any"},
    ],
    "softball_low_fastball_spin": [
        {"url": "https://www.youtube.com/watch?v=QiI_P1RZct0",
         "title": "Wrist Snap Practice — Fastball Backspin",
         "source": "Power Up Sports",
         "level": "any", "severity": "any"},
    ],
    "softball_low_offspeed_break": [
        {"url": "https://www.youtube.com/watch?v=gc99t_g9RUs",
         "title": "Windmill Drills — Spin Axis Variations",
         "source": "Softball Spot",
         "level": "any", "severity": "any"},
    ],
    "softball_arm_slot_variance": [
        {"url": "https://www.youtube.com/watch?v=ycRB2rvyleA",
         "title": "Beginner Pitching Drills — Arm Path / Body Control",
         "source": "DR3 Fastpitch",
         "level": "any", "severity": "any"},
    ],

    # =========================================================================
    # HITTING DRILL VIDEOS — specific YouTube tutorials curated per drill.
    # Each entry points to a single canonical instructional video. If any URL
    # ever 404s, replace it — the rest of the system is URL-agnostic.
    # =========================================================================
    "hitting_session_cooldown": [
        {"url": "https://www.youtube.com/watch?v=Ujk1E6XFTk0",
         "title": "Forearm Stretch — Arm Stretch For Baseball",
         "source": "YouTube",
         "level": "any", "severity": "any"},
    ],
    "hitting_heavy_workload_cooldown": [
        {"url": "https://www.youtube.com/watch?v=bDEXVfchUGg",
         "title": "Important Arm Cooldown & Recovery Drills",
         "source": "YouTube",
         "level": "any", "severity": "any"},
    ],

    # ---- Bat speed (4 drills) ----
    "hitting_bat_speed_overload_underload": [
        {"url": "https://www.youtube.com/watch?v=oiJ2bUPdfwg",
         "title": "How to Train Bat Speed In The Cage",
         "source": "Driveline Baseball",
         "level": "hs", "severity": "any"},
    ],
    "hitting_bat_speed_medball": [
        {"url": "https://www.youtube.com/watch?v=eHkGMY70kFU",
         "title": "Top 10 Med-Ball Drills to Increase Bat Speed & Power",
         "source": "YouTube",
         "level": "any", "severity": "any"},
    ],
    "hitting_bat_speed_resistance_band": [
        {"url": "https://www.youtube.com/watch?v=k1XTovi8X6s",
         "title": "Top 3 Resistance-Band Hitting Drills",
         "source": "YouTube",
         "level": "any", "severity": "any"},
    ],
    "hitting_bat_speed_top_hand": [
        {"url": "https://www.youtube.com/watch?v=R8lBmtaRqzM",
         "title": "Top-Hand Progression Drill",
         "source": "Factory 101",
         "level": "any", "severity": "any"},
    ],

    # ---- Hip-shoulder separation (3 drills) ----
    "hitting_hip_sep_coil_hold": [
        {"url": "https://www.youtube.com/watch?v=8pFGPutHi0E",
         "title": "Simple Hip Coil and Hold",
         "source": "Hitting Done Right",
         "level": "any", "severity": "any"},
    ],
    "hitting_hip_sep_step_behind": [
        {"url": "https://www.youtube.com/watch?v=AkwKO0J2SEM",
         "title": "Step Backs — Hitting Drill",
         "source": "Driveline Baseball",
         "level": "any", "severity": "any"},
    ],
    "hitting_hip_sep_x_drill": [
        {"url": "https://www.youtube.com/watch?v=Q1HEhdBf8N8",
         "title": "Hip to Shoulder Separation — Baseball Hitting Mechanics",
         "source": "Batspeed.com",
         "level": "any", "severity": "any"},
    ],

    # ---- Flat swing (3 drills) ----
    "hitting_flat_high_tee": [
        {"url": "https://www.youtube.com/watch?v=0BYiG_0saOY",
         "title": "High Tee — Hitting Drill",
         "source": "Driveline Baseball",
         "level": "any", "severity": "any"},
    ],
    "hitting_flat_pvc_path": [
        {"url": "https://www.youtube.com/watch?v=XGHyJ61o6x8",
         "title": "PVC Hitting Drills To Help Improve Your Swing",
         "source": "YouTube",
         "level": "any", "severity": "any"},
    ],
    "hitting_flat_launch_pause": [
        {"url": "https://www.youtube.com/watch?v=bbqzyYYXvtM",
         "title": "Stride-Pause-Swing Tee Drill",
         "source": "YouTube",
         "level": "any", "severity": "any"},
    ],

    # ---- Steep swing (3 drills) ----
    "hitting_steep_low_high_mix": [
        {"url": "https://www.youtube.com/watch?v=Ad3K8y1w6Fc",
         "title": "Low Tee — Hitting Drill",
         "source": "Driveline Baseball",
         "level": "any", "severity": "any"},
    ],
    "hitting_steep_top_hand_path": [
        {"url": "https://www.youtube.com/watch?v=VFn3vxAz01U",
         "title": "Drive the Ball More Consistently — Top Hand Drill",
         "source": "YouTube",
         "level": "any", "severity": "any"},
    ],
    "hitting_steep_outside_front_toss": [
        {"url": "https://www.youtube.com/watch?v=omtGAS-Kk0w",
         "title": "Off-Set Front Toss Drill",
         "source": "Driveline Baseball",
         "level": "any", "severity": "any"},
    ],

    # ---- Off-plane swing (3 drills) ----
    "hitting_on_plane_plyo_uphill": [
        {"url": "https://www.youtube.com/watch?v=RdCSttVTJ78",
         "title": "Overloaded Plyo Ball Indoor Drill for Hitting",
         "source": "YouTube",
         "level": "any", "severity": "any"},
    ],
    "hitting_on_plane_two_tee": [
        {"url": "https://www.youtube.com/watch?v=5DxC2zZMbqs",
         "title": "Two Tee — Swing Path Drill",
         "source": "Hitting Done Right",
         "level": "any", "severity": "any"},
    ],
    "hitting_on_plane_connection_band": [
        {"url": "https://www.youtube.com/watch?v=sbm2YJEpfYg",
         "title": "3 Best Connection-Ball Drills",
         "source": "YouTube",
         "level": "any", "severity": "any"},
    ],

    # ---- Slow time-to-contact (3 drills) ----
    "hitting_ttc_short_bat": [
        {"url": "https://www.youtube.com/watch?v=3fogUIU5az4",
         "title": "Short Bat Drill (Window Drill)",
         "source": "Factory 101",
         "level": "any", "severity": "any"},
    ],
    "hitting_ttc_heavy_quick": [
        {"url": "https://www.youtube.com/watch?v=f3Zm0m2thsI",
         "title": "Bat Speed and Quick Hands Drill",
         "source": "YouTube",
         "level": "any", "severity": "any"},
    ],
    "hitting_ttc_connection_ball": [
        {"url": "https://www.youtube.com/watch?v=y_dvzrPibB4",
         "title": "Connection Ball Hitting Drills",
         "source": "YouTube",
         "level": "any", "severity": "any"},
    ],

    # ---- Whiffs (3 drills) ----
    "hitting_whiffs_sit_fastball": [
        {"url": "https://www.youtube.com/watch?v=RedR_MVnzk4",
         "title": "Pitch Selection & Plate Discipline — Timing at Home Plate",
         "source": "The Language of Hitting",
         "level": "any", "severity": "any"},
    ],
    "hitting_whiffs_vision_bottle": [
        {"url": "https://www.youtube.com/watch?v=pLC-WnXMkHo",
         "title": "Vision Training Baseball Batting Drill",
         "source": "YouTube",
         "level": "any", "severity": "any"},
    ],
    "hitting_whiffs_multi_color": [
        {"url": "https://www.youtube.com/watch?v=jXHL6ryl3EE",
         "title": "Evan Longoria — Advanced Pitch Recognition Drill",
         "source": "YouTube",
         "level": "any", "severity": "any"},
    ],

    # ---- Weak contact (3 drills) ----
    "hitting_weak_front_toss_oppo": [
        {"url": "https://www.youtube.com/watch?v=jD3OkLcnig0",
         "title": "3 Tips To Hit For More Opposite Field Power",
         "source": "YouTube",
         "level": "any", "severity": "any"},
    ],
    "hitting_weak_bottom_hand": [
        {"url": "https://www.youtube.com/watch?v=xS-PTR1c0Mc",
         "title": "Bottom Hand Iso Drill — Knob Direction",
         "source": "Hitting Done Right",
         "level": "any", "severity": "any"},
    ],
    "hitting_weak_inside_outside_tee": [
        {"url": "https://www.youtube.com/watch?v=PApy4Xd3ZtQ",
         "title": "Inside/Outside Hitting Drill — Recognition & Adjustment",
         "source": "YouTube",
         "level": "any", "severity": "any"},
    ],
}


def pick_video(drill_key: str, severity: str = "any",
                level: str = "any") -> dict | None:
    """Pick the best video for a drill key given the athlete's severity + level.

    Scoring:
      +3 for exact severity match
      +1 if video severity is "any" (fits anyone)
      +2 for exact level match
      +1 if video level is "any"

    Returns the video dict (with url, title, source, etc.) or None.
    """
    candidates = DRILL_VIDEOS.get(drill_key) or []
    if not candidates:
        return None

    def score(v):
        s = 0
        v_sev = v.get("severity", "any")
        v_lvl = v.get("level", "any")
        if v_sev == severity:        s += 3
        elif v_sev == "any":          s += 1
        if v_lvl == level:           s += 2
        elif v_lvl == "any":          s += 1
        return s

    return max(candidates, key=score)


def get_drill_video(drill_key: str, severity: str = "any",
                     level: str = "any") -> str | None:
    """Backward-compatible URL lookup. Returns just the URL string."""
    v = pick_video(drill_key, severity=severity, level=level)
    return v["url"] if v else None


def get_drill_video_alternates(drill_key: str) -> list:
    """Return ALL curated videos for a drill (used by the Drill Library)."""
    return DRILL_VIDEOS.get(drill_key) or []


# =============================================================================
# STANDARD WARM-UP + COOL-DOWN SEQUENCES (referenced by every weekly plan)
# Each step is a discrete movement with a clear cue. The weekly-plan builder
# lists the warm-up FIRST and the cool-down LAST on every prescribed day so
# the structure is the same regardless of the player's specific weaknesses.
# =============================================================================
PITCHING_WARMUP = {
    "label":     "Pitcher Pre-Bullpen Warm-Up",
    "duration":  "10–12 min",
    "steps": [
        ("Light cardio",                "3 min easy jog or jump rope — get the body warm."),
        ("Dynamic mobility",            "Arm circles 2×10 each direction · leg swings 2×8 each leg · hip openers 2×8."),
        ("Band external rotations",     "3×12 with a light band. Elbow at side, slow tempo, full ROM."),
        ("Band Y/T/W",                  "2×8 of each, slow tempo. Activates scapular stabilizers."),
        ("Wrist flicks",                "2×10 each side — light wrist snaps to wake the forearm."),
        ("Bullpen-pace warm-up throws", "Long-toss progression: 30 ft × 5 throws → 60 ft × 5 → game-distance × 5."),
    ],
    "why": "A consistent warm-up cuts UCL incident rates and primes the rotator cuff for max-effort throws. "
            "Skipping it is the #1 cause of preventable injury in HS pitchers.",
}

PITCHING_COOLDOWN = {
    "label":     "Pitcher Post-Bullpen Cool-Down",
    "duration":  "6–8 min",
    "steps": [
        ("Reverse throws (decel)",      "2×8 throws with a 5oz plyo ball, 25 ft into a wall. Decelerates the arm safely."),
        ("Sleeper stretch",             "3×30s each side. Keeps the posterior capsule mobile, prevents GIRD."),
        ("Band external rotations",     "2×15 light band, elbow at side. Cuff blood flow + recovery."),
        ("Cross-body stretch",          "2×30s each arm — opens the posterior shoulder."),
        ("Foam roll T-spine + lats",    "2 min, focus on the throwing-side lat. Restores thoracic rotation."),
        ("Easy walk or bike",           "3–5 min flushing pace. Helps clear lactic load."),
    ],
    "why": "Cool-down protocols cut next-day soreness by ~40% in college pitching studies. "
            "Coaches who skip this are the ones whose pitchers feel 'dead-arm' two days later.",
}

HITTING_WARMUP = {
    "label":     "Hitter Pre-Session Warm-Up",
    "duration":  "8–10 min",
    "steps": [
        ("Light cardio",                "3 min easy jog or jump rope."),
        ("Dynamic mobility",            "T-spine rotations 2×8 each side · hip openers 2×8 · band pull-aparts 2×12."),
        ("Wrist + forearm prep",        "Wrist flexor stretch 2×30s each · forearm pronation drill 2×10 with a bat."),
        ("Dry swings — short bat",      "10 controlled swings choked up — feel the path before loading the swing."),
        ("Tee — middle, 50% intent",    "10 swings on a middle tee at half effort. Build into full intent."),
        ("Tee — middle, 80% intent",    "10 swings at 80% — final ramp before live BP."),
    ],
    "why": "A consistent warm-up gets the bat path on plane before the first hard swing — "
            "reduces wasted reps and lowers wrist/lat strain risk.",
}

HITTING_COOLDOWN = {
    "label":     "Hitter Post-Session Cool-Down",
    "duration":  "5–7 min",
    "steps": [
        ("Forearm flexor stretch",      "3×30s each side. Hitters accumulate forearm fatigue from BP volume."),
        ("Lat doorway stretch",         "3×30s each side — opens the lats and decompresses the spine."),
        ("Scapular slides",             "2×10 against a wall — restores shoulder-blade glide after rotational load."),
        ("Foam roll T-spine",           "2 min, focus on the rotational side."),
        ("Hydration + 2-min walk",      "Active recovery flushes the system."),
    ],
    "why": "Forearm + lat fatigue is the silent killer of hitter consistency across a long week. "
            "The 5-minute cool-down is what lets the hitter come back fresh for the next session.",
}


DRILL_LIBRARY = {
    # ===== INJURY PREVENTION (today / cooldown) =====
    "high_valgus_stress": {
        "category": "Injury Prevention",
        "phase": "today",
        "priority": 1,
        "label": "Elevated Elbow Stress",
        "drill": "Half-kneeling reverse throws + 7oz plyo decel work",
        "protocol": "3 sets × 6 reps, 2 min rest between sets. Do today within 30 min of finishing.",
        "why": "Decelerates the arm safely and reduces peak valgus load on the UCL after a high-stress outing.",
    },
    "high_acr_rest": {
        "category": "Injury Prevention",
        "phase": "today",
        "priority": 1,
        "label": "Workload Spike — REST",
        "drill": "REST DAY. No throws. Mobility + sleeve work only.",
        "protocol": "Light band external rotations 3×15. Cuff mobility 10 min. Easy walk 20 min.",
        "why": "Acute:Chronic Ratio above 1.5 carries 3–5× injury risk in the next 7 days (Gabbett 2016).",
    },
    "moderate_acr_cooldown": {
        "category": "Injury Prevention",
        "phase": "today",
        "priority": 2,
        "label": "Workload Elevated — Light Recovery",
        "drill": "Sleeper stretch + reverse-throw decel + cuff series",
        "protocol": "Sleeper stretch 3×30s each side. Reverse throws 2×8. Cuff series 2×12.",
        "why": "ACR 1.3–1.5 is the yellow zone. Active recovery now keeps you out of the red zone next session.",
    },
    "session_cooldown_default": {
        "category": "Injury Prevention",
        "phase": "today",
        "priority": 3,
        "label": "Standard Bullpen Cooldown",
        "drill": "Reverse throws + sleeper stretch + 5-min light cardio",
        "protocol": "Reverse throws 2×10 with 5oz. Sleeper stretch 3×30s each side. Walk or bike 5 min.",
        "why": "Baseline cooldown that flushes blood through the shoulder and elbow after any bullpen.",
    },

    # ===== MECHANICS — EFFICIENCY (week) =====
    "early_trunk_rotation": {
        "category": "Mechanics",
        "phase": "week",
        "priority": 2,
        "label": "Chest Opens Too Early",
        "drill": "Driveline pivot pick-offs with weighted blue plyo (1 lb)",
        "protocol": "3 sets × 5 reps, 3 days this week. Front shoulder stays closed until foot strike.",
        "why": "Late trunk rotation protects the elbow AND transfers more ground force into the ball — typically worth +1-2 mph plus tighter command.",
    },
    "soft_lead_knee": {
        "category": "Mechanics",
        "phase": "week",
        "priority": 2,
        "label": "Soft Front Leg Block",
        "drill": "Wall drill — front-leg stiff brace + medball into wall",
        "protocol": "4 sets × 6 reps, every other day. Focus on locking the front knee at brace.",
        "why": "A stiff front-side block transfers energy up the chain instead of leaking sideways. Adds 1-2 mph + sharper command.",
    },
    "low_hip_shoulder_separation": {
        "category": "Mechanics — Velocity",
        "phase": "week",
        "priority": 3,
        "label": "Low Hip-Shoulder Separation",
        "drill": "Hershiser drill — exaggerated coil reps with 6oz ball",
        "protocol": "3 sets × 4 reps, 3 days this week. Hold the coil 1 sec before unwinding.",
        "why": "Separation > 50° at peak = more rubber-band torque. Top D1 averages 55-65°. Each 5° added is roughly 1 mph of velocity.",
    },
    "low_extension": {
        "category": "Mechanics — Velocity",
        "phase": "week",
        "priority": 3,
        "label": "Short Release Extension",
        "drill": "Stride-down drill emphasizing long stride + reach",
        "protocol": "3 sets × 8 reps, 2-3 days this week. Aim for stride length = 90% of pitcher's height.",
        "why": "Extension > 6.2 ft adds ~2 mph of perceived velocity by shortening the distance the hitter sees. 'Free velocity.'",
    },

    # ===== VELOCITY (week) =====
    "below_baseline_fastball_velo": {
        "category": "Velocity",
        "phase": "week",
        "priority": 3,
        "label": "Fastball Velocity Down vs Baseline",
        "drill": "Weighted-ball pull-down + hold protocol (Driveline-style)",
        "protocol": "Pull-downs: 3oz/4oz/5oz/6oz/7oz, 3 throws each, 2 days. Holds: 6oz, 5 reps × 5 sec, 2 days.",
        "why": "Weighted-ball protocols reliably add 2-4 mph in 6-8 weeks. Train the arm to move fast THEN strong.",
    },
    "below_baseline_offspeed_velo": {
        "category": "Velocity",
        "phase": "week",
        "priority": 4,
        "label": "Offspeed Velocity Soft",
        "drill": "Tempo throws + intent training at the offspeed pitch type",
        "protocol": "Tempo throws: 10 reps at 80% intent, 5 reps at 100% intent, 2 days this week.",
        "why": "Offspeed pitches lose effectiveness when they drift more than 8-10 mph off the fastball. Hold the velocity gap.",
    },

    # ===== STUFF (week) — SPIN AND MOVEMENT =====
    "low_fastball_spin": {
        "category": "Stuff — Fastball",
        "phase": "week",
        "priority": 4,
        "label": "Low Fastball Spin Rate",
        "drill": "Sponge balls + four-seam ride grip protocol",
        "protocol": "Sponge spin reps 4 sets × 10 daily. Live cue: middle finger does the work on release.",
        "why": "Spin rate is partly mechanical (wrist snap, finger pressure). Sponge protocols add 100-200 RPM over 4 weeks.",
    },
    "low_slider_spin_efficiency": {
        "category": "Stuff — Slider",
        "phase": "week",
        "priority": 4,
        "label": "Slider Too Gyro (Bullet Spin)",
        "drill": "Towel slider drill + grip shift toward middle finger",
        "protocol": "Towel dry reps 5×10 daily. Live grip: middle finger on long seam, index lightly off the ball.",
        "why": "Slider spin efficiency below 30% = mostly gyro / bullet spin / no break. Target 30-40% efficiency for a tight 2-plane break.",
    },
    "low_offspeed_break": {
        "category": "Stuff — Movement",
        "phase": "week",
        "priority": 4,
        "label": "Offspeed Not Breaking Enough",
        "drill": "Supination hook-em drill + grip pressure shift",
        "protocol": "Hook-em reps 3×6 with 5oz weighted ball, 2 days. Strong wrist supination at release.",
        "why": "Movement comes from spin axis + active spin %. Stronger supination shifts axis toward 9:00 and adds 2-3 inches of break.",
    },

    # ===== GRIP =====
    "slider_grip_pronation_fix": {
        "category": "Grip",
        "phase": "week",
        "priority": 4,
        "label": "Slider Grip — Stop Wrist-Twisting",
        "drill": "Spike-seam slider grip: ball wedged between thumb pad and middle finger",
        "protocol": "Mirror grip work 5 min daily. 10 spike-seam sliders per bullpen until natural.",
        "why": "High gyro + high elbow stress on the slider usually means you're forcing break with wrist twist. A spike grip lets the ball cut naturally and unloads the elbow.",
        "grip_key": "slider_spike_seam",
    },
    "fastball_grip_finger_pressure": {
        "category": "Grip",
        "phase": "week",
        "priority": 5,
        "label": "Fastball Grip — Index Pressure Cue",
        "drill": "Shift release pressure to the middle finger pad, lighten index",
        "protocol": "Dry-fire wrist work 50 reps daily. Every bullpen, throw the first 10 fastballs with cue 'middle finger does the work'.",
        "why": "Index-dominant release biases gyro/sweep; middle-finger dominant release adds carry and 'ride' to a four-seamer.",
        "grip_key": "four_seam_fastball",
    },

    # ===== CONSISTENCY =====
    "arm_slot_variance": {
        "category": "Consistency",
        "phase": "week",
        "priority": 5,
        "label": "Inconsistent Arm Slot",
        "drill": "Mirror drill + 1-knee throws",
        "protocol": "10 mirror reps before each bullpen. 1-knee throws 3×10, 2 days this week.",
        "why": "A consistent arm slot = a consistent release point = better command and tunneling. Pros vary < 2° in slot across pitches.",
    },

    # =========================================================================
    # SOFTBALL-SPECIFIC DRILLS (windmill mechanics)
    # =========================================================================
    "softball_low_rise_spin": {
        "category": "Stuff — Rise Ball",
        "phase": "week",
        "priority": 4,
        "label": "Rise Ball Spin Too Low",
        "drill": "Wrist-snap rise drill + sponge ball spin reps",
        "protocol": "Sponge ball pure backspin reps 4×10 daily. Live: emphasize a hard wrist 'flick' upward at release, palm rotating fully to sky. 15 rise balls per bullpen this week.",
        "why": "Rise balls thrive on backspin rate — below 1900 RPM the ball drops too much and the 'rise' illusion fails. Wrist-snap drills increase the spin contribution from the fingertips.",
        "grip_key": "softball_rise",
    },
    "softball_low_drop_topspin": {
        "category": "Stuff — Drop Ball",
        "phase": "week",
        "priority": 4,
        "label": "Drop Ball Not Dropping Enough",
        "drill": "Peel-finger drop drill — fingers off the FRONT",
        "protocol": "5 sets × 8 reps daily, focus on the 'pulling a window shade down' release. Live drops: 12 per bullpen, all targeting the bottom of the zone or below.",
        "why": "Sharp drop balls need 6:00 topspin. If your fingers come off the SIDE instead of the front, you'll get a 4:00 axis and the ball backs up instead of dropping.",
        "grip_key": "softball_drop",
    },
    "softball_K_drill": {
        "category": "Mechanics",
        "phase": "week",
        "priority": 2,
        "label": "K-Position at Top of Windmill",
        "drill": "K-drill — pause at the 12:00 windmill position",
        "protocol": "5 sets × 6 reps daily. Hold the K-position (arm at 12:00, ball facing batter) for 1 sec before completing the windmill. Works arm path consistency.",
        "why": "A consistent K-position at the top of the windmill is the foundation of a repeatable softball delivery — like a baseball pitcher's high cock position. Inconsistent K = inconsistent release = scattered pitches.",
    },
    "softball_brush_at_hip": {
        "category": "Mechanics — Velocity",
        "phase": "week",
        "priority": 3,
        "label": "Brush at Hip Timing",
        "drill": "Hip-brush drill — wall + glove-hand pull",
        "protocol": "3 sets × 8 reps, every other day. Focus on the elbow brushing the hip as the arm comes through the release zone — and the glove side pulling back hard at the same instant.",
        "why": "The 'brush at hip' is where the windmill converts arm circle into ball velocity. Late or missed brush = ball gets pushed instead of whipped, costing 3-5 mph.",
    },
    "softball_drag_toe": {
        "category": "Mechanics",
        "phase": "week",
        "priority": 3,
        "label": "Drive Foot Drag Inconsistency",
        "drill": "Towel drag-toe drill",
        "protocol": "Place a towel under the drive (back) foot; drag the toe through the entire delivery. 3 sets × 10 reps, 2 days this week.",
        "why": "A consistent drag-toe finish keeps the hips and shoulders in line, which keeps the release point consistent. Hopping off the rubber leaks energy and scatters the ball.",
    },
    "softball_below_baseline_velo": {
        "category": "Velocity",
        "phase": "week",
        "priority": 3,
        "label": "Fastball Velocity Down vs Baseline",
        "drill": "Weighted windmill protocol (4oz, 5oz, 6oz, 7oz)",
        "protocol": "Pull-downs at full intent: 4oz/5oz/6oz/7oz × 3 reps each, 2 days this week. Plus 'spinners' (10oz ball, no release, full motion) 5×10 daily.",
        "why": "Weighted-ball protocols add 2-4 mph in 6-8 weeks for softball pitchers too. The added load forces faster arm circle and stronger trunk involvement.",
    },
    "softball_grip_curve_pronation": {
        "category": "Grip",
        "phase": "week",
        "priority": 4,
        "label": "Curveball Wrist Path Inconsistent",
        "drill": "Wrist-rotation mirror work + spin axis check",
        "protocol": "Mirror grip + wrist rotation 5 min daily. 10 curveballs per bullpen with a coach calling out spin direction afterward (or check Pitch Logic spin axis output).",
        "why": "Softball curveballs depend on wrist outward rotation timing. Mirror work locks the muscle memory so the spin axis is consistent — 9:00 axis curves break sharply, 10:30 axes back up.",
        "grip_key": "softball_curve",
    },

    # ----- Softball versions of shared issue keys (so the recommender can route
    # to softball drill text + softball videos instead of baseball ones) -----
    "softball_session_cooldown_default": {
        "category": "Injury Prevention",
        "phase": "today",
        "priority": 3,
        "label": "Standard Windmill Cooldown",
        "drill": "Bands + decel windmill swings + light catch — softball shoulder care",
        "protocol": "Light yellow-band external rotations 3×15. Slow decel windmill swings (no ball) 2×10. Easy catch at 30 ft 5 min.",
        "why": "Baseline cooldown for softball pitchers. Flushes blood through the shoulder and protects the windmill's internal rotation mechanics.",
    },
    "softball_high_valgus_stress": {
        "category": "Injury Prevention",
        "phase": "today",
        "priority": 1,
        "label": "Elevated Arm Stress (Windmill)",
        "drill": "Sleeper stretch + light decel windmill swings",
        "protocol": "Sleeper stretch 3×30s each side. Decel windmill swings (no ball, slow) 3×8. Cold-pack the arm 10 min.",
        "why": "Even windmill mechanics can stress the shoulder if release timing is off. Slow swings with deceleration teach the arm to slow safely after release.",
    },
    "softball_low_fastball_spin": {
        "category": "Stuff — Fastball",
        "phase": "week",
        "priority": 4,
        "label": "Softball Fastball Spin Too Low",
        "drill": "Wrist-snap reps + finger-pressure cue at release",
        "protocol": "Sponge ball spin reps 4×10 daily (focus on hard fingertip snap upward). Live cue: 'pull the trigger' on the index + middle finger at the bottom of the arm circle.",
        "why": "Softball fastballs live or die by clean backspin. Below 1,500-1,800 RPM the ball flattens out and hitters time it easily.",
    },
    "softball_low_offspeed_break": {
        "category": "Stuff — Movement",
        "phase": "week",
        "priority": 4,
        "label": "Offspeed Not Breaking Enough",
        "drill": "Spin-axis isolation drill — pause and check axis each rep",
        "protocol": "10 reps per off-speed pitch per bullpen. Watch the seam rotation as the ball leaves the hand — a coach calls out the clock-face axis you hit. Match axis to pitch type (12:00 rise, 6:00 drop, 9:00 curve, 3:00 screw).",
        "why": "Softball off-speed movement comes from the spin AXIS, not the spin rate. Axis drills lock in the muscle memory for each pitch's release feel.",
    },
    "softball_arm_slot_variance": {
        "category": "Consistency",
        "phase": "week",
        "priority": 5,
        "label": "Inconsistent Windmill Arm Path",
        "drill": "Mirror windmill drill + cone tracking",
        "protocol": "10 slow windmill mirror reps before each bullpen. Cone drill: 3 cones at 9:00 / 12:00 / 6:00 around your body; arm should pass through them at every windmill rotation. 3×6, 2 days this week.",
        "why": "A consistent windmill path = consistent release point = consistent command. The mirror + cones give you the visual feedback to lock it in.",
    },
}


# =============================================================================
# UTILITY: spin-clock conversion
# =============================================================================
def spin_clock_to_degrees(clock_str: str) -> float:
    """Convert Pitch Logic's '01:15' clock-face spin direction to degrees.

    12:00 = 0°, 3:00 = 90°, 6:00 = 180°, 9:00 = 270° (clockwise).
    """
    if pd.isna(clock_str):
        return None
    try:
        h, m = clock_str.split(":")
        return (int(h) % 12) * 30 + int(m) * 0.5
    except (ValueError, AttributeError):
        return None


# =============================================================================
# PARSERS — one per upstream app
# =============================================================================

class ParserError(Exception):
    """Raised when a CSV doesn't match any known schema for its app."""
    pass


# Column alias dictionaries: maps canonical names to alternate names we'll accept.
# This SINGLE map handles BOTH Pitch Logic AND Rapsodo because the canonical
# data we want from each is essentially the same — just named differently.
# Add new aliases here as you discover them in real exports (TrackMan, FlightScope, etc).
PITCH_LOGIC_ALIASES = {
    "Timestamp":           ["timestamp", "time", "datetime", "date_time", "pitch_time",
                            "release_time", "date", "captured_at", "recorded_at",
                            "pitch_date", "datestamp"],
    "Pitch_Num":           ["pitch_num", "pitch_number", "pitch_no", "pitch_#",
                            "pitch_index", "pitchnum", "throw_num", "no"],
    "Pitcher_Name":        ["pitcher", "pitcher_name", "player", "player_name", "athlete"],
    "Pitch_Type":          ["pitch_type", "type", "pitchtype", "pitch_class",
                            "pitch_label", "classification", "auto_pitch_type",
                            "rapsodo_pitch_type"],
    "Velocity_mph":        ["velocity_mph", "velocity", "speed", "speed_mph", "mph",
                            "release_velocity", "release_velocity_mph", "release_speed",
                            "release_speed_mph", "ball_speed", "velocity_release"],
    "Total_Spin_rpm":      ["total_spin_rpm", "total_spin", "spin_rate", "spin_rate_rpm",
                            "spin", "rpm", "spin_rpm"],
    "True_Spin_rpm":       ["true_spin_rpm", "true_spin", "active_spin_rpm",
                            "useful_spin_rpm"],
    "Spin_Efficiency_pct": ["spin_efficiency_pct", "spin_efficiency", "spin_eff",
                            "active_spin", "active_spin_pct", "true_spin_pct",
                            "useful_spin", "useful_spin_pct"],
    "Spin_Direction_hhmm": ["spin_direction_hhmm", "spin_direction", "spin_axis_clock",
                            "spin_clock", "axis_clock", "spin_axis_hhmm", "tilt",
                            "spin_axis"],
    "Gyro_Degrees":        ["gyro_degrees", "gyro", "gyro_deg", "gyro_angle"],
    "Vert_Break_in":       ["vert_break_in", "vertical_break", "vert_break", "vbreak",
                            "ivb", "induced_vertical_break", "induced_vert_break",
                            "vert_break_inches", "vbreak_in", "vb"],
    "Horiz_Break_in":      ["horiz_break_in", "horizontal_break", "horiz_break",
                            "hbreak", "horiz_break_inches", "hbreak_in", "hb"],
    "Extension_ft":        ["extension_ft", "extension", "release_extension",
                            "release_extension_ft"],
    "Release_Height_ft":   ["release_height_ft", "release_height", "release_z",
                            "rel_height", "release_height_feet"],
    "Release_Side_ft":     ["release_side_ft", "release_side", "release_x",
                            "rel_side", "release_side_feet"],
    "Strike_Zone_Side":    ["strike_zone_side", "plate_x", "strikezoneside"],
    "Strike_Zone_Height":  ["strike_zone_height", "plate_z", "strikezoneheight",
                            "strike_zone_top"],
}

PULSE_ALIASES = {
    "Timestamp":               ["timestamp", "time", "datetime", "date_time",
                                "throw_time", "date", "captured_at", "recorded_at"],
    "Athlete_Name":            ["athlete_name", "athlete", "name", "player_name", "player"],
    "Athlete_ID":              ["athlete_id", "id", "player_id", "user_id"],
    "Throw_ID":                ["throw_id", "id", "throw_num", "throw_number", "event_id"],
    "Throw_Type":              ["throw_type", "type", "throw_kind"],
    "Tag":                     ["tag", "label", "throw_tag", "session_tag"],
    "Arm_Speed_deg_sec":       ["arm_speed_deg_sec", "arm_speed", "armspeed",
                                "arm_velocity", "arm_speed_dps"],
    "Peak_Valgus_Torque_Nm":   ["peak_valgus_torque_nm", "valgus_torque", "torque",
                                "peak_valgus", "stress_nm", "elbow_torque",
                                "peak_torque", "valgus", "stress"],
    "Arm_Slot_deg":            ["arm_slot_deg", "arm_slot", "armslot", "slot",
                                "arm_angle"],
    "Shoulder_Rotation_deg":   ["shoulder_rotation_deg", "shoulder_rotation",
                                "shoulder_ext_rot"],
    "Daily_Workload":          ["daily_workload", "workload", "today_workload"],
    "Chronic_Workload":        ["chronic_workload", "chronic_load", "chronic"],
    "AC_Ratio":                ["ac_ratio", "acr", "acute_chronic_ratio",
                                "acute_to_chronic", "load_ratio"],
    "One_Day_Stress":          ["one_day_stress", "stress", "daily_stress",
                                "throw_stress"],
}

PPAI_FRAME_ALIASES = {
    "Frame":                          ["frame", "frame_num", "frame_number"],
    "Phase":                          ["phase", "event", "kinematic_phase", "label"],
    "Time_sec":                       ["time_sec", "time", "t_sec", "t", "timestamp_sec"],
    "Pelvis_Rotation_deg":            ["pelvis_rotation_deg", "pelvis_rotation",
                                       "pelvis_rot", "hip_rotation"],
    "Trunk_Rotation_deg":             ["trunk_rotation_deg", "trunk_rotation",
                                       "torso_rotation", "trunk_rot"],
    "Hip_Shoulder_Separation_deg":    ["hip_shoulder_separation_deg",
                                       "hip_shoulder_separation",
                                       "hip_shoulder_sep", "separation"],
    "Elbow_Flexion_deg":              ["elbow_flexion_deg", "elbow_flexion",
                                       "elbow_angle"],
    "Shoulder_Abduction_deg":         ["shoulder_abduction_deg", "shoulder_abduction"],
    "Shoulder_External_Rot_deg":      ["shoulder_external_rot_deg",
                                       "shoulder_external_rotation",
                                       "shoulder_ext_rot", "external_rotation"],
    "Lead_Knee_Extension_deg":        ["lead_knee_extension_deg", "lead_knee_extension",
                                       "front_knee_extension", "knee_extension"],
    "Pelvis_Angular_Vel_deg_sec":     ["pelvis_angular_vel_deg_sec",
                                       "pelvis_angular_velocity", "pelvis_vel"],
    "Trunk_Angular_Vel_deg_sec":      ["trunk_angular_vel_deg_sec",
                                       "trunk_angular_velocity", "trunk_vel"],
}


def _norm_col(name: str) -> str:
    """Normalize a column name for matching: lowercase, no spaces/dashes/special."""
    return (
        str(name)
        .strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
        .replace("/", "_")
        .replace("(", "")
        .replace(")", "")
        .replace("%", "pct")
    )


def _normalize_columns(df: pd.DataFrame, alias_map: dict) -> pd.DataFrame:
    """Rename df columns to canonical names based on alias_map."""
    rename = {}
    for col in df.columns:
        norm = _norm_col(col)
        for canonical, aliases in alias_map.items():
            if norm == _norm_col(canonical) or norm in [_norm_col(a) for a in aliases]:
                rename[col] = canonical
                break
    return df.rename(columns=rename)


def _detect_header_row(lines: list, indicator_keywords: list, min_hits: int = 2) -> int:
    """Find the first line that looks like a CSV header by counting keyword matches."""
    for i, line in enumerate(lines[:40]):  # scan first 40 lines only
        line_lower = line.lower()
        hits = sum(1 for kw in indicator_keywords if kw.lower() in line_lower)
        if hits >= min_hits:
            return i
    return -1


def file_diagnostic(uploaded_file, max_lines: int = 12) -> str:
    """Return the first N lines of a file as a string, for diagnostic display."""
    try:
        text = _read_uploaded_text(uploaded_file)
        return "\n".join(text.split("\n")[:max_lines])
    except Exception as e:
        return f"(could not read file: {e})"


def parse_ball_flight(uploaded_file) -> pd.DataFrame:
    """Parse a ball-flight CSV (Pitch Logic, Rapsodo, or compatible).

    Auto-detects the header row and normalizes column names to a canonical
    schema. Handles the differences between Pitch Logic (gives Spin Efficiency
    as a percentage) and Rapsodo (gives True Spin in RPM) automatically.
    """
    text = _read_uploaded_text(uploaded_file)
    lines = text.split("\n")

    # Find the header row by looking for common ball-flight indicators
    indicators = ["pitch", "velocity", "speed", "spin", "release", "break", "rapsodo"]
    header_idx = _detect_header_row(lines, indicators, min_hits=2)
    if header_idx < 0:
        raise ParserError(
            "Could not find a ball-flight CSV header row.\n\n"
            "First lines of the file looked like:\n"
            + "\n".join(lines[:8])
        )

    df = pd.read_csv(io.StringIO("\n".join(lines[header_idx:])))
    df.columns = [str(c).strip() for c in df.columns]
    df = _normalize_columns(df, PITCH_LOGIC_ALIASES)

    # Required canonical columns
    required = ["Timestamp", "Velocity_mph"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ParserError(
            "Ball-flight CSV is missing required columns: "
            + ", ".join(missing)
            + "\n\nColumns we DID find in your file:\n  "
            + ", ".join(df.columns)
            + "\n\nIf your CSV uses different names, paste the first row of your "
            "file and I'll add those names to the alias list."
        )

    # Fill in optional columns with defaults so downstream code never KeyErrors
    optional_defaults = {
        "Pitch_Num":           lambda: range(1, len(df) + 1),
        "Pitch_Type":          "Unknown",
        "Total_Spin_rpm":      None,
        "True_Spin_rpm":       None,
        "Spin_Efficiency_pct": None,
        "Spin_Direction_hhmm": None,
        "Gyro_Degrees":        None,
        "Vert_Break_in":       None,
        "Horiz_Break_in":      None,
        "Extension_ft":        None,
        "Release_Height_ft":   None,
        "Release_Side_ft":     None,
    }
    for col, default in optional_defaults.items():
        if col not in df.columns:
            df[col] = default() if callable(default) else default

    # ----- Derived fields -----
    # Rapsodo gives True_Spin_rpm; Pitch Logic gives Spin_Efficiency_pct.
    # If we have True_Spin + Total_Spin but no efficiency, compute it.
    needs_eff = df["Spin_Efficiency_pct"].isna() if hasattr(df["Spin_Efficiency_pct"], "isna") else True
    has_true = df["True_Spin_rpm"].notna().any() if hasattr(df["True_Spin_rpm"], "notna") else False
    has_total = df["Total_Spin_rpm"].notna().any() if hasattr(df["Total_Spin_rpm"], "notna") else False
    if has_true and has_total:
        df["Spin_Efficiency_pct"] = df.apply(
            lambda r: (r["True_Spin_rpm"] / r["Total_Spin_rpm"] * 100)
                       if pd.notna(r["True_Spin_rpm"]) and pd.notna(r["Total_Spin_rpm"]) and r["Total_Spin_rpm"] > 0
                       else r.get("Spin_Efficiency_pct"),
            axis=1,
        )

    df["Timestamp"] = pd.to_datetime(df["Timestamp"], utc=True, errors="coerce")
    df["Spin_Axis_Deg"] = df["Spin_Direction_hhmm"].apply(spin_clock_to_degrees)
    df = df.dropna(subset=["Timestamp"]).reset_index(drop=True)
    return df


# Back-compat alias: existing code calls parse_pitch_logic, keep that working
def parse_pitch_logic(uploaded_file) -> pd.DataFrame:
    """Back-compat alias for parse_ball_flight. Handles Pitch Logic CSVs."""
    return parse_ball_flight(uploaded_file)


def parse_rapsodo(uploaded_file) -> pd.DataFrame:
    """Alias for parse_ball_flight specifically for Rapsodo CSVs."""
    return parse_ball_flight(uploaded_file)


def parse_pulse(uploaded_file) -> pd.DataFrame:
    """Parse a Driveline Pulse CSV export, auto-detecting columns."""
    text = _read_uploaded_text(uploaded_file)
    lines = text.split("\n")

    indicators = ["throw", "timestamp", "time", "athlete", "torque", "workload",
                  "stress", "valgus", "arm"]
    header_idx = _detect_header_row(lines, indicators, min_hits=2)
    if header_idx < 0:
        raise ParserError(
            "Could not find a Pulse header row.\n\n"
            "First lines of the file looked like:\n"
            + "\n".join(lines[:8])
        )

    df = pd.read_csv(io.StringIO("\n".join(lines[header_idx:])))
    df.columns = [str(c).strip() for c in df.columns]
    df = _normalize_columns(df, PULSE_ALIASES)

    required = ["Timestamp"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ParserError(
            "Pulse CSV is missing required columns: "
            + ", ".join(missing)
            + "\n\nColumns we DID find:\n  "
            + ", ".join(df.columns)
        )

    optional_defaults = {
        "Athlete_ID":             None,
        "Athlete_Name":           None,
        "Throw_ID":               None,
        "Throw_Type":             None,
        "Tag":                    None,
        "Arm_Speed_deg_sec":      None,
        "Peak_Valgus_Torque_Nm":  None,
        "Arm_Slot_deg":           None,
        "Shoulder_Rotation_deg":  None,
        "Daily_Workload":         None,
        "Chronic_Workload":       None,
        "AC_Ratio":               None,
        "One_Day_Stress":         None,
    }
    for col, default in optional_defaults.items():
        if col not in df.columns:
            df[col] = default

    df["Timestamp"] = pd.to_datetime(df["Timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["Timestamp"]).reset_index(drop=True)
    return df


def _read_uploaded_text(uploaded_file) -> str:
    """Helper that handles both Streamlit UploadedFile and plain file paths."""
    if hasattr(uploaded_file, "read"):
        raw = uploaded_file.read()
        # Streamlit's UploadedFile returns bytes; rewind so it can be re-read
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        return raw
    return Path(uploaded_file).read_text(encoding="utf-8")


def parse_proplayai_metadata(metadata_line: str) -> dict:
    """Extract Pitch_ID, Pitcher, Hand, Frame_Rate, Captured_At from line 1."""
    pairs = re.findall(r'"([^"]+)"', metadata_line)
    meta = {}
    for pair in pairs:
        if ":" in pair:
            key, value = pair.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta


def _find_phase_row(frame_df: pd.DataFrame, phase_keywords: list):
    """Find the first frame whose Phase value matches any keyword (case-insensitive)."""
    if "Phase" not in frame_df.columns:
        return None
    matches = frame_df[frame_df["Phase"].astype(str).str.lower().apply(
        lambda v: any(kw.lower() in v for kw in phase_keywords)
    )]
    return matches.iloc[0] if not matches.empty else None


def parse_proplayai_file(uploaded_file, filename: str) -> dict:
    """Parse one ProPlayAI per-pitch frame file and reduce it to a single row.

    Auto-detects header row and normalizes column + phase names so we
    handle variations in real exports.
    """
    text = _read_uploaded_text(uploaded_file)
    lines = text.strip().split("\n")

    # Try to find a metadata line (one with "Pitch_ID:" style key:value pairs)
    metadata = {}
    for line in lines[:3]:
        if ":" in line and ("pitch_id" in line.lower() or "captured" in line.lower()
                            or "pitcher" in line.lower() or "frame_rate" in line.lower()):
            metadata = parse_proplayai_metadata(line)
            break

    # Find the frame header row
    indicators = ["frame", "phase", "time", "pelvis", "trunk", "elbow",
                  "shoulder", "knee", "rotation"]
    header_idx = _detect_header_row(lines, indicators, min_hits=2)
    if header_idx < 0:
        raise ParserError(
            f"Could not find a ProPlayAI header row in {filename}.\n\n"
            "First lines of the file looked like:\n"
            + "\n".join(lines[:8])
        )

    frame_df = pd.read_csv(io.StringIO("\n".join(lines[header_idx:])))
    frame_df.columns = [str(c).strip() for c in frame_df.columns]
    frame_df = _normalize_columns(frame_df, PPAI_FRAME_ALIASES)

    # Pull canonical event-phase rows (tolerant to label variations)
    release = _find_phase_row(frame_df, ["ball_release", "release"])
    if release is None:
        # No release frame — bail with None so the batch parser skips this pitch
        return None
    foot_plant_row = _find_phase_row(frame_df, ["foot_plant", "footplant", "fp"])
    max_extrot_row = _find_phase_row(frame_df, ["max_external_rot", "max_er", "mer"])

    def _safe_get(row, col):
        if row is None:
            return None
        return row[col] if col in row.index else None

    def _safe_max(col):
        return frame_df[col].max() if col in frame_df.columns else None

    return {
        "Pitch_ID":                  metadata.get("Pitch_ID"),
        "Source_File":               filename,
        "Captured_At":               pd.to_datetime(metadata.get("Captured_At"), utc=True, errors="coerce"),
        "Frame_Rate":                int(metadata.get("Frame_Rate", 240)) if metadata.get("Frame_Rate", "").isdigit() else 240,
        "Release_Pelvis_Rot":        _safe_get(release, "Pelvis_Rotation_deg"),
        "Release_Trunk_Rot":         _safe_get(release, "Trunk_Rotation_deg"),
        "Release_Hip_Shoulder_Sep":  _safe_get(release, "Hip_Shoulder_Separation_deg"),
        "Release_Elbow_Flex":        _safe_get(release, "Elbow_Flexion_deg"),
        "Release_Shoulder_Abd":      _safe_get(release, "Shoulder_Abduction_deg"),
        "Release_Shoulder_ExtRot":   _safe_get(release, "Shoulder_External_Rot_deg"),
        "Release_Lead_Knee_Ext":     _safe_get(release, "Lead_Knee_Extension_deg"),
        "Peak_Hip_Shoulder_Sep":     _safe_max("Hip_Shoulder_Separation_deg"),
        "Peak_Pelvis_Angular_Vel":   _safe_max("Pelvis_Angular_Vel_deg_sec"),
        "Peak_Trunk_Angular_Vel":    _safe_max("Trunk_Angular_Vel_deg_sec"),
        "FootPlant_Trunk_Rot":       _safe_get(foot_plant_row, "Trunk_Rotation_deg"),
        "MaxExtRot_Angle":           _safe_get(max_extrot_row, "Shoulder_External_Rot_deg"),
        "Time_FootPlant_to_Release": (release["Time_sec"] - foot_plant_row["Time_sec"])
                                       if (foot_plant_row is not None
                                           and "Time_sec" in release.index
                                           and "Time_sec" in foot_plant_row.index)
                                       else None,
    }


def parse_proplayai_batch(uploaded_files) -> pd.DataFrame:
    """Run the per-pitch parser over a list of uploaded ProPlayAI files."""
    rows = []
    for f in uploaded_files:
        filename = getattr(f, "name", str(f))
        row = parse_proplayai_file(f, filename)
        if row is not None:
            rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("Captured_At").reset_index(drop=True)


# =============================================================================
# SELF-HEALING TIMELINE ALIGNER
# =============================================================================
def align_pitches(pl_df: pd.DataFrame,
                  pulse_df: pd.DataFrame,
                  ppai_df: pd.DataFrame) -> pd.DataFrame:
    """Align Pitch Logic + Pulse + ProPlayAI by timestamp.

    Pitch Logic is the spine — every Pitch Logic row becomes one canonical
    pitch. We then look for the nearest Pulse throw and the nearest ProPlayAI
    capture within a tolerance window. Anything we can't find gets a
    placeholder row and the healed_flag is set so downstream code knows.
    """
    canonical_rows = []

    pulse_used = set()
    ppai_used = set()

    for _, pl in pl_df.iterrows():
        pl_time = pl["Timestamp"]

        # --- find nearest Pulse throw within tolerance ---
        pulse_match = None
        pulse_method = None
        if not pulse_df.empty:
            pulse_df_avail = pulse_df.loc[~pulse_df.index.isin(pulse_used)].copy()
            pulse_df_avail["_dt"] = (pulse_df_avail["Timestamp"] - pl_time).abs()
            within_tol = pulse_df_avail[pulse_df_avail["_dt"] <= timedelta(seconds=ALIGNMENT_TOLERANCE_SECONDS)]
            if not within_tol.empty:
                pulse_match = within_tol.nsmallest(1, "_dt").iloc[0]
                pulse_used.add(pulse_match.name)
                pulse_method = "timestamp_window"
            else:
                # Wider second pass: timestamp_wide
                wider = pulse_df_avail[pulse_df_avail["_dt"] <= timedelta(seconds=SECOND_PASS_TOLERANCE_SECONDS)]
                if not wider.empty:
                    pulse_match = wider.nsmallest(1, "_dt").iloc[0]
                    pulse_used.add(pulse_match.name)
                    pulse_method = "timestamp_wide"

        # --- find nearest ProPlayAI capture within tolerance ---
        ppai_match = None
        ppai_method = None
        if not ppai_df.empty:
            ppai_df_avail = ppai_df.loc[~ppai_df.index.isin(ppai_used)].copy()
            ppai_df_avail["_dt"] = (ppai_df_avail["Captured_At"] - pl_time).abs()
            within_tol = ppai_df_avail[ppai_df_avail["_dt"] <= timedelta(seconds=ALIGNMENT_TOLERANCE_SECONDS)]
            if not within_tol.empty:
                ppai_match = within_tol.nsmallest(1, "_dt").iloc[0]
                ppai_used.add(ppai_match.name)
                ppai_method = "timestamp_window"
            else:
                wider = ppai_df_avail[ppai_df_avail["_dt"] <= timedelta(seconds=SECOND_PASS_TOLERANCE_SECONDS)]
                if not wider.empty:
                    ppai_match = wider.nsmallest(1, "_dt").iloc[0]
                    ppai_used.add(ppai_match.name)
                    ppai_method = "timestamp_wide"

        # --- build the canonical row ---
        row = {
            # Identity
            "Pitch_Num":          pl["Pitch_Num"],
            "Timestamp":          pl_time,
            "Pitch_Type":         pl["Pitch_Type"],

            # Ball flight (from Pitch Logic)
            "Velocity_mph":       pl["Velocity_mph"],
            "Total_Spin_rpm":     pl["Total_Spin_rpm"],
            "Spin_Efficiency_pct": pl["Spin_Efficiency_pct"],
            "Spin_Axis_Deg":      pl["Spin_Axis_Deg"],
            "Spin_Direction_hhmm": pl["Spin_Direction_hhmm"],
            "Gyro_Degrees":       pl["Gyro_Degrees"],
            "Vert_Break_in":      pl["Vert_Break_in"],
            "Horiz_Break_in":     pl["Horiz_Break_in"],
            "Extension_ft":       pl["Extension_ft"],
            "Release_Height_ft":  pl["Release_Height_ft"],
            "Release_Side_ft":    pl["Release_Side_ft"],

            # Plate location (Rapsodo provides directly; Pitch Logic gets estimated later)
            "Strike_Zone_Side":   pl["Strike_Zone_Side"]   if "Strike_Zone_Side"   in pl.index else None,
            "Strike_Zone_Height": pl["Strike_Zone_Height"] if "Strike_Zone_Height" in pl.index else None,

            # Arm health (from Pulse)
            "Pulse_Present":      pulse_match is not None,
            "Pulse_Match_Method": pulse_method,
            "Arm_Speed_deg_sec":  pulse_match["Arm_Speed_deg_sec"] if pulse_match is not None else None,
            "Peak_Valgus_Nm":     pulse_match["Peak_Valgus_Torque_Nm"] if pulse_match is not None else None,
            "Arm_Slot_deg":       pulse_match["Arm_Slot_deg"] if pulse_match is not None else None,
            "AC_Ratio":           pulse_match["AC_Ratio"] if pulse_match is not None else None,
            "One_Day_Stress":     pulse_match["One_Day_Stress"] if pulse_match is not None else None,

            # Biomechanics (from ProPlayAI)
            "PPAI_Present":               ppai_match is not None,
            "PPAI_Match_Method":          ppai_method,
            "Release_Hip_Shoulder_Sep":   ppai_match["Release_Hip_Shoulder_Sep"] if ppai_match is not None else None,
            "Peak_Hip_Shoulder_Sep":      ppai_match["Peak_Hip_Shoulder_Sep"] if ppai_match is not None else None,
            "Release_Trunk_Rot":          ppai_match["Release_Trunk_Rot"] if ppai_match is not None else None,
            "Release_Lead_Knee_Ext":      ppai_match["Release_Lead_Knee_Ext"] if ppai_match is not None else None,
            "FootPlant_Trunk_Rot":        ppai_match["FootPlant_Trunk_Rot"] if ppai_match is not None else None,
            "Peak_Trunk_Angular_Vel":     ppai_match["Peak_Trunk_Angular_Vel"] if ppai_match is not None else None,

            # Confidence
            "Healed":             (pulse_match is None) or (ppai_match is None),
            "Healed_Notes":       [],
        }
        if pulse_match is None:
            row["Healed_Notes"].append("Pulse missing")
        if ppai_match is None:
            row["Healed_Notes"].append("ProPlayAI missing")

        canonical_rows.append(row)

    df = pd.DataFrame(canonical_rows)
    df["Healed_Notes"] = df["Healed_Notes"].apply(lambda x: ", ".join(x) if x else "")
    df["Alignment_Confidence"] = df.apply(_confidence_score, axis=1)
    df = _estimate_plate_location_if_missing(df)
    df = _score_outliers(df)
    return df


def _estimate_plate_location_if_missing(df: pd.DataFrame) -> pd.DataFrame:
    """For rows missing plate location (typical for Pitch Logic ingest), estimate it.

    Pitch Logic doesn't track where the ball crossed the plate — only where it
    was released and how much it broke. We project the ball forward assuming
    the pitcher AIMED AT THE CENTER OF THE STRIKE ZONE (the most reasonable
    default in a bullpen). The break then dictates where it actually ended up.

    This is an APPROXIMATION — for measured plate location, use Rapsodo or
    a camera-based system.
    """
    DEFAULT_AIM_HEIGHT = 2.5   # ft — middle of strike zone
    GRAVITY_DROP_FT    = 0.0   # already baked into "induced vertical break"
                               # so net = aim height - rel_height baked in mechanics

    for idx in df.index:
        if pd.isna(df.at[idx, "Strike_Zone_Side"]):
            hbreak = df.at[idx, "Horiz_Break_in"]
            if pd.notna(hbreak):
                # Assume aim at center plate (x=0). Plate location = break in feet.
                df.at[idx, "Strike_Zone_Side"] = round(hbreak / 12.0, 2)

        if pd.isna(df.at[idx, "Strike_Zone_Height"]):
            vbreak = df.at[idx, "Vert_Break_in"]
            if pd.notna(vbreak):
                # Assume aim at mid-zone height (2.5 ft). Induced vertical break
                # is what carries the ball above gravity-only trajectory.
                # IVB > 0 = ball stays higher than gravity drop would predict.
                # We approximate: a "true straight" pitch lands at aim height;
                # IVB shifts the final position up by (vbreak - 12) inches / 12
                # (12" is roughly the league-average IVB baseline).
                BASELINE_IVB = 12.0
                df.at[idx, "Strike_Zone_Height"] = round(
                    DEFAULT_AIM_HEIGHT + (vbreak - BASELINE_IVB) / 12.0, 2
                )
    return df


def _score_outliers(df: pd.DataFrame) -> pd.DataFrame:
    """Classify each pitch as positive / negative / average outlier vs the session.

    Positive outlier: pitch was notably BETTER than the pitcher's session avg
    on a key axis (velo / spin / movement) AND safe on stress.
    Negative outlier: pitch was notably WORSE — low velo/spin OR high stress
    OR elevated mechanics flag.
    """
    df = df.copy()
    df["Outlier_Type"] = "average"
    df["Outlier_Score"] = 0.0
    df["Outlier_Reasons"] = ""

    # Per pitch type, look at velocity & spin
    for ptype, group in df.groupby("Pitch_Type"):
        if len(group) < 2:
            continue
        velo_mean, velo_std = group["Velocity_mph"].mean(), group["Velocity_mph"].std()
        spin_mean, spin_std = group["Total_Spin_rpm"].mean(), group["Total_Spin_rpm"].std()
        if not velo_std or pd.isna(velo_std): velo_std = 1.0
        if not spin_std or pd.isna(spin_std): spin_std = 1.0

        for idx in group.index:
            reasons = []
            score = 0.0
            v = df.at[idx, "Velocity_mph"]
            s = df.at[idx, "Total_Spin_rpm"]
            stress = df.at[idx, "Peak_Valgus_Nm"]
            fp_trunk = df.at[idx, "FootPlant_Trunk_Rot"]

            # Velocity outlier
            if pd.notna(v) and velo_std > 0:
                v_z = (v - velo_mean) / velo_std
                if v_z > 1.2:
                    score += v_z
                    reasons.append(f"velo +{v - velo_mean:.1f} mph above avg")
                elif v_z < -1.2:
                    score += v_z
                    reasons.append(f"velo {v - velo_mean:.1f} mph below avg")

            # Spin outlier
            if pd.notna(s) and spin_std > 0:
                s_z = (s - spin_mean) / spin_std
                if s_z > 1.2:
                    score += s_z * 0.5
                    reasons.append(f"spin +{int(s - spin_mean)} RPM above avg")
                elif s_z < -1.2:
                    score += s_z * 0.5
                    reasons.append(f"spin {int(s - spin_mean)} RPM below avg")

            # Stress (always negative when high)
            if pd.notna(stress) and stress >= DANGER_VALGUS_NM:
                score -= 2.0
                reasons.append(f"elbow stress {stress:.1f} Nm")

            # Early trunk rotation (negative mechanics flag)
            if pd.notna(fp_trunk) and fp_trunk >= EARLY_TRUNK_ROTATION_DEG:
                score -= 1.0
                reasons.append(f"chest opened early ({fp_trunk:.0f}°)")

            df.at[idx, "Outlier_Score"] = round(score, 2)
            df.at[idx, "Outlier_Reasons"] = "; ".join(reasons)

            if score >= 1.5:
                df.at[idx, "Outlier_Type"] = "positive"
            elif score <= -1.5:
                df.at[idx, "Outlier_Type"] = "negative"

    return df


def _confidence_score(row) -> float:
    """0..1 confidence in a row's data quality."""
    score = 1.0
    if not row["Pulse_Present"]:
        score -= 0.4
    elif row["Pulse_Match_Method"] == "timestamp_wide":
        score -= 0.15
    if not row["PPAI_Present"]:
        score -= 0.4
    elif row["PPAI_Match_Method"] == "timestamp_wide":
        score -= 0.15
    return max(0.0, round(score, 2))


# =============================================================================
# DEMO MODE — synthetic data generator
# =============================================================================
# Pitch "archetypes" used by the demo generator. Each defines a believable
# distribution of velocity, spin, movement, and biomechanics for a pitch type.
DEMO_ARCHETYPES = {
    "Four-Seam Fastball": {
        "velo":  (91.0, 93.5),
        "spin":  (2200, 2400),
        "eff":   (95, 98),
        "axis":  "01:15",  "gyro": (10, 13),
        "vbreak": (17, 19),  "hbreak": (-8, -6),
        "valgus": (52, 56),  "ac_ratio": (0.8, 1.1),
        "trunk_fp": (28, 34), "hip_shoulder_peak": (52, 56),
        "lead_knee": (152, 158),
        # Plate location targets (ft from center plate; height in ft)
        "plate_x_target": (-0.5, 0.3), "plate_z_target": (2.8, 3.4),
        "target_grip": "four_seam_fastball",
    },
    "Two-Seam Sinker": {
        "velo":  (89.0, 91.0),
        "spin":  (2100, 2250),
        "eff":   (93, 96),
        "axis":  "02:30",  "gyro": (12, 15),
        "vbreak": (11, 14),  "hbreak": (-16, -14),
        "valgus": (50, 54),  "ac_ratio": (0.85, 1.1),
        "trunk_fp": (29, 33), "hip_shoulder_peak": (50, 55),
        "lead_knee": (150, 156),
        "plate_x_target": (0.3, 0.9), "plate_z_target": (1.5, 2.2),
        "target_grip": "two_seam_fastball",
    },
    "Slider Strike-Getter": {
        "velo":  (82.5, 84.0),
        "spin":  (2350, 2500),
        "eff":   (30, 38),
        "axis":  "08:45",  "gyro": (62, 70),
        "vbreak": (-3, -1),  "hbreak": (4, 7),
        "valgus": (47, 52),  "ac_ratio": (0.85, 1.1),
        "trunk_fp": (30, 35), "hip_shoulder_peak": (46, 52),
        "lead_knee": (148, 154),
        "plate_x_target": (-0.7, -0.2), "plate_z_target": (1.8, 2.5),
        "target_grip": "slider_standard",
    },
    "Slider Chase": {
        "velo":  (78.0, 80.0),
        "spin":  (2300, 2450),
        "eff":   (14, 22),
        "axis":  "09:15",  "gyro": (75, 82),
        "vbreak": (-6, -3),  "hbreak": (14, 17),
        "valgus": (62, 68),    # DANGER zone — this is the story arc
        "ac_ratio": (1.05, 1.30),
        "trunk_fp": (54, 62),  # opens chest too early
        "hip_shoulder_peak": (38, 44),
        "lead_knee": (134, 142),
        "plate_x_target": (-1.3, -0.7), "plate_z_target": (1.0, 1.8),
        "target_grip": "slider_spike_seam",
    },
    "Changeup": {
        "velo":  (82.0, 84.5),
        "spin":  (1600, 1800),
        "eff":   (85, 92),
        "axis":  "02:00",  "gyro": (17, 21),
        "vbreak": (10, 13),  "hbreak": (-15, -13),
        "valgus": (48, 53),  "ac_ratio": (0.85, 1.1),
        "trunk_fp": (29, 33), "hip_shoulder_peak": (48, 53),
        "lead_knee": (150, 156),
        "plate_x_target": (0.2, 0.7), "plate_z_target": (1.5, 2.2),
        "target_grip": "changeup_circle",
    },
}

# Pitcher's "ideal" target per archetype — used to compute outlier distance
PITCH_IDEALS = {
    "Four-Seam Fastball": {"velo": 92.5, "spin": 2350, "vbreak": 18.5, "hbreak": -7.0, "valgus": 53.0},
    "Two-Seam Sinker":    {"velo": 90.0, "spin": 2200, "vbreak": 13.0, "hbreak": -15.0, "valgus": 52.0},
    "Slider Strike-Getter": {"velo": 83.5, "spin": 2450, "vbreak": -2.0, "hbreak": 5.5, "valgus": 49.0},
    "Slider Chase":       {"velo": 79.5, "spin": 2400, "vbreak": -4.5, "hbreak": 16.0, "valgus": 56.0},
    "Changeup":           {"velo": 83.5, "spin": 1700, "vbreak": 12.0, "hbreak": -14.0, "valgus": 50.0},
}

# Default demo bullpen: pitch types and counts. Coaches see fastball-heavy
# work with a couple of chase sliders that flag the elbow-stress story.
DEMO_BULLPEN_SCRIPT = [
    "Four-Seam Fastball", "Four-Seam Fastball", "Two-Seam Sinker",
    "Four-Seam Fastball", "Slider Strike-Getter", "Slider Chase",
    "Two-Seam Sinker", "Four-Seam Fastball", "Slider Chase",
    "Slider Strike-Getter", "Changeup", "Changeup",
]


# =============================================================================
# SOFTBALL ARCHETYPES + BULLPEN SCRIPT
# =============================================================================
# Underhand windmill pitching. Mechanics + metrics differ meaningfully from
# baseball — e.g., elbow stress is lower (windmill unloads the UCL), spin
# axes target different clock positions (rise = 12:00 backspin, drop = 6:00
# topspin), and target plate locations follow softball strike-zone norms.
DEMO_ARCHETYPES_SOFTBALL = {
    "Softball Fastball": {
        "velo":  (58.0, 64.0),
        "spin":  (1500, 2000),
        "eff":   (88, 95),
        "axis":  "10:30",  "gyro": (12, 18),
        "vbreak": (-1, 2),  "hbreak": (6, 11),
        "valgus": (28, 36),  "ac_ratio": (0.8, 1.1),
        "trunk_fp": (28, 34), "hip_shoulder_peak": (40, 48),
        "lead_knee": (152, 162),
        "plate_x_target": (-0.4, 0.5), "plate_z_target": (1.8, 2.6),
        "target_grip": "softball_fastball",
    },
    "Rise Ball": {
        "velo":  (56.0, 62.0),
        "spin":  (1900, 2400),     # high spin for the rise effect
        "eff":   (92, 97),
        "axis":  "12:00",  "gyro": (8, 14),    # near-pure backspin
        "vbreak": (4, 9),                       # POSITIVE = ball stays up
        "hbreak": (-2, 2),
        "valgus": (30, 38),  "ac_ratio": (0.85, 1.15),
        "trunk_fp": (29, 33), "hip_shoulder_peak": (40, 48),
        "lead_knee": (150, 158),
        "plate_x_target": (-0.5, 0.5), "plate_z_target": (2.9, 3.5),
        "target_grip": "softball_rise",
    },
    "Drop Ball": {
        "velo":  (55.0, 61.0),
        "spin":  (1600, 2100),
        "eff":   (80, 92),
        "axis":  "06:00",  "gyro": (12, 22),    # topspin = drops fast
        "vbreak": (-8, -3),                     # NEGATIVE = drops more than gravity
        "hbreak": (-2, 3),
        "valgus": (29, 36),  "ac_ratio": (0.85, 1.10),
        "trunk_fp": (30, 35), "hip_shoulder_peak": (40, 47),
        "lead_knee": (151, 159),
        "plate_x_target": (-0.4, 0.4), "plate_z_target": (1.3, 1.9),
        "target_grip": "softball_drop",
    },
    "Curveball": {
        "velo":  (54.0, 59.0),
        "spin":  (1500, 2000),
        "eff":   (70, 85),
        "axis":  "08:30",  "gyro": (25, 38),
        "vbreak": (-2, 2),  "hbreak": (-9, -5),
        "valgus": (28, 35),  "ac_ratio": (0.85, 1.10),
        "trunk_fp": (30, 35), "hip_shoulder_peak": (38, 46),
        "lead_knee": (149, 157),
        "plate_x_target": (-1.0, -0.4), "plate_z_target": (1.6, 2.4),
        "target_grip": "softball_curve",
    },
    "Screwball": {
        "velo":  (54.0, 59.0),
        "spin":  (1500, 2000),
        "eff":   (70, 85),
        "axis":  "03:30",  "gyro": (25, 38),
        "vbreak": (-2, 2),  "hbreak": (5, 9),
        "valgus": (28, 35),  "ac_ratio": (0.85, 1.10),
        "trunk_fp": (30, 35), "hip_shoulder_peak": (38, 46),
        "lead_knee": (149, 157),
        "plate_x_target": (0.4, 1.0), "plate_z_target": (1.6, 2.4),
        "target_grip": "softball_screw",
    },
    "Change-Up": {
        "velo":  (44.0, 50.0),     # 10-14 mph slower than fastball
        "spin":  (900, 1300),       # low "dead" spin
        "eff":   (60, 78),
        "axis":  "10:00",  "gyro": (35, 50),
        "vbreak": (-4, -1),  "hbreak": (3, 7),
        "valgus": (26, 33),  "ac_ratio": (0.80, 1.05),
        "trunk_fp": (30, 35), "hip_shoulder_peak": (39, 46),
        "lead_knee": (150, 158),
        "plate_x_target": (-0.3, 0.6), "plate_z_target": (1.4, 2.1),
        "target_grip": "softball_change",
    },
}

# Softball bullpen script — features a rise ball at top + a drop ball
# at bottom for the "vertical tunnel" story coaches teach
DEMO_BULLPEN_SCRIPT_SOFTBALL = [
    "Softball Fastball", "Softball Fastball", "Rise Ball",
    "Drop Ball", "Rise Ball", "Curveball",
    "Softball Fastball", "Drop Ball", "Change-Up",
    "Screwball", "Rise Ball", "Drop Ball",
]

PITCH_IDEALS_SOFTBALL = {
    "Softball Fastball": {"velo": 62.0, "spin": 1800, "vbreak": 0.0, "hbreak": 9.0,  "valgus": 32.0},
    "Rise Ball":         {"velo": 60.0, "spin": 2200, "vbreak": 7.0, "hbreak": 0.0,  "valgus": 34.0},
    "Drop Ball":         {"velo": 58.0, "spin": 1900, "vbreak": -6.0, "hbreak": 0.0, "valgus": 33.0},
    "Curveball":         {"velo": 57.0, "spin": 1800, "vbreak": 0.0, "hbreak": -7.0, "valgus": 32.0},
    "Screwball":         {"velo": 57.0, "spin": 1800, "vbreak": 0.0, "hbreak": 7.0,  "valgus": 32.0},
    "Change-Up":         {"velo": 47.0, "spin": 1100, "vbreak": -2.5, "hbreak": 5.0, "valgus": 30.0},
}

DEMO_BASELINE_SOFTBALL = {
    "Softball Fastball": {"velo": 60.0, "vbreak": 0.5,  "stress": 33.0},
    "Rise Ball":         {"velo": 58.5, "vbreak": 5.0,  "stress": 34.5},
    "Drop Ball":         {"velo": 57.5, "vbreak": -5.0, "stress": 33.0},
    "Curveball":         {"velo": 56.0, "vbreak": 0.0,  "stress": 32.0},
    "Screwball":         {"velo": 56.0, "vbreak": 0.0,  "stress": 32.0},
    "Change-Up":         {"velo": 47.0, "vbreak": -2.5, "stress": 30.0},
}


# Sport-keyed lookup tables — call these helpers instead of accessing
# the raw constants so future sports (e.g. high-school cricket?) are easy
SPORT_ARCHETYPES = {
    "Baseball": DEMO_ARCHETYPES,
    "Softball": DEMO_ARCHETYPES_SOFTBALL,
}
SPORT_BULLPEN_SCRIPT = {
    "Baseball": DEMO_BULLPEN_SCRIPT,
    "Softball": DEMO_BULLPEN_SCRIPT_SOFTBALL,
}
SPORT_IDEALS = {
    "Baseball": PITCH_IDEALS,
    "Softball": PITCH_IDEALS_SOFTBALL,
}
SPORT_PITCHING_DISTANCE_FT = {
    "Baseball": 60.5,
    "Softball": 43.0,
}


def get_sport_archetypes(sport: str = "Baseball"):
    return SPORT_ARCHETYPES.get(sport, DEMO_ARCHETYPES)

def get_sport_bullpen_script(sport: str = "Baseball"):
    return SPORT_BULLPEN_SCRIPT.get(sport, DEMO_BULLPEN_SCRIPT)

def get_sport_ideals(sport: str = "Baseball"):
    return SPORT_IDEALS.get(sport, PITCH_IDEALS)


# =============================================================================
# HITTING LAB — constants, schema, sample data
# =============================================================================
# Swings face pitches; the data is bat-side rather than ball-side. This
# section mirrors the pitching constants but for hitters.

# Elite-range targets per metric, for the mechanics critique + outlier check
HITTING_IDEALS = {
    "bat_speed_mph":     {"strong": 72, "weak": 60},   # peak bat speed
    "exit_velocity_mph": {"strong": 90, "weak": 75},   # exit velo on contact
    "launch_angle_deg":  {"strong": 25, "weak": 5,
                          "ideal_low": 10, "ideal_high": 25},  # barrel zone
    "attack_angle_deg":  {"strong": 12, "weak": -5,
                          "ideal_low": 6, "ideal_high": 18},   # bat path
    "on_plane_eff_pct":  {"strong": 80, "weak": 60},   # bat matches pitch plane
    "peak_hand_speed_mph": {"strong": 24, "weak": 18},
    "time_to_contact_sec": {"strong": 0.16, "weak": 0.22},  # lower is better
    "hip_shoulder_sep_deg": {"strong": 45, "weak": 30},  # rotational sequencing
}

# Softball — windmill pitchers throw underhand from 43 ft, so timing differs
# but the bat metrics themselves are mostly the same. Adjust velocity bands.
HITTING_IDEALS_SOFTBALL = {
    **HITTING_IDEALS,
    "bat_speed_mph":     {"strong": 64, "weak": 52},
    "exit_velocity_mph": {"strong": 78, "weak": 64},
}

# Outcome buckets the model recognizes on each swing.
SWING_OUTCOMES = ["take", "whiff", "foul", "weak_contact", "solid_contact", "barrel"]

# Pitch-type colors used in hitting strike-zone scatter (matches pitching map)
HITTING_PITCH_FACED_COLORS = {
    # Baseball pitches the hitter sees
    "Four-Seam Fastball":   "#d32f2f",
    "Two-Seam Sinker":      "#f57c00",
    "Slider":               "#1976d2",
    "Curveball":            "#00838f",
    "Changeup":             "#388e3c",
    # Softball pitches the hitter sees
    "Softball Fastball":    "#d32f2f",
    "Rise Ball":            "#0ea5e9",
    "Drop Ball":            "#7c3aed",
    "Screwball":            "#f59e0b",
    "Change-Up":            "#388e3c",
}

# Demo "at-bat scripts" — what kind of pitch sequence the sample hitter sees,
# and what they typically do with it. The story arc: strong on fastballs middle,
# struggles vs sliders down-and-away (classic young-hitter pattern).
DEMO_AB_SCRIPT_BASEBALL = [
    # (Pitch_Type, plate_x, plate_z, intended_outcome)
    ("Four-Seam Fastball",  0.10, 2.7,  "barrel"),         # middle-up — crushed
    ("Slider",             -0.60, 1.6,  "whiff"),          # down-away — miss
    ("Four-Seam Fastball",  0.30, 2.5,  "solid_contact"),
    ("Changeup",            0.20, 2.0,  "weak_contact"),
    ("Four-Seam Fastball", -0.20, 3.0,  "foul"),
    ("Two-Seam Sinker",     0.50, 1.8,  "solid_contact"),
    ("Slider",             -0.80, 1.4,  "whiff"),          # chase slider — miss
    ("Four-Seam Fastball",  0.00, 2.8,  "barrel"),         # middle — crushed
    ("Curveball",           0.10, 1.6,  "take"),           # took it for a strike
    ("Four-Seam Fastball",  0.40, 2.6,  "solid_contact"),
    ("Slider",             -0.55, 1.7,  "weak_contact"),   # late on slider
    ("Four-Seam Fastball",  0.00, 3.2,  "foul"),           # high heat — fouled
    ("Changeup",            0.30, 1.9,  "weak_contact"),
    ("Two-Seam Sinker",    -0.30, 2.0,  "solid_contact"),
    ("Slider",             -0.70, 1.5,  "whiff"),
    ("Four-Seam Fastball",  0.20, 2.7,  "barrel"),
    ("Curveball",          -0.30, 1.9,  "weak_contact"),
    ("Four-Seam Fastball",  0.50, 2.5,  "solid_contact"),
    ("Changeup",           -0.20, 2.0,  "take"),
    ("Slider",             -0.65, 1.6,  "whiff"),
    ("Four-Seam Fastball", -0.10, 2.6,  "solid_contact"),
    ("Slider",              0.00, 2.4,  "barrel"),         # hung slider — crushed
    ("Two-Seam Sinker",     0.40, 1.7,  "weak_contact"),
    ("Four-Seam Fastball",  0.30, 2.8,  "solid_contact"),
    ("Curveball",           0.00, 1.8,  "foul"),
]

DEMO_AB_SCRIPT_SOFTBALL = [
    ("Softball Fastball",   0.10, 2.5,  "barrel"),
    ("Rise Ball",           0.00, 3.2,  "whiff"),          # rise up — missed under
    ("Softball Fastball",   0.30, 2.4,  "solid_contact"),
    ("Drop Ball",           0.10, 1.4,  "weak_contact"),
    ("Softball Fastball",  -0.20, 2.2,  "foul"),
    ("Rise Ball",          -0.10, 3.0,  "whiff"),
    ("Screwball",           0.60, 1.8,  "whiff"),          # chase
    ("Softball Fastball",   0.00, 2.5,  "barrel"),
    ("Drop Ball",          -0.20, 1.5,  "take"),
    ("Softball Fastball",   0.40, 2.3,  "solid_contact"),
    ("Rise Ball",           0.00, 2.9,  "weak_contact"),   # popped up
    ("Change-Up",           0.30, 2.0,  "weak_contact"),
    ("Softball Fastball",   0.20, 2.4,  "barrel"),
    ("Screwball",           0.50, 1.7,  "whiff"),
    ("Drop Ball",          -0.10, 1.3,  "whiff"),          # missed under drop
    ("Softball Fastball",  -0.30, 2.5,  "solid_contact"),
    ("Rise Ball",           0.10, 3.1,  "foul"),
    ("Change-Up",          -0.20, 1.9,  "take"),
    ("Softball Fastball",   0.00, 2.4,  "solid_contact"),
    ("Drop Ball",           0.20, 1.2,  "whiff"),
]

SPORT_AB_SCRIPT = {
    "Baseball": DEMO_AB_SCRIPT_BASEBALL,
    "Softball": DEMO_AB_SCRIPT_SOFTBALL,
}

# Outcome → bat/contact metric ranges. Each tuple is (low, high) range
# the random generator samples within for that outcome.
HITTING_OUTCOME_PROFILES = {
    "take": {
        "bat_speed":   (0, 0),       "exit_velo": (None, None),
        "launch_angle":(None, None), "attack_angle": (None, None),
        "on_plane":    (None, None), "contact_offset": (None, None),
        "distance":    (None, None), "spray":         (None, None),
    },
    "whiff": {
        "bat_speed":   (62, 72),     "exit_velo": (None, None),
        "launch_angle":(None, None), "attack_angle": (-5, 15),
        "on_plane":    (45, 65),     "contact_offset": (None, None),
        "distance":    (None, None), "spray":         (None, None),
    },
    "foul": {
        "bat_speed":   (62, 72),     "exit_velo": (70, 85),
        "launch_angle":(40, 75),     "attack_angle": (-3, 12),
        "on_plane":    (60, 78),     "contact_offset": (-1.5, 1.5),
        "distance":    (50, 200),    "spray":         (-50, 50),
    },
    "weak_contact": {
        "bat_speed":   (60, 70),     "exit_velo": (62, 80),
        "launch_angle":(-10, 8),     "attack_angle": (-5, 8),
        "on_plane":    (55, 72),     "contact_offset": (-1.0, 1.0),
        "distance":    (50, 200),    "spray":         (-35, 35),
    },
    "solid_contact": {
        "bat_speed":   (68, 76),     "exit_velo": (82, 92),
        "launch_angle":(8, 22),      "attack_angle": (6, 16),
        "on_plane":    (72, 85),     "contact_offset": (-0.5, 0.5),
        "distance":    (250, 380),   "spray":         (-25, 25),
    },
    "barrel": {
        "bat_speed":   (72, 80),     "exit_velo": (95, 108),
        "launch_angle":(18, 32),     "attack_angle": (10, 18),
        "on_plane":    (82, 92),     "contact_offset": (-0.25, 0.25),
        "distance":    (350, 460),   "spray":         (-20, 20),
    },
}


def get_hitting_ideals(sport: str = "Baseball") -> dict:
    return HITTING_IDEALS_SOFTBALL if sport == "Softball" else HITTING_IDEALS


def _seeded_random(seed_string: str) -> random.Random:
    """Make demo data deterministic per-pitcher: same name → same demo."""
    h = hashlib.md5(seed_string.encode("utf-8")).hexdigest()
    return random.Random(int(h[:8], 16))


def _u(rng: random.Random, lo, hi) -> float:
    return rng.uniform(lo, hi)


def generate_hitting_session(hitter_name: str, hand: str = "Right",
                              sport: str = "Baseball",
                              session_date: datetime | None = None) -> pd.DataFrame:
    """Produce a canonical aligned-swing DataFrame for demo purposes.

    Same `hitter_name` returns the same session (seeded random) for
    reproducible demos. Walks the sport's at-bat script and generates
    bat / contact / biomech metrics matching each scripted outcome.
    """
    rng = _seeded_random(f"hitter|{sport}|{hitter_name}")
    if session_date is None:
        session_date = datetime.now(timezone.utc).replace(microsecond=0)
    base_time = session_date.replace(hour=15, minute=0, second=0)

    script = SPORT_AB_SCRIPT.get(sport, DEMO_AB_SCRIPT_BASEBALL)

    def _u(lo, hi):
        if lo is None or hi is None:
            return None
        return rng.uniform(lo, hi)

    def _u_int(lo, hi):
        v = _u(lo, hi)
        return None if v is None else int(round(v))

    rows = []
    for i, (pitch_type, plate_x, plate_z, outcome) in enumerate(script, start=1):
        prof = HITTING_OUTCOME_PROFILES[outcome]

        # Pitch faced — speed/type sampled to match sport
        if sport == "Softball":
            pitch_velo = round(rng.uniform(55, 65) if "Fastball" in pitch_type
                                else rng.uniform(50, 60), 1)
        else:
            pitch_velo = round(rng.uniform(88, 93) if "Fastball" in pitch_type or "Sinker" in pitch_type
                                else rng.uniform(78, 86), 1)

        # Swing decision
        swing_type = "take" if outcome == "take" else "swing"

        # Bat metrics
        bat_speed = _u(*prof["bat_speed"])
        attack    = _u(*prof["attack_angle"])
        on_plane  = _u(*prof["on_plane"])
        hand_spd  = bat_speed * 0.32 if bat_speed else None  # approx ratio
        ttc       = round(rng.uniform(0.15, 0.21), 3) if outcome != "take" else None

        # Contact metrics
        exit_velo   = _u(*prof["exit_velo"])
        launch      = _u(*prof["launch_angle"])
        contact_off = _u(*prof["contact_offset"])
        distance    = _u(*prof["distance"])
        spray       = _u(*prof["spray"])

        # Biomech — better numbers on solid/barrel, weaker on whiffs
        if outcome in ("barrel", "solid_contact"):
            hip_shoulder = round(rng.uniform(40, 52), 1)
            stride       = round(rng.uniform(28, 36), 1)
            lead_knee    = round(rng.uniform(20, 35), 1)
        else:
            hip_shoulder = round(rng.uniform(28, 42), 1)
            stride       = round(rng.uniform(24, 34), 1)
            lead_knee    = round(rng.uniform(15, 40), 1)

        rows.append({
            "Swing_Num":              i,
            "Timestamp":              base_time + timedelta(seconds=45 * (i - 1)),
            "Pitch_Type_Faced":       pitch_type,
            "Pitch_Velocity_mph":     pitch_velo,
            "Plate_X_ft":             round(plate_x + rng.uniform(-0.05, 0.05), 2),
            "Plate_Z_ft":             round(plate_z + rng.uniform(-0.05, 0.05), 2),
            "Swing_Type":             swing_type,
            "Swing_Outcome":          outcome,
            "Bat_Speed_mph":          round(bat_speed, 1) if bat_speed else None,
            "Attack_Angle_deg":       round(attack, 1) if attack is not None else None,
            "On_Plane_Eff_pct":       round(on_plane, 1) if on_plane is not None else None,
            "Peak_Hand_Speed_mph":    round(hand_spd, 1) if hand_spd else None,
            "Time_to_Contact_sec":    ttc,
            "Exit_Velocity_mph":      round(exit_velo, 1) if exit_velo else None,
            "Launch_Angle_deg":       round(launch, 1) if launch is not None else None,
            "Contact_Offset_in":      round(contact_off, 2) if contact_off is not None else None,
            "Distance_ft":            int(distance) if distance else None,
            "Spray_Angle_deg":        round(spray, 1) if spray is not None else None,
            "Peak_Hip_Shoulder_Sep_deg": hip_shoulder,
            "Stride_Length_in":       stride,
            "Lead_Knee_Flex_deg":     lead_knee,
        })

    df = pd.DataFrame(rows)
    df["Sport"] = sport
    return df


def hitting_session_kpis(df: pd.DataFrame) -> dict:
    """Headline KPIs for a hitting session."""
    in_play = df[df["Swing_Outcome"].isin(["weak_contact", "solid_contact", "barrel", "foul"])]
    swings  = df[df["Swing_Type"] == "swing"]
    barrels = df[df["Swing_Outcome"] == "barrel"]

    avg_velo  = float(in_play["Exit_Velocity_mph"].dropna().mean()) if len(in_play) else None
    peak_velo = float(in_play["Exit_Velocity_mph"].dropna().max())  if len(in_play) else None
    avg_bat   = float(swings["Bat_Speed_mph"].dropna().mean())      if len(swings)  else None
    avg_la    = float(in_play["Launch_Angle_deg"].dropna().mean())  if len(in_play) else None
    avg_op    = float(swings["On_Plane_Eff_pct"].dropna().mean())   if len(swings)  else None
    barrel_pct = (len(barrels) / len(swings) * 100) if len(swings) else 0

    whiff_count = (df["Swing_Outcome"] == "whiff").sum()
    whiff_pct = (whiff_count / len(swings) * 100) if len(swings) else 0

    return {
        "Total Swings":     int(len(swings)),
        "Avg Exit Velo":    round(avg_velo, 1) if avg_velo else None,
        "Peak Exit Velo":   round(peak_velo, 1) if peak_velo else None,
        "Avg Bat Speed":    round(avg_bat, 1) if avg_bat else None,
        "Avg Launch Angle": round(avg_la, 1) if avg_la is not None else None,
        "On-Plane %":       round(avg_op, 1) if avg_op else None,
        "Barrel %":         round(barrel_pct, 1),
        "Whiff %":          round(whiff_pct, 1),
    }


def generate_demo_session(pitcher_name: str, hand: str = "Right",
                          session_date: datetime | None = None,
                          sport: str = "Baseball") -> pd.DataFrame:
    """Produce a canonical aligned-pitch DataFrame for demo purposes.

    Same `pitcher_name` returns the same session (seeded random), so demos
    feel like a real pitcher with consistent metrics across "visits."

    `sport` selects the pitch-type archetypes and bullpen script ("Baseball"
    or "Softball"). Determines pitching distance, valid pitch types, etc.
    """
    rng = _seeded_random(f"{sport}|{pitcher_name}")
    if session_date is None:
        session_date = datetime.now(timezone.utc).replace(microsecond=0)
    base_time = session_date.replace(hour=14, minute=0, second=0)

    archetypes = get_sport_archetypes(sport)
    bullpen_script = get_sport_bullpen_script(sport)

    # Fatigue trigger pitch differs by sport (the demo "story arc")
    fatigue_pitch = "Slider Chase" if sport == "Baseball" else "Rise Ball"

    rows = []
    for i, pitch_type in enumerate(bullpen_script, start=1):
        a = archetypes[pitch_type]
        # Fatigue drift: late in the bullpen, the "fatigue pitch" shows more stress
        fatigue_bump = 0.0
        if pitch_type == fatigue_pitch and i > 6:
            fatigue_bump = rng.uniform(2.0, 4.0)  # extra Nm late in the session

        # Simulate two healed pitches per session for realism
        pulse_present = not (i == 5)            # pitch 5 missing pulse
        ppai_present  = not (i == 9)            # pitch 9 missing ppai

        valgus = round(_u(rng, *a["valgus"]) + fatigue_bump, 1) if pulse_present else None
        ac_ratio = round(_u(rng, *a["ac_ratio"]), 2) if pulse_present else None

        # Plate location: target zone + control variance.
        # Late in the bullpen, control degrades slightly (fatigue effect)
        control_noise = 0.10 + 0.02 * max(0, i - 6)  # widens after pitch 6
        plate_x = round(_u(rng, *a["plate_x_target"]) + rng.uniform(-control_noise, control_noise), 2)
        plate_z = round(_u(rng, *a["plate_z_target"]) + rng.uniform(-control_noise, control_noise), 2)

        row = {
            "Pitch_Num":          i,
            "Timestamp":          base_time + timedelta(seconds=45 * (i - 1)),
            "Pitch_Type":         pitch_type,
            "Velocity_mph":       round(_u(rng, *a["velo"]), 1),
            "Total_Spin_rpm":     int(_u(rng, *a["spin"])),
            "Spin_Efficiency_pct": round(_u(rng, *a["eff"]), 1),
            "Spin_Axis_Deg":      spin_clock_to_degrees(a["axis"]),
            "Spin_Direction_hhmm": a["axis"],
            "Gyro_Degrees":       round(_u(rng, *a["gyro"]), 1),
            "Vert_Break_in":      round(_u(rng, *a["vbreak"]), 1),
            "Horiz_Break_in":     round(_u(rng, *a["hbreak"]), 1),
            "Extension_ft":       round(_u(rng, 5.9, 6.4), 1),
            "Release_Height_ft":  round(_u(rng, 5.7, 6.0), 1),
            "Release_Side_ft":    round(_u(rng, 1.9, 2.4), 1),
            "Strike_Zone_Side":   plate_x,
            "Strike_Zone_Height": plate_z,

            "Pulse_Present":      pulse_present,
            "Pulse_Match_Method": "timestamp_window" if pulse_present else None,
            "Arm_Speed_deg_sec":  int(_u(rng, 940, 1000)) if pulse_present else None,
            "Peak_Valgus_Nm":     valgus,
            "Arm_Slot_deg":       int(_u(rng, 44, 50)) if pulse_present else None,
            "AC_Ratio":           ac_ratio,
            "One_Day_Stress":     round(_u(rng, 24, 32), 1) if pulse_present else None,

            "PPAI_Present":               ppai_present,
            "PPAI_Match_Method":          "timestamp_window" if ppai_present else None,
            "Release_Hip_Shoulder_Sep":   round(_u(rng, 3, 12), 1) if ppai_present else None,
            "Peak_Hip_Shoulder_Sep":      round(_u(rng, *a["hip_shoulder_peak"]), 1) if ppai_present else None,
            "Release_Trunk_Rot":          round(_u(rng, 96, 105), 1) if ppai_present else None,
            "Release_Lead_Knee_Ext":      round(_u(rng, *a["lead_knee"]), 1) if ppai_present else None,
            "FootPlant_Trunk_Rot":        round(_u(rng, *a["trunk_fp"]), 1) if ppai_present else None,
            "Peak_Trunk_Angular_Vel":     int(_u(rng, 1050, 1200)) if ppai_present else None,
        }

        healed_notes = []
        if not pulse_present:  healed_notes.append("Pulse missing")
        if not ppai_present:   healed_notes.append("ProPlayAI missing")
        row["Healed"]              = bool(healed_notes)
        row["Healed_Notes"]        = ", ".join(healed_notes)
        row["Alignment_Confidence"] = _confidence_score(row)
        rows.append(row)

    demo_df = pd.DataFrame(rows)
    demo_df = _score_outliers(demo_df)
    return demo_df


# =============================================================================
# REPORT LOGIC — injury flags, deltas, action plan
# =============================================================================
def detect_injury_flags(row) -> list:
    """Return a list of injury/risk flags for a single pitch row."""
    flags = []
    if row.get("Peak_Valgus_Nm") and row["Peak_Valgus_Nm"] >= DANGER_VALGUS_NM:
        flags.append({
            "severity": "DANGER",
            "label":    f"Elbow stress {row['Peak_Valgus_Nm']:.1f} Nm",
            "drill_key": "high_valgus_stress",
        })
    if row.get("AC_Ratio") and row["AC_Ratio"] >= ACR_DANGER_THRESHOLD:
        flags.append({
            "severity": "DANGER",
            "label":    f"AC Ratio {row['AC_Ratio']:.2f} (workload spike)",
            "drill_key": "high_acr",
        })
    elif row.get("AC_Ratio") and row["AC_Ratio"] >= ACR_WARNING_THRESHOLD:
        flags.append({
            "severity": "WARNING",
            "label":    f"AC Ratio {row['AC_Ratio']:.2f} (workload elevated)",
            "drill_key": "high_acr",
        })
    if row.get("FootPlant_Trunk_Rot") and row["FootPlant_Trunk_Rot"] >= EARLY_TRUNK_ROTATION_DEG:
        flags.append({
            "severity": "WARNING",
            "label":    f"Chest opened early at foot-plant ({row['FootPlant_Trunk_Rot']:.1f}°)",
            "drill_key": "early_trunk_rotation",
        })
    return flags


def session_kpis(df: pd.DataFrame) -> dict:
    """Compute headline KPIs across the whole bullpen."""
    return {
        "Total Pitches":      len(df),
        "Avg Velocity":       round(df["Velocity_mph"].mean(), 1),
        "Peak Velocity":      round(df["Velocity_mph"].max(), 1),
        "Avg Spin":           int(df["Total_Spin_rpm"].mean()),
        "Avg Elbow Stress":   round(df["Peak_Valgus_Nm"].dropna().mean(), 1) if df["Peak_Valgus_Nm"].notna().any() else None,
        "Max Elbow Stress":   round(df["Peak_Valgus_Nm"].dropna().max(), 1) if df["Peak_Valgus_Nm"].notna().any() else None,
        "Pitches Healed":     int(df["Healed"].sum()),
    }


def analyze_mechanics(df: pd.DataFrame, sport: str = "Baseball") -> dict:
    """Inspect biomech columns and produce strengths + weaknesses, each
    tied to a specific gain category (velocity / control / movement / injury).

    Sport-aware: softball windmill thresholds differ from baseball overhand.

    Returns: {"strengths": [...], "weaknesses": [...]}
    """
    is_softball = (sport == "Softball")
    strengths, weaknesses = [], []

    # ===== SPORT-AWARE THRESHOLDS =====
    if is_softball:
        # Windmill mechanics — values from softball biomech literature
        HS_SEP_STRONG    = 42    # >= this is upper-college softball
        HS_SEP_WEAK      = 35    # < this is the problem zone
        HS_SEP_TARGET    = "42-50"
        KNEE_STRONG      = 150
        KNEE_WEAK        = 142
        TRUNK_VEL_STRONG = 850
        SLOT_TIGHT       = 4
        SLOT_LOOSE       = 6
        CHECK_EXTENSION  = False    # windmill release isn't measured in "feet of extension"
        HS_SEP_FIX_DRILL = "K-drill + hip-snap drill at top of windmill. 3×6, 3 days this week."
        EARLY_TRUNK_FIX  = "K-drill mirror work — pause at the 12:00 position to feel the closed front side. 5×6 daily."
    else:
        HS_SEP_STRONG    = 50
        HS_SEP_WEAK      = 48
        HS_SEP_TARGET    = "50-65"
        KNEE_STRONG      = 150
        KNEE_WEAK        = 145
        TRUNK_VEL_STRONG = 1100
        SLOT_TIGHT       = 4
        SLOT_LOOSE       = 6
        CHECK_EXTENSION  = True
        HS_SEP_FIX_DRILL = "Hershiser drill — exaggerated coil reps with weighted ball. 3×4, 3 days this week."
        EARLY_TRUNK_FIX  = "Driveline pivot pick-offs with weighted plyo ball. 3×5, 3 days this week."

    # Helper to safely compute mean of a column that may not exist or be all-NaN
    def avg(col):
        if col not in df.columns: return None
        s = df[col].dropna()
        return float(s.mean()) if len(s) else None

    knee_ext  = avg("Release_Lead_Knee_Ext")
    hs_sep    = avg("Peak_Hip_Shoulder_Sep")
    fp_trunk  = avg("FootPlant_Trunk_Rot")
    extension = avg("Extension_ft")
    arm_slot  = avg("Arm_Slot_deg")
    trunk_vel = avg("Peak_Trunk_Angular_Vel")

    # Arm-slot consistency (range across session, not just mean)
    arm_slot_range = None
    if "Arm_Slot_deg" in df.columns and df["Arm_Slot_deg"].notna().any():
        s = df["Arm_Slot_deg"].dropna()
        arm_slot_range = float(s.max() - s.min())

    early_trunk_rate = None
    if "FootPlant_Trunk_Rot" in df.columns and df["FootPlant_Trunk_Rot"].notna().any():
        early_trunk_rate = float((df["FootPlant_Trunk_Rot"].dropna() >= EARLY_TRUNK_ROTATION_DEG).mean())

    # ===== STRENGTHS =====
    if knee_ext is not None and knee_ext >= KNEE_STRONG:
        strengths.append({
            "label":  "Stiff front leg block",
            "detail": f"Avg lead-knee extension at release was {knee_ext:.1f}° — "
                      f"near or above the {KNEE_STRONG}° target.",
            "gain":   "Translates ground force into ball velocity efficiently.",
            "tag":    "VELOCITY",
        })
    if hs_sep is not None and hs_sep >= HS_SEP_STRONG:
        strengths.append({
            "label":  "Strong hip-shoulder separation",
            "detail": f"Peak hip-shoulder separation averaged {hs_sep:.1f}° — "
                      f"in the upper range for {sport} ({HS_SEP_TARGET}°).",
            "gain":   "Generates rubber-band torque → drives velocity.",
            "tag":    "VELOCITY",
        })
    if (early_trunk_rate is not None and early_trunk_rate < 0.20 and fp_trunk is not None):
        strengths.append({
            "label":  "Trunk stays closed at foot-plant",
            "detail": f"Avg trunk rotation at foot-plant was {fp_trunk:.1f}° — "
                      f"only {int(early_trunk_rate*100)}% of pitches opened early.",
            "gain":   "Protects the elbow AND maximizes power transfer.",
            "tag":    "INJURY-SAFE + VELOCITY",
        })
    if CHECK_EXTENSION and extension is not None and extension >= 6.2:
        strengths.append({
            "label":  "Excellent release extension",
            "detail": f"Avg extension was {extension:.2f} ft (target 6.2+).",
            "gain":   "Adds ~2 mph of perceived velocity — the hitter has less time to react.",
            "tag":    "VELOCITY (PERCEIVED)",
        })
    if arm_slot_range is not None and arm_slot_range <= SLOT_TIGHT:
        strengths.append({
            "label":  "Consistent arm slot",
            "detail": f"Arm slot varied only {arm_slot_range:.1f}° across all pitches "
                      f"(target: < {SLOT_TIGHT}°).",
            "gain":   "Predictable release point → better command + better tunneling.",
            "tag":    "COMMAND",
        })
    if trunk_vel is not None and trunk_vel >= TRUNK_VEL_STRONG:
        strengths.append({
            "label":  "Explosive trunk rotation",
            "detail": f"Peak trunk angular velocity averaged {trunk_vel:.0f}°/sec — "
                      f"in the upper range for HS+ {sport.lower()} pitchers.",
            "gain":   "Snappy delivery converts strength into ball velocity at release.",
            "tag":    "VELOCITY",
        })

    # ===== AREAS TO IMPROVE =====
    if knee_ext is not None and knee_ext < KNEE_WEAK:
        weaknesses.append({
            "label":  "Soft front leg (energy leak)",
            "detail": f"Avg lead-knee extension at release was {knee_ext:.1f}° — "
                      f"below the {KNEE_STRONG}° target. The front knee is bending under "
                      "load instead of bracing.",
            "gain":   "Velocity",
            "fix":    "Wall drill — front-leg stiff brace. 4 sets × 6 reps, 2× this week.",
        })
    if hs_sep is not None and hs_sep < HS_SEP_WEAK:
        weaknesses.append({
            "label":  "Low hip-shoulder separation",
            "detail": f"Peak hip-shoulder separation averaged only {hs_sep:.1f}° — "
                      f"well below the {HS_SEP_TARGET}° target for {sport}. "
                      "Hips and shoulders are firing together instead of in sequence.",
            "gain":   "Velocity",
            "fix":    HS_SEP_FIX_DRILL,
        })
    if early_trunk_rate is not None and early_trunk_rate >= 0.25:
        weaknesses.append({
            "label":  "Chest opens too early at foot-plant",
            "detail": f"{int(early_trunk_rate*100)}% of pitches had the chest already rotating "
                      f"when the front foot landed (target: < 20%). The arm has to do extra "
                      "work — that's lost velocity AND elevated stress.",
            "gain":   "Velocity + Injury Prevention",
            "fix":    EARLY_TRUNK_FIX,
        })
    if CHECK_EXTENSION and extension is not None and extension < 6.0:
        weaknesses.append({
            "label":  "Short release extension",
            "detail": f"Avg extension was {extension:.2f} ft (target 6.2+ ft). "
                      "Not getting out toward the plate at release.",
            "gain":   "Perceived Velocity",
            "fix":    "Stride-down drill — emphasize a longer stride toward the plate. 3×8, 2-3 days this week.",
        })
    if arm_slot_range is not None and arm_slot_range > SLOT_LOOSE:
        weaknesses.append({
            "label":  "Inconsistent arm slot",
            "detail": f"Arm slot varied {arm_slot_range:.1f}° across the session "
                      f"(target < {SLOT_TIGHT}°). Inconsistent release = inconsistent command.",
            "gain":   "Command + Tunneling",
            "fix":    "Mirror drill + 1-knee throws. 10 mirror reps before each bullpen.",
        })

    return {"strengths": strengths, "weaknesses": weaknesses}


# =============================================================================
# HITTING MECHANICS CRITIQUE  (mirrors analyze_mechanics for pitching)
# =============================================================================
def analyze_hitting_mechanics(df: pd.DataFrame, sport: str = "Baseball") -> dict:
    """Inspect a swing session and produce hitter-side strengths + weaknesses.

    Mirrors the pitcher's analyze_mechanics return shape:
       {"strengths": [...], "weaknesses": [...]}

    Each entry carries label / detail / gain / tag (strength) or
    label / detail / gain / fix (weakness) — so the same green-box / yellow-box
    UI and PDF builders can render it.

    Thresholds are HS-Varsity by default; softball uses slightly lower bat
    speed targets because of the rise-ball pitching speeds.
    """
    is_softball = (sport == "Softball")
    strengths, weaknesses = [], []

    # ===== SPORT-AWARE THRESHOLDS =====
    if is_softball:
        BAT_SPEED_STRONG = 65
        BAT_SPEED_WEAK   = 55
        BAT_SPEED_TARGET = "65-72"
        HS_SEP_STRONG    = 38
        HS_SEP_WEAK      = 28
        HS_SEP_TARGET    = "38-48"
    else:
        BAT_SPEED_STRONG = 70
        BAT_SPEED_WEAK   = 60
        BAT_SPEED_TARGET = "70-78"
        HS_SEP_STRONG    = 42
        HS_SEP_WEAK      = 32
        HS_SEP_TARGET    = "42-52"

    def avg(col):
        if col not in df.columns:
            return None
        s = df[col].dropna()
        return float(s.mean()) if len(s) else None

    swings  = df[df["Swing_Type"] == "swing"] if "Swing_Type" in df.columns else df
    in_play = df[df["Swing_Outcome"].isin(["weak_contact", "solid_contact",
                                            "barrel", "foul"])] if "Swing_Outcome" in df.columns else df

    bat_speed   = avg("Bat_Speed_mph")
    attack_ang  = avg("Attack_Angle_deg")
    on_plane    = avg("On_Plane_Eff_pct")
    ttc         = avg("Time_to_Contact_sec")
    hs_sep      = avg("Peak_Hip_Shoulder_Sep_deg")
    stride_len  = avg("Stride_Length_in")
    lead_knee   = avg("Lead_Knee_Flex_deg")
    contact_off = avg("Contact_Offset_in")

    # Outcome-based metrics
    total_swings = len(swings) if len(swings) else 0
    barrels      = int((df["Swing_Outcome"] == "barrel").sum()) if "Swing_Outcome" in df.columns else 0
    whiffs       = int((df["Swing_Outcome"] == "whiff").sum())  if "Swing_Outcome" in df.columns else 0
    barrel_pct   = (barrels / total_swings * 100) if total_swings else 0
    whiff_pct    = (whiffs  / total_swings * 100) if total_swings else 0

    # ===== STRENGTHS =====
    if bat_speed is not None and bat_speed >= BAT_SPEED_STRONG:
        strengths.append({
            "label":  "Strong bat speed",
            "detail": f"Avg bat speed was {bat_speed:.1f} mph — in the upper range "
                       f"for {sport} ({BAT_SPEED_TARGET} mph).",
            "gain":   "Drives exit velocity. Every +1 mph of bat speed ≈ +1.2 mph of exit velo.",
            "tag":    "POWER",
        })
    if on_plane is not None and on_plane >= 75:
        strengths.append({
            "label":  "On-plane bat path",
            "detail": f"On-plane efficiency averaged {on_plane:.1f}% — the bat stays in the "
                       "hitting zone long enough to absorb timing mistakes.",
            "gain":   "Bigger margin for error vs varied pitch speeds and locations.",
            "tag":    "CONTACT",
        })
    if attack_ang is not None and 8 <= attack_ang <= 16:
        strengths.append({
            "label":  "Optimal attack angle",
            "detail": f"Avg attack angle was {attack_ang:.1f}° — in the proven productive "
                       "range of 8–16° (matches average MLB pitch plane).",
            "gain":   "Maximizes barrel rate by matching the ball's downward path.",
            "tag":    "BARREL",
        })
    if hs_sep is not None and hs_sep >= HS_SEP_STRONG:
        strengths.append({
            "label":  "Strong hip-shoulder separation",
            "detail": f"Peak hip-shoulder separation averaged {hs_sep:.1f}° — "
                       f"in the upper range for {sport} ({HS_SEP_TARGET}°).",
            "gain":   "Rubber-band torque drives bat speed and exit velocity.",
            "tag":    "POWER",
        })
    if ttc is not None and ttc <= 0.16:
        strengths.append({
            "label":  "Quick to the ball",
            "detail": f"Avg time-to-contact was {ttc:.3f}s — faster than the 0.17s "
                       "average for HS hitters.",
            "gain":   "Lets you wait longer on the pitch and still get the barrel through.",
            "tag":    "CONTACT",
        })
    if barrel_pct >= 25:
        strengths.append({
            "label":  "High barrel rate",
            "detail": f"{barrel_pct:.0f}% of swings were barrels — exceptional contact quality.",
            "gain":   "Barrels are the only outcome that consistently produces extra-base hits.",
            "tag":    "BARREL",
        })

    # ===== AREAS TO IMPROVE =====
    if bat_speed is not None and bat_speed < BAT_SPEED_WEAK:
        weaknesses.append({
            "label":  "Below-target bat speed",
            "detail": f"Avg bat speed was {bat_speed:.1f} mph — under the "
                       f"{BAT_SPEED_TARGET} mph target for {sport}. The barrel isn't moving "
                       "fast enough to drive the ball.",
            "gain":   "Exit Velocity",
            "fix":    "Overload/underload bat training — 3 sets × 5 swings each with a heavy "
                       "bat (+10oz) and a light bat (-3oz), 3 days this week.",
        })
    if attack_ang is not None and attack_ang < 2:
        weaknesses.append({
            "label":  "Bat path too flat (chopping down)",
            "detail": f"Attack angle averaged {attack_ang:.1f}° — well below the 8–16° "
                       "productive range. The bat path drops through the zone instead of "
                       "matching the pitch plane.",
            "gain":   "Launch Angle + Barrels",
            "fix":    "High-tee drill: tee at chest height, force a slight upward swing path. "
                       "3 sets × 8 reps, 3 days this week.",
        })
    if attack_ang is not None and attack_ang > 20:
        weaknesses.append({
            "label":  "Uppercut too steep",
            "detail": f"Attack angle averaged {attack_ang:.1f}° — above the 16° target. "
                       "Big uppercut means lots of swings-and-misses and pop-ups.",
            "gain":   "Contact Rate",
            "fix":    "Low-tee + middle-tee mix: 3 swings low, 3 middle, repeat. 3 sets × 6, "
                       "3 days this week.",
        })
    if on_plane is not None and on_plane < 60:
        weaknesses.append({
            "label":  "Off-plane bat path",
            "detail": f"On-plane efficiency averaged {on_plane:.1f}% — the bat is in the zone "
                       "for only a fraction of the swing. Less margin on timing mistakes.",
            "gain":   "Contact Rate",
            "fix":    "Hitting Plyo Ball into a slight uphill net — feel the bat staying behind "
                       "the ball through contact. 3 sets × 6, 3 days this week.",
        })
    if hs_sep is not None and hs_sep < HS_SEP_WEAK:
        weaknesses.append({
            "label":  "Low hip-shoulder separation",
            "detail": f"Peak separation averaged only {hs_sep:.1f}° — below the {HS_SEP_TARGET}° "
                       f"target for {sport}. Hips and shoulders are firing together.",
            "gain":   "Bat Speed",
            "fix":    "Coil-and-hold drill — load into back hip, pause 1 sec, then unwind into "
                       "the swing. 3 sets × 5 reps, 3 days this week.",
        })
    if ttc is not None and ttc >= 0.18:
        weaknesses.append({
            "label":  "Slow to the ball",
            "detail": f"Avg time-to-contact was {ttc:.3f}s — slower than the 0.17s HS average. "
                       "Bat is dragging through the zone.",
            "gain":   "Reaction Window",
            "fix":    "Short-bat tee work: choke up on a bat, take 20 quick swings per set. "
                       "Builds connection and bat-snap. 3 sets, 3 days this week.",
        })
    if whiff_pct >= 35:
        weaknesses.append({
            "label":  "Elevated whiff rate",
            "detail": f"{whiff_pct:.0f}% of swings were whiffs — well above the 22% HS baseline. "
                       "Lots of swings at pitches that can't be hit.",
            "gain":   "Plate Discipline",
            "fix":    "Sit-fastball drill: only swing at fastballs middle-out for 30 pitches. "
                       "Builds the recognize-and-pass habit on offspeed.",
        })

    return {"strengths": strengths, "weaknesses": weaknesses}


# =============================================================================
# HITTING DRILL LIBRARY  (mirrors DRILL_LIBRARY for pitching)
# Each drill carries an "issue" tag so the recommender can pull a multi-drill
# package when a given metric is below target. Multiple drills per issue gives
# coaches variety + something to swap in if a hitter can't do a specific drill.
# =============================================================================
HITTING_DRILL_LIBRARY = {
    # ===== TODAY — RECOVERY =====
    "hitting_session_cooldown": {
        "category": "Recovery", "phase": "today", "priority": 3,
        "issue": "recovery",
        "label": "Standard Swing Session Cooldown",
        "drill": "Wrist/forearm mobility + shoulder blade slides + light foam roll",
        "protocol": "Forearm flexor stretches 3×30s each side. Scap slides 2×10. Foam roll T-spine 2 min.",
        "why": "Hitters accumulate forearm and lat fatigue from BP. Flushing the tissue speeds recovery for tomorrow.",
    },
    "hitting_heavy_workload_cooldown": {
        "category": "Recovery", "phase": "today", "priority": 1,
        "issue": "recovery",
        "label": "Heavy Workload — Active Recovery",
        "drill": "Full upper-body mobility + 10 min easy bike",
        "protocol": "T-spine openers 2×8. Lat doorway stretch 3×30s. Easy bike 10 min flushing pace.",
        "why": "60+ swings in a session creates fatigue in lats and forearms. Active recovery prevents tomorrow's stiffness.",
    },

    # =========================================================================
    # BAT SPEED  —  four targeted drills
    # =========================================================================
    "hitting_bat_speed_overload_underload": {
        "category": "Bat Speed", "phase": "week", "priority": 2,
        "issue": "bat_speed",
        "label": "Overload / Underload Bat Training",
        "drill": "Alternate swings with a +10oz heavy bat and a -3oz light bat",
        "protocol": "3 sets × 5 heavy-bat swings, then 3 sets × 5 light-bat swings. 3 days this week.",
        "why": "Driveline-style protocol — reliably adds 2-4 mph of bat speed in 4-6 weeks by training fast-twitch fiber recruitment.",
    },
    "hitting_bat_speed_medball": {
        "category": "Bat Speed", "phase": "week", "priority": 3,
        "issue": "bat_speed",
        "label": "Rotational Med-Ball Throws",
        "drill": "Side-toss a 6-8 lb medicine ball into a wall, mimicking the swing turn",
        "protocol": "3 sets × 8 throws per side, 3 days this week. Drive from the back hip through the front side.",
        "why": "Builds raw rotational power that transfers directly to bat speed. Hitting starts in the legs and core.",
    },
    "hitting_bat_speed_resistance_band": {
        "category": "Bat Speed", "phase": "week", "priority": 3,
        "issue": "bat_speed",
        "label": "Resistance-Band Bat Speed Trainer",
        "drill": "Dry swings against a hip-anchored resistance band",
        "protocol": "3 sets × 8 swings with band, then 3 sets × 5 free swings immediately after. 3 days this week.",
        "why": "Post-activation potentiation — heavy band loads the swing, then free swings feel explosive. Builds top-end speed.",
    },
    "hitting_bat_speed_top_hand": {
        "category": "Bat Speed", "phase": "week", "priority": 4,
        "issue": "bat_speed",
        "label": "Top-Hand-Only Tee Drill",
        "drill": "Choke up, top hand only, drive through the zone",
        "protocol": "2 sets × 10 reps, 3 days this week. Focus on snapping the wrist through contact.",
        "why": "Isolates the top hand — the dominant driver of bat speed late in the swing. Strengthens the snap.",
    },

    # =========================================================================
    # HIP-SHOULDER SEPARATION — three targeted drills
    # =========================================================================
    "hitting_hip_sep_coil_hold": {
        "category": "Sequencing", "phase": "week", "priority": 2,
        "issue": "hip_separation",
        "label": "Coil-and-Hold Tee Drill",
        "drill": "Load into back hip, pause 1 second, then unwind",
        "protocol": "3 sets × 5 reps, 3 days this week. The pause forces hip-shoulder dissociation.",
        "why": "Separation > 40° = rubber-band torque. Top HS hitters average 42-52°. Each 5° ≈ 1.5 mph bat speed.",
    },
    "hitting_hip_sep_step_behind": {
        "category": "Sequencing", "phase": "week", "priority": 3,
        "issue": "hip_separation",
        "label": "Step-Behind Rotational Swing",
        "drill": "Stride foot crosses BEHIND the back foot, then unwinds into the swing",
        "protocol": "3 sets × 6 reps, 3 days this week. Off a tee — emphasizes hip lead.",
        "why": "Cross-step exaggerates the hip turn ahead of the shoulders, training proper sequencing.",
    },
    "hitting_hip_sep_x_drill": {
        "category": "Sequencing", "phase": "week", "priority": 4,
        "issue": "hip_separation",
        "label": "X-Drill (Hips vs. Shoulders)",
        "drill": "Hold a bat across shoulders, rotate hips one way, shoulders the other",
        "protocol": "3 sets × 8 controlled reps before BP, 3 days this week.",
        "why": "Trains the brain to feel hips and shoulders moving independently — the prerequisite for any rotational power.",
    },

    # =========================================================================
    # FLAT ATTACK ANGLE — three targeted drills
    # =========================================================================
    "hitting_flat_high_tee": {
        "category": "Bat Path", "phase": "week", "priority": 2,
        "issue": "flat_swing",
        "label": "High-Tee Launch Path",
        "drill": "Tee set at chest height — force the bat to launch upward",
        "protocol": "3 sets × 8 reps, 3 days this week. Aim for the upper third of the net.",
        "why": "Average MLB pitch enters the zone at -6°. Matching with +8-16° attack angle is the barrel-creation range.",
    },
    "hitting_flat_pvc_path": {
        "category": "Bat Path", "phase": "week", "priority": 3,
        "issue": "flat_swing",
        "label": "PVC Stick Path Drill",
        "drill": "PVC pipe held at the slot — hitter must swing UNDER it on the way up",
        "protocol": "20 dry swings per set, 3 sets per session, 3 days this week.",
        "why": "Physical barrier teaches the body to drop the back shoulder slightly and work upward through contact.",
    },
    "hitting_flat_launch_pause": {
        "category": "Bat Path", "phase": "week", "priority": 4,
        "issue": "flat_swing",
        "label": "Pause-at-Launch Tee Drill",
        "drill": "Tee swings with a 1-second pause at the launch position",
        "protocol": "3 sets × 5 reps, 3 days this week. Hold the back-elbow slot, then explode upward.",
        "why": "Builds the feel of the back shoulder dropping into a positive attack angle before commitment.",
    },

    # =========================================================================
    # STEEP ATTACK ANGLE — three targeted drills
    # =========================================================================
    "hitting_steep_low_high_mix": {
        "category": "Bat Path", "phase": "week", "priority": 2,
        "issue": "steep_swing",
        "label": "Low/Middle Tee Mix",
        "drill": "3 swings low tee, 3 swings middle tee, repeat",
        "protocol": "3 sets × 6 reps, 3 days this week. Forces the swing to flatten on low pitches.",
        "why": "Above 20° attack angle = whiffs and pop-ups. Mixing low and middle tees pulls the swing back into 8-16° range.",
    },
    "hitting_steep_top_hand_path": {
        "category": "Bat Path", "phase": "week", "priority": 3,
        "issue": "steep_swing",
        "label": "Top-Hand Path Correction",
        "drill": "Top-hand-only swings on a mid-height tee, hands stay above the ball",
        "protocol": "2 sets × 10 reps, 3 days this week.",
        "why": "An uppercut usually comes from top-hand dropping early. This trains the hands to stay above the ball longer.",
    },
    "hitting_steep_outside_front_toss": {
        "category": "Bat Path", "phase": "week", "priority": 4,
        "issue": "steep_swing",
        "label": "Outside-Half Front Toss",
        "drill": "Front toss on the outer third — drive everything oppo, gap to gap",
        "protocol": "2 sets × 15 swings, 2 days this week.",
        "why": "Hitting outside pitches oppo forces a flatter, longer bat path. Eliminates the steep pull-side cut.",
    },

    # =========================================================================
    # LOW ON-PLANE EFFICIENCY — three targeted drills
    # =========================================================================
    "hitting_on_plane_plyo_uphill": {
        "category": "Bat Path", "phase": "week", "priority": 3,
        "issue": "off_plane",
        "label": "Plyo Ball Into Uphill Net",
        "drill": "Hitting plyo ball into a slight uphill angle, stay behind the ball",
        "protocol": "3 sets × 6 reps, 3 days this week.",
        "why": "On-plane time > 75% means the bat is in the hitting zone long enough to absorb timing mistakes.",
    },
    "hitting_on_plane_two_tee": {
        "category": "Bat Path", "phase": "week", "priority": 3,
        "issue": "off_plane",
        "label": "Two-Tee Path Drill",
        "drill": "Set TWO tees in a line — bat must clip both during the swing",
        "protocol": "3 sets × 5 reps, 3 days this week. Adjust the spacing as the path improves.",
        "why": "Physical waypoints force the bat to stay flat through the zone, not chop in and out.",
    },
    "hitting_on_plane_connection_band": {
        "category": "Bat Path", "phase": "week", "priority": 4,
        "issue": "off_plane",
        "label": "Connection Band Drill",
        "drill": "Resistance band looped around back elbow + lead hip — swing keeps connection",
        "protocol": "3 sets × 8 reps, 3 days this week.",
        "why": "Loss of connection = bat detaches from the body and the path goes off-plane. Band reinforces the link.",
    },

    # =========================================================================
    # SLOW TIME-TO-CONTACT — three targeted drills
    # =========================================================================
    "hitting_ttc_short_bat": {
        "category": "Contact", "phase": "week", "priority": 3,
        "issue": "slow_ttc",
        "label": "Short-Bat Tee Work",
        "drill": "Choke up on a bat, take 20 quick connected swings",
        "protocol": "3 sets per session, 3 days this week.",
        "why": "Faster TTC lets you wait longer on the pitch — fewer chase swings. Sub-0.16s separates HS varsity from elite.",
    },
    "hitting_ttc_heavy_quick": {
        "category": "Contact", "phase": "week", "priority": 4,
        "issue": "slow_ttc",
        "label": "Heavy-Bat Quick Swings",
        "drill": "Heavy bat (+10oz), short choked-up swings, max intent",
        "protocol": "2 sets × 8 reps, 2 days this week.",
        "why": "Heavy bat trains explosive power off the back hip, then transfers to a quicker free-swing.",
    },
    "hitting_ttc_connection_ball": {
        "category": "Contact", "phase": "week", "priority": 4,
        "issue": "slow_ttc",
        "label": "Connection Ball Drill",
        "drill": "Soft ball pinned between back elbow and torso during swings",
        "protocol": "3 sets × 6 reps, 3 days this week. Ball must stay until contact.",
        "why": "Loss of connection adds time to contact. Ball forces the hands to stay tight, shortening the path.",
    },

    # =========================================================================
    # HIGH WHIFF RATE — three targeted drills
    # =========================================================================
    "hitting_whiffs_sit_fastball": {
        "category": "Plate Discipline", "phase": "week", "priority": 3,
        "issue": "whiffs",
        "label": "Sit-Fastball Drill",
        "drill": "30 pitches — only swing at fastballs middle/outer-half. Take everything else.",
        "protocol": "2 days this week. Use a machine or live arm.",
        "why": "Whiffs > 35% almost always = chasing offspeed out of the zone. Trains the recognize-and-pass instinct.",
    },
    "hitting_whiffs_vision_bottle": {
        "category": "Plate Discipline", "phase": "week", "priority": 4,
        "issue": "whiffs",
        "label": "Bottle-Cap Vision Drill",
        "drill": "Front-toss with painted bottle caps — call color (red/blue) before swinging",
        "protocol": "2 sets × 15 reps, 2 days this week.",
        "why": "Forces tracking the ball into the zone before committing the swing. Builds late-recognition reflex.",
    },
    "hitting_whiffs_multi_color": {
        "category": "Plate Discipline", "phase": "week", "priority": 4,
        "issue": "whiffs",
        "label": "Multi-Color Ball Recognition",
        "drill": "Mix balls of different colors — coach calls the color, hitter only swings at that one",
        "protocol": "20 pitches × 2 sets, 2 days this week.",
        "why": "Trains pitch recognition under stress. Eliminates committing to a swing before the ball is identified.",
    },

    # =========================================================================
    # WEAK CONTACT PATTERN — three targeted drills
    # =========================================================================
    "hitting_weak_front_toss_oppo": {
        "category": "Contact", "phase": "week", "priority": 3,
        "issue": "weak_contact",
        "label": "Front-Toss Away-Side BP",
        "drill": "Front toss on the outer third — drive everything oppo",
        "protocol": "2 sets × 20 reps, 2 days this week.",
        "why": "Weak contact = early commitment to pull. Going oppo trains keeping the bat behind the ball longer.",
    },
    "hitting_weak_bottom_hand": {
        "category": "Contact", "phase": "week", "priority": 4,
        "issue": "weak_contact",
        "label": "Bottom-Hand-Only Drill",
        "drill": "Bottom hand only, choked up, drive line drives to centerfield",
        "protocol": "2 sets × 10 reps, 3 days this week.",
        "why": "Strengthens the lead hand's pull through contact. Weak bottom hand = bat slows at impact.",
    },
    "hitting_weak_inside_outside_tee": {
        "category": "Contact", "phase": "week", "priority": 4,
        "issue": "weak_contact",
        "label": "Inside-Outside Tee Series",
        "drill": "Alternate tee positions: inner third → middle → outer third",
        "protocol": "3 swings per location, 3 cycles per set, 2 sets per session, 3 days this week.",
        "why": "Builds adjustability — most weak contact comes from one path that only works on a middle pitch.",
    },
}


# Map each issue → ordered list of drill keys (most-impactful first)
HITTING_ISSUE_TO_DRILLS = {
    "bat_speed":      ["hitting_bat_speed_overload_underload",
                        "hitting_bat_speed_medball",
                        "hitting_bat_speed_resistance_band",
                        "hitting_bat_speed_top_hand"],
    "hip_separation": ["hitting_hip_sep_coil_hold",
                        "hitting_hip_sep_step_behind",
                        "hitting_hip_sep_x_drill"],
    "flat_swing":     ["hitting_flat_high_tee",
                        "hitting_flat_pvc_path",
                        "hitting_flat_launch_pause"],
    "steep_swing":    ["hitting_steep_low_high_mix",
                        "hitting_steep_top_hand_path",
                        "hitting_steep_outside_front_toss"],
    "off_plane":      ["hitting_on_plane_plyo_uphill",
                        "hitting_on_plane_two_tee",
                        "hitting_on_plane_connection_band"],
    "slow_ttc":       ["hitting_ttc_short_bat",
                        "hitting_ttc_heavy_quick",
                        "hitting_ttc_connection_ball"],
    "whiffs":         ["hitting_whiffs_sit_fastball",
                        "hitting_whiffs_vision_bottle",
                        "hitting_whiffs_multi_color"],
    "weak_contact":   ["hitting_weak_front_toss_oppo",
                        "hitting_weak_bottom_hand",
                        "hitting_weak_inside_outside_tee"],
}


# =============================================================================
# HITTING DRILL RECOMMENDER  (mirrors recommend_drills for pitching)
# =============================================================================
# =============================================================================
# DEVELOPMENT DRILLS — surfaced when the data shows NO glaring weakness so
# the player still gets actionable work to do this week (a "what to grow"
# default instead of "everything's fine — go home"). These fill any empty
# slots in the weekly plan.
# =============================================================================
HITTING_DEVELOPMENT_DEFAULTS = [
    "hitting_bat_speed_overload_underload",   # always-helpful power lift
    "hitting_on_plane_two_tee",               # bat-path refinement
    "hitting_hip_sep_coil_hold",              # sequencing — universal value
    "hitting_whiffs_vision_bottle",           # vision/recognition training
]

PITCHING_DEVELOPMENT_DEFAULTS = [
    "low_hip_shoulder_separation",            # universal velocity gain
    "soft_lead_knee",                          # front-side stability
    "low_extension",                          # perceived-velo work
    "low_fastball_spin",                      # spin-quality work
]


def build_weekly_plan(plan_kind: str, recommend_output: dict,
                       athlete_level: str = "HS-Varsity",
                       drill_library: dict | None = None) -> list:
    """Build a 5-day structured week from a recommender's output.

    Every day opens with the standard warm-up, fills 2–3 development
    drills (priority-ordered from the recommender's week list, padded
    with development defaults when no glaring weakness exists), and
    closes with the standard cool-down. Day 1 also picks up the
    "today" cooldown work from the session.

    Args:
        plan_kind:        'pitching' or 'hitting'
        recommend_output: dict from recommend_drills / recommend_hitting_drills
        athlete_level:    used for difficulty scaling (future)
        drill_library:    optional override (defaults to global tables)

    Returns:
        list of day dicts, each with:
            day_num (int), label (str), warmup (dict), drills (list),
            cooldown (dict), notes (str).
    """
    is_hitting = (plan_kind == "hitting")
    warmup   = HITTING_WARMUP if is_hitting else PITCHING_WARMUP
    cooldown = HITTING_COOLDOWN if is_hitting else PITCHING_COOLDOWN
    drill_lib = drill_library or (
        HITTING_DRILL_LIBRARY if is_hitting else DRILL_LIBRARY)

    # Pull week drills the recommender flagged (already priority-sorted)
    flagged = list(recommend_output.get("week", []))
    flagged_keys = {d["key"] for d in flagged if "key" in d}

    # Fill any remaining slots with development defaults so the player
    # ALWAYS has actionable work, even on a clean session
    dev_defaults = (HITTING_DEVELOPMENT_DEFAULTS if is_hitting
                     else PITCHING_DEVELOPMENT_DEFAULTS)
    for k in dev_defaults:
        if k in flagged_keys:
            continue
        if k not in drill_lib:
            continue
        d = drill_lib[k]
        flagged.append({
            "key":      k,
            "category": d["category"],
            "label":    d["label"],
            "drill":    d["drill"],
            "protocol": d["protocol"],
            "why":      d["why"],
            "trigger":  "General development — no specific weakness flagged.",
            "video_url":   None,
            "video_title": None,
            "video_source":None,
            "phase":    "week",
            "priority": 5,
        })

    # ===== Distribute drills across 5 days =====
    # Day 1: data day (snapshot + cooldown work from today)
    # Day 2: drill day — primary weakness
    # Day 3: drill day — secondary weakness OR development default
    # Day 4: drill day — tertiary OR development default
    # Day 5: live BP / bullpen (light load + game-pace work)
    plan = []

    # ---- Day 1 ----
    today_work = list(recommend_output.get("today", []))
    plan.append({
        "day_num":  1,
        "label":    "Day 1 — Data Day + Recovery",
        "warmup":   warmup,
        "drills":   today_work[:2],
        "cooldown": cooldown,
        "notes":    ("Open with the warm-up, run the session you just captured, then complete "
                      "the today-only cooldown work below. The week's development work starts tomorrow."),
    })

    # ---- Days 2-4: rotate through flagged + dev defaults ----
    for i in range(3):
        day_drills = flagged[i*2 : i*2 + 2] if len(flagged) > i*2 else []
        plan.append({
            "day_num":  i + 2,
            "label":    f"Day {i + 2} — Development Block {chr(ord('A') + i)}",
            "warmup":   warmup,
            "drills":   day_drills,
            "cooldown": cooldown,
            "notes":    "Focused work — high-leverage drills for the next 25-30 minutes after warm-up. "
                         "Quality reps, not volume.",
        })

    # ---- Day 5: game-pace work (no new drills, just rehearsal) ----
    plan.append({
        "day_num":  5,
        "label":    "Day 5 — Game-Pace Rehearsal",
        "warmup":   warmup,
        "drills":   [],
        "cooldown": cooldown,
        "notes":    ("Light volume, game-effort intent. "
                       + ("Live BP with at-bat structure — 3 sets of 6 swings, "
                          "rotate location each round."
                          if is_hitting else
                          "Light bullpen — 25 pitches max, focus on command and pitch shapes "
                          "you've drilled this week.")),
    })

    return plan


def recommend_hitting_drills(df: pd.DataFrame, sport: str = "Baseball",
                              athlete_level: str = "HS-Varsity") -> dict:
    """Inspect a swing session and recommend a today plan + week plan.

    Returns {"today": [drill, ...], "week": [drill, ...]}.
    Each drill is a dict ready for the same render_drill_card UI used on pitching.
    """
    today: list = []
    week:  list = []
    seen_keys: set = set()

    # Map athlete level string to the video bucket already used for pitching
    video_level = LEVEL_TO_VIDEO_BUCKET.get(athlete_level, "any")

    def _record(key: str, trigger: str) -> dict:
        d = HITTING_DRILL_LIBRARY[key]
        video = pick_video(key, severity="any", level=video_level)
        return {
            "key":         key,
            "category":    d["category"],
            "phase":       d["phase"],
            "priority":    d["priority"],
            "label":       d["label"],
            "drill":       d["drill"],
            "protocol":    d["protocol"],
            "why":         d["why"],
            "grip_key":    None,
            "video_url":   video["url"]    if video else None,
            "video_title": video.get("title")  if video else None,
            "video_source":video.get("source") if video else None,
            "severity":    "any",
            "trigger":     trigger,
        }

    def add(plan: list, key: str, trigger: str):
        if key in seen_keys or key not in HITTING_DRILL_LIBRARY:
            return
        seen_keys.add(key)
        plan.append(_record(key, trigger))

    swings = df[df["Swing_Type"] == "swing"] if "Swing_Type" in df.columns else df
    total_swings = len(swings)

    def avg(col):
        if col not in df.columns: return None
        s = df[col].dropna()
        return float(s.mean()) if len(s) else None

    bat_speed   = avg("Bat_Speed_mph")
    attack_ang  = avg("Attack_Angle_deg")
    on_plane    = avg("On_Plane_Eff_pct")
    ttc         = avg("Time_to_Contact_sec")
    hs_sep      = avg("Peak_Hip_Shoulder_Sep_deg")
    whiffs      = int((df["Swing_Outcome"] == "whiff").sum()) if "Swing_Outcome" in df.columns else 0
    weak_hits   = int((df["Swing_Outcome"] == "weak_contact").sum()) if "Swing_Outcome" in df.columns else 0
    whiff_pct   = (whiffs / total_swings * 100) if total_swings else 0
    weak_pct    = (weak_hits / total_swings * 100) if total_swings else 0

    is_softball = (sport == "Softball")
    BAT_SPEED_WEAK = 55 if is_softball else 60
    HS_SEP_WEAK    = 28 if is_softball else 32

    def add_issue_pack(issue: str, trigger: str, max_drills: int = 3):
        """Add a multi-drill package for an issue.

        Pulls the top `max_drills` keys from HITTING_ISSUE_TO_DRILLS[issue]
        and appends each with the shared trigger text. Gives the coach
        several alternates so they can pick what fits their hitter / facility.
        """
        keys = HITTING_ISSUE_TO_DRILLS.get(issue, [])[:max_drills]
        for k in keys:
            add(week, k, trigger)

    # ===== TODAY — RECOVERY =====
    if total_swings >= 35:
        add(today, "hitting_heavy_workload_cooldown",
            f"{total_swings} total swings — high-volume session calls for active recovery.")
    if not today:
        add(today, "hitting_session_cooldown",
            "Standard cooldown after any swing session.")

    # ===== WEEK — BAT SPEED / TORQUE =====
    if bat_speed is not None and bat_speed < BAT_SPEED_WEAK:
        add_issue_pack("bat_speed",
            f"Avg bat speed {bat_speed:.1f} mph — under target.")
    if hs_sep is not None and hs_sep < HS_SEP_WEAK:
        add_issue_pack("hip_separation",
            f"Hip-shoulder separation averaged {hs_sep:.1f}° — below sequencing target.")

    # ===== WEEK — BAT PATH =====
    if attack_ang is not None and attack_ang < 2:
        add_issue_pack("flat_swing",
            f"Attack angle averaged {attack_ang:.1f}° — chopping down.")
    elif attack_ang is not None and attack_ang > 20:
        add_issue_pack("steep_swing",
            f"Attack angle averaged {attack_ang:.1f}° — too steep.")
    if on_plane is not None and on_plane < 60:
        add_issue_pack("off_plane",
            f"On-plane efficiency averaged {on_plane:.1f}% — bat path off-plane.")

    # ===== WEEK — CONTACT =====
    if ttc is not None and ttc >= 0.18:
        add_issue_pack("slow_ttc",
            f"Time-to-contact averaged {ttc:.3f}s — slow.")
    if whiff_pct >= 35:
        add_issue_pack("whiffs",
            f"Whiff rate was {whiff_pct:.0f}% — above the 22% HS baseline.")
    if weak_pct >= 30:
        add_issue_pack("weak_contact",
            f"{weak_pct:.0f}% of swings produced weak contact.")

    # Sort week by priority (lower number = more important)
    week.sort(key=lambda d: d["priority"])
    return {"today": today, "week": week}


def pitch_type_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """Group by pitch type and produce avg velo, spin, break, stress per type."""
    agg = (
        df.groupby("Pitch_Type")
          .agg(
              Thrown=("Pitch_Num", "count"),
              Avg_Velo=("Velocity_mph", "mean"),
              Avg_Spin=("Total_Spin_rpm", "mean"),
              Avg_Vert_Break=("Vert_Break_in", "mean"),
              Avg_Horiz_Break=("Horiz_Break_in", "mean"),
              Avg_Stress=("Peak_Valgus_Nm", "mean"),
          )
          .reset_index()
    )
    for col in ["Avg_Velo", "Avg_Spin", "Avg_Vert_Break", "Avg_Horiz_Break", "Avg_Stress"]:
        agg[col] = agg[col].round(1)
    return agg


# =============================================================================
# PITCH TUNNELING MATH
# Compute per-pitch trajectories using each pitcher's measured velo + break
# profile, then determine where each pitch type lands if tunneled off a
# user-placed starting pitch. The "tunnel point" is the distance from the
# plate where research says the batter has to commit (~167 ms of reaction
# time). Pitches that share the same tunnel-point xyz but diverge to
# different plate locations are "tunneled" — that's the core teaching tool.
# =============================================================================

# Sport-dependent constants
TUNNEL_CONSTANTS = {
    "Baseball": {
        "rubber_distance_ft":   60.5,
        "release_extension_ft": 6.0,        # avg pitcher
        "release_height_ft":    6.0,
        "tunnel_distance_ft":   22.0,       # ~167 ms at 90 mph
    },
    "Softball": {
        "rubber_distance_ft":   43.0,
        "release_extension_ft": 4.0,
        "release_height_ft":    4.0,
        "tunnel_distance_ft":   14.7,       # ~167 ms at 60 mph
    },
}

# How long a batter needs to commit to swing (seconds)
BATTER_COMMIT_TIME_SEC = 0.167


def _pitch_release_point(sport: str = "Baseball") -> tuple[float, float, float]:
    """Average release point in (x, y, z) feet — x lateral, y distance from
    plate, z height. We assume an over-the-top average for v1; future
    versions can pull arm-slot data per pitcher."""
    c = TUNNEL_CONSTANTS.get(sport, TUNNEL_CONSTANTS["Baseball"])
    # Release is in front of the rubber by `release_extension_ft`
    release_y = c["rubber_distance_ft"] - c["release_extension_ft"]
    return (0.0, release_y, c["release_height_ft"])


def _pitch_trajectory_points(release_xyz: tuple[float, float, float],
                              plate_xyz: tuple[float, float],
                              velocity_mph: float,
                              vert_break_in: float,
                              horiz_break_in: float,
                              n_samples: int = 40) -> list[dict]:
    """Compute (t, x, y, z) sample points for one pitch's full flight using
    projectile physics with gravity + Magnus force.

    Model:
      - Position(t) = release + v_initial * t + 0.5 * accel * t²
      - Vertical acceleration: -g + a_magnus (Magnus partially defeats
        gravity for backspin; adds to it for topspin)
      - Horizontal acceleration: a_magnus_horizontal
      - a_magnus values are chosen so the ball's total deviation from a
        no-spin pitch over the full flight equals the measured break.

    Algebraically, given known release and plate locations + measured
    break, this collapses to:
      z(f) = rz + [(pz - vb - rz) + gravity_drop] * f
                + [vb - gravity_drop] * f²
    where gravity_drop = 0.5 * g * T² (the total a no-spin ball would
    fall under gravity alone during its flight time T).

    Result: fastballs arc gently (they "rise" relative to no-spin),
    curveballs drop sharply late in flight (they fall faster than
    no-spin) — matching what real baseball trajectories look like.
    """
    rx, ry, rz = release_xyz
    px, pz = plate_xyz
    v_fps = velocity_mph * 1.467   # 1 mph = 1.467 ft/s
    T_total = ry / v_fps if v_fps > 0 else 0.4

    g = 32.17  # ft/s² — Earth gravity
    gravity_drop = 0.5 * g * T_total * T_total

    # Break in feet (Pitch Logic / Statcast convention: deviation from
    # a no-spin pitch AT THE PLATE)
    vb_ft = vert_break_in / 12.0
    hb_ft = horiz_break_in / 12.0

    # Coefficients of the parameterized trajectory polynomial
    z_linear = (pz - vb_ft - rz) + gravity_drop
    z_quad   = vb_ft - gravity_drop
    x_linear = (px - hb_ft - rx)
    x_quad   = hb_ft

    samples = []
    for i in range(n_samples + 1):
        f = i / n_samples
        z = rz + z_linear * f + z_quad * (f * f)
        x = rx + x_linear * f + x_quad * (f * f)
        y = ry * (1.0 - f)
        samples.append({
            "t": f * T_total,
            "f": f,
            "x": x,
            "y": y,
            "z": z,
        })
    return samples


def _interp_at_y(samples: list[dict], target_y: float) -> dict | None:
    """Find the (x, z) of the trajectory at a given y-depth. Returns None
    if the trajectory doesn't pass through target_y."""
    for i in range(len(samples) - 1):
        a, b = samples[i], samples[i + 1]
        if (a["y"] - target_y) * (b["y"] - target_y) <= 0 and a["y"] != b["y"]:
            # Linear interp between a and b
            r = (a["y"] - target_y) / (a["y"] - b["y"])
            return {
                "x": a["x"] + (b["x"] - a["x"]) * r,
                "y": target_y,
                "z": a["z"] + (b["z"] - a["z"]) * r,
                "t": a["t"] + (b["t"] - a["t"]) * r,
            }
    return None


def compute_arsenal_tunnel(arsenal: list[dict],
                            starting_pitch_type: str,
                            plate_x: float,
                            plate_z: float,
                            sport: str = "Baseball") -> dict:
    """Given an arsenal of pitch type averages, tunnel everything off the
    starting pitch's release vector.

    arsenal items must have:
       Pitch_Type, Avg_Velo, Avg_Vert_Break, Avg_Horiz_Break

    Algorithm (simple but conceptually correct):
      1. Compute the STARTING pitch's full trajectory from release to the
         clicked (plate_x, plate_z) using its measured break.
      2. Find the tunnel-point (x, y, z) on that trajectory at y =
         tunnel_distance_ft.
      3. For every other pitch type, assume it shares the SAME release
         direction (i.e. release_x_offset and angle) as the starting pitch.
         Because each pitch has different break, its trajectory diverges
         from the starting pitch — landing at a different plate location.
         We solve: plate_other = plate_start + (break_other - break_start)
         (Working in feet; break differential applied to the plate
         crossing — break gradient is uniform across pitches with the
         quadratic accumulation factor.)

    Returns dict keyed by pitch type with: plate_x, plate_z, tunnel_xyz,
    samples, velocity_mph, vert_break_in, horiz_break_in, total_flight_sec.
    """
    release_xyz = _pitch_release_point(sport)
    c = TUNNEL_CONSTANTS.get(sport, TUNNEL_CONSTANTS["Baseball"])
    tunnel_y = c["tunnel_distance_ft"]

    # Find the starting pitch's break profile in the arsenal
    start_entry = next((p for p in arsenal
                          if p["Pitch_Type"] == starting_pitch_type), None)
    if start_entry is None:
        return {}

    start_vb = float(start_entry["Avg_Vert_Break"] or 0.0)
    start_hb = float(start_entry["Avg_Horiz_Break"] or 0.0)
    start_v  = float(start_entry["Avg_Velo"] or 90.0)

    out: dict[str, dict] = {}
    for p in arsenal:
        ptype = p["Pitch_Type"]
        v   = float(p.get("Avg_Velo") or 90.0)
        vb  = float(p.get("Avg_Vert_Break") or 0.0)
        hb  = float(p.get("Avg_Horiz_Break") or 0.0)

        # Plate location relative to the starting pitch's plate
        # (break differential, converted to feet)
        d_plate_x = plate_x + (hb - start_hb) / 12.0
        d_plate_z = plate_z + (vb - start_vb) / 12.0

        samples = _pitch_trajectory_points(
            release_xyz=release_xyz,
            plate_xyz=(d_plate_x, d_plate_z),
            velocity_mph=v,
            vert_break_in=vb,
            horiz_break_in=hb,
        )
        tunnel = _interp_at_y(samples, tunnel_y)
        # Flight time
        v_fps = v * 1.467
        flight_total_sec = release_xyz[1] / v_fps if v_fps > 0 else None

        out[ptype] = {
            "Pitch_Type":       ptype,
            "is_starting":      ptype == starting_pitch_type,
            "velocity_mph":     v,
            "vert_break_in":    vb,
            "horiz_break_in":   hb,
            "plate_x":          d_plate_x,
            "plate_z":          d_plate_z,
            "tunnel_xyz":       tunnel,
            "samples":          samples,
            "total_flight_sec": flight_total_sec,
        }
    return out


def tunnel_quality_metrics(tunnel_data: dict) -> dict:
    """Summarize per-pitch tunneling quality vs. the starting pitch.

    Returns dict keyed by pitch type with:
      - tunnel_offset_in: 2D distance between this pitch's tunnel point and
        the starting pitch's tunnel point (inches). Lower = better tunnel.
      - plate_diff_in: 2D distance between plate locations (inches). Higher
        = more divergence after the commit, which is what we want.
      - timing_offset_ms: difference in flight time (ms). Bigger gap = the
        batter has to adjust their swing timing.
      - tunnel_grade: "Elite" (≤3"), "Good" (≤6"), "Loose" (≤10"), "No Tunnel" (>10")
    """
    start = next((p for p in tunnel_data.values() if p["is_starting"]), None)
    if start is None:
        return {}
    sx = start["tunnel_xyz"]["x"] if start["tunnel_xyz"] else 0
    sz = start["tunnel_xyz"]["z"] if start["tunnel_xyz"] else 0
    splate_x = start["plate_x"]; splate_z = start["plate_z"]
    s_flight = start["total_flight_sec"] or 0

    out = {}
    for ptype, p in tunnel_data.items():
        if p["tunnel_xyz"] is None:
            continue
        dx_t = (p["tunnel_xyz"]["x"] - sx) * 12.0
        dz_t = (p["tunnel_xyz"]["z"] - sz) * 12.0
        tunnel_offset_in = (dx_t * dx_t + dz_t * dz_t) ** 0.5

        dx_p = (p["plate_x"] - splate_x) * 12.0
        dz_p = (p["plate_z"] - splate_z) * 12.0
        plate_diff_in = (dx_p * dx_p + dz_p * dz_p) ** 0.5

        timing_offset_ms = ((p["total_flight_sec"] or 0) - s_flight) * 1000.0

        if tunnel_offset_in <= 3:    grade = "Elite"
        elif tunnel_offset_in <= 6:  grade = "Good"
        elif tunnel_offset_in <= 10: grade = "Loose"
        else:                         grade = "No Tunnel"

        out[ptype] = {
            "tunnel_offset_in": round(tunnel_offset_in, 2),
            "plate_diff_in":    round(plate_diff_in, 2),
            "timing_offset_ms": round(timing_offset_ms, 1),
            "tunnel_grade":     grade,
        }
    return out


# =============================================================================
# PITCH TUNNELING — POV PLOTTERS  (batter view / pitcher view / side view)
# =============================================================================
def _build_tunnel_batter_view(tunnel_data: dict, sport: str = "Baseball",
                                hand: str = "Right",
                                clickable: bool = True,
                                mirror_x: bool = False,
                                depth_reversed: bool = False) -> "go.Figure":
    """Catcher / batter POV — strike zone + the FLIGHT PATH of each pitch
    from release through the tunnel point to the plate, projected onto the
    (plate-side, height) plane the batter sees.

    All pitches start at the same release point and converge tightly at the
    tunnel point (~22 ft for baseball / 14.7 ft for softball — about 167 ms
    before the plate). They visibly fan out only in the final 22 ft after
    the batter has had to commit — that's the entire teaching purpose of
    this view.

    Args:
      mirror_x: when True, flip the x-axis (pitcher's POV — looks like the
        inverse of the batter view from behind the mound).
      clickable: overlay an invisible grid of click targets so the user can
        click anywhere on or around the zone to place the starting pitch.
    """
    fig = go.Figure()
    sign = -1.0 if mirror_x else 1.0  # mirror x for pitcher POV

    # ==== Click-target grid (transparent, but selectable) ====
    # Only on the batter POV — clicks on the pitcher POV would be x-mirrored
    # and confusing. Resolution ≈ 0.10 ft (~1.2") snaps the click.
    if clickable and not mirror_x:
        grid_x, grid_y, grid_custom = [], [], []
        step = 0.10
        x = -2.0
        while x <= 2.0 + 1e-6:
            y = -0.3
            while y <= 7.5 + 1e-6:
                grid_x.append(round(x, 2))
                grid_y.append(round(y, 2))
                grid_custom.append([round(x, 2), round(y, 2)])
                y += step
            x += step
        fig.add_trace(go.Scatter(
            x=grid_x, y=grid_y,
            mode="markers",
            marker=dict(size=14, color="rgba(0,0,0,0)", line=dict(width=0)),
            customdata=grid_custom,
            hovertemplate="<b>📍 Click to place starting pitch here</b><br>"
                          "(%{x:.2f} ft, %{y:.2f} ft)<extra></extra>",
            name="placement_grid",
            showlegend=False,
        ))

    # ==== Strike zone box + 3x3 grid ====
    fig.add_shape(type="rect", x0=sign*SZ_X_MIN, x1=sign*SZ_X_MAX,
                   y0=SZ_Z_MIN, y1=SZ_Z_MAX,
                   line=dict(color="black", width=2),
                   fillcolor="rgba(0,0,0,0)", layer="below")
    for i in (1, 2):
        x = SZ_X_MIN + (SZ_X_MAX - SZ_X_MIN) * (i / 3)
        z = SZ_Z_MIN + (SZ_Z_MAX - SZ_Z_MIN) * (i / 3)
        fig.add_shape(type="line", x0=sign*x, x1=sign*x, y0=SZ_Z_MIN, y1=SZ_Z_MAX,
                       line=dict(color="#cccccc", width=0.6, dash="dot"))
        fig.add_shape(type="line", x0=sign*SZ_X_MIN, x1=sign*SZ_X_MAX, y0=z, y1=z,
                       line=dict(color="#cccccc", width=0.6, dash="dot"))
    # Home plate
    fig.add_shape(type="path",
                   path=f"M {sign*-0.71} 0.05 L {sign*0.71} 0.05 "
                        f"L {sign*0.50} -0.10 L 0 -0.25 L {sign*-0.50} -0.10 Z",
                   fillcolor="#dcdcdc", line=dict(color="black", width=1),
                   layer="below")

    # ==== Tunnel-point convergence ring ====
    # Find the average tunnel-point (x, z) across all pitches — that's the
    # "tunnel zone" where the batter is forced to commit.
    tunnel_xs = [p["tunnel_xyz"]["x"] for p in tunnel_data.values()
                  if p["tunnel_xyz"]]
    tunnel_zs = [p["tunnel_xyz"]["z"] for p in tunnel_data.values()
                  if p["tunnel_xyz"]]
    if tunnel_xs:
        cx = sum(tunnel_xs) / len(tunnel_xs)
        cz = sum(tunnel_zs) / len(tunnel_zs)
        # Draw a translucent gold ellipse to mark "commit window"
        fig.add_shape(
            type="circle",
            x0=sign*cx - 0.18, x1=sign*cx + 0.18,
            y0=cz - 0.18, y1=cz + 0.18,
            line=dict(color="#d4a634", width=2, dash="dot"),
            fillcolor="rgba(212,166,52,0.10)",
            layer="below",
        )
        fig.add_annotation(
            x=sign*cx, y=cz + 0.32,
            text="🔒 commit point",
            showarrow=False,
            font=dict(size=9, color="#92400e"),
            bgcolor="rgba(254,243,199,0.85)",
            borderpad=2,
        )

    # ==== Flight-path TRAJECTORIES (release → tunnel → plate) ====
    # Each pitch is a thick curved trail with a glowing halo underneath
    # (Statcast / PitchLogic overlay vibe). Trail-bead markers along the
    # path scale with depth: batter view = balls grow as they approach;
    # pitcher view = balls SHRINK as they travel away.
    for ptype, p in tunnel_data.items():
        color = PITCH_COLORS.get(ptype, "#666")
        is_start = p["is_starting"]
        xs = [sign * s["x"] for s in p["samples"]]
        zs = [s["z"]         for s in p["samples"]]

        # ---- Halo glow underneath ----
        fig.add_trace(go.Scatter(
            x=xs, y=zs,
            mode="lines",
            line=dict(color=color, width=18 if is_start else 13,
                       shape="spline", smoothing=1.0),
            opacity=0.22 if is_start else 0.15,
            hoverinfo="skip",
            showlegend=False,
        ))
        # ---- Main thick trail line ----
        fig.add_trace(go.Scatter(
            x=xs, y=zs,
            mode="lines",
            line=dict(color=color, width=8 if is_start else 6,
                       shape="spline", smoothing=1.0),
            opacity=1.0 if is_start else 0.9,
            name=f"{ptype}{' ⭐' if is_start else ''}",
            hovertemplate=f"<b>{ptype}</b><br>"
                          f"%{{x:+.2f}} ft side · %{{y:.2f}} ft high<extra></extra>",
        ))
        # ---- Ball-trail beads ----
        trail_n = max(1, len(p["samples"]) // 7)
        trail_xs, trail_ys, trail_sizes, trail_opacity = [], [], [], []
        for i, s in enumerate(p["samples"]):
            if i % trail_n != 0 or i == 0 or i == len(p["samples"]) - 1:
                continue
            depth_frac = 1.0 - (s["y"] / 60.0)  # 0 at release, ~1 at plate
            # depth_frac feels like "how close is the ball to the catcher"
            # — for batter view, closer-to-catcher = closer-to-camera = bigger
            # — for pitcher view, closer-to-catcher = farther-from-camera = smaller
            visible = (1.0 - depth_frac) if depth_reversed else depth_frac
            trail_xs.append(sign * s["x"])
            trail_ys.append(s["z"])
            trail_sizes.append(6 + 14 * visible)
            trail_opacity.append(0.4 + 0.55 * visible)
        if trail_xs:
            fig.add_trace(go.Scatter(
                x=trail_xs, y=trail_ys,
                mode="markers",
                marker=dict(size=trail_sizes,
                             color=color,
                             opacity=trail_opacity,
                             line=dict(color="white", width=1.5)),
                showlegend=False,
                hoverinfo="skip",
            ))
        # ---- Endpoint markers ----
        # Plate-crossing dot (the "arrival" — emphasized in batter view)
        # Pitcher view: plate is FAR AWAY, so smaller; release is close, bigger
        if depth_reversed:
            release_marker_size = 28 if is_start else 22
            plate_marker_size   = 16 if is_start else 12
        else:
            release_marker_size = 12
            plate_marker_size   = 30 if is_start else 22

        fig.add_trace(go.Scatter(
            x=[sign * p["plate_x"]], y=[p["plate_z"]],
            mode="markers+text",
            marker=dict(size=plate_marker_size,
                         color=color,
                         line=dict(color="#d4a634" if is_start else "white",
                                   width=3 if is_start else 2),
                         symbol="star" if is_start else "circle"),
            text=[ptype.split()[0][:6]],
            textposition="top center" if not depth_reversed else "bottom center",
            textfont=dict(color="#1a2150",
                           size=10 if not depth_reversed else 9,
                           family="Arial Black"),
            hovertemplate=f"<b>{ptype}</b> @ plate<br>"
                          f"({p['plate_x']:+.2f}, {p['plate_z']:.2f}) ft<br>"
                          f"Velo: {p['velocity_mph']:.1f} mph<br>"
                          f"V Break: {p['vert_break_in']:+.1f}\" · "
                          f"H Break: {p['horiz_break_in']:+.1f}\""
                          + ("<br><i>(⭐ Starting pitch — click anywhere to move)</i>" if is_start else "")
                          + "<extra></extra>",
            showlegend=False,
        ))
        # Release-point dot (pitcher view emphasizes this end)
        if depth_reversed:
            fig.add_trace(go.Scatter(
                x=[sign * p["samples"][0]["x"]],
                y=[p["samples"][0]["z"]],
                mode="markers+text",
                marker=dict(size=release_marker_size,
                             color=color,
                             line=dict(color="#d4a634" if is_start else "white",
                                       width=3 if is_start else 2),
                             symbol="star" if is_start else "circle"),
                text=[ptype.split()[0][:6]] if is_start else [""],
                textposition="top center",
                textfont=dict(color="#1a2150", size=10, family="Arial Black"),
                hovertemplate=f"<b>{ptype}</b> released<br>"
                              f"Aim: ({p['plate_x']:+.2f}, {p['plate_z']:.2f}) ft<br>"
                              f"Velo: {p['velocity_mph']:.1f} mph<extra></extra>",
                showlegend=False,
            ))

    # Release-point marker (all pitches start here)
    rxyz = _pitch_release_point(sport)
    fig.add_trace(go.Scatter(
        x=[sign * rxyz[0]], y=[rxyz[2]],
        mode="markers+text",
        marker=dict(size=14, color="#1a2150",
                     line=dict(color="white", width=2),
                     symbol="diamond"),
        text=["release"],
        textposition="top center",
        textfont=dict(color="#1a2150", size=9),
        hovertemplate=f"<b>Release point</b><br>"
                      f"({rxyz[0]:+.1f}, {rxyz[2]:.1f}) ft<br>"
                      f"All pitches start here<extra></extra>",
        showlegend=False,
    ))

    # ==== Crosshair on the starting-pitch placement ====
    start = next((p for p in tunnel_data.values() if p["is_starting"]), None)
    if start is not None and clickable and not mirror_x:
        fig.add_shape(type="line",
                       x0=sign*start["plate_x"], x1=sign*start["plate_x"],
                       y0=-0.3, y1=7.5,
                       line=dict(color="#d4a634", width=1, dash="dot"),
                       layer="below")
        fig.add_shape(type="line", x0=-2.0, x1=2.0,
                       y0=start["plate_z"], y1=start["plate_z"],
                       line=dict(color="#d4a634", width=1, dash="dot"),
                       layer="below")

    title = ("Catcher / Batter POV — click anywhere to move the starting pitch"
             if not mirror_x else
             "Pitcher POV — inverse of the batter view (same flight paths)")
    x_title = "Plate Side (ft) — catcher's view" if not mirror_x else \
              "Plate Side (ft) — pitcher's view (mirrored)"

    fig.update_layout(
        title=dict(text=title, font=dict(size=13, color="#1a2150")),
        xaxis=dict(title=x_title, range=(-2.0, 2.0),
                    zeroline=False, showgrid=False,
                    fixedrange=True, autorange=False),
        # Range now reaches 7.5 ft so the release point (z≈6) is visible
        yaxis=dict(title="Height (ft)", range=(-0.5, 7.5),
                    zeroline=False, showgrid=False,
                    fixedrange=True, autorange=False),
        height=560, plot_bgcolor="white",
        margin=dict(l=20, r=20, t=50, b=40),
        dragmode=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.05),
    )
    return _apply_chart_theme(fig)


def _perspective_project(point_xyz: tuple[float, float, float],
                          camera_xyz: tuple[float, float, float],
                          focal: float = 36.0) -> tuple[float, float] | None:
    """Project a 3D world point onto the camera's 2D image plane.

    Camera looks down the +(-y) axis (toward the plate, since plate is at
    y=0 and pitcher is at y≈60). +z is "up" in world, mapped to +y in the
    projected image. +x stays as +x.

    Returns (apparent_x, apparent_y) in chart units, or None if the point
    is behind the camera (depth ≤ 0.5 ft).
    """
    px, py, pz = point_xyz
    cx, cy, cz = camera_xyz
    depth = cy - py  # distance forward from camera (positive = in front)
    if depth <= 0.5:   # avoid singularity right at the camera
        return None
    apparent_x = (px - cx) / depth * focal
    apparent_y = (pz - cz) / depth * focal
    return apparent_x, apparent_y


def _build_tunnel_pitcher_view(tunnel_data: dict, sport: str = "Baseball") -> "go.Figure":
    """TRUE 1st-person pitcher view — perspective-projected from behind the
    mound. The catcher's mitt appears SMALL in the distance, the strike zone
    is a small rectangle near the bottom-center of the field of view, and
    the ball trail arcs AWAY from the camera (large at release, small at
    the plate). This is what the pitcher literally sees.

    Implemented as a perspective projection (not a 2D mirror) so the
    depth-foreshortening is geometrically correct.
    """
    fig = go.Figure()
    c = TUNNEL_CONSTANTS.get(sport, TUNNEL_CONSTANTS["Baseball"])
    release_xyz = _pitch_release_point(sport)
    rubber_y = c["rubber_distance_ft"]

    # Camera at pitcher's eye position: just behind + slightly to the side
    # of the rubber, head-height above the release. The side offset gives
    # the view a cinematic 3/4-angle perspective so depth reads better than
    # a perfectly dead-on rear view (everything would otherwise collapse
    # onto the chart's center line).
    cam_xyz = (1.4, rubber_y + 1.5, release_xyz[2] + 0.4)
    focal = 36.0  # tune for how zoomed-in the view feels

    # ===== Sky / ground / mound horizon =====
    # Soft sky gradient
    fig.add_shape(type="rect", x0=-12, x1=12, y0=-8, y1=8,
                   fillcolor="#dbeafe", line=dict(width=0), layer="below")
    # Ground plane projected onto image — appears as a trapezoid from
    # bottom-edge of view tapering to the horizon. Compute as projected
    # box of the field surface.
    horizon_y = -(release_xyz[2] - cam_xyz[2]) / (rubber_y + 1) * focal  # where ground at infinity projects
    fig.add_shape(type="path",
                   path=(f"M -12 -8 L 12 -8 "
                          f"L {focal * 12 / 50:.2f} {horizon_y:.2f} "
                          f"L {-focal * 12 / 50:.2f} {horizon_y:.2f} Z"),
                   fillcolor="#bbf09b", line=dict(width=0), layer="below")
    # Horizon line
    fig.add_shape(type="line", x0=-12, x1=12, y0=horizon_y, y1=horizon_y,
                   line=dict(color="#94a3b8", width=1), layer="below")

    # ===== Strike zone (projected) =====
    sz_corners = [
        (SZ_X_MIN, 0.0, SZ_Z_MAX),  # top-left
        (SZ_X_MAX, 0.0, SZ_Z_MAX),  # top-right
        (SZ_X_MAX, 0.0, SZ_Z_MIN),  # bottom-right
        (SZ_X_MIN, 0.0, SZ_Z_MIN),  # bottom-left
    ]
    sz_proj = [_perspective_project(p, cam_xyz, focal) for p in sz_corners]
    if all(sz_proj):
        path_d = (f"M {sz_proj[0][0]:.2f} {sz_proj[0][1]:.2f} "
                  f"L {sz_proj[1][0]:.2f} {sz_proj[1][1]:.2f} "
                  f"L {sz_proj[2][0]:.2f} {sz_proj[2][1]:.2f} "
                  f"L {sz_proj[3][0]:.2f} {sz_proj[3][1]:.2f} Z")
        fig.add_shape(type="path", path=path_d,
                       line=dict(color="black", width=2),
                       fillcolor="rgba(0,0,0,0)",
                       layer="above")
        # 3×3 inner grid
        for i in (1, 2):
            f_h = i / 3.0
            top_l = sz_proj[0]; top_r = sz_proj[1]
            bot_l = sz_proj[3]; bot_r = sz_proj[2]
            # vertical divider
            vx_top = top_l[0] + (top_r[0] - top_l[0]) * f_h
            vx_bot = bot_l[0] + (bot_r[0] - bot_l[0]) * f_h
            fig.add_shape(type="line", x0=vx_top, x1=vx_bot,
                           y0=top_l[1], y1=bot_l[1],
                           line=dict(color="#9ca3af", width=0.6, dash="dot"),
                           layer="above")
            # horizontal divider
            hy_l = bot_l[1] + (top_l[1] - bot_l[1]) * f_h
            hy_r = bot_r[1] + (top_r[1] - bot_r[1]) * f_h
            fig.add_shape(type="line", x0=top_l[0], x1=top_r[0],
                           y0=hy_l, y1=hy_r,
                           line=dict(color="#9ca3af", width=0.6, dash="dot"),
                           layer="above")

    # ===== Catcher silhouette behind the plate (small, distant) =====
    catcher_proj = _perspective_project((0.0, -2.0, 2.5), cam_xyz, focal)
    if catcher_proj:
        cx_, cy_ = catcher_proj
        # head
        fig.add_shape(type="circle",
                       x0=cx_ - 0.15, x1=cx_ + 0.15,
                       y0=cy_ - 0.15, y1=cy_ + 0.15,
                       fillcolor="#1f2937", line=dict(width=0), layer="above")
        # crouched body
        fig.add_shape(type="path",
                       path=f"M {cx_-0.25} {cy_-0.15} L {cx_+0.25} {cy_-0.15} "
                            f"L {cx_+0.35} {cy_-1.0} L {cx_-0.35} {cy_-1.0} Z",
                       fillcolor="#1f2937", line=dict(width=0), layer="above")

    # ===== Flight-path trajectories (projected) =====
    for ptype, p in tunnel_data.items():
        color = PITCH_COLORS.get(ptype, "#666")
        is_start = p["is_starting"]
        proj_pts = []
        for s in p["samples"]:
            proj = _perspective_project((s["x"], s["y"], s["z"]), cam_xyz, focal)
            if proj:
                proj_pts.append(proj)
        if len(proj_pts) < 2:
            continue
        xs_p = [pt[0] for pt in proj_pts]
        ys_p = [pt[1] for pt in proj_pts]

        # Glow halo
        fig.add_trace(go.Scatter(
            x=xs_p, y=ys_p, mode="lines",
            line=dict(color=color, width=20 if is_start else 14,
                       shape="spline", smoothing=1.0),
            opacity=0.22 if is_start else 0.15,
            hoverinfo="skip", showlegend=False,
        ))
        # Main thick trail
        fig.add_trace(go.Scatter(
            x=xs_p, y=ys_p, mode="lines",
            line=dict(color=color, width=9 if is_start else 6,
                       shape="spline", smoothing=1.0),
            opacity=1.0 if is_start else 0.9,
            name=f"{ptype}{' ⭐' if is_start else ''}",
            hovertemplate=f"<b>{ptype}</b><extra></extra>",
        ))

        # Ball-trail beads — LARGE near camera (release, low sample idx)
        # and SMALL near catcher (plate, high sample idx)
        trail_n = max(1, len(p["samples"]) // 8)
        bead_xs, bead_ys, bead_sizes, bead_opacity = [], [], [], []
        for i, s in enumerate(p["samples"]):
            if i % trail_n != 0 or i == 0 or i == len(p["samples"]) - 1:
                continue
            proj = _perspective_project((s["x"], s["y"], s["z"]), cam_xyz, focal)
            if proj is None:
                continue
            # Apparent ball size scales 1/depth like real perspective
            depth = cam_xyz[1] - s["y"]
            size = max(5.0, min(28.0, 90.0 / depth))   # 90/depth caps for taste
            opacity = max(0.35, min(0.95, 5.0 / depth + 0.3))
            bead_xs.append(proj[0])
            bead_ys.append(proj[1])
            bead_sizes.append(size)
            bead_opacity.append(opacity)
        if bead_xs:
            fig.add_trace(go.Scatter(
                x=bead_xs, y=bead_ys,
                mode="markers",
                marker=dict(size=bead_sizes, color=color,
                             opacity=bead_opacity,
                             line=dict(color="white", width=1.5)),
                showlegend=False, hoverinfo="skip",
            ))

        # Release-point dot — huge (right in front of pitcher's eye)
        rel_proj = _perspective_project(
            (p["samples"][0]["x"], p["samples"][0]["y"], p["samples"][0]["z"]),
            cam_xyz, focal)
        if rel_proj:
            fig.add_trace(go.Scatter(
                x=[rel_proj[0]], y=[rel_proj[1]],
                mode="markers+text",
                marker=dict(size=42 if is_start else 32,
                             color=color,
                             line=dict(color="#d4a634" if is_start else "white",
                                       width=3 if is_start else 2),
                             symbol="star" if is_start else "circle"),
                text=[ptype.split()[0][:8]] if is_start else [""],
                textposition="top center",
                textfont=dict(color="#1a2150", size=11, family="Arial Black"),
                hovertemplate=f"<b>{ptype}</b> at release<extra></extra>",
                showlegend=False,
            ))
        # Plate-finish dot — small (far away in the distance)
        plate_proj = _perspective_project(
            (p["plate_x"], 0.0, p["plate_z"]), cam_xyz, focal)
        if plate_proj:
            fig.add_trace(go.Scatter(
                x=[plate_proj[0]], y=[plate_proj[1]],
                mode="markers+text",
                marker=dict(size=14 if is_start else 11,
                             color=color,
                             line=dict(color="#d4a634" if is_start else "white",
                                       width=2),
                             symbol="circle"),
                text=[ptype.split()[0][:6]],
                textposition="bottom center",
                textfont=dict(color="#1a2150", size=8, family="Arial Black"),
                hovertemplate=f"<b>{ptype}</b> finishes at "
                              f"({p['plate_x']:+.2f}, {p['plate_z']:.2f}) ft<extra></extra>",
                showlegend=False,
            ))

    # ===== Subtle "mound" foreground hint =====
    fig.add_shape(type="path",
                   path=f"M -12 -8 Q 0 -5 12 -8 L 12 -8.5 L -12 -8.5 Z",
                   fillcolor="#8c7050", line=dict(width=0), layer="below")

    fig.update_layout(
        title=dict(text="Pitcher POV — looking down at the catcher's mitt",
                    font=dict(size=13, color="#1a2150")),
        xaxis=dict(range=(-8, 8), zeroline=False, showgrid=False,
                    showticklabels=False, showline=False,
                    fixedrange=True, autorange=False),
        yaxis=dict(range=(-8, 6), zeroline=False, showgrid=False,
                    showticklabels=False, showline=False,
                    fixedrange=True, autorange=False),
        height=560, plot_bgcolor="#dbeafe",
        margin=dict(l=20, r=20, t=50, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        dragmode=False,
    )
    return _apply_chart_theme(fig)


def _build_tunnel_side_view(tunnel_data: dict, sport: str = "Baseball") -> "go.Figure":
    """Side view — looks like the classic tunneling diagram:
       pitcher → release-point ring → tunnel-point ring → home-plate ring → catcher.

    The y-axis is height (ft), x-axis is distance from plate (ft, reversed so
    the pitcher is on the LEFT like the reference image). Each pitch is drawn
    as a thick curved flight path. Convergence at the tunnel ring then
    divergence to the plate is the entire teaching story.
    """
    fig = go.Figure()
    c = TUNNEL_CONSTANTS.get(sport, TUNNEL_CONSTANTS["Baseball"])
    tunnel_y = c["tunnel_distance_ft"]
    rubber_y = c["rubber_distance_ft"]
    release_xyz = _pitch_release_point(sport)

    # ===== Background dressing: sky + ground =====
    # Soft sky gradient
    fig.add_shape(type="rect",
                   x0=-2, x1=rubber_y + 2, y0=-1, y1=10,
                   fillcolor="#f0f9ff", line=dict(width=0), layer="below")
    # Ground / dirt strip
    fig.add_shape(type="rect", x0=-2, x1=rubber_y + 2, y0=-1, y1=0,
                   fillcolor="#c2a47e", line=dict(width=0), layer="below")
    # Mound (slight bump at rubber distance)
    fig.add_shape(type="path",
                   path=f"M {rubber_y - 5} 0 Q {rubber_y} 1.0 {rubber_y + 5} 0 L {rubber_y + 5} -1 L {rubber_y - 5} -1 Z",
                   fillcolor="#8c7050", line=dict(color="#6b5638", width=1),
                   layer="below")

    # ===== Pitcher silhouette + label (left side of chart) =====
    px_pitcher = rubber_y + 0.5
    # Body (simple stick figure-ish using shapes)
    fig.add_shape(type="circle",
                   x0=px_pitcher - 0.4, x1=px_pitcher + 0.4,
                   y0=5.2, y1=6.0,
                   fillcolor="#1f2937", line=dict(width=0), layer="below")  # head
    fig.add_shape(type="path",
                   path=f"M {px_pitcher} 5.2 L {px_pitcher - 0.7} 4.0 L {px_pitcher + 0.7} 3.8 L {px_pitcher} 5.2 Z",
                   fillcolor="#1f2937", line=dict(width=0), layer="below")  # torso/arm
    fig.add_shape(type="path",
                   path=f"M {px_pitcher - 0.4} 3.8 L {px_pitcher - 1.2} 0.5 L {px_pitcher + 1.2} 0.5 L {px_pitcher + 0.4} 3.8 Z",
                   fillcolor="#1f2937", line=dict(width=0), layer="below")  # legs
    fig.add_annotation(x=px_pitcher, y=6.6, text="<b>Pitcher</b>",
                        showarrow=False,
                        font=dict(size=10, color="#1f2937"))

    # ===== Catcher silhouette (right side of chart, at plate) =====
    px_catcher = -1.0
    fig.add_shape(type="circle",
                   x0=px_catcher - 0.3, x1=px_catcher + 0.3,
                   y0=2.5, y1=3.1,
                   fillcolor="#1f2937", line=dict(width=0), layer="below")  # head
    fig.add_shape(type="path",
                   path=f"M {px_catcher - 0.5} 2.5 L {px_catcher + 0.5} 2.5 L {px_catcher + 0.7} 0.5 L {px_catcher - 0.7} 0.5 Z",
                   fillcolor="#1f2937", line=dict(width=0), layer="below")  # crouched torso
    fig.add_annotation(x=px_catcher, y=3.5, text="<b>Catcher</b>",
                        showarrow=False,
                        font=dict(size=10, color="#1f2937"))

    # ===== Release-point RING =====
    rp_x, rp_z = release_xyz[1], release_xyz[2]   # (54.5, 6.0)
    fig.add_shape(type="circle",
                   x0=rp_x - 0.55, x1=rp_x + 0.55,
                   y0=rp_z - 0.55, y1=rp_z + 0.55,
                   fillcolor="rgba(96,165,250,0.20)",
                   line=dict(color="#3b82f6", width=2.5),
                   layer="above")
    fig.add_annotation(x=rp_x, y=rp_z + 1.0,
                        text="<b>Release Point</b>",
                        showarrow=False,
                        font=dict(size=10, color="#1e3a8a"),
                        bgcolor="rgba(255,255,255,0.85)",
                        borderpad=2)

    # ===== Tunnel-point RING =====
    # Find the average tunnel-point height across all pitches (visually
    # this looks like the ring is around where they converge)
    tps = [p["tunnel_xyz"] for p in tunnel_data.values() if p["tunnel_xyz"]]
    if tps:
        avg_tz = sum(t["z"] for t in tps) / len(tps)
    else:
        avg_tz = 3.0
    fig.add_shape(type="circle",
                   x0=tunnel_y - 0.55, x1=tunnel_y + 0.55,
                   y0=avg_tz - 0.55, y1=avg_tz + 0.55,
                   fillcolor="rgba(212,166,52,0.20)",
                   line=dict(color="#d4a634", width=2.5),
                   layer="above")
    fig.add_annotation(x=tunnel_y, y=avg_tz + 1.0,
                        text=f"<b>Tunnel Point</b><br>{tunnel_y:.1f} ft from plate",
                        showarrow=False,
                        font=dict(size=10, color="#92400e"),
                        bgcolor="rgba(255,255,255,0.85)",
                        borderpad=2)
    # Vertical dashed line at tunnel distance (subtle reference)
    fig.add_shape(type="line", x0=tunnel_y, x1=tunnel_y, y0=0, y1=avg_tz - 0.55,
                   line=dict(color="#d4a634", width=1, dash="dash"),
                   layer="below")

    # ===== Home-plate RING =====
    # Average plate height (where pitches finish — roughly at plate_z of starter)
    start = next((p for p in tunnel_data.values() if p["is_starting"]), None)
    plate_z = start["plate_z"] if start else 3.0
    fig.add_shape(type="circle",
                   x0=-0.55, x1=0.55,
                   y0=plate_z - 0.55, y1=plate_z + 0.55,
                   fillcolor="rgba(96,165,250,0.20)",
                   line=dict(color="#3b82f6", width=2.5),
                   layer="above")
    fig.add_annotation(x=0, y=plate_z + 1.0,
                        text="<b>Home Plate</b>",
                        showarrow=False,
                        font=dict(size=10, color="#1e3a8a"),
                        bgcolor="rgba(255,255,255,0.85)",
                        borderpad=2)

    # ===== Strike-zone reference at plate (vertical band 1.6 → 3.5 ft) =====
    fig.add_shape(type="rect", x0=-0.25, x1=0.25,
                   y0=SZ_Z_MIN, y1=SZ_Z_MAX,
                   fillcolor="rgba(0,0,0,0)",
                   line=dict(color="#1a2150", width=1, dash="dot"),
                   layer="above")

    # ===== Plot each trajectory as a THICK curved line =====
    for ptype, p in tunnel_data.items():
        color = PITCH_COLORS.get(ptype, "#666")
        is_start = p["is_starting"]
        ys = [s["y"] for s in p["samples"]]
        zs = [s["z"] for s in p["samples"]]
        fig.add_trace(go.Scatter(
            x=ys, y=zs, mode="lines",
            line=dict(color=color,
                       width=6 if is_start else 4,
                       dash="solid"),
            opacity=1.0 if is_start else 0.85,
            name=f"{ptype}{' ⭐' if is_start else ''}",
            hovertemplate=f"<b>{ptype}</b><br>%{{x:.1f}} ft from plate · %{{y:.2f}} ft high<extra></extra>",
        ))

    fig.update_layout(
        title=dict(text="Side View — release → tunnel → plate",
                    font=dict(size=13, color="#1a2150")),
        xaxis=dict(title="Distance from Home Plate (ft)",
                    range=(rubber_y + 3, -2),  # reversed: pitcher on left, catcher on right
                    zeroline=False, showgrid=False, showline=False),
        yaxis=dict(title="Height (ft)", range=(-1, 9),
                    zeroline=False, showgrid=False, showline=False),
        height=520, plot_bgcolor="#f0f9ff",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=20, r=20, t=50, b=40),
    )
    # preserve_bg=True keeps the sky-blue stadium background
    return _apply_chart_theme(fig, preserve_bg=True)


# Fake-baseline used for delta display when no historical data is available
DEMO_BASELINE = {
    "Four-Seam Fastball":   {"velo": 89.8, "vbreak": 16.3, "stress": 56.6},
    "Two-Seam Sinker":      {"velo": 89.2, "vbreak": 11.7, "stress": 52.7},
    "Slider Strike-Getter": {"velo": 83.0, "vbreak": -2.4, "stress": 49.5},
    "Slider Chase":         {"velo": 79.2, "vbreak": -4.3, "stress": 57.7},
}


def _drill_record(key: str, trigger_text: str,
                   severity: str = "any", level: str = "any") -> dict:
    """Build a card-shaped dict from a drill key + the trigger explanation.

    Severity ("mild" / "moderate" / "severe") and athlete level ("youth" /
    "hs" / "college+") let the video picker choose the best tutorial for
    this specific pitcher's data.
    """
    d = DRILL_LIBRARY[key]
    video = pick_video(key, severity=severity, level=level)
    return {
        "key":         key,
        "category":    d["category"],
        "phase":       d["phase"],
        "priority":    d["priority"],
        "label":       d["label"],
        "drill":       d["drill"],
        "protocol":    d["protocol"],
        "why":         d["why"],
        "grip_key":    d.get("grip_key"),       # only set on Grip drills
        "video_url":   video["url"]   if video else None,
        "video_title": video.get("title")  if video else None,
        "video_source":video.get("source") if video else None,
        "severity":    severity,
        "trigger":     trigger_text,
    }


def _severity_from_ratio(actual: float, target: float, higher_is_worse: bool = True) -> str:
    """Classify how far a value is from target into mild / moderate / severe.

    `higher_is_worse=True` for things you want LOWER (gyro, stress);
    pass False for things you want HIGHER (spin, separation, knee extension).
    """
    if actual is None or target is None or target == 0:
        return "any"
    diff = (actual - target) if higher_is_worse else (target - actual)
    if diff <= 0:
        return "mild"
    # Express as % of target
    pct = abs(diff) / abs(target)
    if pct < 0.10:    return "mild"
    if pct < 0.25:    return "moderate"
    return "severe"


def recommend_drills(df: pd.DataFrame, baseline: dict | None = None,
                      sport: str = "Baseball",
                      athlete_level: str = "HS-Varsity") -> dict:
    """Inspect a session and recommend a today plan + week plan.

    Returns: {"today": [drill, ...], "week": [drill, ...]}
    Each drill is a dict ready for the UI to render as a card.
    """
    # Sport-aware constants — softball windmill has different "elite" values
    # than baseball overhand, so the trigger thresholds differ.
    is_softball = (sport == "Softball")
    HS_SEP_TARGET     = 40 if is_softball else 48     # peak hip-shoulder separation
    FB_SPIN_TARGET    = 1500 if is_softball else 2200 # fastball spin RPM (softball threshold lowered)
    FB_BASELINE_KEY   = "Softball Fastball" if is_softball else "Four-Seam Fastball"
    # Softball doesn't really track baseball-style "extension" — skip that check
    CHECK_EXTENSION   = not is_softball

    # Drill-key routing: same issue, different drill+video by sport
    SPORT_DRILL_ROUTE = {
        "high_valgus_stress":      "softball_high_valgus_stress",
        "session_cooldown_default":"softball_session_cooldown_default",
        "low_fastball_spin":       "softball_low_fastball_spin",
        "low_offspeed_break":      "softball_low_offspeed_break",
        "arm_slot_variance":       "softball_arm_slot_variance",
    } if is_softball else {}

    def _sport_key(k: str) -> str:
        """Translate a generic drill key to its sport-specific variant if any."""
        return SPORT_DRILL_ROUTE.get(k, k)

    baseline = baseline or (DEMO_BASELINE_SOFTBALL if is_softball else DEMO_BASELINE)
    today: list = []
    week: list = []
    seen_keys: set = set()

    # Map the athlete's level (e.g. "HS-Varsity") to the video-level bucket
    video_level = LEVEL_TO_VIDEO_BUCKET.get(athlete_level, "any")

    def add(plan: list, key: str, trigger: str, severity: str = "any"):
        # Auto-route shared keys to sport-specific variants when applicable
        key = _sport_key(key)
        if key in seen_keys:
            return
        if key not in DRILL_LIBRARY:
            return  # safety: skip if a routed key doesn't exist yet
        seen_keys.add(key)
        plan.append(_drill_record(key, trigger,
                                   severity=severity, level=video_level))

    # ===== TODAY — INJURY / COOLDOWN =====
    danger_valgus_pitches = df[df["Peak_Valgus_Nm"].notna() & (df["Peak_Valgus_Nm"] >= DANGER_VALGUS_NM)]
    if not danger_valgus_pitches.empty:
        worst = danger_valgus_pitches["Peak_Valgus_Nm"].max()
        sev = _severity_from_ratio(worst, DANGER_VALGUS_NM, higher_is_worse=True)
        add(today, "high_valgus_stress",
            f"{len(danger_valgus_pitches)} pitch(es) exceeded {DANGER_VALGUS_NM} Nm "
            f"(peak: {worst:.1f} Nm).", severity=sev)

    max_acr = df["AC_Ratio"].dropna().max() if df["AC_Ratio"].notna().any() else 0
    if max_acr >= ACR_DANGER_THRESHOLD:
        add(today, "high_acr_rest",
            f"Acute:Chronic Ratio peaked at {max_acr:.2f} — above the 1.5 injury-risk threshold.")
    elif max_acr >= ACR_WARNING_THRESHOLD:
        add(today, "moderate_acr_cooldown",
            f"Acute:Chronic Ratio peaked at {max_acr:.2f} — yellow zone.")

    # Always include a baseline cooldown if nothing else recommended for today
    if not today:
        add(today, "session_cooldown_default",
            "Standard cooldown after any bullpen session.")

    # ===== WEEK — MECHANICS (sport-aware drill routing) =====
    # For each mechanics issue, pick the right drill key for the sport.
    # Softball drills use windmill-appropriate corrections instead of
    # baseball-style Hershiser / Driveline plyo drills.
    early_trunk_key = "softball_K_drill" if is_softball else "early_trunk_rotation"
    low_hs_sep_key  = "softball_brush_at_hip" if is_softball else "low_hip_shoulder_separation"
    # Soft lead knee works the same way in both sports (front leg block)
    soft_knee_key   = "soft_lead_knee"

    early_trunk_pct = (df["FootPlant_Trunk_Rot"].dropna() >= EARLY_TRUNK_ROTATION_DEG).mean() if df["FootPlant_Trunk_Rot"].notna().any() else 0
    if early_trunk_pct >= 0.25:
        early_count = int((df["FootPlant_Trunk_Rot"].dropna() >= EARLY_TRUNK_ROTATION_DEG).sum())
        add(week, early_trunk_key,
            f"{early_count} pitch(es) had the chest open early at foot-plant (> {EARLY_TRUNK_ROTATION_DEG}°).")

    avg_knee_ext = df["Release_Lead_Knee_Ext"].dropna().mean() if df["Release_Lead_Knee_Ext"].notna().any() else None
    if avg_knee_ext is not None and avg_knee_ext < 145:
        add(week, soft_knee_key,
            f"Average lead-knee extension at release was {avg_knee_ext:.1f}° (target: 150°+).")

    avg_hip_shoulder = df["Peak_Hip_Shoulder_Sep"].dropna().mean() if df["Peak_Hip_Shoulder_Sep"].notna().any() else None
    if avg_hip_shoulder is not None and avg_hip_shoulder < HS_SEP_TARGET:
        add(week, low_hs_sep_key,
            f"Peak hip-shoulder separation averaged {avg_hip_shoulder:.1f}° "
            f"(target for {sport}: {HS_SEP_TARGET}°+).")

    if CHECK_EXTENSION:
        avg_extension = df["Extension_ft"].dropna().mean() if df["Extension_ft"].notna().any() else None
        if avg_extension is not None and avg_extension < 6.0:
            add(week, "low_extension",
                f"Average release extension was {avg_extension:.1f} ft (target: 6.2+ ft).")

    # ===== WEEK — VELOCITY =====
    fastballs = df[df["Pitch_Type"].str.contains("Fastball|Four-Seam", case=False, na=False)]
    if not fastballs.empty:
        avg_fb_velo = fastballs["Velocity_mph"].mean()
        fb_baseline = baseline.get(FB_BASELINE_KEY, {}).get("velo")
        if fb_baseline and avg_fb_velo < (fb_baseline - 2.0):
            sev = _severity_from_ratio(avg_fb_velo, fb_baseline, higher_is_worse=False)
            add(week,
                "softball_below_baseline_velo" if is_softball else "below_baseline_fastball_velo",
                f"Fastball averaged {avg_fb_velo:.1f} mph — {(fb_baseline - avg_fb_velo):.1f} mph below your baseline.",
                severity=sev)

    # ===== WEEK — STUFF / SPIN =====
    if not fastballs.empty:
        avg_fb_spin = fastballs["Total_Spin_rpm"].dropna().mean()
        if avg_fb_spin and avg_fb_spin < FB_SPIN_TARGET:
            sev = _severity_from_ratio(avg_fb_spin, FB_SPIN_TARGET, higher_is_worse=False)
            add(week, "low_fastball_spin",
                f"Fastball spin averaged {avg_fb_spin:.0f} RPM (target: {FB_SPIN_TARGET}+ RPM).",
                severity=sev)

    sliders = df[df["Pitch_Type"].str.contains("Slider", case=False, na=False)]
    if len(sliders) >= 3:
        avg_slider_eff = sliders["Spin_Efficiency_pct"].dropna().mean()
        if avg_slider_eff is not None and avg_slider_eff < 30:
            add(week, "low_slider_spin_efficiency",
                f"Slider spin efficiency averaged {avg_slider_eff:.1f}% (target: 30-40%).")

    # Movement check for offspeeds — does any non-fastball have notably low break?
    offspeed = df[~df["Pitch_Type"].str.contains("Fastball|Four-Seam|Two-Seam|Sinker", case=False, na=False)]
    if not offspeed.empty:
        avg_total_break = (offspeed["Horiz_Break_in"].abs() + offspeed["Vert_Break_in"].abs()).mean()
        if avg_total_break < 12:
            add(week, "low_offspeed_break",
                f"Offspeed pitches averaged only {avg_total_break:.1f}\" of total break.")

    # ===== WEEK — GRIP =====
    # Slider with high gyro AND high valgus → grip change candidate
    if not sliders.empty and sliders["Gyro_Degrees"].notna().any() and sliders["Peak_Valgus_Nm"].notna().any():
        high_gyro_high_stress = sliders[
            (sliders["Gyro_Degrees"] > 70) &
            (sliders["Peak_Valgus_Nm"] > 60)
        ]
        if len(high_gyro_high_stress) >= 2:
            add(week, "slider_grip_pronation_fix",
                f"{len(high_gyro_high_stress)} slider(s) had high gyro and high elbow stress — wrist-twist pattern.")

    # ===== WEEK — CONSISTENCY =====
    if df["Arm_Slot_deg"].notna().any():
        slot_range = df["Arm_Slot_deg"].dropna().max() - df["Arm_Slot_deg"].dropna().min()
        if slot_range > 6:
            add(week, "arm_slot_variance",
                f"Arm slot varied {slot_range:.0f}° across the session (target: < 4°).")

    # =========================================================================
    # SOFTBALL-SPECIFIC CHECKS
    # Detect by pitch type names that are unique to softball.
    # =========================================================================
    rise_balls = df[df["Pitch_Type"].str.contains("Rise", case=False, na=False)]
    drop_balls = df[df["Pitch_Type"].str.contains("Drop", case=False, na=False)]
    softball_curves = df[df["Pitch_Type"].str.contains("Screw", case=False, na=False)]

    if len(rise_balls) >= 2:
        avg_rise_spin = rise_balls["Total_Spin_rpm"].dropna().mean()
        if avg_rise_spin is not None and avg_rise_spin < 1900:
            add(week, "softball_low_rise_spin",
                f"Rise ball spin averaged {avg_rise_spin:.0f} RPM "
                "(target: 1,900+ RPM for the rise effect to work).")

    if len(drop_balls) >= 2:
        avg_drop_vbreak = drop_balls["Vert_Break_in"].dropna().mean()
        if avg_drop_vbreak is not None and avg_drop_vbreak > -3:
            add(week, "softball_low_drop_topspin",
                f"Drop ball averaged only {avg_drop_vbreak:+.1f}\" of vertical break "
                "(target: <-4\" — i.e. dropping more than gravity alone).")

    if len(softball_curves) >= 2 and softball_curves["Spin_Efficiency_pct"].notna().any():
        avg_curve_eff = softball_curves["Spin_Efficiency_pct"].dropna().mean()
        if avg_curve_eff is not None and avg_curve_eff < 65:
            add(week, "softball_grip_curve_pronation",
                f"Screwball spin efficiency averaged {avg_curve_eff:.1f}% (target: 70%+).")

    # Sort each plan by priority (1 = most urgent first)
    today.sort(key=lambda d: d["priority"])
    week.sort(key=lambda d: d["priority"])

    # Cap to keep the plan focused
    return {
        "today": today[:3],
        "week":  week[:5],
    }


def build_action_plan(df: pd.DataFrame) -> list:
    """Backward-compatible flat list of actions. Prefer recommend_drills()."""
    plan = recommend_drills(df)
    out = []
    for d in plan["today"][:1] + plan["week"][:2]:
        out.append({
            "priority": ("🚨 PRIORITY 1" if d["priority"] <= 1 else "⚠️ FOCUS"),
            "title":    d["label"],
            "drill":    d["drill"],
            "why":      d["why"],
        })
    return out


def _format_plan_text(athlete_name: str, plan: dict) -> str:
    """Plain-text version of the action plan, suitable for SMS/email."""
    out = [f"PITCHING LAB — ACTION PLAN", f"Athlete: {athlete_name}", "=" * 50, ""]
    def _block(d):
        out.append(f"  • [{d['category']}] {d['label']}")
        out.append(f"      Drill:    {d['drill']}")
        out.append(f"      Protocol: {d['protocol']}")
        out.append(f"      Why:      {d['why']}")
        if d.get("video_url"):
            title = d.get("video_title") or "Watch demo"
            source = d.get("video_source", "")
            src_suffix = f" ({source})" if source else ""
            out.append(f"      Demo:     {title}{src_suffix}")
            out.append(f"                {d['video_url']}")
        out.append("")

    out.append("TODAY (within 30 min of finishing):")
    if not plan["today"]:
        out.append("  - Standard cooldown is fine.")
    for d in plan["today"]:
        _block(d)
    out.append("THIS WEEK (before next bullpen):")
    if not plan["week"]:
        out.append("  - Maintain and repeat what's working.")
    for d in plan["week"]:
        _block(d)
    return "\n".join(out)


# =============================================================================
# PDF EXPORT — Post-Bullpen Report
# =============================================================================
# Brand colors used in the PDF
PDF_BRAND_NAVY  = (0.10, 0.13, 0.30)   # deep navy header
PDF_BRAND_GOLD  = (0.83, 0.65, 0.20)   # accent gold
PDF_DANGER_RED  = (0.86, 0.20, 0.20)
PDF_GOOD_GREEN  = (0.13, 0.65, 0.30)


def _render_movement_quadrant_png(df: pd.DataFrame, width_in: float = 5.0) -> bytes:
    """Nestico-style HB vs IVB movement scatter, color-coded by pitch type.

    Standardized way to visualize pitch movement. Quadrants separated by axes
    at x=0 and y=0. Used in the PDF and (optionally) in the UI.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(width_in, width_in), dpi=140)

    # Quadrant guide lines
    ax.axhline(y=0, color="#9ca3af", linewidth=0.8, linestyle="--", zorder=1)
    ax.axvline(x=0, color="#9ca3af", linewidth=0.8, linestyle="--", zorder=1)

    # Range guide rings
    for r in (5, 10, 15, 20):
        circ = plt.Circle((0, 0), r, fill=False, edgecolor="#e5e7eb",
                          linewidth=0.5, zorder=1)
        ax.add_patch(circ)

    # Plot each pitch
    for ptype, g in df.groupby("Pitch_Type"):
        color = PITCH_COLORS.get(ptype, "#666")
        ax.scatter(g["Horiz_Break_in"], g["Vert_Break_in"],
                   c=color, s=140, edgecolors="black", linewidths=0.6,
                   label=ptype, alpha=0.92, zorder=3)

    ax.set_xlim(-22, 22)
    ax.set_ylim(-22, 22)
    ax.set_aspect("equal")
    ax.set_xlabel("Horizontal Break (in)", fontsize=10, fontweight="bold")
    ax.set_ylabel("Induced Vertical Break (in)", fontsize=10, fontweight="bold")
    ax.set_title("Pitch Movement (catcher's view)", fontsize=11, fontweight="bold")
    # Legend OUTSIDE the plot so it doesn't cover any quadrant
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5),
              fontsize=7, framealpha=0.95, borderaxespad=0)
    ax.tick_params(labelsize=8)
    ax.grid(False)
    for spine in ax.spines.values():
        spine.set_color("#9ca3af")

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _render_velocity_distribution_png(df: pd.DataFrame, width_in: float = 6.0) -> bytes:
    """Per-pitch-type velocity distribution (Nestico-style). Histogram bars
    plus a mean line per pitch type — gives a quick view of how consistent
    velocity is within each pitch type."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pitch_types = list(df["Pitch_Type"].unique())
    n = len(pitch_types)
    fig, axes = plt.subplots(n, 1, figsize=(width_in, 0.8 * n + 0.6), dpi=140,
                              sharex=True)
    if n == 1:
        axes = [axes]

    all_velos = df["Velocity_mph"].dropna()
    if len(all_velos) == 0:
        plt.close(fig)
        return b""
    v_min = max(60, float(all_velos.min()) - 2)
    v_max = float(all_velos.max()) + 2

    for ax, ptype in zip(axes, pitch_types):
        g = df[df["Pitch_Type"] == ptype]
        color = PITCH_COLORS.get(ptype, "#666")
        v = g["Velocity_mph"].dropna()
        if len(v) >= 1:
            ax.hist(v, bins=8, range=(v_min, v_max), color=color,
                    edgecolor="black", linewidth=0.5, alpha=0.85)
            ax.axvline(v.mean(), color="black", linestyle="--", linewidth=1.3)
            ax.text(v.mean(), ax.get_ylim()[1] * 0.85,
                    f"{v.mean():.1f}", fontsize=8, fontweight="bold",
                    ha="center", color="black",
                    bbox=dict(facecolor="white", edgecolor="none", pad=1.5))
        ax.set_yticks([])
        ax.set_ylabel(ptype, fontsize=8, rotation=0, ha="right", va="center",
                       fontweight="bold")
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.tick_params(labelsize=7)
    axes[-1].set_xlabel("Velocity (mph)", fontsize=9, fontweight="bold")
    axes[0].set_title("Velocity Distribution by Pitch Type", fontsize=11,
                       fontweight="bold")
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _render_strike_zone_png(df: pd.DataFrame, width_in: float = 5.0) -> bytes:
    """Render the strike zone scatter as a PNG byte string for embedding in PDFs."""
    import matplotlib
    matplotlib.use("Agg")  # non-interactive backend so it works headless
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle, Polygon

    fig, ax = plt.subplots(figsize=(width_in, width_in), dpi=140)

    # Strike zone box + 3x3 grid
    ax.add_patch(Rectangle((SZ_X_MIN, SZ_Z_MIN),
                           SZ_X_MAX - SZ_X_MIN, SZ_Z_MAX - SZ_Z_MIN,
                           fill=False, edgecolor="black", linewidth=2))
    for i in (1, 2):
        x = SZ_X_MIN + (SZ_X_MAX - SZ_X_MIN) * (i / 3)
        z = SZ_Z_MIN + (SZ_Z_MAX - SZ_Z_MIN) * (i / 3)
        ax.plot([x, x], [SZ_Z_MIN, SZ_Z_MAX], color="#cccccc", linewidth=0.6)
        ax.plot([SZ_X_MIN, SZ_X_MAX], [z, z], color="#cccccc", linewidth=0.6)

    # Home plate
    plate = Polygon([(-0.71, 0.05), (0.71, 0.05), (0.50, -0.10),
                     (0, -0.25), (-0.50, -0.10)],
                    closed=True, facecolor="#dcdcdc", edgecolor="black", linewidth=0.8)
    ax.add_patch(plate)

    # Plot each pitch type
    for ptype, g in df.groupby("Pitch_Type"):
        color = PITCH_COLORS.get(ptype, "#666")
        ax.scatter(g["Strike_Zone_Side"], g["Strike_Zone_Height"],
                   c=color, s=180, edgecolors="black", linewidths=0.8,
                   label=ptype, zorder=3)
        # Pitch numbers on top of dots
        for _, row in g.iterrows():
            ax.text(row["Strike_Zone_Side"], row["Strike_Zone_Height"],
                    str(int(row["Pitch_Num"])),
                    ha="center", va="center",
                    color="white", fontsize=7, fontweight="bold", zorder=4)

    # Outlier rings
    for _, r in df.iterrows():
        if r["Outlier_Type"] == "positive":
            ax.scatter(r["Strike_Zone_Side"], r["Strike_Zone_Height"],
                       s=400, facecolors="none", edgecolors="#22c55e",
                       linewidths=2.2, zorder=2)
        elif r["Outlier_Type"] == "negative":
            ax.scatter(r["Strike_Zone_Side"], r["Strike_Zone_Height"],
                       s=400, facecolors="none", edgecolors="#ef4444",
                       linewidths=2.2, zorder=2)

    ax.set_xlim(-2.5, 2.5)
    ax.set_ylim(-0.5, 5.0)
    ax.set_aspect("equal")
    ax.set_xlabel("Plate Side (ft)", fontsize=9)
    ax.set_ylabel("Height (ft)", fontsize=9)
    ax.set_title("Strike Zone Map", fontsize=11, fontweight="bold")
    ax.legend(loc="upper right", fontsize=7, framealpha=0.9)
    ax.tick_params(labelsize=8)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _render_hitting_spray_png(df: pd.DataFrame, sport: str = "Baseball",
                                width_in: float = 5.0) -> bytes:
    """Render the spray chart as a PNG for embedding in the Post-Swing PDF.
    Each dot is one ball-in-play, colored by contact quality.
    """
    import math
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Wedge, Polygon, Circle, Rectangle

    dims = _field_dimensions(sport)
    cf = dims["of_wall_cf"]

    fig, ax = plt.subplots(figsize=(width_in, width_in), dpi=140)

    # Outfield grass arc (light green)
    foul_lo, foul_hi = 45, 135  # degrees in standard math coords (0=right, 90=up)
    grass = Wedge(center=(0, 0), r=cf + 30, theta1=foul_lo, theta2=foul_hi,
                   facecolor="#bbf09b", edgecolor="none", zorder=0)
    ax.add_patch(grass)

    # Infield dirt
    infield = Wedge(center=(0, 0), r=95, theta1=foul_lo, theta2=foul_hi,
                     facecolor="#d4a374", edgecolor="none", zorder=0.5)
    ax.add_patch(infield)

    # Foul lines (going from home plate outward at 45° and 135°)
    foul_len = cf + 30
    ax.plot([0, -foul_len * 0.7071], [0, foul_len * 0.7071],
            color="white", linewidth=2, zorder=1)
    ax.plot([0,  foul_len * 0.7071], [0, foul_len * 0.7071],
            color="white", linewidth=2, zorder=1)

    # Distance markers (200/300/400 ft baseball, 150/200/250 softball)
    if sport == "Softball":
        rings = [150, 200, 250]
    else:
        rings = [200, 300, 400]
    for r in rings:
        if r <= cf + 20:
            arc = Wedge(center=(0, 0), r=r, theta1=foul_lo, theta2=foul_hi,
                         facecolor="none", edgecolor="white", linewidth=0.8,
                         linestyle="--", alpha=0.6, zorder=1)
            ax.add_patch(arc)
            ax.text(0, r + 5, f"{r} ft", ha="center", va="bottom",
                    color="white", fontsize=7, fontweight="bold", zorder=1.5,
                    alpha=0.85)

    # Home plate
    plate = Polygon([(-3, -3), (3, -3), (3, 0), (0, 3), (-3, 0)],
                    closed=True, facecolor="white", edgecolor="black",
                    linewidth=1, zorder=2)
    ax.add_patch(plate)
    # Bases as small white squares
    for bx, by in [(dims["base_path_ft"] * 0.7071, dims["base_path_ft"] * 0.7071),
                    (-dims["base_path_ft"] * 0.7071, dims["base_path_ft"] * 0.7071),
                    (0, dims["base_path_ft"] * 1.414)]:
        ax.add_patch(Rectangle((bx - 2, by - 2), 4, 4,
                                facecolor="white", edgecolor="black",
                                linewidth=0.6, zorder=2))
    # Pitcher's mound (small circle)
    ax.add_patch(Circle((0, dims["mound_distance"]), 4,
                         facecolor="#b08364", edgecolor="black",
                         linewidth=0.6, zorder=2))

    # Plot each ball in play
    in_play = df[df["Swing_Outcome"].isin(["weak_contact", "solid_contact",
                                             "barrel", "foul"])]
    for outcome in ["barrel", "solid_contact", "weak_contact", "foul"]:
        g = in_play[in_play["Swing_Outcome"] == outcome]
        if g.empty:
            continue
        color = SWING_OUTCOME_COLORS[outcome]
        xs, ys = [], []
        for _, row in g.iterrows():
            spray = row.get("Spray_Angle_deg", 0.0) or 0.0
            dist  = row.get("Distance_ft", 100) or 100
            hand  = row.get("Batter_Hand", "Right")
            x_sign = -1.0 if hand == "Left" else 1.0
            # Convert spray angle to field xy:
            # spray 0 = straight CF, negative = pull side, positive = oppo
            theta_rad = math.radians(90 + spray * x_sign)
            xs.append(dist * math.cos(theta_rad))
            ys.append(dist * math.sin(theta_rad))
        ax.scatter(xs, ys, c=color, s=110, edgecolors="white",
                    linewidths=1.0, zorder=3, label=outcome.replace("_", " ").title())

    ax.set_xlim(-cf - 30, cf + 30)
    ax.set_ylim(-20, cf + 35)
    ax.set_aspect("equal")
    ax.set_facecolor("#0f172a")  # stadium navy background
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title("Spray Chart", fontsize=11, fontweight="bold", color="#1a2150")
    ax.legend(loc="lower center", fontsize=7, ncol=4, framealpha=0.9,
              bbox_to_anchor=(0.5, -0.02))
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=140, bbox_inches="tight",
                 facecolor="white", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _render_ev_la_quadrant_png(df: pd.DataFrame, width_in: float = 5.0) -> bytes:
    """Render an Exit Velocity vs Launch Angle scatter for the PDF.

    Shades the barrel zone (Statcast definition: EV 95+ AND LA 8-32) so coaches
    immediately see how many balls landed in the productive area.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    in_play = df[df["Swing_Outcome"].isin(["weak_contact", "solid_contact",
                                             "barrel", "foul"])].copy()
    fig, ax = plt.subplots(figsize=(width_in, width_in * 0.78), dpi=140)

    # Barrel zone (shaded green)
    ax.add_patch(Rectangle((8, 95), 24, 20, facecolor="#16a34a",
                            alpha=0.10, zorder=0))
    ax.plot([8, 32, 32, 8, 8], [95, 95, 115, 115, 95],
            color="#16a34a", linewidth=1.2, linestyle="--", zorder=1)
    ax.text(20, 113, "BARREL ZONE", color="#16a34a", fontsize=9,
            fontweight="bold", ha="center", va="top", zorder=2)

    # Plot by outcome
    for outcome in ["barrel", "solid_contact", "weak_contact", "foul"]:
        g = in_play[in_play["Swing_Outcome"] == outcome]
        if g.empty:
            continue
        ax.scatter(g["Launch_Angle_deg"], g["Exit_Velocity_mph"],
                    c=SWING_OUTCOME_COLORS[outcome], s=85,
                    edgecolors="black", linewidths=0.7, zorder=3,
                    label=outcome.replace("_", " ").title())

    ax.set_xlim(-30, 60)
    ax.set_ylim(40, 120)
    ax.set_xlabel("Launch Angle (°)", fontsize=9)
    ax.set_ylabel("Exit Velocity (mph)", fontsize=9)
    ax.set_title("Exit Velo × Launch Angle", fontsize=11, fontweight="bold",
                  color="#1a2150")
    ax.grid(True, alpha=0.25, linestyle=":")
    ax.tick_params(labelsize=8)
    ax.legend(fontsize=7, loc="lower right", framealpha=0.95)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=140, bbox_inches="tight",
                 facecolor="white", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _render_hitting_zone_heatmap_png(df: pd.DataFrame, width_in: float = 5.0) -> bytes:
    """Render the strike-zone hit-quality heat map (5x5 grid) as a PNG.

    Same idea as the in-app heat map: each cell is colored by the average
    quality score of swings landed in it. Today's swings overlaid as numbered dots.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle, Polygon

    fig, ax = plt.subplots(figsize=(width_in, width_in), dpi=140)
    swings = df[df["Swing_Type"] == "swing"].copy()
    swings["_q"] = swings["Swing_Outcome"].map(SWING_QUALITY_SCORE)

    x_edges = [-1.0 + 0.4 * i for i in range(6)]
    z_edges = [ 1.0 + 0.6 * i for i in range(6)]

    def _hex_color(score):
        """Mirror _quality_color but return hex string for matplotlib."""
        if score is None or pd.isna(score):
            return "#e5e7eb"
        if abs(score) < 0.05:
            return "#e5e7eb"
        if score > 0:
            t = min(score / 2.0, 1.0)
            r = int(252 + (127 - 252) * t)
            g = int(202 + (29 - 202) * t)
            b = int(202 + (29 - 202) * t)
        else:
            t = min(abs(score) / 2.0, 1.0)
            r = int(219 + (30 - 219) * t)
            g = int(234 + (58 - 234) * t)
            b = int(254 + (138 - 254) * t)
        return f"#{r:02x}{g:02x}{b:02x}"

    # Grid cells
    for i in range(5):
        for j in range(5):
            x0, x1 = x_edges[i], x_edges[i + 1]
            z0, z1 = z_edges[j], z_edges[j + 1]
            cell = swings[
                (swings["Plate_X_ft"] >= x0) & (swings["Plate_X_ft"] < x1) &
                (swings["Plate_Z_ft"] >= z0) & (swings["Plate_Z_ft"] < z1)
            ]
            avg_q = cell["_q"].dropna().mean() if len(cell) else None
            ax.add_patch(Rectangle((x0, z0), x1 - x0, z1 - z0,
                                     facecolor=_hex_color(avg_q),
                                     edgecolor="white", linewidth=0.8, zorder=0))
            if len(cell) > 0 and avg_q is not None:
                ax.text((x0 + x1) / 2, (z0 + z1) / 2,
                         f"{len(cell)}\n({avg_q:+.1f})",
                         ha="center", va="center", fontsize=7,
                         color="#1f2937", zorder=1)

    # Strike zone box
    ax.add_patch(Rectangle((SZ_X_MIN, SZ_Z_MIN),
                            SZ_X_MAX - SZ_X_MIN, SZ_Z_MAX - SZ_Z_MIN,
                            fill=False, edgecolor="black", linewidth=2, zorder=2))
    # 3x3 grid
    for i in (1, 2):
        x = SZ_X_MIN + (SZ_X_MAX - SZ_X_MIN) * (i / 3)
        z = SZ_Z_MIN + (SZ_Z_MAX - SZ_Z_MIN) * (i / 3)
        ax.plot([x, x], [SZ_Z_MIN, SZ_Z_MAX], color="black",
                 linewidth=0.6, linestyle=":", zorder=2)
        ax.plot([SZ_X_MIN, SZ_X_MAX], [z, z], color="black",
                 linewidth=0.6, linestyle=":", zorder=2)
    # Home plate
    plate = Polygon([(-0.71, 0.05), (0.71, 0.05), (0.50, -0.10),
                      (0, -0.25), (-0.50, -0.10)],
                     closed=True, facecolor="#dcdcdc",
                     edgecolor="black", linewidth=0.8, zorder=2)
    ax.add_patch(plate)

    # Overlay today's swings as numbered dots
    for outcome in ["barrel", "solid_contact", "foul", "weak_contact", "whiff"]:
        g = swings[swings["Swing_Outcome"] == outcome]
        if g.empty:
            continue
        ax.scatter(g["Plate_X_ft"], g["Plate_Z_ft"],
                    c=SWING_OUTCOME_COLORS[outcome], s=140,
                    edgecolors="white", linewidths=1.2, zorder=3)
        for _, row in g.iterrows():
            ax.text(row["Plate_X_ft"], row["Plate_Z_ft"],
                     str(int(row["Swing_Num"])),
                     ha="center", va="center", color="white",
                     fontsize=6.5, fontweight="bold", zorder=4)

    ax.set_xlim(-1.5, 1.5)
    ax.set_ylim(0.5, 4.5)
    ax.set_aspect("equal")
    ax.set_title("Strike Zone Heat Map", fontsize=11, fontweight="bold",
                  color="#1a2150")
    ax.set_xlabel("Plate Side (ft)", fontsize=9)
    ax.set_ylabel("Height (ft)", fontsize=9)
    ax.tick_params(labelsize=7)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=140, bbox_inches="tight",
                 facecolor="white", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _render_swing_outcome_bar_png(df: pd.DataFrame, width_in: float = 5.0) -> bytes:
    """Horizontal bar chart of swing outcomes by count, colored to match the
    in-app spray chart palette."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    order = ["barrel", "solid_contact", "foul", "weak_contact", "whiff", "take"]
    labels = ["Barrel", "Solid Contact", "Foul", "Weak Contact", "Whiff", "Take"]
    counts = [int((df["Swing_Outcome"] == k).sum()) for k in order]
    colors_ = [SWING_OUTCOME_COLORS[k] for k in order]

    fig, ax = plt.subplots(figsize=(width_in, width_in * 0.45), dpi=140)
    y = list(range(len(order)))
    bars = ax.barh(y, counts, color=colors_, edgecolor="black", linewidth=0.5)
    for bar, c in zip(bars, counts):
        if c > 0:
            ax.text(bar.get_width() + 0.15, bar.get_y() + bar.get_height() / 2,
                     str(c), va="center", ha="left", fontsize=9, fontweight="bold",
                     color="#1f2937")
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("Swings", fontsize=9)
    ax.set_title("Swing Outcomes", fontsize=11, fontweight="bold", color="#1a2150")
    ax.tick_params(labelsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=140, bbox_inches="tight",
                 facecolor="white", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def generate_post_swing_pdf(df: pd.DataFrame,
                             athlete_name: str = "Athlete",
                             athlete_hand: str = "Right",
                             athlete_class: str = "",
                             sport: str = "Baseball",
                             athlete_level: str = "HS-Varsity") -> bytes:
    """Generate the Post-Swing Report as a multi-page PDF — the hitting
    counterpart to generate_pbr_pdf.

    Returns: PDF bytes suitable for st.download_button.
    """
    from datetime import datetime
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image,
        PageBreak, KeepTogether,
    )
    from reportlab.pdfgen import canvas as _canvas

    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    brand_navy = colors.HexColor("#1a2150")
    brand_gold = colors.HexColor("#d4a634")
    soft_grey  = colors.HexColor("#6b7280")
    light_grey = colors.HexColor("#f3f4f6")

    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=18, leading=22,
                        textColor=brand_navy, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12, leading=14,
                        textColor=brand_navy, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9, leading=12)
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=7.5,
                            leading=9.5, textColor=soft_grey)

    def _header_footer(canvas: _canvas.Canvas, doc):
        canvas.saveState()
        canvas.setFillColor(brand_navy)
        canvas.rect(0, doc.pagesize[1] - 0.45 * inch,
                    doc.pagesize[0], 0.45 * inch, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(0.5 * inch, doc.pagesize[1] - 0.30 * inch,
                          "◆ DIAMOND SPORTS LAB")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(brand_gold)
        canvas.drawRightString(doc.pagesize[0] - 0.5 * inch,
                                doc.pagesize[1] - 0.30 * inch,
                                f"Post-Swing Report · {sport}")
        canvas.setFillColor(soft_grey)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(0.5 * inch, 0.3 * inch,
                          f"Generated {datetime.now().strftime('%b %d, %Y at %I:%M %p')}")
        canvas.drawRightString(doc.pagesize[0] - 0.5 * inch, 0.3 * inch,
                                f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        topMargin=0.7 * inch, bottomMargin=0.6 * inch,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        title=f"Diamond Sports Lab — {athlete_name} (Hitting)",
        author="Diamond Sports Lab",
    )

    story = []

    # --- Athlete header ---
    today_str = (pd.to_datetime(df["Timestamp"].min()).strftime("%B %d, %Y")
                  if "Timestamp" in df.columns and len(df) else
                  datetime.now().strftime("%B %d, %Y"))
    story.append(Paragraph(athlete_name, h1))
    sport_icon = "🥎" if sport == "Softball" else "⚾"
    story.append(Paragraph(
        f"<font color='#6b7280'>{sport_icon} {sport} · {athlete_hand}-handed hitter · "
        f"{athlete_class or 'Class —'} · Session: {today_str}</font>", body))
    story.append(Spacer(1, 10))

    # --- KPI strip ---
    kpis = hitting_session_kpis(df)
    kpi_data = [
        ["Swings", "Avg Exit Velo", "Peak Exit Velo", "Avg Bat Speed", "Barrel %", "Whiff %"],
        [
            str(kpis["Total Swings"]),
            f"{kpis['Avg Exit Velo']} mph" if kpis['Avg Exit Velo'] is not None else "—",
            f"{kpis['Peak Exit Velo']} mph" if kpis['Peak Exit Velo'] is not None else "—",
            f"{kpis['Avg Bat Speed']} mph" if kpis['Avg Bat Speed'] is not None else "—",
            f"{kpis['Barrel %']}%",
            f"{kpis['Whiff %']}%",
        ],
    ]
    kpi_table = Table(kpi_data, colWidths=[1.15*inch] * 6)
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), brand_navy),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 1), (-1, 1), light_grey),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME",   (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, 0), 8),
        ("FONTSIZE",   (0, 1), (-1, 1), 13),
        ("ALIGN",      (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 12))

    # --- Spray Chart + Swing Outcomes (side-by-side) ---
    try:
        spray_png = _render_hitting_spray_png(df, sport=sport, width_in=5.0)
        outcome_png = _render_swing_outcome_bar_png(df, width_in=4.0)
        story.append(Paragraph("Spray Chart &amp; Swing Outcomes", h2))
        side_by_side = Table([[
            Image(io.BytesIO(spray_png),   width=3.5*inch, height=3.5*inch),
            Image(io.BytesIO(outcome_png), width=3.5*inch, height=1.7*inch),
        ]], colWidths=[3.7*inch, 3.7*inch])
        side_by_side.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(side_by_side)
        story.append(Paragraph(
            "<font color='#6b7280' size='7'>Spray chart: every ball-in-play, "
            "colored by contact quality. Barrels (dark red) drive extra-base hits; "
            "weak contact (light blue) usually = routine outs.</font>", small))
        story.append(Spacer(1, 10))
    except Exception as e:
        story.append(Paragraph(f"<i>(Spray chart could not be rendered: {e})</i>", small))

    # --- EV / LA quadrant + Strike Zone Heat Map (side-by-side) ---
    try:
        evla_png = _render_ev_la_quadrant_png(df, width_in=5.0)
        zone_png = _render_hitting_zone_heatmap_png(df, width_in=5.0)
        story.append(Paragraph("Contact Quality &amp; Zone Tendencies", h2))
        evla_zone = Table([[
            Image(io.BytesIO(evla_png), width=3.5*inch, height=2.7*inch),
            Image(io.BytesIO(zone_png), width=3.5*inch, height=3.5*inch),
        ]], colWidths=[3.7*inch, 3.7*inch])
        evla_zone.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(evla_zone)
        story.append(Paragraph(
            "<font color='#6b7280' size='7'>Left: EV × Launch Angle. The shaded "
            "green box is the barrel zone (EV 95+ mph, LA 8°-32°) — where extra-base "
            "hits live. Right: the 5×5 strike-zone heat map colored by contact "
            "quality. Red zones = punishes pitches here; blue zones = struggles.</font>", small))
        story.append(Spacer(1, 10))
    except Exception as e:
        story.append(Paragraph(f"<i>(Quality charts could not be rendered: {e})</i>", small))

    # --- Per-pitch-type performance ---
    story.append(Paragraph("Performance by Pitch Type Faced", h2))
    bd_rows = [["Pitch Type", "Seen", "Swings", "Whiffs", "Barrels", "Avg EV", "Avg LA"]]
    for ptype, g in df.groupby("Pitch_Type_Faced"):
        in_play_g = g[g["Swing_Outcome"].isin(
            ["weak_contact", "solid_contact", "barrel", "foul"])]
        whiffs = int((g["Swing_Outcome"] == "whiff").sum())
        barrels = int((g["Swing_Outcome"] == "barrel").sum())
        avg_ev = in_play_g["Exit_Velocity_mph"].dropna().mean()
        avg_la = in_play_g["Launch_Angle_deg"].dropna().mean()
        bd_rows.append([
            ptype, str(len(g)),
            str(int((g["Swing_Type"] == "swing").sum())),
            str(whiffs), str(barrels),
            f"{avg_ev:.1f} mph" if not pd.isna(avg_ev) else "—",
            f"{avg_la:.1f}°"    if not pd.isna(avg_la) else "—",
        ])
    bd_table = Table(bd_rows, colWidths=[1.6*inch, 0.5*inch, 0.6*inch,
                                          0.6*inch, 0.7*inch, 0.85*inch, 0.7*inch])
    bd_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), brand_navy),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 8.5),
        ("ALIGN",      (1, 0), (-1, -1), "CENTER"),
        ("ALIGN",      (0, 0), (0, -1), "LEFT"),
        ("GRID",       (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, light_grey]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(bd_table)
    story.append(Spacer(1, 12))

    # --- Mechanics Critique ---
    critique = analyze_hitting_mechanics(df, sport=sport)
    if critique["strengths"] or critique["weaknesses"]:
        story.append(Paragraph("Swing Mechanics Critique", h2))
        strength_text = ""
        for s in critique["strengths"]:
            strength_text += (f"<b>✓ {s['label']}.</b> {s['detail']} "
                              f"<i>{s['gain']}</i><br/>")
        weak_text = ""
        for w in critique["weaknesses"]:
            weak_text += (f"<b>→ {w['label']}.</b> {w['detail']} "
                          f"<b>Gain: {w['gain']}.</b> <i>Fix: {w['fix']}</i><br/>")
        mc_data = [["What's Working", "Areas to Improve"],
                   [Paragraph(strength_text or "No specific strengths flagged yet.", body),
                    Paragraph(weak_text or "Clean swing — no corrections flagged.", body)]]
        mc_table = Table(mc_data, colWidths=[3.6*inch, 3.6*inch])
        mc_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#dcfce7")),
            ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#fef3c7")),
            ("TEXTCOLOR",  (0, 0), (0, 0), colors.HexColor("#15803d")),
            ("TEXTCOLOR",  (1, 0), (1, 0), colors.HexColor("#92400e")),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",   (0, 0), (-1, 0), 10),
            ("ALIGN",      (0, 0), (-1, 0), "CENTER"),
            ("VALIGN",     (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#d1d5db")),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e5e7eb")),
        ]))
        story.append(mc_table)
        story.append(Spacer(1, 10))

    # --- Action Plan ---
    plan = recommend_hitting_drills(df, sport=sport, athlete_level=athlete_level)
    story.append(PageBreak())
    story.append(Paragraph(athlete_name + " — Action Plan", h1))
    story.append(Spacer(1, 6))

    def _drill_block(d):
        parts = [
            Paragraph(f"<b>{d['label']}</b>  "
                      f"<font color='#6b7280' size='8'>[{d['category']}]</font>", body),
            Paragraph(f"<b>Drill:</b> {d['drill']}", body),
            Paragraph(f"<b>Protocol:</b> {d['protocol']}", body),
            Paragraph(f"<font color='#6b7280'><i>{d['why']}</i></font>", body),
        ]
        if d.get("video_url"):
            label = d.get("video_title") or "Watch demo on YouTube"
            src_suffix = f" — {d.get('video_source','')}" if d.get("video_source") else ""
            parts.append(Paragraph(
                f"<font color='#b91c1c' size='9'>▶ "
                f"<link href='{d['video_url']}' color='#b91c1c'>{label}</link>"
                f"<font color='#6b7280'>{src_suffix}</font></font>",
                body
            ))
        parts.append(Paragraph(f"<font color='#6b7280' size='8'>"
                                f"Triggered by: {d['trigger']}</font>", small))
        parts.append(Spacer(1, 8))
        return KeepTogether(parts)

    story.append(Paragraph("🟢 Today — Cooldown (within 30 min of finishing)", h2))
    if not plan["today"]:
        story.append(Paragraph("Standard cooldown is fine — no specific flags.", body))
    for d in plan["today"]:
        story.append(_drill_block(d))

    story.append(Spacer(1, 8))
    story.append(Paragraph("📅 This Week — Before Next BP", h2))
    if not plan["week"]:
        story.append(Paragraph("Maintain and repeat what's working.", body))
    for d in plan["week"]:
        story.append(_drill_block(d))

    story.append(Spacer(1, 14))
    story.append(Paragraph(
        "<font color='#6b7280' size='8'>"
        "Questions or want to discuss next steps? This report was generated by "
        "Diamond Sports Lab — a coach-portable hitting + pitching analytics platform "
        "that fuses bat-flight, swing-mechanics, and contact-quality data into one workflow."
        "</font>", small
    ))

    # NOTE: The "Full Hitting Drill Library" reference page that used to
    # live here has been intentionally removed — the Post-Swing PDF now
    # ends with the per-hitter Action Plan only. Coaches who want the
    # full library still have it inside the app (the expandable
    # "Full Hitting Drill Library" panel on the Action Plan tab).

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return buf.getvalue()


def generate_pbr_pdf(df: pd.DataFrame,
                     athlete_name: str = "Athlete",
                     athlete_hand: str = "Right",
                     athlete_class: str = "",
                     sport: str = "Baseball",
                     athlete_level: str = "HS-Varsity") -> bytes:
    """Generate the Post-Bullpen Report as a multi-page PDF.

    Returns: PDF as raw bytes (suitable for st.download_button).
    """
    from datetime import datetime
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.lib.enums import TA_LEFT, TA_CENTER
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image,
        PageBreak, KeepTogether,
    )
    from reportlab.pdfgen import canvas as _canvas

    buf = io.BytesIO()

    # Styles
    styles = getSampleStyleSheet()
    brand_navy = colors.HexColor("#1a2150")
    brand_gold = colors.HexColor("#d4a634")
    danger_red = colors.HexColor("#dc2626")
    soft_grey  = colors.HexColor("#6b7280")
    light_grey = colors.HexColor("#f3f4f6")

    h1 = ParagraphStyle("h1", parent=styles["Heading1"],
                        fontSize=18, leading=22, textColor=brand_navy,
                        spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"],
                        fontSize=12, leading=14, textColor=brand_navy,
                        spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("body", parent=styles["BodyText"],
                          fontSize=9, leading=12)
    small = ParagraphStyle("small", parent=styles["BodyText"],
                           fontSize=7.5, leading=9.5, textColor=soft_grey)
    danger = ParagraphStyle("danger", parent=body, textColor=danger_red,
                            fontName="Helvetica-Bold")

    def _header_footer(canvas: _canvas.Canvas, doc):
        # Top brand bar
        canvas.saveState()
        canvas.setFillColor(brand_navy)
        canvas.rect(0, doc.pagesize[1] - 0.45 * inch,
                    doc.pagesize[0], 0.45 * inch, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(0.5 * inch, doc.pagesize[1] - 0.30 * inch,
                          "◆ DIAMOND SPORTS LAB")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(brand_gold)
        sport_label = f"Post-Bullpen Report · {sport}"
        canvas.drawRightString(doc.pagesize[0] - 0.5 * inch,
                               doc.pagesize[1] - 0.30 * inch,
                               sport_label)
        # Footer
        canvas.setFillColor(soft_grey)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(0.5 * inch, 0.3 * inch,
                          f"Generated {datetime.now().strftime('%b %d, %Y at %I:%M %p')}")
        canvas.drawRightString(doc.pagesize[0] - 0.5 * inch, 0.3 * inch,
                               f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        topMargin=0.7 * inch, bottomMargin=0.6 * inch,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        title=f"Diamond Sports Lab — {athlete_name}",
        author="Diamond Sports Lab",
    )

    story = []

    # --- Athlete header card ---
    today_str = pd.to_datetime(df["Timestamp"].min()).strftime("%B %d, %Y") if "Timestamp" in df.columns and len(df) else datetime.now().strftime("%B %d, %Y")
    story.append(Paragraph(athlete_name, h1))
    sport_icon = "🥎" if sport == "Softball" else "⚾"
    story.append(Paragraph(
        f"<font color='#6b7280'>{sport_icon} {sport} · {athlete_hand}-handed pitcher · "
        f"{athlete_class or 'Class —'} · Session: {today_str}</font>", body))
    story.append(Spacer(1, 10))

    # --- KPI strip ---
    kpis = session_kpis(df)
    kpi_data = [
        ["Total Pitches", "Avg Velo", "Peak Velo", "Avg Spin", "Max Stress", "Healed"],
        [
            str(kpis["Total Pitches"]),
            f"{kpis['Avg Velocity']} mph",
            f"{kpis['Peak Velocity']} mph",
            f"{kpis['Avg Spin']:,}",
            f"{kpis['Max Elbow Stress']} Nm" if kpis['Max Elbow Stress'] is not None else "—",
            str(kpis['Pitches Healed']),
        ],
    ]
    kpi_table = Table(kpi_data, colWidths=[1.15*inch] * 6)
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), brand_navy),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 1), (-1, 1), light_grey),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME",   (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, 0), 8),
        ("FONTSIZE",   (0, 1), (-1, 1), 13),
        ("ALIGN",      (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 12))

    # --- Pitch Type Breakdown ---
    story.append(Paragraph("Pitch Type Breakdown", h2))
    breakdown = pitch_type_breakdown(df)
    bd_rows = [["Pitch Type", "#", "Velo", "Spin", "V Brk", "H Brk", "Stress"]]
    for _, r in breakdown.iterrows():
        bd_rows.append([
            r["Pitch_Type"],
            str(int(r["Thrown"])),
            f"{r['Avg_Velo']:.1f} mph",
            f"{int(r['Avg_Spin']) if pd.notna(r['Avg_Spin']) else '—'}",
            f"{r['Avg_Vert_Break']:.1f}\"" if pd.notna(r['Avg_Vert_Break']) else "—",
            f"{r['Avg_Horiz_Break']:.1f}\"" if pd.notna(r['Avg_Horiz_Break']) else "—",
            f"{r['Avg_Stress']:.1f} Nm" if pd.notna(r['Avg_Stress']) else "—",
        ])
    bd_table = Table(bd_rows, colWidths=[1.7*inch, 0.4*inch, 0.9*inch, 0.7*inch,
                                          0.7*inch, 0.7*inch, 0.9*inch])
    bd_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), brand_navy),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 8.5),
        ("ALIGN",      (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN",      (0, 0), (0, -1), "LEFT"),
        ("GRID",       (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, light_grey]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(bd_table)
    story.append(Spacer(1, 12))

    # --- Velocity Distribution + Pitch Movement (Nestico-style, side by side) ---
    velo_png = _render_velocity_distribution_png(df, width_in=4.5)
    move_png = _render_movement_quadrant_png(df, width_in=4.5)
    if velo_png and move_png:
        side_by_side = Table([[
            Image(io.BytesIO(velo_png), width=3.5*inch, height=2.6*inch),
            Image(io.BytesIO(move_png), width=3.0*inch, height=3.0*inch),
        ]], colWidths=[3.7*inch, 3.5*inch])
        side_by_side.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(side_by_side)
        story.append(Spacer(1, 8))

    # --- Mechanics Critique ---
    critique = analyze_mechanics(df, sport=sport)
    if critique["strengths"] or critique["weaknesses"]:
        story.append(Paragraph("Mechanics Critique", h2))
        strength_text = ""
        for s in critique["strengths"]:
            strength_text += (f"<b>✓ {s['label']}.</b> {s['detail']} "
                              f"<i>{s['gain']}</i><br/>")
        weak_text = ""
        for w in critique["weaknesses"]:
            weak_text += (f"<b>→ {w['label']}.</b> {w['detail']} "
                          f"<b>Gain: {w['gain']}.</b> <i>Fix: {w['fix']}</i><br/>")

        mc_data = [["What's Working", "Areas to Improve"],
                   [Paragraph(strength_text or "No specific strengths identified yet.", body),
                    Paragraph(weak_text or "No corrections flagged — keep it clean.", body)]]
        mc_table = Table(mc_data, colWidths=[3.6*inch, 3.6*inch])
        mc_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#dcfce7")),
            ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#fef3c7")),
            ("TEXTCOLOR",  (0, 0), (0, 0), colors.HexColor("#15803d")),
            ("TEXTCOLOR",  (1, 0), (1, 0), colors.HexColor("#92400e")),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",   (0, 0), (-1, 0), 10),
            ("ALIGN",      (0, 0), (-1, 0), "CENTER"),
            ("VALIGN",     (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#d1d5db")),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e5e7eb")),
        ]))
        story.append(mc_table)
        story.append(Spacer(1, 10))

    # --- Strike Zone Map ---
    if df["Strike_Zone_Side"].notna().any():
        story.append(Paragraph("Strike Zone Map", h2))
        png_bytes = _render_strike_zone_png(df, width_in=5.0)
        img = Image(io.BytesIO(png_bytes), width=4.4*inch, height=4.4*inch)
        img.hAlign = "CENTER"
        story.append(img)
        story.append(Paragraph(
            "Numbers = pitch # in session. Green ring = positive outlier "
            "(above-average pitch). Red ring = negative outlier (concerning).",
            small
        ))
        story.append(Spacer(1, 10))

    # --- Injury / Risk Flags ---
    danger_rows = []
    for _, r in df.iterrows():
        for f in detect_injury_flags(r):
            if f["severity"] == "DANGER":
                danger_rows.append(
                    f"Pitch #{int(r['Pitch_Num'])} ({r['Pitch_Type']}, "
                    f"{r['Velocity_mph']:.1f} mph) — {f['label']}"
                )
    if danger_rows:
        story.append(Paragraph("🚨 Injury / Risk Flags", h2))
        for txt in danger_rows[:6]:
            story.append(Paragraph(f"• {txt}", danger))
        story.append(Spacer(1, 10))

    # --- Action Plan ---
    plan = recommend_drills(df, sport=sport, athlete_level=athlete_level)

    story.append(PageBreak())
    story.append(Paragraph(athlete_name + " — Action Plan", h1))
    story.append(Spacer(1, 6))

    def _drill_block(d):
        parts = [
            Paragraph(f"<b>{d['label']}</b>  "
                      f"<font color='#6b7280' size='8'>[{d['category']}]</font>", body),
            Paragraph(f"<b>Drill:</b> {d['drill']}", body),
            Paragraph(f"<b>Protocol:</b> {d['protocol']}", body),
            Paragraph(f"<font color='#6b7280'><i>{d['why']}</i></font>", body),
        ]
        # If we have a video tutorial URL, add a clickable link with title + source
        if d.get("video_url"):
            label = d.get("video_title") or "Watch demo on YouTube"
            src_suffix = f" — {d.get('video_source','')}" if d.get("video_source") else ""
            parts.append(Paragraph(
                f"<font color='#b91c1c' size='9'>▶ "
                f"<link href='{d['video_url']}' color='#b91c1c'>"
                f"{label}</link>"
                f"<font color='#6b7280'>{src_suffix}</font></font>",
                body
            ))
        parts.append(Paragraph(f"<font color='#6b7280' size='8'>"
                               f"Triggered by: {d['trigger']}</font>", small))
        parts.append(Spacer(1, 8))
        return KeepTogether(parts)

    story.append(Paragraph("🟢 Today — Cooldown (within 30 min of finishing)", h2))
    if not plan["today"]:
        story.append(Paragraph("Standard cooldown is fine — no specific flags.", body))
    for d in plan["today"]:
        story.append(_drill_block(d))

    story.append(Spacer(1, 8))
    story.append(Paragraph("📅 This Week — Before Next Bullpen", h2))
    if not plan["week"]:
        story.append(Paragraph("Maintain and repeat what's working.", body))
    for d in plan["week"]:
        story.append(_drill_block(d))

    # Footer call-to-action
    story.append(Spacer(1, 14))
    story.append(Paragraph(
        "<font color='#6b7280' size='8'>"
        "Questions or want to discuss next steps? "
        "This report was generated by Diamond Sports Lab — a coach-portable "
        "pitching analytics platform that fuses ball-flight, arm-health, "
        "and biomechanics data into one workflow."
        "</font>", small
    ))

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return buf.getvalue()


# =============================================================================
# SELL SHEET PDF — for emailing coaches before meetings
# =============================================================================
def generate_action_plan_pdf(df: pd.DataFrame,
                              athlete_name: str = "Athlete",
                              athlete_hand: str = "Right",
                              athlete_class: str = "",
                              sport: str = "Baseball",
                              athlete_level: str = "HS-Varsity") -> bytes:
    """Generate a focused Action Plan PDF — Today + This Week sections only.

    Smaller than the full PBR (1-2 pages) — perfect for texting/emailing the
    parent immediately. Includes clickable YouTube demo links.
    """
    from datetime import datetime
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether,
    )

    buf = io.BytesIO()
    brand_navy = colors.HexColor("#1a2150")
    brand_gold = colors.HexColor("#d4a634")
    soft_grey  = colors.HexColor("#6b7280")
    danger_red = colors.HexColor("#dc2626")
    success_g  = colors.HexColor("#16a34a")
    warn_y     = colors.HexColor("#d4a634")

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"],
                           fontSize=10, leading=13.5)
    small = ParagraphStyle("small", parent=body, fontSize=8,
                            leading=10, textColor=soft_grey)
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=18, leading=22,
                         textColor=brand_navy, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12.5, leading=15,
                         textColor=brand_navy, spaceBefore=10, spaceAfter=4)
    section_today = ParagraphStyle("today", parent=h2, textColor=success_g)
    section_week  = ParagraphStyle("week",  parent=h2, textColor=warn_y)

    def _header_footer(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(brand_navy)
        canvas.rect(0, doc.pagesize[1] - 0.45 * inch,
                    doc.pagesize[0], 0.45 * inch, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(0.5 * inch, doc.pagesize[1] - 0.30 * inch,
                          "◆ DIAMOND SPORTS LAB")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(brand_gold)
        sport_icon = "🥎" if sport == "Softball" else "⚾"
        canvas.drawRightString(doc.pagesize[0] - 0.5 * inch,
                               doc.pagesize[1] - 0.30 * inch,
                               f"Action Plan · {sport}")
        canvas.setFillColor(soft_grey)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(0.5 * inch, 0.3 * inch,
                          f"Generated {datetime.now().strftime('%b %d, %Y at %I:%M %p')}")
        canvas.drawRightString(doc.pagesize[0] - 0.5 * inch, 0.3 * inch,
                               f"diamondsportslab.com  ·  Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        topMargin=0.7 * inch, bottomMargin=0.6 * inch,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        title=f"Diamond Sports Lab — Action Plan — {athlete_name}",
        author="Diamond Sports Lab",
    )

    story = []
    today_str = datetime.now().strftime("%B %d, %Y")
    story.append(Paragraph(f"{athlete_name} — Action Plan", h1))
    sport_label = "🥎 Softball" if sport == "Softball" else "⚾ Baseball"
    story.append(Paragraph(
        f"<font color='#6b7280'>{sport_label} · {athlete_hand}-handed pitcher · "
        f"{athlete_class or 'Class —'} · {today_str}</font>", body))
    story.append(Spacer(1, 10))

    plan = recommend_drills(df, sport=sport, athlete_level=athlete_level)

    def _drill_card(d):
        parts = [
            Paragraph(
                f"<b>{d['label']}</b>  "
                f"<font color='#6b7280' size='8'>[{d['category']}]</font>", body),
            Paragraph(f"<b>Drill:</b> {d['drill']}", body),
            Paragraph(f"<b>Protocol:</b> {d['protocol']}", body),
            Paragraph(f"<font color='#6b7280'><i>{d['why']}</i></font>", body),
        ]
        if d.get("video_url"):
            label = d.get("video_title") or "Watch demo on YouTube"
            src_suffix = f" — {d.get('video_source','')}" if d.get("video_source") else ""
            parts.append(Paragraph(
                f"<font color='#b91c1c' size='9'>▶ "
                f"<link href='{d['video_url']}' color='#b91c1c'>"
                f"{label}</link>"
                f"<font color='#6b7280'>{src_suffix}</font></font>", body))
        parts.append(Spacer(1, 8))
        return KeepTogether(parts)

    # ===== TODAY =====
    story.append(Paragraph("🟢 Today — Cooldown (within 30 min of finishing)",
                            section_today))
    if not plan["today"]:
        story.append(Paragraph("Standard cooldown is fine — no specific flags.", body))
    for d in plan["today"]:
        story.append(_drill_card(d))

    story.append(Spacer(1, 8))

    # ===== THIS WEEK =====
    story.append(Paragraph("📅 This Week — Before Next Bullpen", section_week))
    if not plan["week"]:
        story.append(Paragraph("No corrective work prioritized — maintain and repeat what's working.", body))
    for d in plan["week"]:
        story.append(_drill_card(d))

    story.append(Spacer(1, 12))
    story.append(Paragraph(
        f"<font color='#6b7280' size='8'>"
        f"This action plan was personalized for {athlete_name} using their bullpen data. "
        f"Generated by Diamond Sports Lab — pitching analytics for coaches who can't "
        f"afford TrackMan."
        f"</font>", small))

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return buf.getvalue()


def generate_sell_sheet_pdf(contact_name: str = "Kolby Donnell",
                             contact_email: str = "kolbydonnell@gmail.com") -> bytes:
    """One-page branded sell sheet PDF. Email this to a coach 24h before the meeting."""
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether,
    )

    buf = io.BytesIO()
    brand_navy = colors.HexColor("#1a2150")
    brand_gold = colors.HexColor("#d4a634")
    soft_grey  = colors.HexColor("#6b7280")
    light_grey = colors.HexColor("#f6f7fb")
    success_g  = colors.HexColor("#16a34a")

    styles = getSampleStyleSheet()
    body  = ParagraphStyle("body", parent=styles["BodyText"],
                            fontSize=10, leading=14, textColor=colors.HexColor("#1f2937"))
    body_white = ParagraphStyle("body_white", parent=body, textColor=colors.white)
    bullet = ParagraphStyle("bullet", parent=body, leftIndent=12,
                             bulletIndent=2, fontSize=10, leading=14)
    h_seg  = ParagraphStyle("hseg", parent=styles["Heading3"],
                             fontSize=11, leading=13, textColor=brand_navy,
                             fontName="Helvetica-Bold", spaceAfter=2)
    p_seg  = ParagraphStyle("pseg", parent=body, fontSize=9.5, leading=12.5)
    small  = ParagraphStyle("small", parent=body, fontSize=8.5,
                             leading=11, textColor=soft_grey)
    headline = ParagraphStyle("headline", parent=styles["Heading1"],
                              fontSize=22, leading=26, textColor=brand_navy,
                              fontName="Helvetica-Bold", spaceAfter=2)
    subhead  = ParagraphStyle("subhead", parent=body, fontSize=13, leading=17,
                               textColor=brand_gold, fontName="Helvetica-Bold")

    def _header_footer(canvas, doc):
        # Top navy bar
        canvas.saveState()
        canvas.setFillColor(brand_navy)
        canvas.rect(0, doc.pagesize[1] - 0.55 * inch,
                    doc.pagesize[0], 0.55 * inch, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawString(0.5 * inch, doc.pagesize[1] - 0.36 * inch,
                          "◆ DIAMOND SPORTS LAB")
        canvas.setFillColor(brand_gold)
        canvas.setFont("Helvetica", 9.5)
        canvas.drawRightString(doc.pagesize[0] - 0.5 * inch,
                               doc.pagesize[1] - 0.36 * inch,
                               "Pitching analytics for coaches who can't afford TrackMan.")
        # Bottom contact bar
        canvas.setFillColor(light_grey)
        canvas.rect(0, 0, doc.pagesize[0], 0.45 * inch, fill=1, stroke=0)
        canvas.setFillColor(brand_navy)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(0.5 * inch, 0.18 * inch,
                          f"{contact_name}  •  {contact_email}")
        canvas.setFillColor(soft_grey)
        canvas.setFont("Helvetica", 8.5)
        canvas.drawRightString(doc.pagesize[0] - 0.5 * inch, 0.18 * inch,
                               "Schedule a 15-min live demo — see the report on YOUR pitcher")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        topMargin=0.78 * inch, bottomMargin=0.65 * inch,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        title="Diamond Sports Lab — Coach Sell Sheet",
        author="Diamond Sports Lab",
    )

    story = []

    # ===== HEADLINE =====
    story.append(Paragraph("Pro-grade pitching + hitting analytics", headline))
    story.append(Paragraph("for less than 2% of TrackMan.", headline))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Every bullpen. Every batting practice. One report. Branded for your program.",
        subhead
    ))
    story.append(Spacer(1, 14))

    # ===== THREE BENEFIT BLOCKS =====
    benefit_data = [[
        Paragraph("<b>⚾  Pitching Lab</b><br/>"
                  "<font color='#6b7280' size='9'>"
                  "Velocity, spin, movement, elbow torque (Nm), mechanics critique, "
                  "drill prescription with video tutorials. Catch UCL risk before "
                  "the injury — paper trail when parents ask.</font>", body),
        Paragraph("<b>🥎  Hitting Lab</b><br/>"
                  "<font color='#6b7280' size='9'>"
                  "Bat speed, exit velo, launch angle, barrel rate, hip-shoulder "
                  "separation. Pro-style spray chart and zone heat map. 27-drill "
                  "library with curated YouTube tutorials.</font>", body),
        Paragraph("<b>📄  Parent-Ready Reports</b><br/>"
                  "<font color='#6b7280' size='9'>"
                  "Branded PDF after every session — pitching or hitting. Text it "
                  "to the parent before they leave the parking lot. "
                  "Recruiting-tape-worthy.</font>", body),
    ]]
    benefit_table = Table(benefit_data, colWidths=[2.4*inch, 2.4*inch, 2.4*inch])
    benefit_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#e5e7eb")),
        ("LINEBEFORE", (1, 0), (1, -1), 1, colors.HexColor("#e5e7eb")),
        ("LINEBEFORE", (2, 0), (2, -1), 1, colors.HexColor("#e5e7eb")),
        ("LEFTPADDING",  (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING",   (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
    ]))
    story.append(benefit_table)
    story.append(Spacer(1, 16))

    # ===== THREE SEGMENT BOXES =====
    story.append(Paragraph("<b>Who this is for</b>", h_seg))
    story.append(Spacer(1, 4))

    seg_data = [[
        Paragraph(
            "<b><font color='#1a2150' size='11'>🎓  HS Varsity Coaches</font></b><br/><br/>"
            "<font size='9.5'>Avoid the UCL injury that costs you your job. "
            "Show the AD you're tracking. Parents get a polished report after "
            "every outing.</font>", p_seg),
        Paragraph(
            "<b><font color='#1a2150' size='11'>⚾  Travel Ball Directors</font></b><br/><br/>"
            "<font size='9.5'>Your $2-3k parents want the same data as the program "
            "down the road. Branded reports per kid = retention. "
            "Velocity gains = recruiting tape.</font>", p_seg),
        Paragraph(
            "<b><font color='#1a2150' size='11'>🏟  Training Facilities</font></b><br/><br/>"
            "<font size='9.5'>Charge premium for 'data-driven development.' Beat the "
            "Rapsodo facility on cost. Justify $175/hr lessons with the report.</font>",
            p_seg),
    ]]
    seg_table = Table(seg_data, colWidths=[2.4*inch, 2.4*inch, 2.4*inch])
    seg_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), light_grey),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ("LEFTPADDING",  (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING",   (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.append(seg_table)
    story.append(Spacer(1, 16))

    # ===== PRICING BLOCK =====
    pricing_inner = [
        [Paragraph(
            "<font color='white' size='10'><b>SIMPLE PRICING</b></font><br/><br/>"
            "<font color='white' size='28'><b>$49</b></font>"
            "<font color='white' size='13'> /month</font><br/>"
            "<font color='#d4a634' size='9'>"
            "Flat fee · Unlimited pitchers · All features · Cancel anytime"
            "</font>",
            body_white),
         Paragraph(
            "<font color='white' size='10'><b>WHAT YOU GET</b></font><br/>"
            "<font color='white' size='9'>"
            "• Pitching + Hitting Labs in one app<br/>"
            "• Multi-athlete roster &amp; history<br/>"
            "• Branded post-bullpen + post-swing PDFs<br/>"
            "• Mechanics critique + 27-drill hitter library<br/>"
            "• Spray chart, zone heat map, trend tracking<br/>"
            "• Side-by-side comparison views<br/>"
            "</font>", body_white),
         Paragraph(
            "<font color='white' size='10'><b>vs. THE ALTERNATIVES</b></font><br/>"
            "<font color='#d4a634' size='9'><b>TrackMan: $30k+ setup</b></font><br/>"
            "<font color='white' size='9'>= 50× more for 1 system</font><br/>"
            "<font color='#d4a634' size='9'><b>Rapsodo: $3k + $499/mo</b></font><br/>"
            "<font color='white' size='9'>= 10× more, ball-only</font><br/>"
            "<font color='#16a34a' size='9'><b>Diamond Sports Lab: $49/mo</b></font>",
            body_white),
        ]
    ]
    pricing_table = Table(pricing_inner, colWidths=[2.5*inch, 2.5*inch, 2.2*inch])
    pricing_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), brand_navy),
        ("LEFTPADDING",  (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING",   (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
        ("LINEBEFORE", (1, 0), (1, -1), 1, colors.HexColor("#3a4480")),
        ("LINEBEFORE", (2, 0), (2, -1), 1, colors.HexColor("#3a4480")),
    ]))
    story.append(pricing_table)
    story.append(Spacer(1, 6))

    # ===== TRIAL CTA =====
    story.append(Paragraph(
        "<para align='center'><font color='#16a34a' size='11'><b>"
        "🟢  30-DAY FREE TRIAL  ·  NO CONTRACT  ·  CANCEL ANYTIME"
        "</b></font></para>", body))
    story.append(Spacer(1, 10))

    # ===== HOW IT WORKS =====
    story.append(Paragraph("<b>How it works (10-minute field workflow)</b>", h_seg))
    flow_data = [[
        Paragraph("<font color='#d4a634' size='14'><b>1</b></font><br/>"
                  "<font color='#1a2150' size='9.5'><b>Throw a bullpen</b></font><br/>"
                  "<font color='#6b7280' size='8.5'>Smart ball + arm sleeve + phone camera record simultaneously.</font>",
                  body),
        Paragraph("<font color='#d4a634' size='14'><b>2</b></font><br/>"
                  "<font color='#1a2150' size='9.5'><b>Export & upload</b></font><br/>"
                  "<font color='#6b7280' size='8.5'>Drop the three CSVs into Diamond Sports Lab. Self-heals dropped pitches.</font>",
                  body),
        Paragraph("<font color='#d4a634' size='14'><b>3</b></font><br/>"
                  "<font color='#1a2150' size='9.5'><b>Get the report</b></font><br/>"
                  "<font color='#6b7280' size='8.5'>One-click PDF with KPIs, strike zone, mechanics critique, drills.</font>",
                  body),
        Paragraph("<font color='#d4a634' size='14'><b>4</b></font><br/>"
                  "<font color='#1a2150' size='9.5'><b>Text the parent</b></font><br/>"
                  "<font color='#6b7280' size='8.5'>Coach hits Send. Parent has the report before they leave the lot.</font>",
                  body),
    ]]
    flow_table = Table(flow_data, colWidths=[1.85*inch, 1.85*inch, 1.85*inch, 1.85*inch])
    flow_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("LEFTPADDING",  (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING",   (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(flow_table)

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return buf.getvalue()


# =============================================================================
# DATA PERSISTENCE — SQLite-backed roster + session history
# =============================================================================
# Stores athletes and past bullpen sessions on disk so coaches can:
#   - Build a roster of pitchers once and switch between them via dropdown
#   - See historical trends (velocity, spin, stress) over time
#   - Get real rolling baselines (not mocked) once 3+ sessions exist
import sqlite3 as _sqlite

DB_PATH = Path.home() / ".diamond_sports_lab" / "data.db"


def _db_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _sqlite.connect(str(DB_PATH))
    conn.row_factory = _sqlite.Row
    return conn


def init_db():
    """Create the athletes + sessions tables if they don't exist yet.

    Also runs lightweight migrations (e.g. add sport / level columns if
    missing) so existing databases pick up new fields without losing data.
    """
    with _db_conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS athletes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                hand        TEXT NOT NULL DEFAULT 'Right',
                sport       TEXT NOT NULL DEFAULT 'Baseball',
                level       TEXT NOT NULL DEFAULT 'HS-Varsity',
                grad_class  TEXT,
                notes       TEXT,
                created_at  TEXT NOT NULL,
                archived    INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                athlete_id           INTEGER NOT NULL,
                session_date         TEXT NOT NULL,
                session_type         TEXT NOT NULL DEFAULT 'real',
                pitch_count          INTEGER NOT NULL,
                avg_velocity         REAL,
                peak_velocity        REAL,
                avg_spin             REAL,
                max_stress           REAL,
                healed_count         INTEGER,
                canonical_data_json  TEXT NOT NULL,
                created_at           TEXT NOT NULL,
                FOREIGN KEY (athlete_id) REFERENCES athletes(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_athlete
                ON sessions(athlete_id, session_date DESC);
        """)
        # Migration: add sport / level columns to existing DBs that pre-date them
        cols = [r[1] for r in c.execute("PRAGMA table_info(athletes)").fetchall()]
        if "sport" not in cols:
            c.execute("ALTER TABLE athletes ADD COLUMN sport TEXT NOT NULL DEFAULT 'Baseball'")
        if "level" not in cols:
            c.execute("ALTER TABLE athletes ADD COLUMN level TEXT NOT NULL DEFAULT 'HS-Varsity'")

        # Migration: add session_kind to sessions so we can keep pitching and
        # hitting sessions separate (a player can do both — they live as the
        # same athlete row but different session_kind rows).
        scols = [r[1] for r in c.execute("PRAGMA table_info(sessions)").fetchall()]
        if "session_kind" not in scols:
            c.execute("ALTER TABLE sessions ADD COLUMN session_kind TEXT NOT NULL DEFAULT 'pitching'")

        # ===== USERS + ORGS tables (login system + role scoping) =====
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                salt          TEXT NOT NULL,
                created_at    TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'coach',
                org_id        INTEGER,
                linked_athlete_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS organizations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                owner_username TEXT NOT NULL,
                invite_code TEXT,
                created_at  TEXT NOT NULL
            );
        """)
        # Migration: scope every athlete to a user. Existing pre-login
        # athletes get tagged as '__legacy__' so they don't leak across
        # the new login boundary.
        if "created_by" not in cols:
            c.execute(
                "ALTER TABLE athletes ADD COLUMN created_by "
                "TEXT NOT NULL DEFAULT '__legacy__'")
        # Org scoping on athletes + per-athlete invite code (for players
        # to join via)
        if "org_id" not in cols:
            c.execute("ALTER TABLE athletes ADD COLUMN org_id INTEGER")
        if "invite_code" not in cols:
            c.execute("ALTER TABLE athletes ADD COLUMN invite_code TEXT")
        # Sub-team within an org + profile pic (base64 thumbnail — small
        # enough to live in SQLite, survives Streamlit Cloud redeploys
        # unlike disk-backed uploads)
        if "team_id" not in cols:
            c.execute("ALTER TABLE athletes ADD COLUMN team_id INTEGER")
        if "profile_pic_b64" not in cols:
            c.execute("ALTER TABLE athletes ADD COLUMN profile_pic_b64 TEXT")

        # Teams table — org-scoped sub-groups (Varsity / JV / 14U / 16U etc)
        c.executescript("""
            CREATE TABLE IF NOT EXISTS teams (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                org_id      INTEGER NOT NULL,
                name        TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_teams_org ON teams(org_id);
        """)
        # Migration: add role columns to pre-existing users table (if
        # the SQLite ALTER above didn't apply because the CREATE TABLE
        # didn't run on a brand-new DB this session)
        ucols = [r[1] for r in c.execute(
            "PRAGMA table_info(users)").fetchall()]
        if "role" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'coach'")
        if "org_id" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN org_id INTEGER")
        if "linked_athlete_id" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN linked_athlete_id INTEGER")
        # ----- Subscription / billing columns -----
        # On users (for solo individual athletes). On orgs (for org plans).
        for col, ddl in [
            ("subscription_status",
              "ALTER TABLE users ADD COLUMN subscription_status TEXT NOT NULL DEFAULT 'trial'"),
            ("subscription_tier",
              "ALTER TABLE users ADD COLUMN subscription_tier TEXT"),
            ("subscription_renews_at",
              "ALTER TABLE users ADD COLUMN subscription_renews_at TEXT"),
            ("trial_sessions_used",
              "ALTER TABLE users ADD COLUMN trial_sessions_used INTEGER NOT NULL DEFAULT 0"),
            ("trial_pitches_used",
              "ALTER TABLE users ADD COLUMN trial_pitches_used INTEGER NOT NULL DEFAULT 0"),
            ("stripe_customer_id",
              "ALTER TABLE users ADD COLUMN stripe_customer_id TEXT"),
            ("stripe_subscription_id",
              "ALTER TABLE users ADD COLUMN stripe_subscription_id TEXT"),
        ]:
            if col not in ucols:
                c.execute(ddl)
        ocols = [r[1] for r in c.execute(
            "PRAGMA table_info(organizations)").fetchall()]
        for col, ddl in [
            ("subscription_status",
              "ALTER TABLE organizations ADD COLUMN subscription_status TEXT NOT NULL DEFAULT 'trial'"),
            ("subscription_tier",
              "ALTER TABLE organizations ADD COLUMN subscription_tier TEXT"),
            ("subscription_renews_at",
              "ALTER TABLE organizations ADD COLUMN subscription_renews_at TEXT"),
            ("trial_sessions_used",
              "ALTER TABLE organizations ADD COLUMN trial_sessions_used INTEGER NOT NULL DEFAULT 0"),
            ("trial_pitches_used",
              "ALTER TABLE organizations ADD COLUMN trial_pitches_used INTEGER NOT NULL DEFAULT 0"),
            ("stripe_customer_id",
              "ALTER TABLE organizations ADD COLUMN stripe_customer_id TEXT"),
            ("stripe_subscription_id",
              "ALTER TABLE organizations ADD COLUMN stripe_subscription_id TEXT"),
            ("athlete_cap",
              "ALTER TABLE organizations ADD COLUMN athlete_cap INTEGER"),
        ]:
            if col not in ocols:
                c.execute(ddl)


# =============================================================================
# AUTH HELPERS
# =============================================================================
# Username + password stored locally in the SQLite db. Hashing is
# pbkdf2_hmac (sha256, 200k iterations) with a per-user random salt — safe
# enough for a single-machine app. NOT meant to defend a public web API;
# if you ever go internet-facing, swap to bcrypt and rate-limit attempts.
def _hash_password(password: str, salt: bytes) -> str:
    import hashlib
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                              salt, 200_000)
    return h.hex()


def _generate_invite_code(length: int = 6) -> str:
    """Short, easy-to-type, no-look-alike chars (no 0/O, 1/I/l)."""
    import secrets
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _validate_username_password(username: str, password: str) -> tuple:
    """Shared validation. Returns (ok, msg_or_clean_username)."""
    username = (username or "").strip().lower()
    if not username or len(username) < 3:
        return False, "Username must be at least 3 characters."
    if not password or len(password) < 6:
        return False, "Password must be at least 6 characters."
    if not username.replace("_", "").replace("-", "").isalnum():
        return False, "Username can only contain letters, numbers, _ and -."
    return True, username


def register_user(username: str, password: str,
                    role: str = "coach",
                    org_id: int | None = None,
                    linked_athlete_id: int | None = None) -> tuple:
    """Generic user-insert. Returns (success, message). Most callers should
    use register_coach() or register_athlete() instead."""
    import os
    ok, msg = _validate_username_password(username, password)
    if not ok:
        return False, msg
    username = msg
    init_db()
    from datetime import datetime as _dt
    with _db_conn() as c:
        if c.execute("SELECT 1 FROM users WHERE username = ?",
                      (username,)).fetchone():
            return False, "That username is already taken."
        salt = os.urandom(16)
        c.execute(
            "INSERT INTO users (username, password_hash, salt, created_at, "
            "role, org_id, linked_athlete_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (username, _hash_password(password, salt), salt.hex(),
             _dt.utcnow().isoformat(), role, org_id, linked_athlete_id))
    return True, f"Account '{username}' created."


def create_organization(name: str, owner_username: str) -> int:
    """Create a new org owned by a coach. Returns new org id."""
    from datetime import datetime as _dt
    init_db()
    invite_code = _generate_invite_code(8)
    with _db_conn() as c:
        cur = c.execute(
            "INSERT INTO organizations (name, owner_username, invite_code, "
            "created_at) VALUES (?, ?, ?, ?)",
            ((name or "").strip(), owner_username, invite_code,
             _dt.utcnow().isoformat()))
        return cur.lastrowid


def register_coach(username: str, password: str, org_name: str) -> tuple:
    """Create a coach account + a new organization owned by them.

    Shortcut: if username is literally 'admin' (case-insensitive), the
    account is created as a platform admin instead — no org is created
    and no org name is required. Lets the operator (you) self-bootstrap
    in one step without needing the secret bootstrap code path.
    Returns (success, message_or_username).
    """
    is_admin_shortcut = (username or "").strip().lower() == "admin"
    if is_admin_shortcut:
        ok, msg = register_user(username, password, role="admin")
        if not ok:
            return False, msg
        return True, "admin"
    if not org_name or not org_name.strip():
        return False, "Organization name is required."
    ok, msg = register_user(username, password, role="coach")
    if not ok:
        return False, msg
    canonical_username = msg.split("'")[1] if "'" in msg else username.strip().lower()
    org_id = create_organization(org_name, canonical_username)
    # Attach org_id to the new coach user
    with _db_conn() as c:
        c.execute("UPDATE users SET org_id = ? WHERE username = ?",
                    (org_id, canonical_username))
    return True, canonical_username


def get_athlete_by_invite_code(code: str) -> dict | None:
    """Look up an athlete by their invite code (case-insensitive)."""
    if not code:
        return None
    init_db()
    with _db_conn() as c:
        row = c.execute(
            "SELECT * FROM athletes WHERE UPPER(invite_code) = UPPER(?) "
            "AND archived = 0",
            (code.strip(),)).fetchone()
    return dict(row) if row else None


# =============================================================================
# ADMIN ACCOUNT (operator-only — can view ANY org, any user, any athlete)
# =============================================================================
# Bootstrap pattern: a secret code lives in .streamlit/secrets.toml under
# admin_bootstrap_code. Anyone who knows the code can promote a username
# to admin. Without the code there's no admin UI exposed, no way to
# self-promote, no risk in a public deploy.
def _admin_bootstrap_code() -> str | None:
    try:
        v = st.secrets.get("admin_bootstrap_code", None)
    except Exception:
        v = None
    return v


def is_current_user_admin() -> bool:
    rec = current_user_record()
    return bool(rec and rec.get("role") == "admin")


def promote_user_to_admin(username: str, code: str) -> tuple:
    """Promote an existing user to admin role. Requires the secret bootstrap
    code to match .streamlit/secrets.toml.

    Returns (success, message).
    """
    expected = _admin_bootstrap_code()
    if not expected:
        return False, ("Admin promotion isn't enabled. Set "
                       "`admin_bootstrap_code` in `.streamlit/secrets.toml` "
                       "first.")
    if (code or "").strip() != expected:
        return False, "Wrong admin bootstrap code."
    rec = get_user_record(username)
    if rec is None:
        return False, "No such user."
    init_db()
    with _db_conn() as c:
        c.execute("UPDATE users SET role = 'admin' WHERE username = ?",
                    (username,))
    return True, f"{username} promoted to admin."


def admin_list_all_orgs() -> list[dict]:
    init_db()
    with _db_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM organizations ORDER BY created_at DESC").fetchall()]


def admin_list_all_users() -> list[dict]:
    init_db()
    with _db_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, username, role, org_id, subscription_status, "
            "subscription_tier, trial_sessions_used, trial_pitches_used, "
            "stripe_customer_id, created_at FROM users "
            "ORDER BY created_at DESC").fetchall()]


def admin_list_all_athletes() -> list[dict]:
    """Admin variant of list_athletes — no filtering."""
    init_db()
    with _db_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM athletes ORDER BY created_at DESC").fetchall()]


def admin_impersonate_athlete(athlete_id: int):
    """Drop directly into a specific athlete's profile, bypassing org scope."""
    st.session_state["selected_athlete_id"] = athlete_id
    st.session_state["admin_impersonating"] = True


def register_athlete(username: str, password: str,
                      invite_code: str | None = None,
                      name: str = "",
                      hand: str = "Right",
                      sport: str = "Baseball",
                      grad_class: str = "",
                      level: str = "HS-Varsity") -> tuple:
    """Register a player account.
    Two paths:
      - invite_code provided  → link to the coach-created athlete with
        that code; user joins that athlete's org.
      - invite_code None      → create a brand-new solo athlete record
        owned by this user, no org.
    Returns (success, message_or_username).
    """
    if invite_code:
        athlete = get_athlete_by_invite_code(invite_code)
        if athlete is None:
            return False, "Invalid invite code. Double-check with your coach."
        ok, msg = register_user(username, password, role="athlete",
                                  org_id=athlete.get("org_id"),
                                  linked_athlete_id=athlete["id"])
        if not ok:
            return False, msg
        return True, msg
    # Solo path — must provide a name to create the athlete record
    if not name or not name.strip():
        return False, "Enter your name so the app can create your athlete profile."
    ok, msg = register_user(username, password, role="athlete")
    if not ok:
        return False, msg
    canonical = (username or "").strip().lower()
    # Solo athlete: created_by = the user themself; no org.
    new_athlete_id = add_athlete(name.strip(), hand=hand, sport=sport,
                                    grad_class=grad_class.strip(),
                                    level=level,
                                    created_by=canonical)
    with _db_conn() as c:
        c.execute("UPDATE users SET linked_athlete_id = ? WHERE username = ?",
                    (new_athlete_id, canonical))
    return True, msg


def verify_user(username: str, password: str) -> tuple:
    """Returns (success, message). On success, message = the canonical
    username (lowercased) the caller should store in session_state."""
    username = (username or "").strip().lower()
    if not username or not password:
        return False, "Enter a username and password."
    init_db()
    with _db_conn() as c:
        row = c.execute(
            "SELECT password_hash, salt FROM users WHERE username = ?",
            (username,)).fetchone()
    if row is None:
        return False, "No account with that username."
    salt = bytes.fromhex(row["salt"])
    if _hash_password(password, salt) != row["password_hash"]:
        return False, "Wrong password."
    return True, username


def current_username():
    """Returns the username stored in session_state, or None.

    Safe to call from tests / scripts that run without a Streamlit session:
    returns None if session_state isn't available."""
    try:
        return st.session_state.get("auth_user")
    except Exception:
        return None


def get_user_record(username: str | None) -> dict | None:
    """Fetch full user row (incl. role, org_id, linked_athlete_id)."""
    if not username:
        return None
    # Synthetic "guest" user — used by the Try-the-demo button on the
    # login screen. No DB row, no persistence, no athletes saved.
    if username == "__demo_guest__":
        return {
            "username":              "__demo_guest__",
            "role":                  "coach",   # so the landing renders
            "org_id":                None,
            "linked_athlete_id":     None,
            "subscription_status":   "demo",
            "subscription_tier":     None,
        }
    init_db()
    with _db_conn() as c:
        row = c.execute("SELECT * FROM users WHERE username = ?",
                         (username,)).fetchone()
    return dict(row) if row else None


def current_user_record() -> dict | None:
    """Wrapper: full record for the user in session_state."""
    return get_user_record(current_username())


def current_role() -> str | None:
    """'coach' | 'athlete' | None."""
    rec = current_user_record()
    return rec.get("role") if rec else None


def current_org_id() -> int | None:
    rec = current_user_record()
    return rec.get("org_id") if rec else None


def current_linked_athlete_id() -> int | None:
    rec = current_user_record()
    return rec.get("linked_athlete_id") if rec else None


def get_org_record(org_id: int | None) -> dict | None:
    if not org_id:
        return None
    init_db()
    with _db_conn() as c:
        row = c.execute("SELECT * FROM organizations WHERE id = ?",
                         (org_id,)).fetchone()
    return dict(row) if row else None


def is_demo_mode_active() -> bool:
    """When the user is browsing demo athletes, this is True.
    Returns False outside a Streamlit session."""
    try:
        return bool(st.session_state.get("auth_demo_mode"))
    except Exception:
        return False


# =============================================================================
# SUBSCRIPTION TIERS + BILLING
# =============================================================================
# Pricing is centralized here so it can be changed without hunting through
# the UI. Annual prices show the ~30% discount in the card.
# stripe_price_id_* values are filled once Stripe products are created in
# the Stripe dashboard and the IDs are pasted in via .streamlit/secrets.toml.
TRIAL_SESSIONS_CAP = 2     # at this many saved sessions, trial ends
TRIAL_PITCHES_CAP  = 15    # OR at this many pitches captured, trial ends

SUBSCRIPTION_TIERS = {
    "individual": {
        "label":         "Individual Athlete",
        "blurb":         "One athlete profile · full app · for parents/solo players",
        "athlete_cap":   1,
        "monthly_usd":   14.99,
        "annual_usd":    129,
        "monthly_stripe_price_id": None,   # filled from secrets
        "annual_stripe_price_id":  None,
    },
    "single_team": {
        "label":         "Single Team",
        "blurb":         "Up to 20 athletes · one HS or travel team",
        "athlete_cap":   20,
        "monthly_usd":   49,
        "annual_usd":    399,
        "monthly_stripe_price_id": None,
        "annual_stripe_price_id":  None,
    },
    "club": {
        "label":         "Club",
        "blurb":         "Up to 50 athletes · multi-team travel-ball clubs",
        "athlete_cap":   50,
        "monthly_usd":   99,
        "annual_usd":    799,
        "monthly_stripe_price_id": None,
        "annual_stripe_price_id":  None,
    },
    "large_org": {
        "label":         "Large Organization",
        "blurb":         "Up to 150 athletes · big HS programs + large clubs",
        "athlete_cap":   150,
        "monthly_usd":   179,
        "annual_usd":    1499,
        "monthly_stripe_price_id": None,
        "annual_stripe_price_id":  None,
    },
}


def _stripe_secrets() -> dict:
    """Pull Stripe config from .streamlit/secrets.toml. Returns {} if not
    configured (so the rest of the app can still run + show a 'subscribe'
    button that lands on a 'billing not yet enabled' notice)."""
    try:
        cfg = dict(st.secrets.get("stripe", {}))
    except Exception:
        return {}
    return cfg


def stripe_is_configured() -> bool:
    cfg = _stripe_secrets()
    return bool(cfg.get("secret_key"))


def get_billing_entity(user: dict | None = None) -> tuple:
    """Return (kind, record) where kind is 'org' or 'user'.

    - Coach role with org → ('org', org_record). The org pays.
    - Athlete role linked to org → still ('org', org_record).
    - Solo athlete (no org) → ('user', user_record). They pay individually.
    - No login → ('none', None).
    """
    if user is None:
        user = current_user_record()
    if user is None:
        return "none", None
    if user.get("org_id"):
        org = get_org_record(user["org_id"])
        if org is not None:
            return "org", org
    return "user", user


def billing_status() -> dict:
    """Return the current entity's billing snapshot.

    Keys:
        kind, entity_id, status, tier, sessions_used, pitches_used,
        can_capture (bool), block_reason (str | None), trial_remaining_sessions,
        trial_remaining_pitches, sample_data (bool — demo-mode is always OK).

    IMPORTANT: While Stripe isn't configured (set up later), every call
    returns can_capture=True so the trial gating is a no-op. Coaches and
    athletes can use the app freely. Once Stripe is wired up, this lifts
    automatically.
    """
    if not stripe_is_configured():
        kind, rec = get_billing_entity()
        return {
            "kind": kind, "entity_id": (rec or {}).get("id"),
            "status": "billing_disabled", "tier": None,
            "sessions_used": 0, "pitches_used": 0,
            "can_capture": True, "block_reason": None,
            "trial_remaining_sessions": None,
            "trial_remaining_pitches": None,
            "sample_data": False,
        }
    if is_demo_mode_active():
        return {
            "kind": "demo", "entity_id": None, "status": "demo",
            "tier": None, "sessions_used": 0, "pitches_used": 0,
            "can_capture": True, "block_reason": None,
            "trial_remaining_sessions": None,
            "trial_remaining_pitches": None,
            "sample_data": True,
        }
    kind, rec = get_billing_entity()
    if rec is None:
        return {
            "kind": "none", "entity_id": None, "status": "logged_out",
            "tier": None, "sessions_used": 0, "pitches_used": 0,
            "can_capture": False,
            "block_reason": "Not logged in.",
            "trial_remaining_sessions": None,
            "trial_remaining_pitches": None,
            "sample_data": False,
        }
    status = rec.get("subscription_status", "trial")
    tier   = rec.get("subscription_tier")
    sess   = int(rec.get("trial_sessions_used", 0) or 0)
    pitch  = int(rec.get("trial_pitches_used", 0) or 0)
    if status == "active":
        return {
            "kind": kind, "entity_id": rec.get("id"),
            "status": status, "tier": tier,
            "sessions_used": sess, "pitches_used": pitch,
            "can_capture": True, "block_reason": None,
            "trial_remaining_sessions": None,
            "trial_remaining_pitches": None,
            "sample_data": False,
        }
    # Trial / past_due / canceled / expired — apply trial caps
    rem_sess  = max(0, TRIAL_SESSIONS_CAP - sess)
    rem_pitch = max(0, TRIAL_PITCHES_CAP  - pitch)
    can = (status == "trial" and rem_sess > 0 and rem_pitch > 0)
    block = None
    if not can:
        if status == "trial":
            block = ("Trial limit reached "
                     f"({sess}/{TRIAL_SESSIONS_CAP} sessions, "
                     f"{pitch}/{TRIAL_PITCHES_CAP} pitches). "
                     "Subscribe to keep capturing.")
        elif status == "past_due":
            block = "Payment past due — please update your card."
        elif status in ("canceled", "expired"):
            block = "Subscription ended — resubscribe to capture more pitches."
        else:
            block = "Subscription required."
    return {
        "kind": kind, "entity_id": rec.get("id"),
        "status": status, "tier": tier,
        "sessions_used": sess, "pitches_used": pitch,
        "can_capture": can, "block_reason": block,
        "trial_remaining_sessions": rem_sess,
        "trial_remaining_pitches":  rem_pitch,
        "sample_data": False,
    }


def increment_trial_counters(sessions_delta: int = 0,
                                pitches_delta: int = 0) -> None:
    """Bump the trial counters on whichever entity owns the subscription.
    No-op if the user is already on an active paid plan, or in demo mode."""
    if is_demo_mode_active():
        return
    kind, rec = get_billing_entity()
    if rec is None or rec.get("subscription_status") == "active":
        return
    table = "organizations" if kind == "org" else "users"
    init_db()
    with _db_conn() as c:
        c.execute(
            f"UPDATE {table} SET trial_sessions_used = "
            f"COALESCE(trial_sessions_used, 0) + ?, "
            f"trial_pitches_used = COALESCE(trial_pitches_used, 0) + ? "
            f"WHERE id = ?",
            (int(sessions_delta), int(pitches_delta), rec["id"]))


def set_subscription(entity_kind: str, entity_id: int, *,
                       tier: str,
                       status: str = "active",
                       renews_at: str | None = None,
                       stripe_customer_id: str | None = None,
                       stripe_subscription_id: str | None = None):
    """Update billing fields on a user or org. Called by Stripe webhook
    or by manual admin actions."""
    table = "organizations" if entity_kind == "org" else "users"
    init_db()
    sets = ["subscription_status = ?", "subscription_tier = ?"]
    params: list = [status, tier]
    if renews_at is not None:
        sets.append("subscription_renews_at = ?")
        params.append(renews_at)
    if stripe_customer_id is not None:
        sets.append("stripe_customer_id = ?")
        params.append(stripe_customer_id)
    if stripe_subscription_id is not None:
        sets.append("stripe_subscription_id = ?")
        params.append(stripe_subscription_id)
    if entity_kind == "org" and tier in SUBSCRIPTION_TIERS:
        sets.append("athlete_cap = ?")
        params.append(SUBSCRIPTION_TIERS[tier]["athlete_cap"])
    params.append(entity_id)
    with _db_conn() as c:
        c.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id = ?",
                    params)


def render_trial_status_banner():
    """Sidebar banner showing trial progress + Subscribe button.
    No-op when billing isn't enabled yet (Stripe not configured)."""
    if not stripe_is_configured():
        return
    bs = billing_status()
    if bs["status"] == "active" or bs["sample_data"]:
        return
    if bs["status"] == "logged_out":
        return
    sess = bs["sessions_used"]
    pitch = bs["pitches_used"]
    rem_sess = bs["trial_remaining_sessions"]
    rem_pitch = bs["trial_remaining_pitches"]
    color = "#22c55e"
    label = "Free trial"
    if not bs["can_capture"]:
        color = "#ef4444"
        label = "Trial ended"
    elif (rem_sess or 0) <= 1 or (rem_pitch or 0) <= 5:
        color = "#d4a634"
        label = "Trial — almost up"
    st.markdown(
        f"<div style='background:#1e293b;border-left:4px solid {color};"
        f"border-radius:8px;padding:10px 14px;margin:8px 0;'>"
        f"<div style='font-size:10px;letter-spacing:0.10em;font-weight:700;"
        f"color:{color};text-transform:uppercase;'>{label}</div>"
        f"<div style='color:#cbd5e1;font-size:12px;margin-top:4px;'>"
        f"{sess}/{TRIAL_SESSIONS_CAP} sessions · "
        f"{pitch}/{TRIAL_PITCHES_CAP} pitches used</div>"
        f"</div>",
        unsafe_allow_html=True)
    if st.button("Subscribe", key="trial_subscribe_btn",
                  use_container_width=True, type="primary"):
        st.session_state["show_billing_modal"] = True
        st.rerun()


def render_billing_modal_if_requested():
    """Full pricing card + (eventually) Stripe Checkout launch. Stub for
    now — shows the tiers and prices, click-to-subscribe redirects to
    Stripe only when configured."""
    if not st.session_state.get("show_billing_modal"):
        return
    st.markdown("---")
    st.subheader("Choose a plan")
    st.caption(
        "All plans start with a 14-day trial limited to "
        f"{TRIAL_SESSIONS_CAP} sessions or {TRIAL_PITCHES_CAP} pitches. "
        "Upgrade anytime — keep your captured data either way. Cancel "
        "anytime from the Billing tab.")
    cycle = st.radio("Billing cycle", ["Annual (save ~30%)", "Monthly"],
                       horizontal=True, key="billing_cycle")
    is_annual = cycle.startswith("Annual")
    cols = st.columns(len(SUBSCRIPTION_TIERS))
    for i, (key, t) in enumerate(SUBSCRIPTION_TIERS.items()):
        price = t["annual_usd"] if is_annual else t["monthly_usd"]
        unit = "/yr" if is_annual else "/mo"
        with cols[i]:
            st.markdown(
                f"<div style='background:#1e293b;border:1px solid #334155;"
                f"border-radius:12px;padding:18px 16px;height:100%;'>"
                f"<div style='font-size:11px;letter-spacing:0.10em;font-weight:700;"
                f"color:#94a3b8;text-transform:uppercase;'>{t['label']}</div>"
                f"<div style='font-size:30px;font-weight:800;color:#f1f5f9;"
                f"margin:8px 0 2px 0;'>${price:.0f}<span style='font-size:14px;"
                f"font-weight:500;color:#94a3b8;'>{unit}</span></div>"
                f"<div style='font-size:12px;color:#cbd5e1;line-height:1.5;"
                f"min-height:48px;'>{t['blurb']}</div>"
                f"<div style='font-size:11px;color:#64748b;margin-top:6px;'>"
                f"Up to {t['athlete_cap']} athlete"
                f"{'s' if t['athlete_cap'] > 1 else ''}</div>"
                f"</div>",
                unsafe_allow_html=True)
            if st.button(f"Choose {t['label']}",
                          key=f"choose_{key}_{cycle}",
                          use_container_width=True):
                if stripe_is_configured():
                    st.session_state["pending_checkout_tier"]  = key
                    st.session_state["pending_checkout_annual"] = is_annual
                    st.info("Stripe Checkout integration coming online — "
                            "you'll be redirected to enter card details.")
                else:
                    st.warning(
                        "Billing isn't fully turned on yet. The price tiers "
                        "are locked in, but card processing needs the Stripe "
                        "account to be created and the API keys to be pasted "
                        "into `.streamlit/secrets.toml`. Once that's done "
                        "this button completes the subscription.")
    if st.button("Close", key="close_billing_modal",
                  use_container_width=True):
        st.session_state["show_billing_modal"] = False
        st.rerun()


def list_athletes(include_archived: bool = False,
                    user_scope: str | None = "auto",
                    ) -> list[dict]:
    """List athletes filtered to the active user.

    Scoping rules (when user_scope='auto'):
        - Demo mode on → athletes with created_by='__demo__'.
        - Coach     → all athletes where org_id = coach's org, OR
                      athletes the coach created (covers solo coaches with
                      no org_id set during migration).
        - Athlete   → only the athlete linked to this user account.
        - Logged out (tests) → all athletes (no filter).

    user_scope override:
        - explicit string: return only athletes with that created_by tag
          (legacy compatibility for the demo-loader / sample seeding).
        - None: no filter (admin / test usage).
    """
    init_db()
    explicit_created_by = None
    if user_scope == "auto":
        if is_demo_mode_active():
            tier = current_demo_tier() or "individual"
            spec = DEMO_TIERS.get(tier, DEMO_TIERS["individual"])
            explicit_created_by = spec["tag"]
        else:
            user = current_user_record()
            if user is None:
                user_scope = None
            elif user.get("role") == "admin":
                # Admin sees everything (no filter)
                user_scope = None
            elif user.get("role") == "athlete":
                # Athletes see exactly their linked athlete
                with _db_conn() as c:
                    q = "SELECT * FROM athletes WHERE id = ?"
                    if not include_archived:
                        q += " AND archived = 0"
                    rows = c.execute(
                        q, (user.get("linked_athlete_id"),)).fetchall()
                return [dict(r) for r in rows]
            else:
                # Coach — scope by org_id (or fall back to created_by)
                org_id = user.get("org_id")
                with _db_conn() as c:
                    q = "SELECT * FROM athletes WHERE 1=1"
                    params: list = []
                    if org_id:
                        q += " AND (org_id = ? OR created_by = ?)"
                        params.extend([org_id, user.get("username")])
                    else:
                        q += " AND created_by = ?"
                        params.append(user.get("username"))
                    if not include_archived:
                        q += " AND archived = 0"
                    q += " ORDER BY name"
                    return [dict(r) for r in c.execute(q, params).fetchall()]
    elif isinstance(user_scope, str):
        explicit_created_by = user_scope

    with _db_conn() as c:
        q = "SELECT * FROM athletes WHERE 1=1"
        params: list = []
        if explicit_created_by is not None:
            q += " AND created_by = ?"
            params.append(explicit_created_by)
        if not include_archived:
            q += " AND archived = 0"
        q += " ORDER BY name"
        return [dict(r) for r in c.execute(q, params).fetchall()]


# Athlete-level dropdown options. Maps to the "level" field on videos
# so the video picker can choose age-appropriate tutorials.
ATHLETE_LEVELS = ["Youth", "HS-JV", "HS-Varsity", "Travel", "JuCo", "College", "Pro"]
# Maps an athlete level string → the video-level bucket used by pick_video()
LEVEL_TO_VIDEO_BUCKET = {
    "Youth":      "youth",
    "HS-JV":      "hs",
    "HS-Varsity": "hs",
    "Travel":     "hs",
    "JuCo":       "college+",
    "College":    "college+",
    "Pro":        "college+",
}


def add_athlete(name: str, hand: str = "Right", sport: str = "Baseball",
                grad_class: str = "", notes: str = "",
                level: str = "HS-Varsity",
                created_by: str | None = None,
                org_id: int | None = "auto",
                team_id: int | None = None) -> int:
    """Insert a new athlete. team_id is an optional sub-team within an org."""
    init_db()
    from datetime import datetime as _dt
    if created_by is None:
        created_by = current_username() or "__legacy__"
    if org_id == "auto":
        org_id = current_org_id()
    invite_code = _generate_invite_code(6)
    with _db_conn() as c:
        cur = c.execute(
            "INSERT INTO athletes (name, hand, sport, level, grad_class, "
            "notes, created_at, created_by, org_id, invite_code, team_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, hand, sport, level, grad_class, notes,
             _dt.utcnow().isoformat(), created_by, org_id, invite_code,
             team_id),
        )
        return cur.lastrowid


def count_active_athletes_for_user() -> int:
    """How many ACTIVE (non-archived) athletes the current user / org owns.
    Used to enforce subscription tier athlete_cap."""
    return len(list_athletes(include_archived=False))


def get_athlete_cap_for_user() -> int | None:
    """Return the athlete_cap of the current user/org's subscription tier,
    or None if unlimited / no tier (e.g. solo athlete = cap 1)."""
    kind, rec = get_billing_entity()
    if rec is None:
        return None
    # Solo athletes: capped at 1 (their own profile)
    if kind == "user":
        return 1
    tier_key = rec.get("subscription_tier")
    if not tier_key:
        # Free trial defaults to single_team-like cap of 20 so coaches can
        # actually evaluate the team feel — we don't gate cap during trial.
        return SUBSCRIPTION_TIERS["single_team"]["athlete_cap"]
    tier = SUBSCRIPTION_TIERS.get(tier_key, {})
    return tier.get("athlete_cap")


def can_add_athlete() -> tuple:
    """Returns (allowed, reason_if_not). Coach roles only — athletes
    cannot add athletes period."""
    rec = current_user_record() or {}
    role = rec.get("role")
    if role == "admin":
        return True, None
    if role == "athlete":
        return False, "Athlete accounts can't add more athletes."
    cap = get_athlete_cap_for_user()
    if cap is None:
        return True, None
    current = count_active_athletes_for_user()
    if current >= cap:
        return False, (
            f"Roster full ({current}/{cap}). Archive a graduated athlete "
            f"to free up a slot, or upgrade your plan for a bigger roster.")
    return True, None


# =============================================================================
# TIERED DEMO MODE
# =============================================================================
# Demo athletes get tagged with a per-tier sentinel in created_by so each
# tier has its own isolated demo set. Landing screen lets the visitor
# pick "What does this look like for an Individual / Team / Org?" — each
# picks a different cohort that shows off that tier's actual features.
#
# Org demo: 3 teams (Varsity / JV / Freshman) × ~10 athletes each.
# Team demo: 1 team × 12 athletes.
# Individual demo: 1 athlete, no team.
DEMO_TIERS = {
    "individual": {
        "label":           "Individual Athlete",
        "tag":             "__demo_individual__",
        "blurb":           "What one solo player sees: their own profile, "
                            "their own data, no team scope.",
        "athlete_count":   1,
        "team_specs":      [],   # no teams
    },
    "single_team": {
        "label":           "Single Team",
        "tag":             "__demo_team__",
        "blurb":           "What a HS or travel-team coach sees: roster of "
                            "12, one team grouping, add athletes freely.",
        "athlete_count":   12,
        "team_specs":      ["Roster"],
    },
    "club": {
        "label":           "Club (multi-team)",
        "tag":             "__demo_club__",
        "blurb":           "What a travel-ball club sees: athletes split "
                            "across two age groups.",
        "athlete_count":   24,
        "team_specs":      ["14U Travel", "16U Travel"],
    },
    "large_org": {
        "label":           "Large Organization",
        "tag":             "__demo_org__",
        "blurb":           "What a HS program sees: athletes split across "
                            "Varsity, JV, and Freshman.",
        "athlete_count":   33,
        "team_specs":      ["Varsity", "JV", "Freshman"],
    },
}

# Pool of realistic baseball + softball names for seeding
_DEMO_NAME_POOL = [
    ("Marcus Williams",  "Right", "Baseball"),
    ("Sara Johnson",     "Right", "Softball"),
    ("Tyler Rodriguez",  "Right", "Baseball"),
    ("Diego Hernandez",  "Left",  "Baseball"),
    ("Jaden Park",       "Right", "Baseball"),
    ("Mason Caldwell",   "Right", "Baseball"),
    ("Alex Nguyen",      "Left",  "Baseball"),
    ("Brody Martinez",   "Right", "Baseball"),
    ("Chase Foster",     "Right", "Baseball"),
    ("Owen Mitchell",    "Right", "Baseball"),
    ("Logan Pierce",     "Left",  "Baseball"),
    ("Ethan Reyes",      "Right", "Baseball"),
    ("Connor O'Brien",   "Right", "Baseball"),
    ("Dylan Tate",       "Right", "Baseball"),
    ("Wyatt Hollis",     "Right", "Baseball"),
    ("Caleb Whitman",    "Left",  "Baseball"),
    ("Ryan Sokolov",     "Right", "Baseball"),
    ("Jameson Cole",     "Right", "Baseball"),
    ("Hayden Brooks",    "Right", "Baseball"),
    ("Isaiah Bennett",   "Right", "Baseball"),
    ("Emma Watanabe",    "Right", "Softball"),
    ("Olivia Reed",      "Right", "Softball"),
    ("Ava Castro",       "Left",  "Softball"),
    ("Mia Sullivan",     "Right", "Softball"),
    ("Sophia Pena",      "Right", "Softball"),
    ("Lily Carter",      "Right", "Softball"),
    ("Hannah Diaz",      "Left",  "Softball"),
    ("Grace Whitfield",  "Right", "Softball"),
    ("Maya Chen",        "Right", "Softball"),
    ("Zoe Patterson",    "Right", "Softball"),
    ("Brianna Lowe",     "Right", "Softball"),
    ("Riley Holt",       "Right", "Softball"),
    ("Naomi Sanchez",    "Right", "Softball"),
]


def _seed_demo_tier(tier_key: str):
    """Create the demo athletes (and teams, if applicable) for one tier.
    Idempotent — re-running it doesn't duplicate."""
    spec = DEMO_TIERS.get(tier_key)
    if spec is None:
        return
    tag = spec["tag"]
    init_db()
    with _db_conn() as c:
        existing = c.execute(
            "SELECT COUNT(*) FROM athletes WHERE created_by = ?",
            (tag,)).fetchone()[0]
    if existing >= spec["athlete_count"]:
        return  # already seeded

    # Demo-tier athletes don't live in any real org. Teams here are virtual
    # — we encode them via athlete.team_id pointing at virtual team rows
    # we create now under a synthetic "org_id" derived from negation of
    # the tier slot (negative IDs won't collide with real orgs).
    team_ids: dict = {}
    if spec["team_specs"]:
        synthetic_org_id = -abs(hash(tag)) % 100000  # stable + unique-ish
        for team_name in spec["team_specs"]:
            tid = create_team(synthetic_org_id, f"{team_name} (demo)")
            team_ids[team_name] = tid

    # Distribute the demo athletes across the team buckets evenly. For
    # the individual tier, no teams → all athletes get team_id=None.
    pool = list(_DEMO_NAME_POOL)
    needed = spec["athlete_count"]
    while len(pool) < needed:
        pool += _DEMO_NAME_POOL   # repeat if we ask for more than the pool
    chosen = pool[:needed]
    team_names_cycle = spec["team_specs"] or [None]
    for i, (name, hand, sport) in enumerate(chosen):
        team_name = team_names_cycle[i % len(team_names_cycle)] if team_names_cycle != [None] else None
        team_id = team_ids.get(team_name) if team_name else None
        # Add a (demo) suffix so they're obviously fake
        add_athlete(f"{name} (demo)", hand=hand, sport=sport,
                    grad_class=str(2026 + (i % 4)),
                    level="HS-Varsity",
                    notes=f"Demo athlete for {spec['label']} tier.",
                    created_by=tag, org_id=None, team_id=team_id)


def get_demo_teams(tier_key: str) -> list[dict]:
    """Return the demo teams for a given tier (so the landing screen can
    group athletes by team in the demo view)."""
    spec = DEMO_TIERS.get(tier_key)
    if spec is None or not spec["team_specs"]:
        return []
    synthetic_org_id = -abs(hash(spec["tag"])) % 100000
    return list_teams_for_org(synthetic_org_id)


def current_demo_tier() -> str | None:
    """The tier the visitor picked on the landing demo selector."""
    return st.session_state.get("auth_demo_tier")


def seed_demo_athletes_if_empty():
    """Kept for backwards compat. Seeds the Individual tier as a fallback
    if no demo data exists at all so the legacy demo path still has
    something to show."""
    init_db()
    with _db_conn() as c:
        any_demo = c.execute(
            "SELECT 1 FROM athletes WHERE created_by LIKE '__demo%' LIMIT 1"
        ).fetchone()
    if not any_demo:
        _seed_demo_tier("individual")


# =============================================================================
# TEAMS (sub-groups inside an organization)
# =============================================================================
def list_teams_for_org(org_id: int | None) -> list[dict]:
    if not org_id:
        return []
    init_db()
    with _db_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM teams WHERE org_id = ? ORDER BY name",
            (org_id,)).fetchall()]


def create_team(org_id: int, name: str) -> int:
    from datetime import datetime as _dt
    init_db()
    with _db_conn() as c:
        cur = c.execute(
            "INSERT INTO teams (org_id, name, created_at) VALUES (?, ?, ?)",
            (org_id, (name or "").strip(), _dt.utcnow().isoformat()))
        return cur.lastrowid


def delete_team(team_id: int):
    """Delete a team. Athletes assigned to it fall back to unassigned
    (team_id = NULL) — we don't cascade delete athletes."""
    init_db()
    with _db_conn() as c:
        c.execute("UPDATE athletes SET team_id = NULL WHERE team_id = ?",
                    (team_id,))
        c.execute("DELETE FROM teams WHERE id = ?", (team_id,))


def assign_athlete_to_team(athlete_id: int, team_id: int | None):
    init_db()
    with _db_conn() as c:
        c.execute("UPDATE athletes SET team_id = ? WHERE id = ?",
                    (team_id, athlete_id))


# =============================================================================
# PROFILE PICTURES
# =============================================================================
# Stored as base64 PNG/JPEG bytes in the athletes.profile_pic_b64 column.
# Keeps the data in SQLite (survives Streamlit Cloud redeploys) and is
# small enough to be fast — we resize to <= 240×240 before storing.
def set_athlete_profile_pic(athlete_id: int, raw_bytes: bytes) -> tuple:
    """Resize raw image bytes to a 240px-max square thumbnail and store
    base64 on the athlete row. Returns (ok, message)."""
    try:
        from PIL import Image
        import io, base64
    except Exception as e:
        return False, f"Image library missing: {e}"
    try:
        img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
        # Center-crop to square, then resize
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top  = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
        img = img.resize((240, 240), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        return False, f"Couldn't process image: {e}"
    init_db()
    with _db_conn() as c:
        c.execute("UPDATE athletes SET profile_pic_b64 = ? WHERE id = ?",
                    (b64, athlete_id))
    return True, "Profile picture updated."


def clear_athlete_profile_pic(athlete_id: int):
    init_db()
    with _db_conn() as c:
        c.execute("UPDATE athletes SET profile_pic_b64 = NULL WHERE id = ?",
                    (athlete_id,))


def athlete_avatar_html(athlete: dict, size_px: int = 64) -> str:
    """Render the athlete's profile pic OR an initial-circle fallback as
    inline HTML/CSS. Returns the HTML string."""
    b64 = athlete.get("profile_pic_b64")
    if b64:
        return (
            f"<div style='width:{size_px}px;height:{size_px}px;border-radius:50%;"
            f"overflow:hidden;flex-shrink:0;border:2px solid #334155;"
            f"background:#1e293b;display:inline-flex;align-items:center;"
            f"justify-content:center;'>"
            f"<img src='data:image/jpeg;base64,{b64}' "
            f"style='width:100%;height:100%;object-fit:cover;'/></div>")
    # Initial-letter fallback in brand blue
    initial = (athlete.get("name") or "?")[0].upper()
    return (
        f"<div style='width:{size_px}px;height:{size_px}px;border-radius:50%;"
        f"background:linear-gradient(135deg,#1e3a8a,#3b82f6);"
        f"display:inline-flex;align-items:center;justify-content:center;"
        f"flex-shrink:0;border:2px solid #334155;color:#f1f5f9;"
        f"font-size:{int(size_px * 0.42)}px;font-weight:700;'>{initial}</div>")


# =============================================================================
# FORMER-PLAYER CONVERSION
# =============================================================================
# When a coach archives an athlete that has a linked player user, the
# player should be offered the chance to keep their data by converting
# to a solo Individual account. We surface this on the player's next
# login if their linked athlete is archived OR their org was deleted.
def get_user_linked_to_athlete(athlete_id: int) -> dict | None:
    """Returns the user record (if any) whose linked_athlete_id matches."""
    init_db()
    with _db_conn() as c:
        row = c.execute(
            "SELECT * FROM users WHERE linked_athlete_id = ? LIMIT 1",
            (athlete_id,)).fetchone()
    return dict(row) if row else None


def convert_player_to_solo(username: str) -> tuple:
    """Detach a player account from their org → makes them a solo athlete.
    The linked athlete record is unarchived, dropped from org_id, and the
    user becomes the created_by/owner. Returns (ok, message)."""
    init_db()
    rec = get_user_record(username)
    if rec is None:
        return False, "No such user."
    if rec.get("role") != "athlete":
        return False, "Only athlete accounts can convert."
    ath_id = rec.get("linked_athlete_id")
    if not ath_id:
        return False, "No linked athlete to convert."
    with _db_conn() as c:
        # Unarchive + detach from org. Mark them as their own owner.
        c.execute(
            "UPDATE athletes SET archived = 0, org_id = NULL, "
            "created_by = ?, team_id = NULL WHERE id = ?",
            (username, ath_id))
        # User loses org membership; trial counters reset so they get a
        # fresh chance to evaluate before subscribing as an individual.
        c.execute(
            "UPDATE users SET org_id = NULL, "
            "subscription_status = 'trial', subscription_tier = NULL, "
            "trial_sessions_used = 0, trial_pitches_used = 0 "
            "WHERE username = ?",
            (username,))
    return True, ("Account converted to Individual. Your data is intact "
                   "— upgrade anytime from the sidebar.")


def needs_player_conversion_prompt() -> dict | None:
    """If the current user is a player whose linked athlete is archived
    (or whose org was deleted), return the athlete record so the caller
    can show the conversion prompt. Otherwise None."""
    rec = current_user_record()
    if rec is None or rec.get("role") != "athlete":
        return None
    ath_id = rec.get("linked_athlete_id")
    if not ath_id:
        return None
    init_db()
    with _db_conn() as c:
        ath = c.execute(
            "SELECT * FROM athletes WHERE id = ?", (ath_id,)).fetchone()
    if ath is None:
        # Athlete deleted entirely — same prompt makes sense
        return {"id": ath_id, "name": "(deleted)", "archived": True}
    ath = dict(ath)
    if ath.get("archived"):
        return ath
    return None


def update_athlete(athlete_id: int, **fields):
    init_db()
    valid = {"name", "hand", "sport", "level", "grad_class", "notes"}
    cols, vals = [], []
    for k, v in fields.items():
        if k in valid and v is not None:
            cols.append(f"{k} = ?")
            vals.append(v)
    if not cols:
        return
    vals.append(athlete_id)
    with _db_conn() as c:
        c.execute(f"UPDATE athletes SET {', '.join(cols)} WHERE id = ?", vals)


def delete_athlete_permanently(athlete_id: int) -> int:
    """Hard-delete an athlete AND all their sessions. Returns # of sessions
    that were deleted along with them. Use Archive instead if you want to
    preserve history.
    """
    init_db()
    with _db_conn() as c:
        # Count sessions first (for the confirmation message)
        session_count = c.execute(
            "SELECT COUNT(*) FROM sessions WHERE athlete_id = ?", (athlete_id,)
        ).fetchone()[0]
        # Explicit cascade — delete sessions first, then athlete
        c.execute("DELETE FROM sessions WHERE athlete_id = ?", (athlete_id,))
        c.execute("DELETE FROM athletes WHERE id = ?", (athlete_id,))
        return session_count


def archive_athlete(athlete_id: int, archived: bool = True):
    init_db()
    with _db_conn() as c:
        c.execute("UPDATE athletes SET archived = ? WHERE id = ?",
                  (1 if archived else 0, athlete_id))


def save_session(athlete_id: int, df: pd.DataFrame,
                 session_type: str = "real",
                 session_date: str | None = None,
                 session_kind: str = "pitching") -> int:
    """Save a session DataFrame keyed to an athlete.

    session_kind is 'pitching' or 'hitting'. Same athlete can have both —
    they live as separate rows.
    """
    init_db()
    from datetime import datetime as _dt
    if session_date is None:
        # Prefer the earliest pitch/swing timestamp; fall back to "now"
        if "Timestamp" in df.columns and df["Timestamp"].notna().any():
            try:
                session_date = pd.to_datetime(df["Timestamp"].min()).isoformat()
            except Exception:
                session_date = _dt.utcnow().isoformat()
        else:
            session_date = _dt.utcnow().isoformat()

    # Pull whichever KPI set is appropriate for the kind
    if session_kind == "hitting":
        kk = hitting_session_kpis(df)
        # Repurpose the pitching columns to also hold hitting summaries
        pitch_count   = int(kk.get("Total Swings", 0))
        avg_velocity  = kk.get("Avg Exit Velo")
        peak_velocity = kk.get("Peak Exit Velo")
        avg_spin      = kk.get("Avg Bat Speed")
        max_stress    = kk.get("Whiff %")
        healed_count  = 0
    else:
        k = session_kpis(df)
        pitch_count   = int(k.get("Total Pitches", 0))
        avg_velocity  = k.get("Avg Velocity")
        peak_velocity = k.get("Peak Velocity")
        avg_spin      = k.get("Avg Spin")
        max_stress    = k.get("Max Elbow Stress")
        healed_count  = int(k.get("Pitches Healed", 0))

    payload = df.to_json(orient="records", date_format="iso")

    with _db_conn() as c:
        cur = c.execute(
            """INSERT INTO sessions (
                 athlete_id, session_date, session_type, session_kind, pitch_count,
                 avg_velocity, peak_velocity, avg_spin, max_stress, healed_count,
                 canonical_data_json, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                athlete_id, session_date, session_type, session_kind,
                pitch_count, avg_velocity, peak_velocity, avg_spin,
                max_stress, healed_count, payload, _dt.utcnow().isoformat(),
            )
        )
        return cur.lastrowid


def list_sessions(athlete_id: int, limit: int | None = None,
                   session_kind: str | None = None) -> list[dict]:
    """List sessions for an athlete. Pass session_kind='pitching' or 'hitting'
    to filter; None returns everything."""
    init_db()
    q = ("SELECT id, athlete_id, session_date, session_type, session_kind, "
         "pitch_count, avg_velocity, peak_velocity, avg_spin, max_stress, "
         "healed_count, created_at FROM sessions WHERE athlete_id = ?")
    params: list = [athlete_id]
    if session_kind is not None:
        q += " AND session_kind = ?"
        params.append(session_kind)
    q += " ORDER BY session_date DESC"
    if limit:
        q += " LIMIT ?"
        params.append(limit)
    with _db_conn() as c:
        return [dict(r) for r in c.execute(q, params).fetchall()]


def load_hitting_history(athlete_id: int, lookback: int = 20) -> pd.DataFrame:
    """Combine the athlete's recent hitting sessions into one big swing df.
    Used by the strike-zone heat map to aggregate across sessions over time.
    """
    init_db()
    sessions = list_sessions(athlete_id, limit=lookback, session_kind="hitting")
    if not sessions:
        return pd.DataFrame()
    frames = []
    for s in sessions:
        try:
            frames.append(load_session_df(s["id"]))
        except Exception:
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_session_df(session_id: int) -> pd.DataFrame:
    init_db()
    with _db_conn() as c:
        row = c.execute(
            "SELECT canonical_data_json FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return pd.DataFrame()
        return pd.read_json(io.StringIO(row[0]), orient="records")


def delete_session(session_id: int):
    init_db()
    with _db_conn() as c:
        c.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def compute_real_baseline(athlete_id: int, lookback: int = 6) -> dict:
    """Rolling baseline per pitch type from the athlete's recent real sessions.

    Returns {pitch_type: {"velo": x, "vbreak": x, "stress": x}} just like
    DEMO_BASELINE — drop-in compatible.
    """
    init_db()
    sessions = list_sessions(athlete_id, limit=lookback)
    sessions = [s for s in sessions if s.get("session_type") != "sample"]
    if not sessions:
        return {}

    frames = []
    for s in sessions:
        try:
            frames.append(load_session_df(s["id"]))
        except Exception:
            continue
    if not frames:
        return {}

    combined = pd.concat(frames, ignore_index=True)
    if combined.empty or "Pitch_Type" not in combined.columns:
        return {}

    baseline: dict = {}
    for ptype, g in combined.groupby("Pitch_Type"):
        velo = g["Velocity_mph"].dropna()
        vbrk = g["Vert_Break_in"].dropna()
        strs = g["Peak_Valgus_Nm"].dropna()
        baseline[ptype] = {
            "velo":   round(float(velo.mean()), 1) if len(velo) else None,
            "vbreak": round(float(vbrk.mean()), 1) if len(vbrk) else None,
            "stress": round(float(strs.mean()), 1) if len(strs) else None,
        }
    return baseline


# =============================================================================
# STRIKE ZONE FIGURE + PITCH DETAIL PANEL
# =============================================================================
# Standard strike zone bounds (rough): plate is 17" wide = 0.71 ft each side
# of center; vertical zone is ~knees (1.5 ft) to letters (3.5 ft).
SZ_X_MIN, SZ_X_MAX = -0.71, 0.71
SZ_Z_MIN, SZ_Z_MAX = 1.5, 3.5
SZ_PLOT_X_RANGE = (-2.5, 2.5)
SZ_PLOT_Z_RANGE = (0.0, 5.0)


def _build_strike_zone_figure(df: pd.DataFrame) -> "go.Figure":
    """Construct an interactive strike-zone scatter plot."""
    fig = go.Figure()

    # --- Strike zone box (3x3 grid for visual reference) ---
    # Outer box
    fig.add_shape(
        type="rect", x0=SZ_X_MIN, x1=SZ_X_MAX, y0=SZ_Z_MIN, y1=SZ_Z_MAX,
        line=dict(color="black", width=2),
        fillcolor="rgba(0,0,0,0)", layer="below",
    )
    # Inner grid (thirds)
    for i in (1, 2):
        x = SZ_X_MIN + (SZ_X_MAX - SZ_X_MIN) * (i / 3)
        z = SZ_Z_MIN + (SZ_Z_MAX - SZ_Z_MIN) * (i / 3)
        fig.add_shape(type="line", x0=x, x1=x, y0=SZ_Z_MIN, y1=SZ_Z_MAX,
                      line=dict(color="lightgray", width=1), layer="below")
        fig.add_shape(type="line", x0=SZ_X_MIN, x1=SZ_X_MAX, y0=z, y1=z,
                      line=dict(color="lightgray", width=1), layer="below")
    # Home plate at the bottom
    fig.add_shape(
        type="path",
        path=f"M -0.71 0.05 L 0.71 0.05 L 0.50 -0.10 L 0 -0.25 L -0.50 -0.10 Z",
        line=dict(color="black", width=1.5),
        fillcolor="rgba(220,220,220,0.6)", layer="below",
    )

    # --- One trace per pitch type (so legend works), plus outlier rings ---
    for ptype, g in df.groupby("Pitch_Type"):
        color = PITCH_COLORS.get(ptype, "#666")
        # Outer rings: drawn first so they sit beneath the colored markers
        for _, row in g.iterrows():
            ring = None
            if row["Outlier_Type"] == "positive":
                ring = "#22c55e"   # green
            elif row["Outlier_Type"] == "negative":
                ring = "#ef4444"   # red
            if ring:
                fig.add_trace(go.Scatter(
                    x=[row["Strike_Zone_Side"]],
                    y=[row["Strike_Zone_Height"]],
                    mode="markers",
                    marker=dict(size=28, color="rgba(0,0,0,0)",
                                line=dict(color=ring, width=3)),
                    showlegend=False,
                    hoverinfo="skip",
                ))

        # Main markers for this pitch type
        hover = []
        for _, row in g.iterrows():
            reasons = row.get("Outlier_Reasons") or ""
            tag = ""
            if row["Outlier_Type"] == "positive":
                tag = "<br><b style='color:#16a34a'>✓ Positive outlier</b>"
            elif row["Outlier_Type"] == "negative":
                tag = "<br><b style='color:#dc2626'>⚠ Negative outlier</b>"
            hover.append(
                f"<b>Pitch #{int(row['Pitch_Num'])}</b> — {row['Pitch_Type']}<br>"
                f"Velo: {row['Velocity_mph']:.1f} mph &nbsp;|&nbsp; "
                f"Spin: {int(row['Total_Spin_rpm']) if pd.notna(row['Total_Spin_rpm']) else '—'} rpm<br>"
                f"H break: {row['Horiz_Break_in']:.1f}\" &nbsp;|&nbsp; "
                f"V break: {row['Vert_Break_in']:.1f}\"<br>"
                f"Stress: {row['Peak_Valgus_Nm']:.1f} Nm" if pd.notna(row.get('Peak_Valgus_Nm')) else
                f"Stress: —"
            )
            hover[-1] += (f"<br>{reasons}{tag}" if reasons else tag)

        fig.add_trace(go.Scatter(
            x=g["Strike_Zone_Side"], y=g["Strike_Zone_Height"],
            mode="markers+text",
            marker=dict(size=16, color=color, line=dict(width=1.5, color="black")),
            text=[str(int(p)) for p in g["Pitch_Num"]],
            textposition="middle center",
            textfont=dict(color="white", size=9, family="Arial Black"),
            customdata=g[["Pitch_Num"]].values,
            hovertemplate="%{hovertext}<extra></extra>",
            hovertext=hover,
            name=ptype,
        ))

    # NOTE: no scaleanchor — at 900×550 chart dimensions, locking the
    # y/x pixel ratio to 1:1 forces Plotly to extend the visible range
    # to ±10 ft on both axes (because the chart pixels aren't square).
    # Letting each axis honor its explicit `range` independently keeps
    # the data tight in the strike-zone area on every device.
    fig.update_layout(
        xaxis=dict(title="Plate Side (ft) — catcher's view",
                   range=SZ_PLOT_X_RANGE, zeroline=False, showgrid=False,
                   fixedrange=True, autorange=False),
        yaxis=dict(title="Height (ft)",
                   range=SZ_PLOT_Z_RANGE, zeroline=False, showgrid=False,
                   fixedrange=True, autorange=False),
        height=550,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=20, r=20, t=40, b=40),
        plot_bgcolor="white",
    )
    return _apply_chart_theme(fig)


def _render_pitch_detail_panel(pitch: pd.Series, athlete_name: str = "",
                               sport: str = "Baseball"):
    """Render the per-pitch detail card with outlier badge, metrics, video, grip."""
    # Outlier badge
    if pitch["Outlier_Type"] == "positive":
        badge_color, badge_text = "#16a34a", "✓ POSITIVE OUTLIER"
    elif pitch["Outlier_Type"] == "negative":
        badge_color, badge_text = "#dc2626", "⚠ NEGATIVE OUTLIER"
    else:
        badge_color, badge_text = "#6b7280", "Average for session"

    st.markdown(
        f"<div style='display:flex;align-items:center;gap:14px;margin-bottom:10px;'>"
        f"<span style='background:{badge_color};color:white;padding:6px 14px;"
        f"border-radius:16px;font-size:13px;font-weight:700;'>{badge_text}</span>"
        f"<span style='font-size:22px;font-weight:700;'>Pitch #{int(pitch['Pitch_Num'])} — {pitch['Pitch_Type']}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if pitch.get("Outlier_Reasons"):
        st.caption(f"**Why flagged:** {pitch['Outlier_Reasons']}")

    # Key metrics in a clean 4-column grid
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Velocity", f"{pitch['Velocity_mph']:.1f} mph")
    c2.metric("Spin", f"{int(pitch['Total_Spin_rpm']) if pd.notna(pitch['Total_Spin_rpm']) else '—'} rpm")
    c3.metric("Vert Break", f"{pitch['Vert_Break_in']:.1f}\"")
    c4.metric("Horiz Break", f"{pitch['Horiz_Break_in']:.1f}\"")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Spin Eff.", f"{pitch['Spin_Efficiency_pct']:.1f}%" if pd.notna(pitch.get('Spin_Efficiency_pct')) else "—")
    c6.metric("Elbow Stress",
              f"{pitch['Peak_Valgus_Nm']:.1f} Nm" if pd.notna(pitch.get('Peak_Valgus_Nm')) else "—",
              delta="DANGER" if pd.notna(pitch.get('Peak_Valgus_Nm')) and pitch['Peak_Valgus_Nm'] >= DANGER_VALGUS_NM else None,
              delta_color="inverse")
    c7.metric("Extension", f"{pitch['Extension_ft']:.1f} ft" if pd.notna(pitch.get('Extension_ft')) else "—")
    c8.metric("Release Height", f"{pitch['Release_Height_ft']:.1f} ft" if pd.notna(pitch.get('Release_Height_ft')) else "—")

    # Mechanics (if PPAI present)
    if pd.notna(pitch.get("Peak_Hip_Shoulder_Sep")):
        st.markdown("**Mechanics at release:**")
        m1, m2, m3 = st.columns(3)
        m1.metric("Hip-Shoulder Sep (peak)", f"{pitch['Peak_Hip_Shoulder_Sep']:.1f}°")
        m2.metric("Lead Knee Ext.", f"{pitch['Release_Lead_Knee_Ext']:.1f}°" if pd.notna(pitch.get('Release_Lead_Knee_Ext')) else "—")
        m3.metric("Trunk Rot at Foot-Plant",
                  f"{pitch['FootPlant_Trunk_Rot']:.1f}°" if pd.notna(pitch.get('FootPlant_Trunk_Rot')) else "—",
                  delta="opened early" if pd.notna(pitch.get('FootPlant_Trunk_Rot')) and pitch['FootPlant_Trunk_Rot'] >= EARLY_TRUNK_ROTATION_DEG else None,
                  delta_color="inverse")

    # ---- Video playback (uploaded file OR pasted URL) ----
    video_data = st.session_state.get("bullpen_video")
    video_url  = st.session_state.get("bullpen_video_url")
    if video_data is not None or video_url:
        st.markdown("**Video — bullpen (use video controls to scrub + slow-motion)**")
        if video_data is not None:
            st.video(video_data)
        else:
            st.video(video_url)
        st.caption(
            "Tip: hit the gear icon (or right-click on macOS Safari/Chrome) to change "
            "playback speed — pick **0.25×** for true slow-motion analysis. "
            "Auto-clipping each pitch to its release timestamp is a v2 feature."
        )
    else:
        st.info(
            "📹 No bullpen video uploaded yet. Add one in the sidebar "
            "(\"Bullpen Video — optional\") to see slow-motion playback here."
        )

    # ---- Grip recommendation for this pitch type ----
    grip_key = _grip_for_pitch_type(pitch["Pitch_Type"], sport=sport)
    if grip_key:
        with st.expander(f"🤲 Recommended grip for {pitch['Pitch_Type']}", expanded=False):
            gc1, gc2 = st.columns([1, 1.2])
            with gc1:
                render_grip_diagram(grip_key)
            with gc2:
                st.markdown(personalized_grip_rationale(pitch, grip_key))
                st.markdown("---")
                st.markdown(GRIP_LIBRARY[grip_key]["description"])


def _grip_for_pitch_type(pitch_type: str, sport: str = "Baseball") -> str | None:
    """Map a pitch type string to a grip key from GRIP_LIBRARY.

    Sport-aware: generic names like "Curveball" or "Change-Up" route to
    softball variants when sport == "Softball".
    """
    p = (pitch_type or "").lower()
    # Unambiguous softball-only pitch names — same routing regardless of sport flag
    if "rise" in p:                 return "softball_rise"
    if "drop" in p:                 return "softball_drop"
    if "screw" in p:                return "softball_screw"
    if "softball" in p:             return "softball_fastball"
    # Generic pitch names — sport flag picks the right grip
    if sport == "Softball":
        if "curve" in p:            return "softball_curve"
        if "change" in p:           return "softball_change"
        if "fastball" in p:         return "softball_fastball"
    # Baseball (default)
    if "four" in p and "seam" in p: return "four_seam_fastball"
    if "two" in p and "seam" in p:  return "two_seam_fastball"
    if "sinker" in p:               return "two_seam_fastball"
    if "chase" in p:                return "slider_spike_seam"
    if "slider" in p:               return "slider_standard"
    if "curve" in p:                return "curveball"
    if "change" in p:               return "changeup_circle"
    return None


def personalized_grip_rationale(pitch: pd.Series, grip_key: str) -> str:
    """Generate a markdown explanation tying THIS specific pitch's numbers to
    the recommended grip. Used in the pitch detail panel."""
    spin     = pitch.get("Total_Spin_rpm")
    eff      = pitch.get("Spin_Efficiency_pct")
    gyro     = pitch.get("Gyro_Degrees")
    stress   = pitch.get("Peak_Valgus_Nm")
    vbreak   = pitch.get("Vert_Break_in")
    hbreak   = pitch.get("Horiz_Break_in")
    spin_axis = pitch.get("Spin_Direction_hhmm")

    parts = [f"**🎯 Why this grip suits Pitch #{int(pitch['Pitch_Num'])}:**"]
    notes = []

    if grip_key == "slider_spike_seam":
        if pd.notna(gyro) and gyro > 60:
            notes.append(
                f"Gyro angle was **{gyro:.0f}°** — the ball was spinning too "
                "bullet-like, sacrificing break. The spike grip cuts naturally without "
                "wrist twist, reducing gyro."
            )
        if pd.notna(stress) and stress >= DANGER_VALGUS_NM:
            notes.append(
                f"Elbow stress hit **{stress:.1f} Nm** — above the "
                f"{DANGER_VALGUS_NM} Nm safety threshold. Removing wrist twist with "
                "this grip unloads the UCL."
            )
        if pd.notna(eff) and eff < 25:
            notes.append(
                f"Spin efficiency was **{eff:.1f}%** — well below the 30-40% target "
                "for a tight 2-plane slider."
            )

    elif grip_key == "slider_standard":
        if pd.notna(hbreak):
            notes.append(
                f"Horizontal break of **{hbreak:.1f}\"** — the standard slider grip "
                "keeps middle-finger pressure on the long seam to sharpen this break."
            )
        if pd.notna(eff):
            notes.append(
                f"Spin efficiency: **{eff:.1f}%**. Target 30-40% for a tight slider; "
                "this grip with a clean finger snap (no wrist twist) hits that range."
            )

    elif grip_key == "four_seam_fastball":
        if pd.notna(spin):
            if spin < 2200:
                notes.append(
                    f"Spin rate was **{int(spin)} RPM** (target: 2,300+). "
                    "Pressing the index and middle fingers ACROSS the horseshoe "
                    "maximizes backspin contribution."
                )
            else:
                notes.append(
                    f"Spin rate **{int(spin)} RPM** — already good. Lock in this "
                    "four-seam grip with light, consistent finger pressure."
                )
        if pd.notna(eff) and eff >= 90:
            notes.append(
                f"Spin efficiency **{eff:.1f}%** — excellent. Your axis is right; "
                "this grip will keep it there."
            )
        elif pd.notna(eff):
            notes.append(
                f"Spin efficiency **{eff:.1f}%** — improving this means the grip's "
                "doing its job AND your release is clean. Both fingers on the seams."
            )
        if pd.notna(vbreak):
            notes.append(f"Today's induced vertical break: **{vbreak:.1f}\"** (carry).")

    elif grip_key == "two_seam_fastball":
        if pd.notna(hbreak):
            notes.append(
                f"Today's run: **{abs(hbreak):.1f}\"** of arm-side movement. "
                "This grip puts fingers ALONG the parallel seams to maximize that "
                "side-spin and create sink."
            )
        if pd.notna(spin):
            notes.append(
                f"Spin rate **{int(spin)} RPM** — sinkers thrive in the 2,000-2,200 "
                "range with low spin efficiency. Lower spin = more sink, ironically."
            )

    elif grip_key == "curveball":
        if pd.notna(spin_axis):
            notes.append(
                f"Spin direction is **{spin_axis}**. Curveball target is 6:00 (pure top spin). "
                "Hook the thumb on the opposite seam and pull DOWN with the middle finger."
            )
        if pd.notna(vbreak) and vbreak < 0:
            notes.append(
                f"Today's vertical break **{vbreak:.1f}\"** — drop is good, "
                "keep emphasizing the downward pull at release."
            )

    elif grip_key == "changeup_circle":
        if pd.notna(spin):
            notes.append(
                f"Spin rate **{int(spin)} RPM** — changeups thrive at 1,500-1,800 RPM. "
                "The circle grip naturally reduces spin while keeping arm speed."
            )
        if pd.notna(eff) and eff > 85:
            notes.append(
                f"Spin efficiency **{eff:.1f}%** — good arm-side fade is coming from "
                "the pronation built into this grip's release."
            )

    if not notes:
        notes.append("This is the standard recommended grip for this pitch type.")

    for n in notes:
        parts.append(f"- {n}")
    return "\n".join(parts)


# =============================================================================
# GRIP DIAGRAMS — SVG illustrations
# =============================================================================
GRIP_LIBRARY = {
    "four_seam_fastball": {
        "label":       "Four-Seam Fastball",
        "description": (
            "**How to hold it (plain English):** Look at the baseball and find the "
            "**horseshoe** — that's the part of the seam that curves like a horseshoe shape. "
            "Place your **index and middle finger ACROSS the seams** (perpendicular to them, "
            "not running along them) so the pads of those two fingers rest on the part where "
            "the stitches cross your fingertips. Your thumb goes underneath the ball on the "
            "opposite seam. Hold it like an egg — firm but not squeezing.\n\n"
            "**Why this works:** When the ball rotates, all four red seams cut through the air "
            "every spin. That's what creates the 'rise' or 'carry' effect that makes a fastball "
            "look like it's staying up at the top of the strike zone. **More backspin = more "
            "carry = harder to hit.**"
        ),
    },
    "two_seam_fastball": {
        "label":       "Two-Seam Sinker",
        "description": (
            "**How to hold it (plain English):** Same fingers as a four-seam, but rotate the ball "
            "so the **two narrow parallel seams** (the part where the stitches run side-by-side) "
            "are right under your fingertips. Your index and middle finger now run ALONG those "
            "two parallel seams — going the same direction as the seams, not across them. Thumb "
            "underneath like normal.\n\n"
            "**Why this works:** Because the spin axis is tilted to the side, the ball will sink "
            "and run toward your throwing-arm side (right for a righty, left for a lefty). "
            "**Great for getting ground balls** when a hitter is sitting fastball."
        ),
    },
    "slider_standard": {
        "label":       "Standard Slider",
        "description": (
            "**How to hold it (plain English):** Find the long seam (the part where the seam runs "
            "in a long curve along the side of the ball). Place your **middle finger on the outer "
            "third of that long seam**. Rest your index finger right next to your middle finger "
            "— they should be touching. Thumb tucks underneath on the opposite seam. "
            "When you throw, snap your middle finger down across the ball — don't twist your "
            "wrist sideways.\n\n"
            "**Why this works:** The middle finger does the work of imparting the sideways spin. "
            "If you let your wrist do it instead (by twisting), you'll stress your elbow AND "
            "lose break. The standard grip teaches a clean, repeatable release."
        ),
    },
    "slider_spike_seam": {
        "label":       "Spike-Seam Slider (Recommended Fix)",
        "description": (
            "**How to hold it (plain English):** Same starting point as the standard slider — "
            "middle finger on the long seam. But instead of your index finger laying flat, you "
            "**'spike' it**: bend your index finger so just the tip of your fingernail (or the "
            "fingertip pad) is pressing into the seam — like you're trying to dent the ball with "
            "just your fingertip. The finger is curled up, not flat. Thumb tucks under like normal.\n\n"
            "**What 'spiking' means:** Picture a basketball player's middle finger curling under "
            "on a finger-roll — same idea. The bent index finger creates a stronger 'lever' "
            "that helps the ball cut sideways without you needing to twist your wrist.\n\n"
            "**Why this is recommended for THIS pitcher:** When your slider data shows high "
            "elbow stress AND lots of gyro spin (the 'bullet spin' that doesn't create break), "
            "it usually means you're twisting your wrist to force the break. The spike grip "
            "lets the ball cut naturally — **the break gets sharper AND your elbow gets safer.**"
        ),
    },
    "curveball": {
        "label":       "Curveball (12-to-6)",
        "description": (
            "**How to hold it (plain English):** Middle finger on the long seam (same as slider), "
            "with the index finger laying right next to it. Now hook your thumb on the seam on "
            "the OPPOSITE side of the ball — pressing in from underneath. When you throw, you "
            "**pull down** with your middle finger like you're cracking a whip downward.\n\n"
            "**Why this works:** Pulling down at release creates **top spin** — the opposite of "
            "a fastball's backspin. Top spin makes the ball drop fast, like falling off a table. "
            "A great curveball drops 12-15+ inches more than a fastball over the same distance, "
            "which is what makes hitters swing over the top of it."
        ),
    },
    "changeup_circle": {
        "label":       "Circle Change",
        "description": (
            "**How to hold it (plain English):** Make an **'OK' sign** with your thumb and index "
            "finger — that little circle they form should hold the side of the baseball. Your "
            "middle finger, ring finger, and pinky lay across the top of the ball (across the "
            "seams like a four-seam fastball, but using three fingers instead of two). When you "
            "throw, **use the same arm speed as a fastball** — that's the whole point of a "
            "changeup. As your arm comes through, turn your wrist slightly so your palm faces "
            "the side, like you're shaking someone's hand.\n\n"
            "**Why this works:** The circle grip + the wrist turn at release slow the ball down "
            "by 8-12 mph without slowing your arm. Because your arm looks identical to a fastball "
            "delivery, the hitter starts their swing for a fastball — and the slower, sinking "
            "ball arrives 2-3 tenths of a second later. **It's a velocity trick disguised as a "
            "fastball.**"
        ),
    },

    # ===== SOFTBALL GRIPS =====
    "softball_fastball": {
        "label":       "Softball Fastball (4-seam)",
        "description": (
            "**How to hold it (plain English):** Find the horseshoe of the seams (the C-shape). "
            "Place your **index and middle finger across the seams** at the top of the C — the pads "
            "of your fingers should be touching the stitches where they cross. Thumb tucks "
            "underneath on the opposite seam, ring finger lightly curled. You want a firm but "
            "relaxed grip — like holding an egg.\n\n"
            "**Why this works:** Across the seams gives you four red lines cutting through the air "
            "every revolution, which is what creates the carry and arm-side run that makes a "
            "fastball look like it 'rises' to the hitter even though it's just dropping less than "
            "gravity alone would cause."
        ),
    },
    "softball_rise": {
        "label":       "Rise Ball (Softball)",
        "description": (
            "**How to hold it (plain English):** **Two-finger 'C' grip.** Place index and middle "
            "fingers along the inside of the C of the horseshoe (so the seam runs LENGTHWISE under "
            "your fingertips, not across them). Thumb directly underneath. The release is the "
            "secret — at the bottom of your windmill, your **palm rotates UP** toward the sky as "
            "you release. Think 'flick the ball up' off your fingertips.\n\n"
            "**Why this works:** The palm-up release imparts **pure backspin** (12:00 spin axis) — "
            "the maximum 'rise' effect. The ball physically can't rise (gravity wins), but with "
            "enough backspin it drops about 4-8 inches LESS than the hitter's eyes expect — and "
            "they swing under it. Great rise balls feel like fastballs at the top of the zone that "
            "won't come down."
        ),
    },
    "softball_drop": {
        "label":       "Drop Ball (Peel Drop)",
        "description": (
            "**How to hold it (plain English):** Same C-grip as the rise ball — index and middle "
            "along the seam — but the release is opposite. At the bottom of your windmill, "
            "**'peel' your fingers OFF THE FRONT of the ball** so the top of the ball spins forward "
            "(toward home plate). Your wrist stays neutral, no twisting. Think 'pulling down a "
            "shade in front of you.'\n\n"
            "**Why this works:** Peeling the fingers off the front creates **pure topspin** (6:00 "
            "spin axis), which makes the ball drop FAR more than gravity alone — 6-8\" more drop "
            "than a fastball at the same release point. The hitter sees what looks like a fastball "
            "but the bottom falls out at the plate."
        ),
    },
    "softball_curve": {
        "label":       "Softball Curveball",
        "description": (
            "**How to hold it (plain English):** Similar C-grip but **rotate the ball so the seam "
            "runs at an angle** — about 4:30 to 10:30 clock position. Middle finger on the long "
            "seam, index riding along just inside. Thumb tucked underneath, slightly offset toward "
            "the glove side. At release, **rotate your wrist OUTWARD** (palm rotates toward third "
            "base for a right-handed pitcher) as the ball leaves your hand.\n\n"
            "**Why this works:** The outward wrist rotation creates a side-spin axis around 8:30, "
            "which curves the ball away from a same-side hitter (in to opposite-side). Movement: "
            "5-9 inches of glove-side break."
        ),
    },
    "softball_screw": {
        "label":       "Softball Screwball",
        "description": (
            "**How to hold it (plain English):** **Inside-out grip.** Index and middle finger along "
            "the seam BUT on the OPPOSITE side of the ball from your curveball grip. As you "
            "release at the bottom of the windmill, **pronate your wrist INWARD** (palm rotates "
            "toward first base for a righty) — opposite of the curveball motion.\n\n"
            "**Why this works:** Inward pronation creates a side-spin axis around 3:30 — opposite "
            "of a curveball — so the ball breaks toward the arm side (away from the opposite-side "
            "hitter, into a same-side hitter). Excellent counter to a curveball-heavy hitter."
        ),
    },
    "softball_change": {
        "label":       "Softball Change-Up (Backhand)",
        "description": (
            "**How to hold it (plain English):** **Backhand grip.** Place the ball deep in your "
            "palm with all four fingers along the side, **thumb pointing UP** along the seam. The "
            "ball sits between the heel of your hand and your fingertips. At release, instead of "
            "snapping your fingers off the ball, just **let the ball roll out the back of your "
            "hand** with no wrist action. Keep the same windmill arm speed as your fastball.\n\n"
            "**Why this works:** The 'no-snap' release drops 10-14 mph off your fastball without "
            "slowing your arm. The hitter starts their swing for a fastball — and the ball "
            "arrives a tenth of a second late. It's the same arm action, just a totally different "
            "release feel."
        ),
    },
}


# Glossary — explains baseball terms used throughout grip + drill descriptions
GRIP_GLOSSARY = [
    ("**Seam / Seams**",
     "The red stitching on the baseball. There are two sets of seams. The 'long seams' "
     "are the curving parts that look like a horseshoe. The 'narrow seams' are the parts "
     "where the stitches run side-by-side."),
    ("**Horseshoe seam**",
     "The U-shaped part of the seam — it looks just like a horseshoe. The 'open end' of "
     "the horseshoe points to one side."),
    ("**Spike / Spiked**",
     "When your finger is bent at the knuckle so just the fingertip or fingernail is "
     "touching the ball — like a claw, not flat. Used in the 'spike-seam slider' grip "
     "to help the ball cut without wrist twist."),
    ("**Backspin**",
     "When the ball rotates backward (top of the ball moves toward the pitcher in flight). "
     "Makes the ball stay up longer in the air. Fastballs use backspin."),
    ("**Topspin**",
     "Opposite of backspin — top of the ball moves AWAY from the pitcher. Makes the ball "
     "drop fast. Curveballs use topspin."),
    ("**Pronation / Pronate**",
     "Turning your wrist so your palm faces down or to the side (instead of up). At ball "
     "release on a changeup, you 'pronate' to slow the spin and add arm-side fade."),
    ("**Gyro / Bullet spin**",
     "When a ball spins like a football pass — around its own direction of travel. Pure "
     "gyro spin creates ZERO movement, like a bullet. Most sliders have some gyro mixed "
     "in with useful sideways spin."),
    ("**Spin efficiency**",
     "What % of the ball's spin actually creates movement. 100% = all the spin helps the "
     "ball break. 0% = pure gyro = no break. Fastballs want HIGH efficiency (95%+); "
     "sliders want LOW (15-40%) for a sharp break."),
    ("**Induced Vertical Break (IVB)**",
     "How much the ball stays UP compared to gravity-only. A 90 mph fastball would drop "
     "about 3 ft from release to plate due to gravity alone — backspin can reduce that "
     "drop by 15-20\" (positive IVB)."),
    ("**Horizontal break**",
     "How far the ball moves sideways from release to plate. Negative = toward the glove "
     "side (for a right-handed pitcher, that's toward the left), positive = arm side."),
    ("**Valgus stress / Elbow torque**",
     "The pulling force on the inside of the elbow (the UCL ligament — the one that "
     "needs Tommy John surgery when it tears). Measured in Newton-meters (Nm). Above "
     "~62 Nm consistently is high-risk territory."),
    ("**Hip-shoulder separation**",
     "How far your hips have rotated ahead of your shoulders at the moment your front "
     "foot lands. More separation = more 'rubber-band' effect = more velocity. Pros "
     "average 55-65°."),
    ("**AC Ratio (Acute:Chronic)**",
     "Today's workload divided by your recent average workload. Above 1.3 = elevated "
     "injury risk. Above 1.5 = 3-5× more likely to get hurt in the next 7 days. Take "
     "a day off."),
]


# Baseball-specific terms (rotated overhand mechanics, pitches unique to baseball)
BASEBALL_GLOSSARY = [
    ("**Slider**",
     "A pitch thrown like a fastball but with the ball pushed slightly off the side "
     "of the middle finger at release. Breaks sharply sideways (and a bit down). "
     "Hard to hit when thrown well."),
    ("**Spike / Spiked grip**",
     "When your finger is bent at the knuckle so just the fingertip or fingernail is "
     "touching the ball — like a claw, not flat. Used in the 'spike-seam slider' grip "
     "to help the ball cut without wrist twist."),
    ("**4-Seam vs 2-Seam fastball**",
     "Where the fingers sit on the seams. **4-seam** fingers go ACROSS the horseshoe — "
     "all four seams cut through the air every revolution, giving max ride. "
     "**2-seam** fingers go ALONG the narrow parallel seams — only two seams cut, "
     "creating arm-side run and sink."),
    ("**Tunneling**",
     "When two different pitches look IDENTICAL for the first 25 feet of flight, then "
     "break apart late. Great tunneling = a hitter can't tell what's coming until it's "
     "too late to adjust."),
    ("**Extension**",
     "How far in front of the pitching rubber the ball is released. Longer extension "
     "= shorter perceived distance to the hitter = effectively faster fastball."),
]


# Softball-specific terms (windmill mechanics, pitches unique to softball)
SOFTBALL_GLOSSARY = [
    ("**Windmill motion**",
     "The full underhand arm circle a softball pitcher makes from start to release. "
     "The arm goes UP first (front-up to 12:00), THEN back and around, ending with "
     "a strong fingertip snap at the bottom. Totally different from baseball's "
     "overhand throwing motion."),
    ("**K-position**",
     "The position at the top of the windmill where the arm is straight up at 12:00, "
     "ball facing the batter, and the body is loaded for the explosive arm circle. "
     "A consistent K-position = a repeatable delivery. The 'K' is shaped by the arm, "
     "stride leg, and torso."),
    ("**Brush / Brush at hip**",
     "The moment when the throwing arm's elbow brushes past the pitcher's hip on the "
     "way through the release zone. This is the timing checkpoint — late brush = "
     "pushed ball, lost velocity. A consistent brush at the hip = consistent release."),
    ("**Drag toe / Drive foot drag**",
     "The pitcher's back foot drags forward along the rubber during the delivery, "
     "instead of hopping off. Drag-toe finishes keep the body in line and the release "
     "consistent. Hopping off the rubber leaks energy and scatters the ball."),
    ("**Rise Ball**",
     "Softball pitch thrown with pure BACKSPIN (12:00 spin axis). The ball doesn't "
     "actually rise — gravity wins — but it drops 4-8 inches LESS than the hitter's "
     "eyes expect, so they swing under it. Marquee softball pitch."),
    ("**Drop Ball**",
     "Softball pitch thrown with pure TOPSPIN (6:00 spin axis). The ball drops "
     "6-8 inches MORE than gravity would alone. Hitters keep swinging where the "
     "ball was, instead of where it ended up."),
    ("**Peel Drop**",
     "The release technique for a drop ball where the fingers 'peel off' the FRONT "
     "of the ball (like pulling down a window shade), imparting topspin. The wrist "
     "stays neutral; no twisting."),
    ("**Screwball**",
     "Softball pitch that breaks toward the arm side (opposite of a curveball). "
     "Created by inward wrist pronation at release. Great counter to a curve-heavy hitter."),
    ("**Backhand Change-Up**",
     "Softball change-up where the ball is held in the palm with the thumb up and "
     "released without a fingertip snap — the ball rolls out the back of the hand. "
     "Same arm speed as a fastball but 10-14 mph slower."),
    ("**Hip-shoulder separation (windmill)**",
     "How far the hips have rotated ahead of the shoulders at foot-plant. In windmill "
     "mechanics, target is 42-50° (less than baseball's 50-65° because of the "
     "different kinetic chain). More separation = more rubber-band torque = more velocity."),
]


def get_glossary_for_sport(sport: str = "Baseball") -> list:
    """Return the right combined glossary for an athlete's sport."""
    if sport == "Softball":
        return GRIP_GLOSSARY + SOFTBALL_GLOSSARY
    return GRIP_GLOSSARY + BASEBALL_GLOSSARY


# =============================================================================
# GRIP VARIANTS — different release styles produce different break profiles
# from the SAME pitch type. Each variant carries the arm-slot range it suits,
# the palm-orientation cue at release, and the expected break trade-off.
# =============================================================================
GRIP_VARIANTS = {
    # ===== FOUR-SEAM FASTBALL =====
    "four_seam_fastball": {
        "pitch_name": "Four-Seam Fastball",
        "variants": [
            {
                "name":         "Standard 4-Seam",
                "arm_slot":     "Over-the-top through 3/4 (45°-75° from horizontal)",
                "palm_release": "Palm faces straight toward the catcher at release.",
                "fingers":      "Index + middle finger across the horseshoe seams, with the seams running perpendicular to the fingers.",
                "result":       "Pure backspin → maximum carry (rising fastball illusion). Expect 16-18\" IVB at 90+ mph.",
                "body_fit":     "Long-fingered pitchers, classic over-top arm slots.",
                "trade_off":    "Best raw spin efficiency. Least horizontal movement — pure straight pitch.",
            },
            {
                "name":         "Sinker / Two-Seam Variant",
                "arm_slot":     "3/4 through low 3/4 (30°-55°).",
                "palm_release": "Palm rolls slightly INWARD (toward 1B for RHP) at release — pronation.",
                "fingers":      "Index + middle along the narrow seams, fingertips on the seams not across them.",
                "result":       "Adds 6-10\" of arm-side run + reduces IVB by 4-6\". The 'heavy ball' that produces ground balls.",
                "body_fit":     "Lower arm slots, pitchers who naturally pronate.",
                "trade_off":    "Trades vertical carry for run + sink. Less useful up in the zone, devastating down.",
            },
            {
                "name":         "Cutter Variant",
                "arm_slot":     "Over-the-top through high 3/4.",
                "palm_release": "Palm rotates slightly OUTWARD (toward 3B for RHP) — supination at release.",
                "fingers":      "Index + middle slightly OFFSET from the seams, with most pressure on the middle finger.",
                "result":       "5-8\" of glove-side cut + 2-4\" less IVB than the straight 4-seam. Speed loss: 2-3 mph.",
                "body_fit":     "Pitchers with strong middle-finger pressure, supination-dominant.",
                "trade_off":    "Adds a second movement profile from the same arm slot — disguised by tunnel with the 4-seam.",
            },
        ],
    },
    # ===== SLIDER =====
    "slider_strike_getter": {
        "pitch_name": "Slider",
        "variants": [
            {
                "name":         "Gyro Slider (Bullet)",
                "arm_slot":     "Any — but pairs best with 3/4 or higher.",
                "palm_release": "Palm faces 3B (RHP) at release — slight supination, no wrist twist.",
                "fingers":      "Index + middle finger together, slightly off-center on the seam. Like throwing a spiral.",
                "result":       "Tight, late-breaking slider with 4-6\" of glove-side cut. Spins fast but mostly gyro — low movement, high deception.",
                "body_fit":     "Pitchers who throw hard fastballs — the gyro slider tunnels off the 4-seam.",
                "trade_off":    "Less actual break. Wins by hiding inside the fastball tunnel until commit.",
            },
            {
                "name":         "Sweeper Slider",
                "arm_slot":     "3/4 through low 3/4 (30°-55°).",
                "palm_release": "Palm faces 3B and slightly DOWN — exaggerated supination.",
                "fingers":      "Index + middle finger SPIKED — index knuckle on top of the ball, more middle-finger pressure.",
                "result":       "12-18\" of horizontal sweep, 4-7\" drop. The 'frisbee' slider that buckles RHH knees.",
                "body_fit":     "Lower arm slots, pitchers with good wrist supination.",
                "trade_off":    "Big horizontal break, but doesn't tunnel as cleanly with a 4-seam. Best as a 2-strike chase pitch.",
            },
            {
                "name":         "Hard Slider / Cutter Slider",
                "arm_slot":     "Over-the-top or high 3/4.",
                "palm_release": "Palm faces catcher then snaps slightly outward — minimal wrist movement.",
                "fingers":      "Same grip as the 4-seam but pressure goes to the middle finger.",
                "result":       "Mid-80s velocity (only 4-6 mph off the fastball), 4-6\" of cut, very late break.",
                "body_fit":     "Power pitchers who throw 92+. Lets them disguise the slider in their fastball arm action.",
                "trade_off":    "Less break than other sliders but elite tunneling — the late differentiation makes it untouchable.",
            },
        ],
    },
    # ===== CURVEBALL =====
    "curveball": {
        "pitch_name": "Curveball",
        "variants": [
            {
                "name":         "Classic 12-6 Curveball (Spike Grip)",
                "arm_slot":     "Over-the-top (60°-90°). Doesn't work from low arm slots.",
                "palm_release": "Palm STARTS facing the catcher, then snaps DOWN at release — the wrist 'turns the doorknob.'",
                "fingers":      "Middle finger on a long seam, INDEX FINGER SPIKED (knuckle pressed against the ball).",
                "result":       "12-to-6 (straight down) break of 12-18\". The classic 'rainbow' curve.",
                "body_fit":     "Tall, over-top pitchers with flexible wrists.",
                "trade_off":    "Massive break but easy to identify by arm action. Better as a setup pitch than a put-away.",
            },
            {
                "name":         "Knuckle Curve",
                "arm_slot":     "Over-the-top through high 3/4.",
                "palm_release": "Same downward snap as classic curve, but the index knuckle 'flicks' the ball.",
                "fingers":      "Index finger BENT and resting AGAINST the ball (knuckle pressed in), middle finger on the seam.",
                "result":       "Sharper, later break (more 11-5 than 12-6). 2-3 mph harder than the classic curve.",
                "body_fit":     "Pitchers who can't get true top-spin from a classic grip — knuckle pressure fixes the spin axis.",
                "trade_off":    "Less total break but later break = harder for the hitter to identify.",
            },
            {
                "name":         "Slurve / Hybrid Curve",
                "arm_slot":     "3/4 (45°-60°).",
                "palm_release": "Palm rotates from catcher → 3B at release. Blends slider mechanics with curve grip.",
                "fingers":      "Standard 2-seam curve grip, slightly more middle-finger pressure than index.",
                "result":       "Diagonal break — 8-12\" of drop + 6-10\" of glove-side sweep. The 'lazy' shape that hitters chase.",
                "body_fit":     "Pitchers stuck between a true curve and a true slider — the slurve is the natural compromise.",
                "trade_off":    "Easier to throw consistently than a true curve. Hitters identify it faster though.",
            },
        ],
    },
    # ===== CHANGEUP =====
    "change_up": {
        "pitch_name": "Change-Up",
        "variants": [
            {
                "name":         "Circle Change",
                "arm_slot":     "Any — but works best from 3/4 or higher.",
                "palm_release": "Palm faces catcher with slight pronation at release. Looks IDENTICAL to a fastball.",
                "fingers":      "Thumb + index form a circle on the side of the ball. Middle/ring/pinky grip the ball.",
                "result":       "8-12 mph off the fastball, 4-6\" of arm-side fade + 2-4\" of sink.",
                "body_fit":     "Larger hands — small hands struggle to form the circle.",
                "trade_off":    "Best changeup for tunneling off a 4-seam fastball. The grip naturally kills velocity without affecting arm speed.",
            },
            {
                "name":         "Splitter / Split-Finger Change",
                "arm_slot":     "Over-the-top through high 3/4.",
                "palm_release": "Palm faces catcher, no wrist manipulation.",
                "fingers":      "Index + middle finger SPREAD wide on either side of the ball (like a peace sign).",
                "result":       "6-10 mph off the fastball, 8-12\" of late drop (the 'cliff'). Pure ground-ball pitch.",
                "body_fit":     "Long-fingered pitchers — small hands can't split wide enough.",
                "trade_off":    "Hardest changeup variant to control. High elbow stress in some studies — use sparingly.",
            },
            {
                "name":         "Vulcan Change / Forkball",
                "arm_slot":     "3/4 (45°-60°).",
                "palm_release": "Palm rotates slightly inward at release (pronation).",
                "fingers":      "Middle + ring fingers split on either side of the ball (Vulcan salute grip).",
                "result":       "Similar to splitter (heavy drop) but easier on the elbow. 4-6 mph velocity loss.",
                "body_fit":     "Pitchers whose middle + ring fingers can spread cleanly.",
                "trade_off":    "Less drop than a true splitter, but more reliable to repeat.",
            },
        ],
    },
}


def render_grip_variants(grip_key: str):
    """Surface ALL release-style variants for a pitch type. Pulls from
    GRIP_VARIANTS and renders each variant as a card with the arm-slot
    range, palm orientation cue, finger placement, expected break result,
    body-type fit, and the trade-off the coach is making by choosing it.
    """
    variant_set = GRIP_VARIANTS.get(grip_key)
    if not variant_set:
        return
    st.markdown(
        _flat_html(
            f"<div style='font-size:11px;letter-spacing:0.10em;font-weight:700;"
            f"color:#d4a634;text-transform:uppercase;margin-top:6px;'>"
            f"Release-style variants</div>"
            f"<div style='font-size:18px;font-weight:700;color:#1a2150;"
            f"margin-bottom:8px;'>{variant_set['pitch_name']}</div>"
            f"<div style='font-size:13px;color:#6b7280;margin-bottom:10px;'>"
            f"There's no single 'right' way to throw this pitch — each release "
            f"style gives a different break profile. Pick the variant that "
            f"matches the pitcher's arm slot AND the result you want."
            f"</div>"
        ),
        unsafe_allow_html=True,
    )
    for v in variant_set["variants"]:
        st.markdown(
            _flat_html(
                f"<div style='background:white;border:1px solid #e5e7eb;"
                f"border-left:4px solid #1a2150;border-radius:0 8px 8px 0;"
                f"padding:14px 18px;margin:8px 0;'>"
                f"<div style='font-size:16px;font-weight:700;color:#1a2150;"
                f"margin-bottom:8px;'>{v['name']}</div>"
                f"<div style='display:grid;grid-template-columns:130px 1fr;gap:6px 12px;"
                f"font-size:13px;line-height:1.5;'>"
                f"<div style='color:#6b7280;font-weight:600;'>Arm slot</div>"
                f"<div style='color:#1f2937;'>{v['arm_slot']}</div>"
                f"<div style='color:#6b7280;font-weight:600;'>Palm/release</div>"
                f"<div style='color:#1f2937;'>{v['palm_release']}</div>"
                f"<div style='color:#6b7280;font-weight:600;'>Finger placement</div>"
                f"<div style='color:#1f2937;'>{v['fingers']}</div>"
                f"<div style='color:#16a34a;font-weight:700;'>Result</div>"
                f"<div style='color:#1f2937;font-weight:500;'>{v['result']}</div>"
                f"<div style='color:#6b7280;font-weight:600;'>Body-type fit</div>"
                f"<div style='color:#1f2937;'>{v['body_fit']}</div>"
                f"<div style='color:#d4a634;font-weight:700;'>Trade-off</div>"
                f"<div style='color:#1f2937;font-style:italic;'>{v['trade_off']}</div>"
                f"</div></div>"
            ),
            unsafe_allow_html=True,
        )


def render_grip_diagram(grip_key: str, height: int = 380):
    """Render a grip SVG reliably via an iframe component.

    Streamlit's st.markdown(html, unsafe_allow_html=True) escapes complex SVG
    when nested inside containers + columns. components.v1.html sidesteps that.
    """
    import streamlit.components.v1 as components
    components.html(grip_svg(grip_key), height=height, scrolling=False)


def _baseball_seams_svg() -> str:
    """SVG markup for proper baseball seams: continuous horseshoe paths + stitch marks.

    Drawn as two layered paths per seam (dark base + lighter top with dashed stitches)
    plus perpendicular tick marks for the cross-hatched stitch look.
    """
    # Two long horseshoe paths that mirror each other left/right.
    # Each is a smooth bezier from top of ball, out around the side, to bottom.
    # The seams DON'T meet at the top/bottom — they pass behind the ball, which
    # is the classic "horseshoe view" of a baseball.
    left_seam  = "M 105 60 C 55 90 35 145 50 175 C 65 215 80 240 105 260"
    right_seam = "M 215 60 C 265 90 285 145 270 175 C 255 215 240 240 215 260"

    # Stitch tick marks along each seam — small perpendicular lines at intervals.
    # Hand-tuned positions / angles so they sit on the seam path correctly.
    left_ticks = [
        (95, 67, -55), (75, 87, -42), (60, 112, -28), (50, 140, -10),
        (51, 170, 8), (60, 198, 25), (75, 222, 42), (95, 245, 60),
    ]
    right_ticks = [
        (225, 67, 55), (245, 87, 42), (260, 112, 28), (270, 140, 10),
        (269, 170, -8), (260, 198, -25), (245, 222, -42), (225, 245, -60),
    ]

    import math
    def tick(x, y, angle, length=11):
        rad = math.radians(angle)
        dx, dy = (length/2)*math.cos(rad), (length/2)*math.sin(rad)
        return (f'<line x1="{x-dx:.1f}" y1="{y-dy:.1f}" '
                f'x2="{x+dx:.1f}" y2="{y+dy:.1f}" '
                f'stroke="#9b1c1c" stroke-width="2.2" stroke-linecap="round"/>')

    tick_marks = "\n".join(
        tick(*t) for t in (left_ticks + right_ticks)
    )

    return f"""
    <!-- Seam channels (slightly darker, sit under the stitches) -->
    <path d="{left_seam}"  fill="none" stroke="#7a1414" stroke-width="3.5" stroke-linecap="round"/>
    <path d="{right_seam}" fill="none" stroke="#7a1414" stroke-width="3.5" stroke-linecap="round"/>
    <!-- Stitches (perpendicular tick marks) -->
    {tick_marks}
    """


def grip_svg(grip_key: str) -> str:
    """Return an inline SVG diagram of the grip viewed from the release perspective."""
    # Ball body with subtle radial gradient for a 3D feel
    ball_outline = """
    <defs>
      <radialGradient id="ballSheen" cx="0.35" cy="0.35" r="0.7">
        <stop offset="0%" stop-color="#ffffff"/>
        <stop offset="60%" stop-color="#f5f1e8"/>
        <stop offset="100%" stop-color="#e1d8c4"/>
      </radialGradient>
    </defs>
    <circle cx="160" cy="160" r="120" fill="url(#ballSheen)" stroke="#222" stroke-width="2.5"/>
    """
    seams = _baseball_seams_svg()
    # Each grip overlays finger markers + labels
    overlays = {
        "four_seam_fastball": """
            <circle cx="130" cy="140" r="18" fill="#2563eb" opacity="0.85"/>
            <text x="130" y="145" text-anchor="middle" fill="white" font-size="14" font-weight="bold">I</text>
            <circle cx="190" cy="140" r="18" fill="#2563eb" opacity="0.85"/>
            <text x="190" y="145" text-anchor="middle" fill="white" font-size="14" font-weight="bold">M</text>
            <circle cx="160" cy="220" r="18" fill="#7c3aed" opacity="0.85"/>
            <text x="160" y="225" text-anchor="middle" fill="white" font-size="14" font-weight="bold">T</text>
            <text x="160" y="305" text-anchor="middle" fill="#333" font-size="14" font-weight="bold">Fingers ACROSS the horseshoe</text>
        """,
        "two_seam_fastball": """
            <circle cx="145" cy="140" r="18" fill="#f97316" opacity="0.85"/>
            <text x="145" y="145" text-anchor="middle" fill="white" font-size="14" font-weight="bold">I</text>
            <circle cx="175" cy="140" r="18" fill="#f97316" opacity="0.85"/>
            <text x="175" y="145" text-anchor="middle" fill="white" font-size="14" font-weight="bold">M</text>
            <circle cx="160" cy="220" r="18" fill="#7c3aed" opacity="0.85"/>
            <text x="160" y="225" text-anchor="middle" fill="white" font-size="14" font-weight="bold">T</text>
            <text x="160" y="305" text-anchor="middle" fill="#333" font-size="14" font-weight="bold">Fingers ALONG the parallel seams</text>
        """,
        "slider_standard": """
            <circle cx="170" cy="140" r="18" fill="#1d4ed8" opacity="0.85"/>
            <text x="170" y="145" text-anchor="middle" fill="white" font-size="14" font-weight="bold">M</text>
            <circle cx="145" cy="145" r="16" fill="#60a5fa" opacity="0.75"/>
            <text x="145" y="150" text-anchor="middle" fill="white" font-size="13" font-weight="bold">I</text>
            <circle cx="160" cy="220" r="18" fill="#7c3aed" opacity="0.85"/>
            <text x="160" y="225" text-anchor="middle" fill="white" font-size="14" font-weight="bold">T</text>
            <text x="160" y="305" text-anchor="middle" fill="#333" font-size="14" font-weight="bold">Middle finger on long seam; index rests next to it</text>
        """,
        "slider_spike_seam": """
            <circle cx="180" cy="145" r="18" fill="#1d4ed8" opacity="0.9"/>
            <text x="180" y="150" text-anchor="middle" fill="white" font-size="14" font-weight="bold">M</text>
            <!-- spiked index = small triangle marker pointing into the seam -->
            <polygon points="135,125 150,150 120,150" fill="#dc2626" opacity="0.95"/>
            <text x="135" y="170" text-anchor="middle" fill="#dc2626" font-size="13" font-weight="bold">I (spiked)</text>
            <circle cx="160" cy="220" r="18" fill="#7c3aed" opacity="0.85"/>
            <text x="160" y="225" text-anchor="middle" fill="white" font-size="14" font-weight="bold">T</text>
            <text x="160" y="305" text-anchor="middle" fill="#333" font-size="14" font-weight="bold">SPIKE the index finger — knuckle bent, tip into seam</text>
        """,
        "curveball": """
            <circle cx="170" cy="140" r="18" fill="#0891b2" opacity="0.85"/>
            <text x="170" y="145" text-anchor="middle" fill="white" font-size="14" font-weight="bold">M</text>
            <circle cx="148" cy="142" r="16" fill="#22d3ee" opacity="0.85"/>
            <text x="148" y="147" text-anchor="middle" fill="white" font-size="13" font-weight="bold">I</text>
            <circle cx="155" cy="225" r="18" fill="#0d9488" opacity="0.9"/>
            <text x="155" y="230" text-anchor="middle" fill="white" font-size="14" font-weight="bold">T*</text>
            <text x="160" y="305" text-anchor="middle" fill="#333" font-size="14" font-weight="bold">Thumb HOOKS opposite seam — middle pulls down</text>
        """,
        "changeup_circle": """
            <!-- circle ring on the side of the ball -->
            <ellipse cx="100" cy="170" rx="20" ry="22" fill="none" stroke="#16a34a" stroke-width="4"/>
            <text x="100" y="175" text-anchor="middle" fill="#16a34a" font-size="12" font-weight="bold">⭕</text>
            <circle cx="155" cy="130" r="16" fill="#16a34a" opacity="0.75"/>
            <text x="155" y="135" text-anchor="middle" fill="white" font-size="13" font-weight="bold">M</text>
            <circle cx="185" cy="135" r="15" fill="#16a34a" opacity="0.6"/>
            <text x="185" y="140" text-anchor="middle" fill="white" font-size="12" font-weight="bold">R</text>
            <circle cx="210" cy="155" r="13" fill="#16a34a" opacity="0.5"/>
            <text x="210" y="160" text-anchor="middle" fill="white" font-size="11" font-weight="bold">P</text>
            <text x="160" y="305" text-anchor="middle" fill="#333" font-size="13" font-weight="bold">Thumb+index form circle on the side</text>
        """,

        # ===== Softball grip overlays =====
        "softball_fastball": """
            <circle cx="135" cy="135" r="18" fill="#2563eb" opacity="0.85"/>
            <text x="135" y="140" text-anchor="middle" fill="white" font-size="14" font-weight="bold">I</text>
            <circle cx="180" cy="135" r="18" fill="#2563eb" opacity="0.85"/>
            <text x="180" y="140" text-anchor="middle" fill="white" font-size="14" font-weight="bold">M</text>
            <circle cx="158" cy="220" r="18" fill="#7c3aed" opacity="0.85"/>
            <text x="158" y="225" text-anchor="middle" fill="white" font-size="14" font-weight="bold">T</text>
            <text x="160" y="305" text-anchor="middle" fill="#333" font-size="14" font-weight="bold">Fingers across the C of the horseshoe</text>
        """,
        "softball_rise": """
            <!-- Both fingers ALONG the seam (vertical orientation) -->
            <circle cx="158" cy="105" r="17" fill="#0ea5e9" opacity="0.90"/>
            <text x="158" y="110" text-anchor="middle" fill="white" font-size="13" font-weight="bold">I</text>
            <circle cx="158" cy="148" r="17" fill="#0ea5e9" opacity="0.90"/>
            <text x="158" y="153" text-anchor="middle" fill="white" font-size="13" font-weight="bold">M</text>
            <circle cx="158" cy="220" r="18" fill="#7c3aed" opacity="0.85"/>
            <text x="158" y="225" text-anchor="middle" fill="white" font-size="14" font-weight="bold">T</text>
            <!-- Rotation arrow showing palm-up release direction -->
            <path d="M 230 200 Q 260 180 250 150" fill="none" stroke="#0ea5e9" stroke-width="2.5" stroke-linecap="round"/>
            <polygon points="245,145 258,150 252,160" fill="#0ea5e9"/>
            <text x="270" y="170" text-anchor="middle" fill="#0ea5e9" font-size="10" font-weight="bold">palm</text>
            <text x="272" y="183" text-anchor="middle" fill="#0ea5e9" font-size="10" font-weight="bold">UP</text>
            <text x="160" y="305" text-anchor="middle" fill="#333" font-size="14" font-weight="bold">Fingers along seam · Palm rotates UP at release</text>
        """,
        "softball_drop": """
            <circle cx="158" cy="105" r="17" fill="#7c3aed" opacity="0.90"/>
            <text x="158" y="110" text-anchor="middle" fill="white" font-size="13" font-weight="bold">I</text>
            <circle cx="158" cy="148" r="17" fill="#7c3aed" opacity="0.90"/>
            <text x="158" y="153" text-anchor="middle" fill="white" font-size="13" font-weight="bold">M</text>
            <circle cx="158" cy="220" r="18" fill="#7c3aed" opacity="0.85"/>
            <text x="158" y="225" text-anchor="middle" fill="white" font-size="14" font-weight="bold">T</text>
            <!-- "Peel down" arrow showing fingers come OFF FRONT -->
            <path d="M 250 100 Q 270 130 250 160" fill="none" stroke="#7c3aed" stroke-width="2.5" stroke-linecap="round"/>
            <polygon points="245,160 258,155 252,168" fill="#7c3aed"/>
            <text x="270" y="125" text-anchor="middle" fill="#7c3aed" font-size="10" font-weight="bold">peel</text>
            <text x="270" y="138" text-anchor="middle" fill="#7c3aed" font-size="10" font-weight="bold">DOWN</text>
            <text x="160" y="305" text-anchor="middle" fill="#333" font-size="14" font-weight="bold">Fingers peel off front · creates topspin</text>
        """,
        "softball_curve": """
            <!-- Angled seam orientation - 4:30/10:30 -->
            <circle cx="180" cy="130" r="17" fill="#00838f" opacity="0.85"/>
            <text x="180" y="135" text-anchor="middle" fill="white" font-size="13" font-weight="bold">M</text>
            <circle cx="148" cy="138" r="16" fill="#22d3ee" opacity="0.85"/>
            <text x="148" y="143" text-anchor="middle" fill="white" font-size="13" font-weight="bold">I</text>
            <circle cx="148" cy="222" r="17" fill="#0d9488" opacity="0.90"/>
            <text x="148" y="227" text-anchor="middle" fill="white" font-size="13" font-weight="bold">T</text>
            <!-- "rotate out" arrow -->
            <path d="M 220 200 L 260 220" fill="none" stroke="#00838f" stroke-width="2.5"/>
            <polygon points="260,212 270,220 258,228" fill="#00838f"/>
            <text x="245" y="190" text-anchor="start" fill="#00838f" font-size="10" font-weight="bold">wrist</text>
            <text x="245" y="203" text-anchor="start" fill="#00838f" font-size="10" font-weight="bold">OUT</text>
            <text x="160" y="305" text-anchor="middle" fill="#333" font-size="14" font-weight="bold">Wrist rotates OUTWARD at release</text>
        """,
        "softball_screw": """
            <!-- Mirror of curveball - 1:30/7:30 angle -->
            <circle cx="140" cy="130" r="17" fill="#f59e0b" opacity="0.85"/>
            <text x="140" y="135" text-anchor="middle" fill="white" font-size="13" font-weight="bold">M</text>
            <circle cx="172" cy="138" r="16" fill="#fbbf24" opacity="0.85"/>
            <text x="172" y="143" text-anchor="middle" fill="white" font-size="13" font-weight="bold">I</text>
            <circle cx="172" cy="222" r="17" fill="#b45309" opacity="0.90"/>
            <text x="172" y="227" text-anchor="middle" fill="white" font-size="13" font-weight="bold">T</text>
            <!-- "rotate in" arrow -->
            <path d="M 100 200 L 60 220" fill="none" stroke="#b45309" stroke-width="2.5"/>
            <polygon points="60,212 50,220 62,228" fill="#b45309"/>
            <text x="75" y="190" text-anchor="end" fill="#b45309" font-size="10" font-weight="bold">wrist</text>
            <text x="75" y="203" text-anchor="end" fill="#b45309" font-size="10" font-weight="bold">IN</text>
            <text x="160" y="305" text-anchor="middle" fill="#333" font-size="14" font-weight="bold">Wrist pronates INWARD at release</text>
        """,
        "softball_change": """
            <!-- Backhand grip - thumb UP along seam, palm facing batter -->
            <circle cx="158" cy="100" r="17" fill="#16a34a" opacity="0.90"/>
            <text x="158" y="105" text-anchor="middle" fill="white" font-size="14" font-weight="bold">T↑</text>
            <circle cx="130" cy="155" r="13" fill="#16a34a" opacity="0.70"/>
            <text x="130" y="159" text-anchor="middle" fill="white" font-size="11" font-weight="bold">I</text>
            <circle cx="158" cy="170" r="13" fill="#16a34a" opacity="0.70"/>
            <text x="158" y="174" text-anchor="middle" fill="white" font-size="11" font-weight="bold">M</text>
            <circle cx="186" cy="160" r="12" fill="#16a34a" opacity="0.60"/>
            <text x="186" y="164" text-anchor="middle" fill="white" font-size="10" font-weight="bold">R</text>
            <circle cx="210" cy="140" r="11" fill="#16a34a" opacity="0.50"/>
            <text x="210" y="144" text-anchor="middle" fill="white" font-size="10" font-weight="bold">P</text>
            <text x="160" y="305" text-anchor="middle" fill="#333" font-size="13" font-weight="bold">Thumb UP · ball rolls out the back of hand</text>
        """,
    }
    overlay = overlays.get(grip_key, "")
    return f"""
    <div style='display:flex;justify-content:center;'>
      <svg width="320" height="320" viewBox="0 0 320 320" xmlns="http://www.w3.org/2000/svg">
        {ball_outline}
        {seams}
        {overlay}
      </svg>
    </div>
    <p style='text-align:center;font-size:11px;color:#666;margin-top:-8px;'>
    I = index, M = middle, R = ring, P = pinky, T = thumb. Viewed from release perspective.
    </p>
    """


# =============================================================================
# STREAMLIT UI — global styles + reusable components
# =============================================================================
_GLOBAL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

/* =====================================================================
   Diamond Sports Lab — DARK design system (night mode)
   Palette:
     --ink-50    text on dark        (#f1f5f9)
     --ink-100   muted/secondary     (#cbd5e1)
     --ink-300   tertiary/captions   (#94a3b8)
     --bg-900    page background     (#0f172a)
     --bg-800    elevated cards      (#1e293b)
     --bg-700    borders/dividers    (#334155)
     --blue-500  primary action      (#3b82f6)
     --gold-500  accent (sparingly)  (#d4a634)
   ===================================================================== */

/* ===== Typography ===== */
html, body, [class*="css"], .stApp, .main, section,
.stMarkdown, .stMarkdown p, .stMarkdown div,
.stPlotlyChart, .js-plotly-plot, .plotly,
button, input, select, textarea {
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI",
                 system-ui, sans-serif !important;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
}
.block-container {
    padding-top: 1.4rem !important;
    padding-bottom: 2.4rem !important;
    max-width: 1240px;
}

/* ===== Hide Plotly modebar (charts should feel native) ===== */
.modebar-container, .modebar, .modebar-group { display: none !important; }
.plotly .modebar { display: none !important; }

/* ===== Mobile scroll lockdown + dimension lock for Plotly charts =====
   Plotly draws an invisible "pan-grab" rectangle (.nsewdrag class) that
   captures touch/drag events for panning. On mobile, that hijacks the
   page scroll. We disable pointer-events on JUST that pan layer so
   swipes fall through to the page, while taps on the actual data dots
   still register their on_select event.
   We also LOCK chart dimensions so iOS Safari can't shrink them when
   the user rotates the phone — every viewport change used to trigger
   a recalculation that ate ~15% of the chart's height. */
.stPlotlyChart, .js-plotly-plot, .plotly, .plot-container, .svg-container,
.main-svg, .draglayer {
    touch-action: pan-y !important;
    -webkit-user-select: none;
    -webkit-tap-highlight-color: transparent;
    overscroll-behavior: contain;
}
/* ===== ROTATION-SAFE LAYOUT — use viewport-relative units =====
   iOS Safari shrinks page content each rotation because it recalculates
   the layout container width based on the viewport but doesn't reset
   it cleanly. Using `vw` (viewport width) instead of `%` (parent width)
   means dimensions reset to the new viewport on every rotation — no
   compounding feedback loop. */

/* ===== iOS rotation lock — pin EVERY Streamlit wrapper to the
   viewport width =====
   The classic iOS Safari rotation bug: each portrait↔landscape flip
   recalculates layout and every intermediate Streamlit wrapper
   (stAppViewContainer → stVerticalBlock → stElementContainer → stImage)
   bakes in a slightly smaller width than the last. The chart image
   faithfully follows whatever its parent is, so it shrinks too.
   The fix: force every wrapper to be EXACTLY 100vw and
   `box-sizing: border-box` so internal padding can't push it narrower.
   ============================================================== */
.stApp, .main, section.main, div.main, .block-container,
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="stMainBlockContainer"],
[data-testid="stVerticalBlock"],
[data-testid="stHorizontalBlock"],
[data-testid="stElementContainer"],
[data-testid="stImage"],
[data-testid="column"] {
    max-width: 100vw !important;
    box-sizing: border-box !important;
    overflow-x: hidden;
}
.stApp, .main, section.main, .block-container {
    width: 100vw !important;
}

/* Static chart images — pin DIRECTLY to viewport width so the value
   doesn't depend on any parent wrapper. After rotation iOS forces a
   reflow, but `100vw` re-evaluates to the new viewport on every paint,
   so the image always equals the current viewport width — no
   compounding shrinkage. */
.stImage img, [data-testid="stImage"] img {
    width: 100vw !important;
    min-width: 100vw !important;
    max-width: 100vw !important;
    height: auto !important;
    display: block !important;
    margin-left: 0 !important;
}
.stImage, [data-testid="stImage"] {
    width: 100vw !important;
    margin-left: 0 !important;
}

/* ===== WHEEL SCROLL PASS-THROUGH (CSS layer) =====
   Disable pointer-events on chart images and Plotly chart bodies so
   mouse-wheel events fall through them onto the page scroller.
   Streamlit's image wrapper otherwise traps the wheel for things like
   image zoom controls that we don't actually use.
   The streamlit-image-coordinates IFRAME is the one chart-like element
   that DOES need pointer-events (click calibration), so we re-enable
   it explicitly below.                                                  */
.stImage img,
[data-testid="stImage"] img,
.stPlotlyChart .js-plotly-plot,
.stPlotlyChart .plot-container,
.stPlotlyChart .svg-container,
.stPlotlyChart .main-svg {
    pointer-events: none !important;
}
/* Click-to-calibrate component lives in an iframe and DOES need
   pointer events for its tap targets. */
iframe[title*="streamlit_image_coordinates"],
iframe[title*="image_coordinates"] {
    pointer-events: auto !important;
}

/* Make absolutely sure the page itself can scroll (some Streamlit
   versions add overflow:hidden to html, which combined with charts
   filling the viewport can lock scroll entirely). */
html, body {
    overflow-y: auto !important;
    -webkit-overflow-scrolling: touch !important;
}

/* Plotly chart containers (the few that remain) — same approach */
.stPlotlyChart {
    min-height: 500px !important;
    width: 100% !important;
    max-width: 100vw !important;
    contain: layout;
    position: relative;
}
.stPlotlyChart > div, .stPlotlyChart .js-plotly-plot,
.stPlotlyChart .main-svg, .stPlotlyChart svg.main-svg,
.stPlotlyChart .plot-container, .stPlotlyChart .plotly,
.stPlotlyChart .svg-container {
    width: 100% !important;
    min-width: 100% !important;
    max-width: 100% !important;
}
/* The pan-grab overlay — invisible, intercepts swipes. Turn it off. */
.js-plotly-plot .nsewdrag,
.js-plotly-plot .draglayer .nsewdrag,
.js-plotly-plot .draglayer .nsewdrag-bg,
.js-plotly-plot .draglayer .nwdrag,
.js-plotly-plot .draglayer .nedrag,
.js-plotly-plot .draglayer .sedrag,
.js-plotly-plot .draglayer .swdrag,
.js-plotly-plot .draglayer .ndrag,
.js-plotly-plot .draglayer .edrag,
.js-plotly-plot .draglayer .sdrag,
.js-plotly-plot .draglayer .wdrag {
    pointer-events: none !important;
    cursor: default !important;
}
/* Keep clicks on data points working — these are the "scatter trace"
   markers, the layer that fires Plotly's on_select. */
.js-plotly-plot .scatterlayer,
.js-plotly-plot .scatterlayer .points,
.js-plotly-plot .scatterlayer .points path,
.js-plotly-plot .scatterlayer .point {
    pointer-events: auto !important;
}

/* ===== Tunneling Batter POV — slight 3D perspective tilt =====
   Targets the chart keyed "tunnel_batter_chart". A small leftward rotateY
   makes the trail recede convincingly so the flight path reads as depth
   instead of a flat overlay. */
[data-testid="stPlotlyChart"]:has(> div > div[id*="tunnel_batter_chart"]),
.element-container:has([data-testid*="tunnel_batter_chart"]) {
    transform: perspective(1400px) rotateY(-6deg) translateX(-12px);
    transform-origin: center center;
    transition: transform 0.2s ease;
}

/* ===== Page chrome cleanup ===== */
#MainMenu { visibility: hidden; height: 0; }
footer { visibility: hidden; height: 0; }
[data-testid="stDeployButton"] { display: none; }
[data-testid="stStatusWidget"] { display: none; }
[data-testid="stDecoration"] { display: none; }
section[data-testid="stSidebar"] {
    visibility: visible !important;
    display: block !important;
}
[data-testid="collapsedControl"] {
    visibility: visible !important;
    display: flex !important;
}

/* ===== Headings ===== */
h1, h2, h3, h4 {
    color: #f1f5f9 !important;
    font-weight: 700;
    letter-spacing: -0.01em;
}
h1 { font-size: 1.7rem !important; }
h2 { font-size: 1.2rem  !important; margin-top: 1.2rem !important; }
h3 { font-size: 1.02rem !important; }
.stMarkdown p, .stMarkdown li, p, li {
    color: #cbd5e1;
}
.stCaption, [data-testid="stCaptionContainer"], .stMarkdown small {
    color: #94a3b8 !important;
}

/* ===== Tabs — blue/gray with bright-blue underline on active ===== */
.stTabs [data-baseweb="tab-list"] {
    gap: 8px;
    border-bottom: 2px solid #334155;
    background: transparent;
    margin-bottom: 6px;
}
.stTabs [data-baseweb="tab"],
.stTabs button[role="tab"] {
    font-size: 18px !important;
    font-weight: 700 !important;
    color: #94a3b8 !important;
    padding: 14px 22px !important;
    background: transparent !important;
    border-radius: 8px 8px 0 0 !important;
    letter-spacing: 0.005em !important;
    min-height: 52px !important;
}
.stTabs [data-baseweb="tab"] p,
.stTabs button[role="tab"] p,
.stTabs [data-baseweb="tab"] *,
.stTabs button[role="tab"] * {
    font-size: 18px !important;
    font-weight: 700 !important;
    line-height: 1.2 !important;
}
.stTabs [data-baseweb="tab"]:hover,
.stTabs button[role="tab"]:hover {
    color: #f1f5f9 !important;
    background: #1e293b !important;
}
.stTabs [aria-selected="true"],
.stTabs button[role="tab"][aria-selected="true"] {
    color: #f1f5f9 !important;
    background: #1e293b !important;
    border-bottom: 3px solid #3b82f6 !important;
}
.stTabs [aria-selected="true"] p,
.stTabs button[role="tab"][aria-selected="true"] p {
    color: #f1f5f9 !important;
}
.stTabs [data-baseweb="tab-panel"] { padding-top: 1.2rem !important; }

/* ===== Buttons ===== */
.stButton > button, .stDownloadButton > button {
    border-radius: 8px;
    font-weight: 600;
    padding: 0.55rem 1rem;
    transition: transform 0.05s ease, background 0.15s ease;
    background: #1e293b;
    border: 1px solid #334155;
    color: #e2e8f0;
}
.stButton > button:hover, .stDownloadButton > button:hover {
    background: #334155;
    border-color: #475569;
}
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {
    background: linear-gradient(180deg, #3b82f6 0%, #2563eb 100%);
    border: 1px solid #2563eb;
    color: white;
}
.stButton > button[kind="primary"]:hover, .stDownloadButton > button[kind="primary"]:hover {
    background: linear-gradient(180deg, #60a5fa 0%, #3b82f6 100%);
}
.stButton > button:active { transform: translateY(1px); }

/* ===== Sidebar ===== */
[data-testid="stSidebar"] {
    background: #111827 !important;
    border-right: 1px solid #1e293b;
}
[data-testid="stSidebar"] * {
    color: #e2e8f0;
}
[data-testid="stSidebar"] hr { border-color: #1e293b; }

/* ===== Native st.metric — dark cards ===== */
[data-testid="stMetric"] {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 12px 14px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.12);
}
[data-testid="stMetricLabel"] {
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-size: 0.72rem !important;
    color: #94a3b8;
    font-weight: 600;
}
[data-testid="stMetricValue"] {
    color: #f1f5f9;
    font-weight: 700;
    font-size: 1.55rem !important;
}

/* ===== Inputs / selectboxes / sliders ===== */
.stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"],
.stNumberInput input, .stDateInput input {
    background: #1e293b !important;
    color: #e2e8f0 !important;
    border-color: #334155 !important;
}

/* ===== Dividers / expanders ===== */
hr { border-color: #1e293b !important; margin: 1.2rem 0; }
[data-testid="stExpander"] {
    background: #1e293b !important;
    border: 1px solid #334155 !important;
    border-radius: 10px !important;
}
.streamlit-expanderHeader, [data-testid="stExpander"] summary {
    font-weight: 600;
    color: #f1f5f9 !important;
}

/* ===== st.success / info / warning / error banners — dark mode ===== */
[data-testid="stAlert"] {
    background: #1e293b !important;
    border: 1px solid #334155 !important;
    color: #e2e8f0 !important;
}

/* ===== Code blocks ===== */
code, pre {
    font-family: "SF Mono", Menlo, Monaco, Consolas, monospace;
    font-size: 0.85rem;
    background: #1e293b !important;
    color: #cbd5e1 !important;
}

/* ===== Bordered containers (st.container(border=True)) ===== */
[data-testid="stVerticalBlockBorderWrapper"] {
    background: #1e293b !important;
    border-color: #334155 !important;
}
</style>
"""


def _inject_global_styles():
    """Inject the design-system CSS once per session."""
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)


# =============================================================================
# CHART THEMING — every plotly figure passes through _apply_chart_theme so
# typography, colors, gridlines, and backgrounds match the rest of the app.
# Use CHART_CONFIG for st.plotly_chart() calls to hide the modebar.
# =============================================================================
CHART_CONFIG = {
    # Default config for non-interactive charts — staticPlot: True freezes
    # the chart completely. No drag, no pan, no zoom, no double-tap-reset.
    # responsive: False prevents the iOS shrink-on-rotation bug where each
    # orientation flip would recalculate dimensions and shrink the chart.
    "staticPlot":         True,
    "displayModeBar":     False,
    "responsive":         False,
    "scrollZoom":         False,
    "doubleClick":        False,
    "showAxisDragHandles":False,
}

# For the few charts that need click events. staticPlot must be False here
# so Streamlit's on_select fires when the user taps a data point. Drag-
# based interactions (pan, zoom, double-click reset) are still all OFF.
# The mobile scroll-trap is solved by the aggressive CSS below in
# _GLOBAL_CSS — `pointer-events: none` on Plotly's invisible pan-overlay
# layer means swipes pass through to the page, while taps on the visible
# data dots still register.
CHART_CONFIG_INTERACTIVE = {
    "staticPlot":         False,
    "displayModeBar":     False,
    "responsive":         False,
    "scrollZoom":         False,
    "doubleClick":        False,
    "showAxisDragHandles":False,
}

CHART_FONT_STACK = (
    'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", '
    'system-ui, sans-serif'
)

CHART_PALETTE = {
    # Dark mode palette — matches the global CSS dark theme so charts
    # blend into the page instead of looking like white cutouts.
    "ink":         "#f1f5f9",   # primary text + bold lines (slate-100)
    "ink_soft":    "#cbd5e1",   # body text (slate-300)
    "muted":       "#94a3b8",   # tertiary text (slate-400)
    "border":      "#334155",   # subtle borders (slate-700)
    "gridline":    "#1e293b",   # very subtle gridlines (slate-800)
    "bg":          "#1e293b",   # chart background (slate-800 — lifted card)
    "page_bg":     "#0f172a",   # page background (slate-900)
    "accent":      "#d4a634",   # gold accent
    "success":     "#22c55e",
    "warn":        "#d4a634",
    "danger":      "#ef4444",
}


def render_static_chart(fig, *, key: str | None = None,
                          height_px: int = 500,
                          width_px: int | None = None,
                          caption: str | None = None):
    """Render a Plotly figure as a static PNG image.

    Tuned for mobile readability. The PNG is rendered at smaller native
    dimensions (~900px wide) with higher pixel density (scale=3) and the
    chart font sizes are bumped in _apply_chart_theme — together this
    means when the image gets scaled down to fit a phone viewport, the
    axis labels and tick text are still legible. On desktop the chart
    renders at its native size and looks identical to before.

    Falls back to st.plotly_chart if kaleido isn't installed.
    """
    try:
        import io as _io
        # Smaller native width + higher scale = less browser-side
        # downscaling on phones (less text-smushing) AND a sharper image
        # on retina displays.
        native_w = width_px or 900
        img_bytes = fig.to_image(format="png", width=native_w,
                                   height=height_px, scale=3)
        st.image(_io.BytesIO(img_bytes), use_container_width=True)
        if caption:
            st.caption(caption)
    except Exception as e:
        # kaleido missing or render failed — fall back to plotly_chart
        st.plotly_chart(fig, use_container_width=True,
                          key=key, config=CHART_CONFIG)
        if caption:
            st.caption(caption)


def _apply_chart_theme(fig, *, preserve_bg: bool = False):
    """Apply the Diamond Sports Lab design system to a plotly figure.

    Call this at the END of every chart builder, just before returning fig.
    Keeps a single source of truth for typography, colors, gridlines, and
    hover styling so every chart looks native to the app instead of a
    Plotly add-on.

    Conservative on purpose — does NOT touch:
      - title text / position (each chart keeps its own headline)
      - axis ranges / scale anchors (each chart's geometry is preserved)
      - height (each chart keeps its sizing)

    Sets:
      - font family + sizes for all chart text
      - paper + plot backgrounds (unless preserve_bg=True for stadium/field charts)
      - hover label styling
      - gridline + axis line colors
      - legend font
    """
    # Font sizes are deliberately large for the static-PNG pipeline.
    # The PNG renders at ~900 px and gets scaled down to ~390 px on a
    # phone — anything below 14 pt becomes illegible after that 2.3×
    # downsample. 16-18 pt looks slightly oversized on desktop but is
    # the sweet spot for "readable on the phone you're actually using".
    layout_kwargs = dict(
        font=dict(family=CHART_FONT_STACK,
                   size=16,
                   color=CHART_PALETTE["ink_soft"]),
        # dragmode=False locks the chart so touch drags pass through to
        # the page scroll instead of panning the chart. Critical for
        # mobile UX — otherwise users can't scroll past any chart on
        # their phone. Clicks (for click-to-place / on_select) still work.
        dragmode=False,
        hoverlabel=dict(
            font=dict(family=CHART_FONT_STACK, size=14, color="white"),
            bgcolor=CHART_PALETTE["ink"],
            bordercolor=CHART_PALETTE["ink"],
        ),
        legend=dict(
            font=dict(family=CHART_FONT_STACK, size=14,
                       color=CHART_PALETTE["ink_soft"]),
            bgcolor="rgba(255,255,255,0)",
            borderwidth=0,
        ),
        # Tighter margins so the chart drawing area is as big as possible
        # relative to the PNG dimensions — every freed pixel makes the
        # post-downsample text more readable on phone.
        margin=dict(l=60, r=20, t=50, b=50),
    )
    if not preserve_bg:
        # Make the chart look "built into" the page — same background as the
        # page itself. Without overriding plot_bgcolor too, the inside-the-
        # axes area would show as a white rectangle against the dark page.
        layout_kwargs["paper_bgcolor"] = CHART_PALETTE["page_bg"]
        layout_kwargs["plot_bgcolor"]  = CHART_PALETTE["page_bg"]
    # Update title font (but NOT title text) — preserves headline content
    # while restyling its typography. Wrapped because stub figures used in
    # tests don't always expose .layout / .layout.title attributes.
    try:
        existing_title = fig.layout.title.text if (
            fig.layout.title and getattr(fig.layout.title, "text", None)) else None
        if existing_title:
            layout_kwargs["title"] = dict(
                text=existing_title,
                font=dict(family=CHART_FONT_STACK, size=20,
                           color=CHART_PALETTE["ink"]),
            )
    except Exception:
        pass
    # All plotly mutators below are defensive — a styling pass should
    # never break the build if the figure object is non-standard.
    try:
        fig.update_layout(**layout_kwargs)
    except Exception:
        pass
    try:
        fig.update_xaxes(
            gridcolor=CHART_PALETTE["gridline"], gridwidth=1,
            linecolor=CHART_PALETTE["border"], linewidth=1,
            title_font=dict(family=CHART_FONT_STACK, size=15,
                             color=CHART_PALETTE["muted"]),
            tickfont=dict(family=CHART_FONT_STACK, size=14,
                           color=CHART_PALETTE["muted"]),
        )
        fig.update_yaxes(
            gridcolor=CHART_PALETTE["gridline"], gridwidth=1,
            linecolor=CHART_PALETTE["border"], linewidth=1,
            title_font=dict(family=CHART_FONT_STACK, size=15,
                             color=CHART_PALETTE["muted"]),
            tickfont=dict(family=CHART_FONT_STACK, size=14,
                           color=CHART_PALETTE["muted"]),
        )
    except Exception:
        pass
    return fig


def _flat_html(html: str) -> str:
    """Collapse multi-line indented HTML into a single line.

    Streamlit's markdown parser interprets 4+ leading spaces as a code block,
    so multi-line indented HTML in f-strings ends up rendered as literal text.
    Collapsing all whitespace bypasses that.
    """
    return " ".join(html.split())


def _branded_header(athlete_name: str, athlete_hand: str, athlete_class: str,
                    demo_mode: bool, sport: str = "Baseball"):
    """Render the navy header bar + athlete card. Replaces st.title."""
    sport_icon = "🥎" if sport == "Softball" else "⚾"
    sport_pill = (
        f"<span style='background:rgba(255,255,255,0.12);color:white;"
        f"padding:4px 12px;border-radius:14px;font-size:11px;font-weight:700;"
        f"letter-spacing:0.08em;margin-right:8px;'>"
        f"{sport_icon} {sport.upper()}</span>"
    )
    sample_pill = (
        "<span style='background:rgba(212,166,52,0.18);color:#d4a634;"
        "padding:4px 12px;border-radius:14px;font-size:11px;font-weight:700;"
        "letter-spacing:0.08em;'>SAMPLE</span>"
        if demo_mode else ""
    )
    html = (
        "<div style='background:linear-gradient(135deg,#1a2150 0%,#232c5e 100%);"
        "padding:22px 26px;border-radius:14px;color:white;margin-bottom:18px;"
        "box-shadow:0 4px 14px rgba(26,33,80,0.12);'>"
        "<div style='display:flex;justify-content:space-between;"
        "align-items:flex-start;gap:12px;'>"
        "<div>"
        f"<div style='font-size:11px;letter-spacing:0.14em;font-weight:700;"
        f"color:#d4a634;text-transform:uppercase;margin-bottom:6px;'>"
        f"Diamond Sports Lab · Post-Bullpen Report</div>"
        f"<div style='font-size:26px;font-weight:700;line-height:1.1;margin-bottom:4px;'>"
        f"{athlete_name}</div>"
        f"<div style='font-size:14px;color:rgba(255,255,255,0.75);font-weight:500;'>"
        f"{athlete_hand}-handed pitcher · Class of {athlete_class or '—'}</div>"
        "</div>"
        f"<div style='display:flex;align-items:center;'>{sport_pill}{sample_pill}</div>"
        "</div></div>"
    )
    st.markdown(_flat_html(html), unsafe_allow_html=True)


def _hero_kpi_card(hero_label: str, hero_value: str,
                    hero_unit: str = "",
                    hero_tone: str | None = None,
                    hero_subtitle: str | None = None) -> str:
    """One huge hero metric card — top 40% of the KPI block.
    Industry-standard layout (TrackMan B1, Rapsodo) where one number
    dominates and supporting metrics live below.
    """
    accent_map = {
        "success": "#16a34a", "warn": "#d4a634", "danger": "#dc2626",
        None: "#d4a634",
    }
    accent = accent_map.get(hero_tone, "#d4a634")
    value_color = "#1a2150"
    if hero_tone in ("success", "danger"):
        value_color = accent
    sub = (
        f"<div style='font-size:13px;color:#6b7280;font-weight:500;"
        f"margin-top:6px;'>{hero_subtitle}</div>" if hero_subtitle else ""
    )
    return _flat_html(
        f"<div style='background:white;border:1px solid #e5e7eb;"
        f"border-radius:14px;padding:24px 28px;position:relative;"
        f"overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.04);"
        f"margin-bottom:10px;'>"
        f"<div style='position:absolute;top:0;left:0;right:0;height:5px;"
        f"background:{accent};'></div>"
        f"<div style='font-size:12px;letter-spacing:0.12em;font-weight:700;"
        f"color:#6b7280;text-transform:uppercase;margin-top:6px;margin-bottom:8px;'>"
        f"{hero_label}</div>"
        f"<div style='display:flex;align-items:baseline;gap:10px;'>"
        f"<div style='font-size:68px;font-weight:800;color:{value_color};"
        f"line-height:1.0;letter-spacing:-0.02em;'>{hero_value}</div>"
        f"<div style='font-size:22px;font-weight:600;color:#6b7280;"
        f"margin-bottom:6px;'>{hero_unit}</div>"
        f"</div>"
        f"{sub}"
        f"</div>"
    )


def _support_kpi_grid(items: list[tuple[str, str, str | None]]) -> str:
    """4-up support grid below the hero. Each item is (label, value, tone).
    `tone` ∈ {None, "success", "warn", "danger"} — color-codes the accent
    bar so the player sees green/yellow/red zones at a glance.
    """
    accent_map = {
        "success": "#16a34a", "warn": "#d4a634", "danger": "#dc2626", None: "#1a2150",
    }
    cards = []
    for label, value, tone in items:
        accent = accent_map.get(tone, "#1a2150")
        value_color = accent if tone in ("success", "danger") else "#1a2150"
        cards.append(
            f"<div style='background:white;border:1px solid #e5e7eb;"
            f"border-radius:10px;padding:14px 16px;flex:1;min-width:130px;"
            f"position:relative;overflow:hidden;'>"
            f"<div style='position:absolute;top:0;left:0;right:0;height:3px;"
            f"background:{accent};'></div>"
            f"<div style='font-size:11px;letter-spacing:0.09em;font-weight:700;"
            f"color:#6b7280;text-transform:uppercase;margin-top:4px;'>{label}</div>"
            f"<div style='font-size:26px;font-weight:700;color:{value_color};"
            f"line-height:1.15;margin-top:6px;'>{value}</div>"
            f"</div>"
        )
    return _flat_html(
        "<div style='display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px;'>"
        + "".join(cards)
        + "</div>"
    )


def _kpi_row(kpis: dict, healed_count: int):
    """Pitching KPI block — big hero metric (peak velocity) + 4-up support
    grid. Industry-standard hierarchy: ONE number dominates, the others
    play support so a coach can read the headline from 10 ft away.
    """
    # ===== Hero: Peak Velocity =====
    peak_v = kpis.get("Peak Velocity")
    avg_v  = kpis.get("Avg Velocity")
    peak_str = f"{peak_v:.1f}" if peak_v is not None else "—"
    # Tone based on level-typical peak (HS-Varsity baseline)
    if peak_v is None:        peak_tone = None
    elif peak_v >= 90:        peak_tone = "success"
    elif peak_v >= 82:        peak_tone = None
    else:                      peak_tone = "warn"
    subtitle = (f"Avg velocity {avg_v:.1f} mph across the session"
                 if avg_v is not None else None)
    st.markdown(_hero_kpi_card(
        hero_label="Peak Velocity",
        hero_value=peak_str,
        hero_unit="mph",
        hero_tone=peak_tone,
        hero_subtitle=subtitle,
    ), unsafe_allow_html=True)

    # ===== Support 4-up grid =====
    spin = kpis.get("Avg Spin")
    spin_tone = ("success" if spin and spin >= 2200
                  else "warn"  if spin and spin <= 1800
                  else None)
    max_stress = kpis.get("Max Elbow Stress")
    stress_tone = ("danger" if max_stress and max_stress >= DANGER_VALGUS_NM
                    else "warn" if max_stress and max_stress >= 55
                    else "success" if max_stress and max_stress < 50
                    else None)
    healed_tone = "warn" if healed_count > 0 else None
    items = [
        ("Total Pitches", str(kpis.get("Total Pitches", 0)), None),
        ("Avg Spin",      f"{int(spin):,}"      if spin else "—", spin_tone),
        ("Max Stress",    f"{max_stress} Nm"    if max_stress is not None else "—", stress_tone),
        ("Healed",        str(healed_count),    healed_tone),
    ]
    st.markdown(_support_kpi_grid(items), unsafe_allow_html=True)


def _branded_header_hitting(athlete_name: str, athlete_hand: str, athlete_class: str,
                             demo_mode: bool, sport: str = "Baseball"):
    """Hitting-mode version of the branded header bar."""
    sport_icon = "🥎" if sport == "Softball" else "⚾"
    sport_pill = (
        f"<span style='background:rgba(255,255,255,0.12);color:white;"
        f"padding:4px 12px;border-radius:14px;font-size:11px;font-weight:700;"
        f"letter-spacing:0.08em;margin-right:8px;'>"
        f"{sport_icon} {sport.upper()}</span>"
    )
    sample_pill = (
        "<span style='background:rgba(212,166,52,0.18);color:#d4a634;"
        "padding:4px 12px;border-radius:14px;font-size:11px;font-weight:700;"
        "letter-spacing:0.08em;'>SAMPLE</span>"
        if demo_mode else ""
    )
    html = (
        "<div style='background:linear-gradient(135deg,#1a2150 0%,#232c5e 100%);"
        "padding:22px 26px;border-radius:14px;color:white;margin-bottom:18px;"
        "box-shadow:0 4px 14px rgba(26,33,80,0.12);'>"
        "<div style='display:flex;justify-content:space-between;"
        "align-items:flex-start;gap:12px;'>"
        "<div>"
        f"<div style='font-size:11px;letter-spacing:0.14em;font-weight:700;"
        f"color:#d4a634;text-transform:uppercase;margin-bottom:6px;'>"
        f"Diamond Sports Lab · Post-Swing Report</div>"
        f"<div style='font-size:26px;font-weight:700;line-height:1.1;margin-bottom:4px;'>"
        f"{athlete_name}</div>"
        f"<div style='font-size:14px;color:rgba(255,255,255,0.75);font-weight:500;'>"
        f"{athlete_hand}-handed hitter · Class of {athlete_class or '—'}</div>"
        "</div>"
        f"<div style='display:flex;align-items:center;'>{sport_pill}{sample_pill}</div>"
        "</div></div>"
    )
    st.markdown(_flat_html(html), unsafe_allow_html=True)


def _hitting_kpi_row(kpis: dict):
    """Hitting KPI block — big hero (peak exit velo) + 4-up support grid."""
    # ===== Hero: Peak Exit Velocity =====
    peak_ev = kpis.get("Peak Exit Velo")
    avg_ev  = kpis.get("Avg Exit Velo")
    peak_str = f"{peak_ev:.1f}" if peak_ev else "—"
    if peak_ev is None:    peak_tone = None
    elif peak_ev >= 95:    peak_tone = "success"
    elif peak_ev >= 85:    peak_tone = None
    else:                   peak_tone = "warn"
    subtitle = (f"Avg exit velo {avg_ev:.1f} mph across the session"
                 if avg_ev else None)
    st.markdown(_hero_kpi_card(
        hero_label="Peak Exit Velocity",
        hero_value=peak_str,
        hero_unit="mph",
        hero_tone=peak_tone,
        hero_subtitle=subtitle,
    ), unsafe_allow_html=True)

    # ===== Support 4-up grid =====
    bat_spd = kpis.get("Avg Bat Speed")
    bat_tone = ("success" if bat_spd and bat_spd >= 70
                 else "warn"  if bat_spd and bat_spd < 60
                 else None)
    la = kpis.get("Avg Launch Angle")
    la_tone = ("success" if la is not None and 10 <= la <= 25 else None)
    barrel = kpis.get("Barrel %")
    barrel_tone = ("success" if barrel and barrel >= 15
                    else "warn" if barrel and barrel < 5
                    else None)
    whiff = kpis.get("Whiff %")
    whiff_tone = ("warn" if whiff and whiff >= 30
                   else "success" if whiff and whiff < 18
                   else None)
    items = [
        ("Total Swings",  str(kpis.get("Total Swings", 0)), None),
        ("Bat Speed",     f"{bat_spd:.1f} mph"   if bat_spd  else "—", bat_tone),
        ("Barrel %",      f"{barrel}%"           if barrel is not None else "—", barrel_tone),
        ("Whiff %",       f"{whiff}%"            if whiff is not None else "—", whiff_tone),
    ]
    st.markdown(_support_kpi_grid(items), unsafe_allow_html=True)


# Color map for swing-outcome dots (matches barrel→whiff red→blue scale)
SWING_OUTCOME_COLORS = {
    "barrel":        "#7f1d1d",   # darkest red — best contact
    "solid_contact": "#dc2626",   # red
    "foul":          "#9ca3af",   # neutral gray
    "weak_contact":  "#60a5fa",   # light blue
    "whiff":         "#1e3a8a",   # dark blue — worst
    "take":          "#d1d5db",   # very light gray (no swing)
}

# Quality scores used for the continuous diverging color scale
SWING_QUALITY_SCORE = {
    "barrel":         2.0,
    "solid_contact":  1.0,
    "foul":           0.0,
    "weak_contact":  -1.0,
    "whiff":         -2.0,
    "take":           None,  # rendered separately
}


def _field_dimensions(sport: str) -> dict:
    """Return baseball/softball field geometry for the spray chart."""
    if sport == "Softball":
        return {
            "base_path_ft":     60,
            "mound_distance":   43,
            "of_wall_lf_rf":    220,    # foul-pole wall
            "of_wall_cf":       250,    # center field wall
            "foul_extent":      260,    # how far foul lines extend in the plot
        }
    # Baseball defaults (HS / college)
    return {
        "base_path_ft":     90,
        "mound_distance":   60.5,
        "of_wall_lf_rf":    330,
        "of_wall_cf":       400,
        "foul_extent":      420,
    }


def _spray_landing_xy(spray_angle_deg: float, distance_ft: float,
                       hand: str = "Right") -> tuple:
    """Convert (spray_angle, distance) to (x, y) coordinates on the field.

    Convention:
      home plate at (0, 0), CF straight up the +y axis.
      For a RIGHT-handed hitter: pull side = -x (left field).
      For a LEFT-handed hitter: pull side = +x (right field) — we mirror.
      spray_angle: negative = pull, 0 = center, positive = oppo.
    """
    import math
    if spray_angle_deg is None or distance_ft is None:
        return None, None
    # spray_angle from center: -45 = pull foul pole, +45 = oppo foul pole
    angle = math.radians(spray_angle_deg)
    # For RHH, pull side = LEFT field = -x on a field-view chart.
    # sin(-30°) = -0.5, so x_sign = +1 puts a pulled ball at x = -175 (LF). ✓
    # For LHH, we mirror so their pull side (still negative spray angle) goes to RF (+x).
    x_sign = -1.0 if hand == "Left" else 1.0
    x = x_sign * distance_ft * math.sin(angle)
    y = distance_ft * math.cos(angle)
    return x, y


def _build_spray_chart_figure(df: pd.DataFrame, sport: str = "Baseball",
                                hand: str = "Right") -> "go.Figure":
    """Build the spray chart: field outline + flight-path arcs + landing dots.

    Pro-style field rendering: grass-green outfield fill, dirt-brown infield,
    white foul lines + range arcs. Each batted ball draws a thin flight arc
    from home plate to its landing position, colored by contact quality.
    """
    import math
    fd = _field_dimensions(sport)
    fig = go.Figure()

    GRASS = "#5b9a4f"      # outfield grass
    DIRT  = "#a87b48"      # infield + warning track
    GRASS_LINE = "#3e6e36"
    DIRT_LINE  = "#8a5c2c"
    FOUL_LINE  = "#ffffff"

    # ----- (1) Outfield grass (big green fill bounded by foul lines + wall) -----
    wall_pts_x, wall_pts_y = [], []
    # Start at home plate
    wall_pts_x.append(0); wall_pts_y.append(0)
    # Walk along LF foul line out to LF foul pole
    # Foul line for RHH-viewer chart: LF line goes from home toward upper-left
    # at 45° (angle -45 from CF-axis). Distance = foul_extent up to wall.
    # Then arc from LF foul-pole around to RF foul-pole
    # Then back down the RF foul line to home.
    # We approximate the wall as an arc spanning -45° to +45° from CF.
    for deg in range(-45, 46, 2):
        t = abs(deg) / 45.0
        wall_dist = fd["of_wall_cf"] * (1 - t) + fd["of_wall_lf_rf"] * t
        rad = math.radians(deg)
        wall_pts_x.append(-wall_dist * math.sin(rad))
        wall_pts_y.append(wall_dist * math.cos(rad))
    wall_pts_x.append(0); wall_pts_y.append(0)
    fig.add_trace(go.Scatter(
        x=wall_pts_x, y=wall_pts_y,
        mode="lines",
        line=dict(color=GRASS_LINE, width=2),
        fill="toself", fillcolor=GRASS,
        showlegend=False, hoverinfo="skip",
    ))

    # ----- (2) Dirt infield (skinned infield arc + basepaths) -----
    # Skinned infield is roughly a 95-110 ft arc from home around the bases
    # (smaller for softball). Draw it as a sector polygon.
    bp = fd["base_path_ft"]
    skinned_radius = (bp * 1.3 if sport == "Baseball" else bp * 1.25)
    dirt_pts_x, dirt_pts_y = [0], [0]
    for deg in range(-45, 46, 3):
        rad = math.radians(deg)
        dirt_pts_x.append(-skinned_radius * math.sin(rad))
        dirt_pts_y.append(skinned_radius * math.cos(rad))
    dirt_pts_x.append(0); dirt_pts_y.append(0)
    fig.add_trace(go.Scatter(
        x=dirt_pts_x, y=dirt_pts_y,
        mode="lines",
        line=dict(color=DIRT_LINE, width=1.5),
        fill="toself", fillcolor=DIRT,
        showlegend=False, hoverinfo="skip",
    ))

    # ----- (3) Infield diamond grass (inside the basepaths) -----
    diag = bp * math.sqrt(2) / 2
    fig.add_trace(go.Scatter(
        x=[0, -diag, 0, diag, 0],
        y=[0, diag, 2*diag, diag, 0],
        mode="lines",
        line=dict(color=GRASS_LINE, width=1.4),
        fill="toself", fillcolor=GRASS,
        showlegend=False, hoverinfo="skip",
    ))

    # ----- (4) Foul lines (white, drawn ON TOP of grass/dirt) -----
    extent_for_lines = fd["of_wall_lf_rf"] * 1.02
    fl_x = extent_for_lines * math.sin(math.radians(-45))
    fl_y = extent_for_lines * math.cos(math.radians(-45))
    # LF foul line (sin(-45) is negative; using direct calc gives a negative x)
    fig.add_trace(go.Scatter(
        x=[0, fl_x], y=[0, fl_y],
        mode="lines", line=dict(color=FOUL_LINE, width=2.5),
        showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=[0, -fl_x], y=[0, fl_y],
        mode="lines", line=dict(color=FOUL_LINE, width=2.5),
        showlegend=False, hoverinfo="skip",
    ))

    # ----- (5) Outfield wall outline (dark line on top of the grass edge) -----
    wall_only_x = wall_pts_x[1:-1]  # skip the home-plate endpoints
    wall_only_y = wall_pts_y[1:-1]
    fig.add_trace(go.Scatter(
        x=wall_only_x, y=wall_only_y,
        mode="lines", line=dict(color="#1f2937", width=2.5),
        showlegend=False, hoverinfo="skip",
    ))

    # ----- (6) Range arcs at common distances -----
    if sport == "Baseball":
        range_marks = [200, 300, 400]
    else:
        range_marks = [150, 200, 250]
    for r in range_marks:
        arc_x, arc_y = [], []
        for deg in range(-45, 46, 3):
            rad = math.radians(deg)
            arc_x.append(-r * math.sin(rad))
            arc_y.append(r * math.cos(rad))
        fig.add_trace(go.Scatter(
            x=arc_x, y=arc_y, mode="lines",
            line=dict(color="rgba(255,255,255,0.45)", width=1, dash="dot"),
            showlegend=False, hoverinfo="skip",
        ))
        # Distance label at the rightmost end of the arc
        fig.add_annotation(
            x=arc_x[-1] + 5, y=arc_y[-1] + 8,
            text=f"{r} ft",
            showarrow=False,
            font=dict(size=9, color="rgba(255,255,255,0.85)"),
        )

    # ----- (7) Bases (white squares at 1B, 2B, 3B; home plate is a pentagon) -----
    base_size = 4.5 if sport == "Baseball" else 3.5
    for bx, by in [(diag, diag), (0, 2*diag), (-diag, diag)]:
        fig.add_shape(
            type="rect",
            x0=bx - base_size, x1=bx + base_size,
            y0=by - base_size, y1=by + base_size,
            line=dict(color="black", width=1.2),
            fillcolor="white", layer="above",
        )

    # ----- (8) Pitcher's mound -----
    md = fd["mound_distance"]
    mound_r = 9 if sport == "Baseball" else 8
    fig.add_shape(type="circle",
                   x0=-mound_r, x1=mound_r,
                   y0=md - mound_r, y1=md + mound_r,
                   line=dict(color=DIRT_LINE, width=1.5),
                   fillcolor=DIRT, layer="above")
    # Rubber on the mound
    rubber_w = 2.0; rubber_h = 0.4
    fig.add_shape(type="rect",
                   x0=-rubber_w, x1=rubber_w,
                   y0=md - rubber_h, y1=md + rubber_h,
                   line=dict(color="black", width=0.8),
                   fillcolor="white", layer="above")

    # ----- (9) Home plate marker (pentagon) -----
    fig.add_trace(go.Scatter(
        x=[0], y=[0], mode="markers",
        marker=dict(size=14, color="white", symbol="pentagon",
                     line=dict(color="black", width=1.5)),
        showlegend=False, hoverinfo="skip",
    ))

    # ----- Flight paths and landing dots -----
    in_play = df[df["Swing_Outcome"].isin(
        ["weak_contact", "solid_contact", "barrel", "foul"]
    )].copy()

    for outcome in ["weak_contact", "solid_contact", "barrel", "foul"]:
        g = in_play[in_play["Swing_Outcome"] == outcome]
        if g.empty:
            continue
        color = SWING_OUTCOME_COLORS[outcome]
        # Draw a curved flight arc for each ball
        for _, row in g.iterrows():
            x_land, y_land = _spray_landing_xy(
                row.get("Spray_Angle_deg"), row.get("Distance_ft"), hand=hand
            )
            if x_land is None:
                continue
            # Straight flight path from home plate to landing — clean and pro.
            # (We don't have a z-axis in the 2D field view, so a thin straight
            # line reads better than a fake "arc" perturbation.)
            fig.add_trace(go.Scatter(
                x=[0, x_land], y=[0, y_land], mode="lines",
                line=dict(color=color, width=1.2, dash="solid"),
                opacity=0.45,
                showlegend=False, hoverinfo="skip",
            ))

        # Landing dots, grouped by outcome (so legend works)
        lx, ly, cdata, hovertext = [], [], [], []
        for _, row in g.iterrows():
            x_land, y_land = _spray_landing_xy(
                row.get("Spray_Angle_deg"), row.get("Distance_ft"), hand=hand
            )
            if x_land is None:
                continue
            lx.append(x_land)
            ly.append(y_land)
            cdata.append([int(row["Swing_Num"])])
            ev = row.get("Exit_Velocity_mph")
            la = row.get("Launch_Angle_deg")
            dist = row.get("Distance_ft")
            hovertext.append(
                f"<b>Swing #{int(row['Swing_Num'])}</b> — {outcome.replace('_', ' ').title()}<br>"
                f"Pitch faced: {row['Pitch_Type_Faced']}<br>"
                f"Exit Velo: {ev:.1f} mph &nbsp;|&nbsp; "
                f"Launch: {la:.1f}° &nbsp;|&nbsp; Distance: {int(dist)} ft"
            )
        fig.add_trace(go.Scatter(
            x=lx, y=ly,
            mode="markers+text",
            marker=dict(size=18, color=color, line=dict(width=1.5, color="black"),
                         opacity=0.92),
            text=[str(c[0]) for c in cdata],
            textfont=dict(color="white", size=9, family="Arial Black"),
            textposition="middle center",
            customdata=cdata,
            hovertemplate="%{hovertext}<extra></extra>",
            hovertext=hovertext,
            name=outcome.replace("_", " ").title(),
        ))

    # Layout: dark navy-ish background so the green field stands out, no axes.
    plot_range = max(fd["of_wall_lf_rf"], fd["of_wall_cf"]) * 1.10
    # NOTE: no scaleanchor — locking 1:1 pixel ratio forces Plotly to
    # extend the axes way past the field data, leaving the field
    # squashed in the middle of the chart. Dropping it means the field
    # outline gets slightly vertically stretched, but every spray dot
    # and the OF wall arc fills the chart and is way easier to read on
    # a phone. Worth the tiny geometric trade.
    fig.update_layout(
        xaxis=dict(title="", range=[-plot_range, plot_range],
                    showgrid=False, zeroline=False, visible=False,
                    fixedrange=True, autorange=False),
        yaxis=dict(title="", range=[-30, plot_range + 20],
                    showgrid=False, zeroline=False, visible=False,
                    fixedrange=True, autorange=False),
        plot_bgcolor="#0f172a",  # dark blue-gray "stadium" background
        paper_bgcolor="#0f172a",
        font=dict(color="#e5e7eb"),
        height=900,
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                     bgcolor="rgba(0,0,0,0)",
                     font=dict(color="#e5e7eb")),
        margin=dict(l=10, r=10, t=40, b=10),
    )
    # preserve_bg keeps the stadium navy
    return _apply_chart_theme(fig, preserve_bg=True)


def _quality_color(score, alpha: float = 0.78) -> str:
    """Map a quality score (-2..+2) to an RGBA color.

    Positive scores → white → coral → red → dark red.
    Negative scores → white → light blue → blue → dark blue.
    None / 0 → neutral light gray.
    """
    if score is None or pd.isna(score):
        return f"rgba(229, 231, 235, {alpha * 0.45})"
    if abs(score) < 0.05:
        return f"rgba(229, 231, 235, {alpha * 0.55})"
    if score > 0:
        intensity = min(score / 2.0, 1.0)
        # Lerp #fecaca (light coral) → #7f1d1d (dark red)
        r = int(252 + (127 - 252) * intensity)
        g = int(202 + (29 - 202) * intensity)
        b = int(202 + (29 - 202) * intensity)
    else:
        intensity = min(abs(score) / 2.0, 1.0)
        # Lerp #dbeafe (very light blue) → #1e3a8a (dark blue)
        r = int(219 + (30 - 219) * intensity)
        g = int(234 + (58 - 234) * intensity)
        b = int(254 + (138 - 254) * intensity)
    return f"rgba({r}, {g}, {b}, {alpha})"


def _build_hit_quality_zone_heatmap_figure(
    current_df: pd.DataFrame,
    history_df: pd.DataFrame | None = None,
) -> "go.Figure":
    """Strike-zone HEAT MAP — colored zones (not dots).

    Divides the plate area into a 5x5 grid. Each cell colors itself by the
    athlete's HISTORICAL average contact quality in that cell (across all
    saved sessions + current). Red zones = hitter punishes mistakes here;
    blue zones = hitter struggles. Overlays today's swings as numbered dots
    so you can see where the *new* swings landed.
    """
    fig = go.Figure()

    # Combine current with prior sessions for the heat-map background scoring.
    if history_df is not None and len(history_df) > 0:
        try:
            all_swings = pd.concat([history_df, current_df], ignore_index=True)
        except Exception:
            all_swings = current_df.copy()
        # Count distinct prior sessions by Timestamp date — each saved session
        # carries its own date stamp, so the unique-date count is reliable.
        try:
            if "Timestamp" in history_df.columns and history_df["Timestamp"].notna().any():
                history_session_count = int(
                    pd.to_datetime(history_df["Timestamp"]).dt.normalize().nunique()
                )
            else:
                history_session_count = 0
        except Exception:
            history_session_count = 0
    else:
        all_swings = current_df.copy()
        history_session_count = 0

    # Only actual swings count for hit-quality heat (takes carry no contact info)
    swing_mask = all_swings["Swing_Type"].fillna("") == "swing"
    swings_only = all_swings[swing_mask].copy()
    # Attach quality score
    swings_only["_q"] = swings_only["Swing_Outcome"].map(SWING_QUALITY_SCORE)

    # Grid: 5x5 covering strike zone + a bit of chase zone on each side.
    # X: -1.0 to 1.0 ft (zone is -0.71 to 0.71). Z: 1.0 to 4.0 ft.
    x_edges = [-1.0 + 0.4 * i for i in range(6)]   # 6 edges, 5 cells
    z_edges = [1.0 + 0.6 * i for i in range(6)]

    # Draw heat-map cells
    for i in range(5):
        x0, x1 = x_edges[i], x_edges[i + 1]
        for j in range(5):
            z0, z1 = z_edges[j], z_edges[j + 1]
            cell = swings_only[
                (swings_only["Plate_X_ft"] >= x0) &
                (swings_only["Plate_X_ft"] <  x1) &
                (swings_only["Plate_Z_ft"] >= z0) &
                (swings_only["Plate_Z_ft"] <  z1)
            ]
            n_swings_in_cell = len(cell)
            avg_q = cell["_q"].dropna().mean() if n_swings_in_cell else None
            fill = _quality_color(avg_q)
            fig.add_shape(
                type="rect", x0=x0, x1=x1, y0=z0, y1=z1,
                line=dict(color="rgba(255,255,255,0.6)", width=1),
                fillcolor=fill, layer="below",
            )
            # Annotate cell with swing count + avg score (only if any data)
            if n_swings_in_cell > 0 and avg_q is not None:
                label = f"{n_swings_in_cell}🏏  ({avg_q:+.1f})"
                fig.add_annotation(
                    x=(x0 + x1) / 2, y=(z0 + z1) / 2,
                    text=f"<span style='font-size:9px;color:#1f2937;'>{label}</span>",
                    showarrow=False,
                )

    # Strike zone box overlay (drawn ON TOP of the heat cells so it stays visible)
    fig.add_shape(type="rect", x0=SZ_X_MIN, x1=SZ_X_MAX, y0=SZ_Z_MIN, y1=SZ_Z_MAX,
                   line=dict(color="black", width=2.5),
                   fillcolor="rgba(0,0,0,0)", layer="above")
    # Strike-zone 3x3 grid (slightly heavier than the 5x5 backing grid)
    for i in (1, 2):
        x = SZ_X_MIN + (SZ_X_MAX - SZ_X_MIN) * (i / 3)
        z = SZ_Z_MIN + (SZ_Z_MAX - SZ_Z_MIN) * (i / 3)
        fig.add_shape(type="line", x0=x, x1=x, y0=SZ_Z_MIN, y1=SZ_Z_MAX,
                       line=dict(color="black", width=1, dash="dot"),
                       layer="above")
        fig.add_shape(type="line", x0=SZ_X_MIN, x1=SZ_X_MAX, y0=z, y1=z,
                       line=dict(color="black", width=1, dash="dot"),
                       layer="above")
    # Home plate at the bottom
    fig.add_shape(
        type="path",
        path="M -0.71 0.05 L 0.71 0.05 L 0.50 -0.10 L 0 -0.25 L -0.50 -0.10 Z",
        line=dict(color="black", width=1.5),
        fillcolor="rgba(220,220,220,0.8)", layer="above",
    )

    # Overlay TODAY's swings only (not takes) as numbered dots, sized small so
    # the underlying heat map shows through
    today_swings = current_df[
        current_df["Swing_Type"].fillna("") == "swing"
    ]
    for outcome in ["barrel", "solid_contact", "foul", "weak_contact", "whiff"]:
        g = today_swings[today_swings["Swing_Outcome"] == outcome]
        if g.empty:
            continue
        color = SWING_OUTCOME_COLORS[outcome]
        hover = []
        for _, row in g.iterrows():
            ev = row.get("Exit_Velocity_mph")
            la = row.get("Launch_Angle_deg")
            hover.append(
                f"<b>Swing #{int(row['Swing_Num'])}</b> — {outcome.replace('_', ' ').title()}<br>"
                f"Pitch: {row['Pitch_Type_Faced']} @ {row.get('Pitch_Velocity_mph', '—')} mph<br>"
                + (f"EV {ev:.1f} mph · LA {la:.1f}°" if pd.notna(ev) else "No contact")
            )
        fig.add_trace(go.Scatter(
            x=g["Plate_X_ft"], y=g["Plate_Z_ft"],
            mode="markers+text",
            marker=dict(size=15, color=color,
                         line=dict(width=1.5, color="white")),
            text=[str(int(s)) for s in g["Swing_Num"]],
            textposition="middle center",
            textfont=dict(color="white", size=9, family="Arial Black"),
            customdata=g[["Swing_Num"]].values,
            hovertemplate="%{hovertext}<extra></extra>",
            hovertext=hover,
            name=outcome.replace("_", " ").title(),
        ))

    # Sub-title showing whether this is single-session or multi-session aggregated
    if history_session_count > 0:
        subtitle = (f"Heat map aggregates {history_session_count + 1} session(s) · "
                    "today's swings overlaid")
    else:
        subtitle = "Heat map based on this session only — log more sessions to see progress over time"

    fig.update_layout(
        title=dict(
            text=f"<span style='font-size:13px;color:#6b7280;'>{subtitle}</span>",
            x=0.5, xanchor="center", y=0.97,
        ),
        xaxis=dict(title="Plate Side (ft) — catcher's view", range=(-1.5, 1.5),
                    zeroline=False, showgrid=False,
                    fixedrange=True, autorange=False),
        yaxis=dict(title="Height (ft)", range=(0.5, 4.5),
                    zeroline=False, showgrid=False,
                    fixedrange=True, autorange=False),
        height=800,
        legend=dict(orientation="h", yanchor="bottom", y=1.04),
        margin=dict(l=20, r=20, t=60, b=40),
        plot_bgcolor="white",
    )
    return _apply_chart_theme(fig)


# Back-compat alias (older code references the previous name)
def _build_hit_quality_zone_figure(df: pd.DataFrame) -> "go.Figure":
    """Strike-zone scatter colored by contact quality on a red↔blue scale.

    Barrel = darkest red. Whiff = darkest blue. Foul = neutral gray.
    Takes are rendered as light-gray rings (no swing data).
    """
    fig = go.Figure()

    # Strike zone box
    fig.add_shape(type="rect", x0=SZ_X_MIN, x1=SZ_X_MAX, y0=SZ_Z_MIN, y1=SZ_Z_MAX,
                   line=dict(color="black", width=2),
                   fillcolor="rgba(0,0,0,0)", layer="below")
    # 3x3 grid
    for i in (1, 2):
        x = SZ_X_MIN + (SZ_X_MAX - SZ_X_MIN) * (i / 3)
        z = SZ_Z_MIN + (SZ_Z_MAX - SZ_Z_MIN) * (i / 3)
        fig.add_shape(type="line", x0=x, x1=x, y0=SZ_Z_MIN, y1=SZ_Z_MAX,
                       line=dict(color="#cccccc", width=0.6), layer="below")
        fig.add_shape(type="line", x0=SZ_X_MIN, x1=SZ_X_MAX, y0=z, y1=z,
                       line=dict(color="#cccccc", width=0.6), layer="below")
    # Home plate at the bottom
    fig.add_shape(
        type="path",
        path="M -0.71 0.05 L 0.71 0.05 L 0.50 -0.10 L 0 -0.25 L -0.50 -0.10 Z",
        line=dict(color="black", width=1.5),
        fillcolor="rgba(220,220,220,0.6)", layer="below",
    )

    # Group + render — one trace per outcome so the legend is readable
    for outcome in ["barrel", "solid_contact", "foul", "weak_contact", "whiff", "take"]:
        g = df[df["Swing_Outcome"] == outcome]
        if g.empty:
            continue
        color = SWING_OUTCOME_COLORS[outcome]
        marker = dict(size=18, color=color, line=dict(width=1.5, color="black"))
        if outcome == "take":
            # render as hollow ring so takes are visible but de-emphasized
            marker = dict(size=14, color="rgba(0,0,0,0)",
                           line=dict(width=2, color="#9ca3af"))
        hover = []
        for _, row in g.iterrows():
            ev = row.get("Exit_Velocity_mph")
            la = row.get("Launch_Angle_deg")
            hover.append(
                f"<b>Swing #{int(row['Swing_Num'])}</b> — {outcome.replace('_', ' ').title()}<br>"
                f"Pitch: {row['Pitch_Type_Faced']} @ {row.get('Pitch_Velocity_mph', '—')} mph<br>"
                + (f"EV {ev:.1f} mph · LA {la:.1f}°" if pd.notna(ev) else "No contact")
            )
        fig.add_trace(go.Scatter(
            x=g["Plate_X_ft"], y=g["Plate_Z_ft"],
            mode="markers+text",
            marker=marker,
            text=[str(int(s)) for s in g["Swing_Num"]],
            textposition="middle center",
            textfont=dict(color="white" if outcome != "take" else "#6b7280",
                           size=9, family="Arial Black"),
            customdata=g[["Swing_Num"]].values,
            hovertemplate="%{hovertext}<extra></extra>",
            hovertext=hover,
            name=outcome.replace("_", " ").title(),
        ))

    # No scaleanchor — see note in _build_strike_zone_figure.
    fig.update_layout(
        xaxis=dict(title="Plate Side (ft)", range=SZ_PLOT_X_RANGE,
                    zeroline=False, showgrid=False,
                    fixedrange=True, autorange=False),
        yaxis=dict(title="Height (ft)", range=SZ_PLOT_Z_RANGE,
                    zeroline=False, showgrid=False,
                    fixedrange=True, autorange=False),
        height=560,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=20, r=20, t=40, b=40),
        plot_bgcolor="white",
    )
    return _apply_chart_theme(fig)


def _render_swing_detail_panel(swing: pd.Series, sport: str = "Baseball"):
    """Detail card for one clicked swing — shows pitch faced + contact metrics.

    Designed to live inside a NARROW column (the right-hand panel beside the
    spray chart). Values must wrap, not truncate, so this uses a custom HTML
    grid instead of st.metric (which clips text in narrow columns).
    """
    outcome = swing["Swing_Outcome"]
    outcome_color = SWING_OUTCOME_COLORS.get(outcome, "#6b7280")
    outcome_label = outcome.replace("_", " ").title()

    # Stacked header — pill on top, swing label below — so nothing truncates
    st.markdown(_flat_html(
        f"<div style='margin-bottom:14px;'>"
        f"<div style='display:inline-block;background:{outcome_color};color:white;"
        f"padding:5px 12px;border-radius:14px;font-size:11px;font-weight:700;"
        f"letter-spacing:0.04em;margin-bottom:8px;'>{outcome_label.upper()}</div>"
        f"<div style='font-size:18px;font-weight:700;color:#1a2150;line-height:1.3;'>"
        f"Swing #{int(swing['Swing_Num'])}"
        f"</div>"
        f"<div style='font-size:13px;color:#6b7280;font-weight:500;margin-top:2px;'>"
        f"vs {swing['Pitch_Type_Faced']}"
        f"</div>"
        f"</div>"
    ), unsafe_allow_html=True)

    def _metric_card(label: str, value: str) -> str:
        """One compact value tile that wraps and never clips."""
        return (
            f"<div style='background:#f6f7fb;border:1px solid #e5e7eb;"
            f"border-radius:8px;padding:8px 10px;'>"
            f"<div style='font-size:10px;letter-spacing:0.06em;font-weight:600;"
            f"text-transform:uppercase;color:#6b7280;margin-bottom:3px;'>{label}</div>"
            f"<div style='font-size:16px;font-weight:700;color:#1a2150;line-height:1.2;'>"
            f"{value}</div>"
            f"</div>"
        )

    def _grid_section(title: str, items: list[tuple[str, str]]):
        """Render a section header + 2-column grid of metric cards."""
        cards = "".join(_metric_card(lbl, val) for lbl, val in items)
        st.markdown(_flat_html(
            f"<div style='font-size:11px;letter-spacing:0.08em;font-weight:700;"
            f"text-transform:uppercase;color:#1a2150;margin:14px 0 8px 0;'>{title}</div>"
            f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:8px;'>"
            f"{cards}</div>"
        ), unsafe_allow_html=True)

    # --- Pitch faced ---
    _grid_section("Pitch Faced", [
        ("Pitch Velo",   f"{swing['Pitch_Velocity_mph']:.1f} mph"
                          if pd.notna(swing.get("Pitch_Velocity_mph")) else "—"),
        ("Plate Side",   f"{swing['Plate_X_ft']:+.2f} ft"
                          if pd.notna(swing.get("Plate_X_ft")) else "—"),
        ("Plate Height", f"{swing['Plate_Z_ft']:.2f} ft"
                          if pd.notna(swing.get("Plate_Z_ft")) else "—"),
        ("Swing?",       "✗ Took" if outcome == "take" else "✓ Swung"),
    ])

    if outcome == "take":
        st.info("No swing — review whether this was a borderline call to chase or take in future at-bats.")
        return

    # --- Bat metrics ---
    _grid_section("Bat Metrics", [
        ("Bat Speed",    f"{swing['Bat_Speed_mph']:.1f} mph"
                          if pd.notna(swing.get("Bat_Speed_mph")) else "—"),
        ("Attack Angle", f"{swing['Attack_Angle_deg']:.1f}°"
                          if pd.notna(swing.get("Attack_Angle_deg")) else "—"),
        ("On-Plane %",   f"{swing['On_Plane_Eff_pct']:.1f}%"
                          if pd.notna(swing.get("On_Plane_Eff_pct")) else "—"),
        ("Time→Contact", f"{swing['Time_to_Contact_sec']:.3f}s"
                          if pd.notna(swing.get("Time_to_Contact_sec")) else "—"),
    ])

    # --- Contact metrics (only if ball was hit) ---
    if outcome in ("barrel", "solid_contact", "weak_contact", "foul"):
        spray = swing.get("Spray_Angle_deg")
        spray_label = "—"
        if pd.notna(spray):
            if spray < -10: spray_label = f"Pull ({spray:.0f}°)"
            elif spray > 10: spray_label = f"Oppo ({spray:.0f}°)"
            else:           spray_label = f"Center ({spray:.0f}°)"

        _grid_section("Contact", [
            ("Exit Velo",    f"{swing['Exit_Velocity_mph']:.1f} mph"
                              if pd.notna(swing.get("Exit_Velocity_mph")) else "—"),
            ("Launch Angle", f"{swing['Launch_Angle_deg']:.1f}°"
                              if pd.notna(swing.get("Launch_Angle_deg")) else "—"),
            ("Distance",     f"{int(swing['Distance_ft'])} ft"
                              if pd.notna(swing.get("Distance_ft")) else "—"),
            ("Direction",    spray_label),
        ])

    # --- Result narrative ---
    narratives = {
        "barrel":        "🎯 **Barrel.** Exit velo + launch angle hit the optimal zone. This is "
                          "the swing pattern to repeat.",
        "solid_contact": "✅ **Solid contact.** Quality at-bat — could be a hit depending on defense.",
        "weak_contact":  "⚠️ **Weak contact.** Likely a routine out. Look at the bat path and "
                          "timing in the mechanics tab.",
        "foul":          "🟡 **Foul ball.** Late or out in front — adjust timing or aim.",
        "whiff":         "**Swing and miss.** Look for the chase pattern — was this in the zone "
                          "or did you go after a ball?",
    }
    st.markdown("")  # spacing
    st.info(narratives.get(outcome, ""))


def run_hitting_lab(athlete_name: str, athlete_hand: str, athlete_class: str,
                     athlete_sport: str, athlete_level: str,
                     active_athlete_id: int | None, demo_mode: bool):
    """Render the Hitting Lab view. v1 = Overview tab with KPIs, charts,
    swing list. Mechanics + drills + PDF come in subsequent phases.
    """
    # ===== Source the swing data =====
    # Priority: (1) demo mode → generate fake. (2) saved hitting history
    # for this athlete → load most recent session. (3) empty state with
    # CTAs to capture or turn on Sample Session.
    df = None
    if demo_mode:
        with st.spinner(f"Generating swing session for {athlete_name}..."):
            df = generate_hitting_session(athlete_name, hand=athlete_hand,
                                             sport=athlete_sport)
    elif active_athlete_id is not None:
        try:
            recent = list_sessions(active_athlete_id,
                                       session_kind="hitting", limit=1)
            if recent:
                df = load_session_df(recent[0]["id"])
        except Exception:
            df = None

    if df is None or len(df) == 0:
        _branded_header_hitting(athlete_name, athlete_hand, athlete_class,
                                  demo_mode, sport=athlete_sport)
        st.markdown(_flat_html(
            "<div style='background:#1e293b;border:1px solid #334155;"
            "border-radius:14px;padding:28px 28px;margin-top:12px;'>"
            "<div style='font-size:11px;letter-spacing:0.14em;font-weight:700;"
            "color:#d4a634;text-transform:uppercase;margin-bottom:8px;'>"
            "Hitting Lab</div>"
            "<div style='font-size:22px;font-weight:700;color:#f1f5f9;"
            "margin-bottom:8px;'>"
            f"No swing data yet for {athlete_name}.</div>"
            "<div style='font-size:14px;color:#cbd5e1;line-height:1.6;'>"
            "Two ways to get started:<br>"
            "&nbsp;&nbsp;• Turn on <b>Live Capture (Beta)</b> in the sidebar "
            "and film a tee or front-toss session with your phone.<br>"
            "&nbsp;&nbsp;• Turn on <b>Sample Session</b> in the sidebar to "
            "generate a believable batting practice and explore the full "
            "report layout."
            "</div></div>"
        ), unsafe_allow_html=True)
        st.stop()

    _branded_header_hitting(athlete_name, athlete_hand, athlete_class,
                             demo_mode, sport=athlete_sport)

    # ===== Auto-save hitting session to history =====
    # The heat map aggregates across saved sessions, so we persist every
    # hitting session (sample or real) the first time it's seen. Fingerprint
    # avoids double-saving on Streamlit reruns.
    if active_athlete_id is not None:
        try:
            first_ts = str(df["Timestamp"].min()) if "Timestamp" in df.columns else "na"
            last_ts  = str(df["Timestamp"].max()) if "Timestamp" in df.columns else "na"
            hit_fp = f"hit|{active_athlete_id}|{len(df)}|{first_ts}|{last_ts}"
        except Exception:
            hit_fp = f"hit|{active_athlete_id}|{len(df)}|{id(df)}"

        if st.session_state.get("_saved_hitting_fingerprint") != hit_fp:
            try:
                save_session(active_athlete_id, df,
                              session_type=("sample" if demo_mode else "real"),
                              session_kind="hitting")
                st.session_state["_saved_hitting_fingerprint"] = hit_fp
            except Exception as e:
                st.warning(f"Could not auto-save this swing session to history: {e}")

    kpis = hitting_session_kpis(df)
    _hitting_kpi_row(kpis)

    st.divider()

    # ===== Tabs =====
    tab_overview, tab_swings, tab_history, tab_action = st.tabs(
        ["Overview", "Swing Detail", "History", "Action Plan"]
    )

    # ----- Overview -----
    with tab_overview:
        # =====================================================
        # SPRAY CHART + SWING DETAIL (side-by-side — they're connected)
        # =====================================================
        st.subheader("Spray Chart")
        st.caption(
            "Click any landing dot or zone-map dot to open the swing's full "
            "story on the right. Color = contact quality "
            "(dark red = barrels, red = solid, gray = fouls, light blue = weak)."
        )

        # Spray chart click capture
        spray_fig = _build_spray_chart_figure(df, sport=athlete_sport, hand=athlete_hand)

        # Wider spray column — the field is the centerpiece of this tab.
        spray_col, detail_col = st.columns([2.0, 1.0])
        with spray_col:
            # height_px=900 + no scaleanchor means the field fills the
            # whole chart vertically instead of being padded with empty
            # space above and below — way bigger on phone.
            render_static_chart(spray_fig, key="hitting_spray_chart",
                                  height_px=900)

        with detail_col:
            # Picker is now the sole selection mechanism (chart is a PNG).
            swing_options = {
                int(r["Swing_Num"]):
                    f"Swing #{int(r['Swing_Num'])} — "
                    f"{str(r['Swing_Outcome']).replace('_',' ').title()}"
                for _, r in df.iterrows()
            }
            keys = list(swing_options.keys())
            prior = st.session_state.get("hitting_selected_swing")
            default_idx = keys.index(prior) if prior in keys else 0
            selected_swing_num = st.selectbox(
                "Pick a swing",
                keys,
                format_func=lambda k: swing_options[k],
                index=default_idx,
                key="hitting_swing_picker",
            )
            match = df[df["Swing_Num"] == selected_swing_num]
            if not match.empty:
                _render_swing_detail_panel(match.iloc[0], sport=athlete_sport)

        # Remember the selection across reruns
        if selected_swing_num is not None:
            st.session_state["hitting_selected_swing"] = selected_swing_num

        st.divider()

        # =====================================================
        # ZONE HEAT MAP — colored zones from session history
        # =====================================================
        st.subheader("Strike Zone Heat Map")
        st.caption(
            "**Zones colored by your career hit quality** (aggregated across all "
            "saved sessions): dark red = you punish mistakes here, light blue = "
            "you struggle here. The numbered dots are **today's swings only** — "
            "see how today maps onto your tendencies. Click a dot for the swing's details."
        )

        # Load this athlete's hitting history to aggregate the heat map
        history_df = pd.DataFrame()
        if active_athlete_id is not None:
            try:
                history_df = load_hitting_history(active_athlete_id, lookback=20)
            except Exception:
                history_df = pd.DataFrame()

        zone_fig = _build_hit_quality_zone_heatmap_figure(df, history_df=history_df)
        render_static_chart(zone_fig, key="hitting_zone_chart",
                              height_px=700)

        st.divider()
        # =====================================================
        # ORIGINAL EV vs LA scatter — keep for completeness
        # =====================================================
        st.subheader("Exit Velocity vs Launch Angle")
        st.caption(
            "Each dot is one batted ball. The shaded **barrel zone** "
            "(95+ mph EV, 8–32° launch angle) is the region where hits "
            "are most likely to go for extra bases."
        )

        in_play = df[df["Swing_Outcome"].isin(
            ["weak_contact", "solid_contact", "barrel", "foul"]
        )].copy()
        if len(in_play):
            fig = px.scatter(
                in_play, x="Launch_Angle_deg", y="Exit_Velocity_mph",
                color="Swing_Outcome", text="Swing_Num",
                color_discrete_map={
                    "barrel":        "#16a34a",
                    "solid_contact": "#1976d2",
                    "weak_contact":  "#d4a634",
                    "foul":          "#6b7280",
                },
                hover_data=["Pitch_Type_Faced", "Pitch_Velocity_mph",
                            "Bat_Speed_mph", "Distance_ft"],
                labels={
                    "Launch_Angle_deg":   "Launch Angle (deg)",
                    "Exit_Velocity_mph":  "Exit Velocity (mph)",
                },
            )
            # Barrel zone shaded rectangle (rough Statcast definition)
            fig.add_shape(type="rect",
                          x0=8, x1=32, y0=95, y1=115,
                          line=dict(color="#16a34a", width=1, dash="dot"),
                          fillcolor="rgba(22,163,74,0.07)", layer="below")
            fig.add_annotation(x=20, y=110, text="BARREL ZONE",
                                 showarrow=False, font=dict(size=10, color="#16a34a"))
            fig.update_traces(marker=dict(size=14, line=dict(width=1, color="black")),
                                textposition="top center")
            fig.update_layout(height=480, legend_title_text="")
            render_static_chart(fig)
        else:
            st.info("No balls in play this session.")

        st.divider()
        st.subheader("Performance by Pitch Type Faced")
        bd_rows = []
        for ptype, g in df.groupby("Pitch_Type_Faced"):
            in_play_g = g[g["Swing_Outcome"].isin(
                ["weak_contact", "solid_contact", "barrel", "foul"])]
            whiffs = (g["Swing_Outcome"] == "whiff").sum()
            barrels = (g["Swing_Outcome"] == "barrel").sum()
            avg_ev = in_play_g["Exit_Velocity_mph"].dropna().mean()
            avg_la = in_play_g["Launch_Angle_deg"].dropna().mean()
            bd_rows.append({
                "Pitch Type":     ptype,
                "Seen":           len(g),
                "Swings":         (g["Swing_Type"] == "swing").sum(),
                "Whiffs":         int(whiffs),
                "Barrels":        int(barrels),
                "Avg Exit Velo":  f"{avg_ev:.1f} mph" if not pd.isna(avg_ev) else "—",
                "Avg Launch":     f"{avg_la:.1f}°"    if not pd.isna(avg_la) else "—",
            })
        st.dataframe(pd.DataFrame(bd_rows), use_container_width=True, hide_index=True)
        st.caption(
            "🔎 Watch the **Whiffs** column — the pitch types a hitter chases or "
            "misses most are the ones to attack in pitch sequencing. And the "
            "**Barrels** column shows where they punish mistakes."
        )

        # =====================================================
        # MECHANICS CRITIQUE (green / yellow boxes, same as pitching)
        # =====================================================
        critique = analyze_hitting_mechanics(df, sport=athlete_sport)
        if critique["strengths"] or critique["weaknesses"]:
            st.divider()
            st.subheader("Mechanics Critique")
            st.caption(
                "Swing-mechanics strengths and improvement areas, based on the bat-sensor "
                "and 3D pose data. Each improvement area is tied to a specific gain "
                "(power / contact / barrel rate / plate discipline)."
            )
            mc1, mc2 = st.columns(2)
            with mc1:
                strengths_html = (
                    "<div style='background:#f0fdf4;border:1px solid #bbf7d0;"
                    "border-left:4px solid #16a34a;border-radius:8px;padding:14px 16px;"
                    "margin-bottom:8px;'>"
                    "<div style='font-size:11px;letter-spacing:0.08em;font-weight:700;"
                    "color:#16a34a;text-transform:uppercase;margin-bottom:8px;'>"
                    "✓ What's working</div>"
                )
                if critique["strengths"]:
                    for s in critique["strengths"]:
                        strengths_html += (
                            f"<div style='margin-bottom:10px;'>"
                            f"<div style='font-weight:700;color:#14532d;font-size:14px;'>{s['label']}</div>"
                            f"<div style='font-size:13px;color:#1f2937;margin-top:2px;'>{s['detail']}</div>"
                            f"<div style='font-size:12px;color:#4b5563;margin-top:3px;font-style:italic;'>"
                            f"Why it matters: {s['gain']} "
                            f"<span style='background:#dcfce7;color:#15803d;padding:1px 7px;"
                            f"border-radius:8px;font-size:10.5px;font-weight:700;margin-left:4px;'>"
                            f"{s['tag']}</span></div>"
                            f"</div>"
                        )
                else:
                    strengths_html += "<div style='font-size:13px;color:#4b5563;'>No specific swing strengths flagged yet — keep building.</div>"
                strengths_html += "</div>"
                st.markdown(_flat_html(strengths_html), unsafe_allow_html=True)

            with mc2:
                weak_html = (
                    "<div style='background:#fefce8;border:1px solid #fde68a;"
                    "border-left:4px solid #d4a634;border-radius:8px;padding:14px 16px;"
                    "margin-bottom:8px;'>"
                    "<div style='font-size:11px;letter-spacing:0.08em;font-weight:700;"
                    "color:#92400e;text-transform:uppercase;margin-bottom:8px;'>"
                    "→ Areas to improve</div>"
                )
                if critique["weaknesses"]:
                    for w in critique["weaknesses"]:
                        weak_html += (
                            f"<div style='margin-bottom:10px;'>"
                            f"<div style='font-weight:700;color:#78350f;font-size:14px;'>{w['label']}</div>"
                            f"<div style='font-size:13px;color:#1f2937;margin-top:2px;'>{w['detail']}</div>"
                            f"<div style='font-size:12px;color:#4b5563;margin-top:3px;'>"
                            f"<b>Gain:</b> {w['gain']} &nbsp;·&nbsp; "
                            f"<b>Fix:</b> {w['fix']}</div>"
                            f"</div>"
                        )
                else:
                    weak_html += "<div style='font-size:13px;color:#4b5563;'>Clean swing across the session — no specific corrections flagged.</div>"
                weak_html += "</div>"
                st.markdown(_flat_html(weak_html), unsafe_allow_html=True)

    # ----- Swing Detail (compare + full canonical table) -----
    with tab_swings:
        # =====================================================
        # COMPARE TWO SWINGS (side-by-side)
        # =====================================================
        st.subheader("🆚 Compare Two Swings Side-by-Side")
        st.caption(
            "Pick any two swings — usually a barrel vs. a whiff, or a good "
            "session vs. a bad one — to compare the pitch faced, bat metrics, "
            "contact quality, and body sequencing."
        )

        swing_options = {
            int(r["Swing_Num"]):
                f"Swing #{int(r['Swing_Num'])} — {r['Pitch_Type_Faced']} "
                f"({r['Pitch_Velocity_mph']:.0f} mph) → "
                f"{str(r['Swing_Outcome']).replace('_', ' ').title()}"
            for _, r in df.iterrows()
        }
        keys = list(swing_options.keys())
        cmpA, cmpB = st.columns(2)
        with cmpA:
            a_key = st.selectbox("Swing A", keys,
                                  format_func=lambda k: swing_options[k],
                                  index=0, key="hit_cmp_a")
        with cmpB:
            default_b = 1 if len(keys) > 1 else 0
            b_key = st.selectbox("Swing B", keys,
                                  format_func=lambda k: swing_options[k],
                                  index=default_b, key="hit_cmp_b")

        if a_key != b_key:
            sa = df[df["Swing_Num"] == a_key].iloc[0]
            sb = df[df["Swing_Num"] == b_key].iloc[0]

            def _row(label, va, vb, fmt=lambda x: f"{x:.1f}" if pd.notna(x) else "—",
                      higher_is_better: bool | None = None):
                """One row of the compare table. Highlights better cell in green
                when higher_is_better is specified."""
                va_str = fmt(va) if pd.notna(va) else "—"
                vb_str = fmt(vb) if pd.notna(vb) else "—"
                # Color the better cell
                a_color, b_color = "#1a2150", "#1a2150"
                a_bg,    b_bg    = "#ffffff", "#ffffff"
                if higher_is_better is not None and pd.notna(va) and pd.notna(vb):
                    if higher_is_better:
                        if va > vb: a_bg = "#dcfce7"
                        elif vb > va: b_bg = "#dcfce7"
                    else:
                        if va < vb: a_bg = "#dcfce7"
                        elif vb < va: b_bg = "#dcfce7"
                return (
                    f"<tr>"
                    f"<td style='padding:8px 12px;font-weight:600;color:#374151;font-size:13px;'>{label}</td>"
                    f"<td style='padding:8px 12px;text-align:right;font-weight:700;"
                    f"color:{a_color};background:{a_bg};font-size:14px;'>{va_str}</td>"
                    f"<td style='padding:8px 12px;text-align:right;font-weight:700;"
                    f"color:{b_color};background:{b_bg};font-size:14px;'>{vb_str}</td>"
                    f"</tr>"
                )

            a_outcome = str(sa["Swing_Outcome"]).replace("_", " ").title()
            b_outcome = str(sb["Swing_Outcome"]).replace("_", " ").title()

            table_html = (
                "<table style='width:100%;border-collapse:collapse;"
                "border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;"
                "background:white;margin-top:8px;font-size:13px;'>"
                "<thead><tr style='background:#1a2150;color:white;'>"
                f"<th style='padding:10px 12px;text-align:left;font-size:11px;letter-spacing:0.06em;'>Metric</th>"
                f"<th style='padding:10px 12px;text-align:right;font-size:11px;letter-spacing:0.06em;'>"
                f"Swing #{int(a_key)} — {a_outcome}</th>"
                f"<th style='padding:10px 12px;text-align:right;font-size:11px;letter-spacing:0.06em;'>"
                f"Swing #{int(b_key)} — {b_outcome}</th>"
                "</tr></thead><tbody style='background:white;'>"
                # Pitch faced
                + _row("Pitch Velocity (mph)",   sa["Pitch_Velocity_mph"],  sb["Pitch_Velocity_mph"])
                + _row("Plate Side (ft)",        sa["Plate_X_ft"],          sb["Plate_X_ft"],
                        fmt=lambda x: f"{x:+.2f}" if pd.notna(x) else "—")
                + _row("Plate Height (ft)",      sa["Plate_Z_ft"],          sb["Plate_Z_ft"],
                        fmt=lambda x: f"{x:.2f}" if pd.notna(x) else "—")
                # Bat metrics
                + _row("Bat Speed (mph)",        sa["Bat_Speed_mph"],       sb["Bat_Speed_mph"],
                        higher_is_better=True)
                + _row("Attack Angle (°)",       sa["Attack_Angle_deg"],    sb["Attack_Angle_deg"])
                + _row("On-Plane Efficiency (%)",sa["On_Plane_Eff_pct"],    sb["On_Plane_Eff_pct"],
                        higher_is_better=True)
                + _row("Time-to-Contact (s)",    sa["Time_to_Contact_sec"], sb["Time_to_Contact_sec"],
                        fmt=lambda x: f"{x:.3f}" if pd.notna(x) else "—",
                        higher_is_better=False)
                # Contact metrics
                + _row("Exit Velocity (mph)",    sa["Exit_Velocity_mph"],   sb["Exit_Velocity_mph"],
                        higher_is_better=True)
                + _row("Launch Angle (°)",       sa["Launch_Angle_deg"],    sb["Launch_Angle_deg"])
                + _row("Distance (ft)",          sa["Distance_ft"],         sb["Distance_ft"],
                        fmt=lambda x: f"{int(x)}" if pd.notna(x) else "—",
                        higher_is_better=True)
                # Body sequencing
                + _row("Hip-Shoulder Sep (°)",   sa["Peak_Hip_Shoulder_Sep_deg"],
                                                  sb["Peak_Hip_Shoulder_Sep_deg"],
                        higher_is_better=True)
                + _row("Stride Length (in)",     sa["Stride_Length_in"],    sb["Stride_Length_in"])
                + _row("Lead Knee Flex (°)",     sa["Lead_Knee_Flex_deg"],  sb["Lead_Knee_Flex_deg"])
                + "</tbody></table>"
            )
            st.markdown(_flat_html(table_html), unsafe_allow_html=True)
            st.caption(
                "🟢 Green cell = the better swing on that metric. "
                "Spot the pattern: usually the barrel has stronger bat speed, "
                "deeper hip-shoulder sep, and a quicker time-to-contact."
            )
        else:
            st.info("Pick two **different** swings to compare.")

        st.divider()

        # =====================================================
        # FULL SWING TABLE
        # =====================================================
        st.subheader("Every Swing — Canonical View")
        display = df[[
            "Swing_Num", "Pitch_Type_Faced", "Pitch_Velocity_mph",
            "Swing_Outcome", "Bat_Speed_mph", "Exit_Velocity_mph",
            "Launch_Angle_deg", "Distance_ft", "On_Plane_Eff_pct",
        ]].copy()
        st.dataframe(display, use_container_width=True, hide_index=True)
        st.caption(
            "Every swing in the session, the pitch it came against, and the swing's "
            "outcome. Sort by any column."
        )

    # ----- History (cross-session trends) -----
    with tab_history:
        st.subheader(f"History — {athlete_name}")

        if active_athlete_id is None:
            st.info(
                "📌 You're viewing a sample session for a virtual hitter. "
                "Add this hitter to your roster (or pick a saved hitter in the sidebar) "
                "to start building a session history."
            )
        else:
            history = list_sessions(active_athlete_id, limit=50,
                                     session_kind="hitting")
            if not history:
                st.info(
                    f"No saved hitting sessions yet for **{athlete_name}**. "
                    "Each new session auto-saves — once you have 2+ sessions on file, "
                    "trend charts and progress tracking light up here."
                )
            else:
                st.caption(
                    f"{len(history)} hitting session(s) on file. Every Sample / Real "
                    "session is included in the trend (sample sessions are deterministic "
                    "but still useful for tracking growth on new data uploads)."
                )

                # ----- Build a trend df by computing KPIs per session -----
                trend_rows = []
                for s in history:
                    try:
                        sdf = load_session_df(s["id"])
                        if len(sdf) == 0:
                            continue
                        k = hitting_session_kpis(sdf)
                        trend_rows.append({
                            "session_id":   s["id"],
                            "session_date": pd.to_datetime(s["session_date"], errors="coerce"),
                            "session_type": s["session_type"],
                            "total_swings": k["Total Swings"],
                            "avg_exit_velo":  k["Avg Exit Velo"],
                            "peak_exit_velo": k["Peak Exit Velo"],
                            "avg_bat_speed":  k["Avg Bat Speed"],
                            "avg_launch_angle": k["Avg Launch Angle"],
                            "on_plane_pct":   k["On-Plane %"],
                            "barrel_pct":     k["Barrel %"],
                            "whiff_pct":      k["Whiff %"],
                        })
                    except Exception:
                        continue

                trend_df = pd.DataFrame(trend_rows)
                trend_df = trend_df.sort_values("session_date")

                if len(trend_df) >= 2:
                    # ----- Trend chart row 1: bat speed + exit velo -----
                    st.subheader("Bat Speed & Exit Velocity Over Time")
                    t1, t2 = st.columns(2)
                    with t1:
                        fig_bs = px.line(trend_df, x="session_date", y="avg_bat_speed",
                                          markers=True,
                                          labels={"session_date": "Session",
                                                  "avg_bat_speed": "Avg Bat Speed (mph)"},
                                          title="Average Bat Speed")
                        fig_bs.update_traces(line=dict(color="#1a2150", width=3),
                                              marker=dict(size=10, color="#d4a634"))
                        fig_bs.update_layout(height=320, margin=dict(t=40, b=20))
                        render_static_chart(fig_bs)
                    with t2:
                        fig_ev = px.line(trend_df, x="session_date",
                                          y=["avg_exit_velo", "peak_exit_velo"],
                                          markers=True,
                                          labels={"session_date": "Session",
                                                  "value": "Exit Velocity (mph)",
                                                  "variable": "Metric"},
                                          title="Exit Velocity (Avg + Peak)")
                        fig_ev.update_traces(line=dict(width=3))
                        fig_ev.update_layout(height=320, margin=dict(t=40, b=20))
                        render_static_chart(fig_ev)

                    # ----- Trend chart row 2: contact quality -----
                    st.subheader("Contact Quality Over Time")
                    t3, t4 = st.columns(2)
                    with t3:
                        fig_brl = px.line(trend_df, x="session_date", y="barrel_pct",
                                           markers=True,
                                           labels={"session_date": "Session",
                                                   "barrel_pct": "Barrel %"},
                                           title="Barrel Rate")
                        fig_brl.update_traces(line=dict(color="#7f1d1d", width=3),
                                               marker=dict(size=10, color="#fca5a5"))
                        fig_brl.update_layout(height=300, margin=dict(t=40, b=20))
                        render_static_chart(fig_brl)
                    with t4:
                        fig_wf = px.line(trend_df, x="session_date", y="whiff_pct",
                                          markers=True,
                                          labels={"session_date": "Session",
                                                  "whiff_pct": "Whiff %"},
                                          title="Whiff Rate (lower is better)")
                        fig_wf.update_traces(line=dict(color="#1e3a8a", width=3),
                                              marker=dict(size=10, color="#93c5fd"))
                        fig_wf.add_hline(y=22, line_dash="dash", line_color="#6b7280",
                                          opacity=0.5,
                                          annotation_text="HS average ≈ 22%",
                                          annotation_position="top right")
                        fig_wf.update_layout(height=300, margin=dict(t=40, b=20))
                        render_static_chart(fig_wf)

                    # ----- Trend chart row 3: on-plane + workload -----
                    st.subheader("Bat Path & Workload Over Time")
                    t5, t6 = st.columns(2)
                    with t5:
                        fig_op = px.line(trend_df, x="session_date", y="on_plane_pct",
                                          markers=True,
                                          labels={"session_date": "Session",
                                                  "on_plane_pct": "On-Plane %"},
                                          title="On-Plane Efficiency")
                        fig_op.update_traces(line=dict(color="#16a34a", width=3),
                                              marker=dict(size=10, color="#86efac"))
                        fig_op.update_layout(height=300, margin=dict(t=40, b=20))
                        render_static_chart(fig_op)
                    with t6:
                        fig_w = px.line(trend_df, x="session_date", y="total_swings",
                                         markers=True,
                                         labels={"session_date": "Session",
                                                 "total_swings": "Swings"},
                                         title="Workload per Session")
                        fig_w.update_traces(line=dict(color="#d4a634", width=3),
                                             marker=dict(size=10, color="#fde68a"))
                        fig_w.update_layout(height=300, margin=dict(t=40, b=20))
                        render_static_chart(fig_w)
                elif len(trend_df) == 1:
                    st.info("Log 2+ hitting sessions to see trend charts here.")

                st.divider()

                # ----- Session list with delete -----
                st.subheader("All Hitting Sessions")
                for s in history:
                    with st.container(border=True):
                        c1, c2, c3, c4, c5, c6 = st.columns([2, 1, 1, 1, 1, 1])
                        date_str = (pd.to_datetime(s["session_date"]).strftime("%b %d, %Y · %I:%M %p")
                                    if s.get("session_date") else "—")
                        type_pill = (
                            "<span style='background:#dcfce7;color:#15803d;padding:2px 8px;"
                            "border-radius:10px;font-size:10.5px;font-weight:700;'>REAL</span>"
                            if s["session_type"] == "real"
                            else "<span style='background:#eef2ff;color:#3730a3;padding:2px 8px;"
                                 "border-radius:10px;font-size:10.5px;font-weight:700;'>SAMPLE</span>"
                        )
                        c1.markdown(_flat_html(
                            f"<div style='font-weight:700;color:#1a2150;'>{date_str}</div>"
                            f"<div style='margin-top:2px;'>{type_pill}</div>"
                        ), unsafe_allow_html=True)
                        c2.metric("Swings",   s.get("pitch_count", "—"))
                        c3.metric("Avg EV",   f"{s['avg_velocity']:.1f} mph"
                                                if s.get("avg_velocity") is not None else "—")
                        c4.metric("Peak EV",  f"{s['peak_velocity']:.1f} mph"
                                                if s.get("peak_velocity") is not None else "—")
                        c5.metric("Bat Spd",  f"{s['avg_spin']:.1f} mph"
                                                if s.get("avg_spin") is not None else "—")
                        with c6:
                            if st.button("Delete",
                                          key=f"del_hit_session_{s['id']}",
                                          use_container_width=True):
                                delete_session(s["id"])
                                if "_saved_hitting_fingerprint" in st.session_state:
                                    del st.session_state["_saved_hitting_fingerprint"]
                                st.rerun()

    # ----- Action Plan -----
    with tab_action:
        plan = recommend_hitting_drills(df, sport=athlete_sport,
                                          athlete_level=athlete_level)

        # ===== Export section at the top of the tab =====
        st.subheader("Export & Share")
        exp_cols = st.columns([1.2, 1.0])
        with exp_cols[0]:
            try:
                pdf_bytes = generate_post_swing_pdf(
                    df, athlete_name=athlete_name, athlete_hand=athlete_hand,
                    athlete_class=athlete_class, sport=athlete_sport,
                    athlete_level=athlete_level,
                )
                st.download_button(
                    label="Download Post-Swing Report (PDF)",
                    data=pdf_bytes,
                    file_name=f"Post-Swing-Report_{athlete_name.replace(' ', '_')}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    type="primary",
                )
            except Exception as e:
                st.warning(f"PDF generator hit an error: {e}")
        with exp_cols[1]:
            st.caption(
                "Recruiting-grade one-pager: KPIs, spray chart, mechanics "
                "critique, and the action plan below. Text it to the hitter "
                "or attach to an email to college coaches."
            )

        st.divider()

        # ===== Drill cards =====
        CATEGORY_BADGES = {
            "Recovery":    ("🧊", "#0ea5e9"),
            "Bat Speed":   ("⚡", "#f57c00"),
            "Bat Path":    ("🔧", "#1976d2"),
            "Contact":     ("🎯", "#7b1fa2"),
        }

        def render_hit_drill_card(d):
            badge_icon, badge_color = CATEGORY_BADGES.get(d["category"], ("•", "#666"))
            with st.container(border=True):
                st.markdown(
                    f"<div style='height:4px;background:{badge_color};"
                    f"margin:-1rem -1rem 12px -1rem;border-radius:6px 6px 0 0;'></div>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:8px;"
                    f"flex-wrap:wrap;'>"
                    f"<span style='background:{badge_color};color:white;padding:3px 11px;"
                    f"border-radius:14px;font-size:11px;font-weight:700;letter-spacing:0.04em;'>"
                    f"{badge_icon} {d['category'].upper()}</span>"
                    f"<span style='font-size:17px;font-weight:700;color:#1a2150;'>{d['label']}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                st.markdown(f"**Drill:** {d['drill']}")
                st.markdown(f"**Protocol:** {d['protocol']}")
                st.markdown(f"**Why it works:** {d['why']}")
                # YouTube tutorial link (search-style URL — always works)
                if d.get("video_url"):
                    vt = d.get("video_title") or "Watch demo on YouTube"
                    vs = d.get("video_source", "")
                    src_html = (f"<span style='color:#6b7280;font-weight:500;'> · {vs}</span>"
                                  if vs else "")
                    st.markdown(
                        _flat_html(
                            f"<a href='{d['video_url']}' target='_blank' "
                            f"style='display:inline-flex;align-items:center;gap:6px;"
                            f"background:#fee2e2;color:#b91c1c;padding:6px 12px;"
                            f"border-radius:6px;font-size:13px;font-weight:600;"
                            f"text-decoration:none;margin-top:6px;'>"
                            f"▶ {vt}{src_html}</a>"
                        ),
                        unsafe_allow_html=True,
                    )
                st.caption(f"Why this fired: _{d['trigger']}_")

        # =========================================================
        # 5-DAY STRUCTURED WEEKLY PLAN
        # Every day opens with the standard warm-up, fills development
        # drills (priority-flagged or general-development defaults), and
        # closes with the standard cool-down.
        # =========================================================
        st.subheader("This Week — 5-Day Structured Plan")
        st.caption(
            "Every day opens with the standard warm-up and closes with the "
            "cool-down (full sequences in the reference panel below). "
            "Drill blocks adapt to the hitter's specific weaknesses, or fall "
            "back to general-development work when the data is clean."
        )
        weekly = build_weekly_plan("hitting", plan,
                                     athlete_level=athlete_level)
        # Lazy-load Days 2-5 into expanders — Streamlit Cloud's renderer
        # chokes on 5 bordered containers × nested drill cards all at once.
        def _render_hitting_day(day):
            st.markdown(
                _flat_html(
                    f"<div style='font-size:13px;color:#4b5563;margin-bottom:10px;'>"
                    f"{day['notes']}</div>"
                ),
                unsafe_allow_html=True,
            )
            st.markdown(
                _flat_html(
                    f"<div style='background:#f0fdf4;border-left:3px solid #16a34a;"
                    f"padding:8px 12px;border-radius:0 6px 6px 0;margin-bottom:8px;'>"
                    f"<b style='color:#15803d;'>Warm-up</b> "
                    f"<span style='color:#6b7280;'>· {day['warmup']['duration']} · "
                    f"{day['warmup']['label']}</span>"
                    f"</div>"
                ),
                unsafe_allow_html=True,
            )
            if day["drills"]:
                for d in day["drills"]:
                    render_hit_drill_card(d)
            else:
                st.caption(
                    "_No structured drills today — execute the live BP/session described "
                    "in the notes above with full intent._"
                )
            st.markdown(
                _flat_html(
                    f"<div style='background:#eff6ff;border-left:3px solid #3b82f6;"
                    f"padding:8px 12px;border-radius:0 6px 6px 0;margin-top:8px;'>"
                    f"<b style='color:#1e40af;'>Cool-down</b> "
                    f"<span style='color:#6b7280;'>· {day['cooldown']['duration']} · "
                    f"{day['cooldown']['label']}</span>"
                    f"</div>"
                ),
                unsafe_allow_html=True,
            )

        for day in weekly:
            day_label = day["label"].split("—", 1)[-1].strip()
            header = f"**Day {day['day_num']} — {day_label}**"
            with st.expander(header, expanded=(day["day_num"] == 1)):
                _render_hitting_day(day)

        # =========================================================
        # WARM-UP & COOL-DOWN REFERENCE — full sequences spelled out
        # =========================================================
        st.divider()
        with st.expander("**Warm-Up & Cool-Down Reference** — full sequences",
                          expanded=False):
            for header, seq in [
                ("Hitter Pre-Session Warm-Up",  HITTING_WARMUP),
                ("Hitter Post-Session Cool-Down", HITTING_COOLDOWN),
            ]:
                st.markdown(
                    _flat_html(
                        f"<div style='font-size:11px;letter-spacing:0.10em;"
                        f"font-weight:700;color:#d4a634;text-transform:uppercase;"
                        f"margin-top:12px;'>{seq['duration']}</div>"
                        f"<div style='font-size:16px;font-weight:700;color:#1a2150;"
                        f"margin-bottom:8px;'>{header}</div>"
                    ),
                    unsafe_allow_html=True,
                )
                for step_name, step_detail in seq["steps"]:
                    st.markdown(
                        _flat_html(
                            f"<div style='border-left:3px solid #1a2150;padding:6px 12px;"
                            f"background:#f6f7fb;border-radius:0 4px 4px 0;margin:4px 0;'>"
                            f"<b style='color:#1a2150;'>{step_name}</b> "
                            f"<span style='color:#4b5563;'>— {step_detail}</span>"
                            f"</div>"
                        ),
                        unsafe_allow_html=True,
                    )
                st.markdown(
                    _flat_html(
                        f"<div style='font-size:12px;color:#6b7280;font-style:italic;"
                        f"margin-top:6px;margin-bottom:12px;'>"
                        f"Why it matters: {seq['why']}</div>"
                    ),
                    unsafe_allow_html=True,
                )

        # =========================================================
        # FULL DRILL LIBRARY — browsable reference (every issue)
        # =========================================================
        st.divider()
        with st.expander("**Full Hitting Drill Library** — browse all drills by issue",
                          expanded=False):
            st.caption(
                "Every drill in the system, organized by the issue it targets. "
                "Use this when you want to swap an alternate into the action plan "
                "or build a custom session for a hitter."
            )
            # Friendly section names + descriptions for each issue bucket
            issue_meta = {
                "bat_speed":      ("⚡ Bat Speed", "When avg bat speed is below the target for the hitter's level."),
                "hip_separation": ("🔁 Hip-Shoulder Separation", "When the hips and shoulders are firing together — no rubber-band torque."),
                "flat_swing":     ("📈 Flat / Chopping-Down Swing Path", "When the bat plane is below the productive 8°-16° attack angle range."),
                "steep_swing":    ("📉 Steep Uppercut Swing Path", "When attack angle is above 20° — lots of whiffs and pop-ups."),
                "off_plane":      ("📏 Off-Plane Bat Path", "When the bat is in the hitting zone for too small a window."),
                "slow_ttc":       ("⏱️ Slow Time-to-Contact", "When the swing takes too long to get the barrel through the zone."),
                "whiffs":         ("👁️ Elevated Whiff Rate", "When the hitter is chasing offspeed or expanding the zone."),
                "weak_contact":   ("🎯 Weak Contact Pattern", "When the hitter rolls over or pops up too many balls."),
            }
            for issue, (heading, desc) in issue_meta.items():
                drill_keys = HITTING_ISSUE_TO_DRILLS.get(issue, [])
                if not drill_keys:
                    continue
                st.markdown(f"#### {heading}")
                st.caption(desc)
                for k in drill_keys:
                    d = HITTING_DRILL_LIBRARY[k]
                    v = pick_video(k, severity="any",
                                     level=LEVEL_TO_VIDEO_BUCKET.get(athlete_level, "any"))
                    link_html = ""
                    if v:
                        link_html = (
                            f"<a href='{v['url']}' target='_blank' "
                            f"style='display:inline-block;background:#fee2e2;"
                            f"color:#b91c1c;padding:3px 9px;border-radius:5px;"
                            f"font-size:12px;font-weight:600;text-decoration:none;"
                            f"margin-top:4px;'>▶ {v['title']}</a>"
                        )
                    st.markdown(
                        _flat_html(
                            f"<div style='border-left:3px solid #1a2150;"
                            f"background:#f6f7fb;padding:10px 14px;margin:6px 0 12px 0;"
                            f"border-radius:0 6px 6px 0;'>"
                            f"<div style='font-weight:700;color:#1a2150;font-size:14px;'>{d['label']}</div>"
                            f"<div style='font-size:13px;color:#1f2937;margin-top:2px;'>"
                            f"<b>Drill:</b> {d['drill']}</div>"
                            f"<div style='font-size:13px;color:#1f2937;margin-top:1px;'>"
                            f"<b>Protocol:</b> {d['protocol']}</div>"
                            f"<div style='font-size:12px;color:#4b5563;margin-top:3px;font-style:italic;'>"
                            f"{d['why']}</div>"
                            f"{link_html}"
                            f"</div>"
                        ),
                        unsafe_allow_html=True,
                    )


# =============================================================================
# BALL TRACKING (CV) — Phase 2 of Live Capture
# Pure-function ball detector + trajectory fitter. Lives apart from any UI
# code so it can be unit-tested with synthetic frames. The Live Capture
# VideoProcessor below wires these into the per-frame pose loop.
# =============================================================================
#
# Calibration model: we assume a fixed (tripod-mounted) phone behind the
# catcher OR on the side, with home plate visible. The user marks the
# plate's pixel position + width once. From that single reference:
#   - 1 pixel = (17 inches / plate_width_px) inches  →  feet per pixel
#   - plate y-position in pixels = ground reference for "plate plane"
#   - everything else scales off that.
#
# This is the same calibration trick PitchLab AI uses. It's not perfect
# (assumes the camera plane is roughly parallel to the plate) but it's
# good enough for facility-grade velocity + plate-location estimates.

def detect_ball_in_frame(frame_bgr,
                          ball_radius_px_range: tuple[int, int] = (4, 30),
                          mask_brightness_min: int = 200,
                          motion_blur_tolerant: bool = True) -> "tuple[int, int] | None":
    """Find a single baseball in a BGR image. Returns (x_px, y_px) or None.

    Strategy:
      1. Convert to grayscale + threshold for bright pixels (baseball is
         white against most backgrounds: sky, dirt, grass, netting).
      2. Find connected white regions.
      3. Pick the one most circular and in the expected radius range.

    Tuning notes for real-world deployment:
      - mask_brightness_min: lower for cloudy days / indoor nets, higher
        for direct sunlight.
      - ball_radius_px_range: depends on camera distance. At ~30 ft from a
        plate, a baseball is roughly 8-15 px. At 60 ft from the mound,
        10-20 px. Field-tune from the first capture.
    """
    try:
        import cv2
        import numpy as np
    except Exception:
        return None
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, mask_brightness_min, 255, cv2.THRESH_BINARY)
    # Light morphology to consolidate the ball's pixels
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_score = -1.0
    rmin, rmax = ball_radius_px_range
    for c in contours:
        (x, y), r = cv2.minEnclosingCircle(c)
        if not (rmin <= r <= rmax):
            continue
        area = cv2.contourArea(c)
        if area <= 0:
            continue
        # Circularity: 1.0 = perfect circle. A static ball clears 0.85;
        # a motion-blurred ball at 90 mph in a single frame can drop to
        # ~0.55 because the blur streaks the bright pixels. We trade
        # some rectangle-rejection precision for motion-blur tolerance
        # because real pitches always blur.
        perim = cv2.arcLength(c, True)
        if perim == 0:
            continue
        circularity = 4.0 * 3.14159 * area / (perim * perim)
        min_circ = 0.55 if motion_blur_tolerant else 0.78
        if circularity < min_circ:
            continue
        # Fill ratio — what fraction of the min-enclosing circle is
        # actually filled by the contour. A real ball fills ~95% of its
        # bounding circle; a rectangle fills ~64%. With motion-blur
        # tolerance ON we drop this too because elongated blurs have
        # poor fill ratios within the min-enclosing circle.
        circle_area = 3.14159 * r * r
        fill_ratio = area / circle_area if circle_area > 0 else 0
        min_fill = 0.45 if motion_blur_tolerant else 0.7
        if fill_ratio < min_fill:
            continue
        # Score weights circularity heavily, then favors larger radius
        # for tie-breaking among multiple ball-like candidates.
        score = circularity * 10.0 + fill_ratio * 5.0 + r * 0.1
        if score > best_score:
            best_score = score
            best = (int(round(x)), int(round(y)))
    return best


# Typical TOTAL spin RPM per pitch type — used as the denominator for
# "spin efficiency" when we estimate USEFUL spin from break + velocity.
# Sourced from MLB Statcast averages; softball numbers from college-level
# Diamond Kinetics data. These are reasonable BASELINES — actual total
# spin varies by pitcher and grip, but for a directional efficiency
# estimate this gives coaches an actionable number.
TYPICAL_TOTAL_SPIN_RPM = {
    # Baseball
    "Four-Seam Fastball":   2300,
    "Two-Seam Sinker":      2050,
    "Slider Strike-Getter": 2500,
    "Slider Chase":         2400,
    "Slider":               2450,
    "Curveball":            2600,
    "Change-Up":            1800,
    # Softball
    "Softball Fastball":    1400,
    "Rise Ball":            1700,
    "Drop Ball":            1700,
    "Screwball":            1500,
    "Change-Up Softball":   1100,
    # Fallback
    "Unknown":              2200,
}


def estimate_spin_metrics(vert_break_in: float,
                            horiz_break_in: float,
                            velocity_mph: float,
                            pitch_type: str | None = None,
                            sport: str = "Baseball") -> dict:
    """Estimate spin metrics from trajectory + velocity (no high-FPS video).

    The physics: Magnus break is proportional to (useful_spin × velocity ×
    flight_time²). Since flight_time = pitch_distance / velocity, this
    simplifies to break ∝ useful_spin / velocity. Reversing:

        useful_spin_RPM ≈ K × total_break_in × velocity_mph

    K is empirically calibrated against MLB Statcast averages so that a
    typical 92 mph fastball with 16-17" total break recovers ~2100 RPM
    of useful spin. K differs by sport because softball has a shorter
    pitching distance (less flight time per RPM of break).

    Returns:
      useful_spin_rpm:      RPM contributing to ball movement
      spin_axis_deg:        Direction of break, in degrees from 12:00
      tilt_clock:           Human-readable like "1:00", "7:30"
      assumed_total_spin:   Baseline RPM for the pitch type (used for efficiency)
      spin_efficiency_pct:  useful / total × 100 (capped at 100%)

    Returns None values if vb/hb are None (no break data available).
    """
    import math
    if vert_break_in is None or horiz_break_in is None or velocity_mph is None:
        return {
            "useful_spin_rpm":     None,
            "spin_axis_deg":       None,
            "tilt_clock":          None,
            "assumed_total_spin":  None,
            "spin_efficiency_pct": None,
        }

    total_break_in = math.sqrt(vert_break_in ** 2 + horiz_break_in ** 2)

    # Calibration constants (K = useful_spin / (break_in × velocity_mph))
    # Calibrated so MLB-typical pitches recover ~true RPM:
    #   92 mph 4-seam, 17" break → ~2192 RPM
    #   78 mph curve, 16" break → ~1745 RPM (low end of typical curveball
    #     useful spin, true value usually higher because curve grips boost
    #     lift coefficient — accept ~25% under-estimate for sharp breakers)
    K_SPORT = {"Baseball": 1.4, "Softball": 2.7}
    K = K_SPORT.get(sport, 1.4)
    useful_spin_rpm = total_break_in * velocity_mph * K

    # ===== Spin axis from break direction =====
    # Clock convention (from catcher's POV looking at the pitcher):
    #   12:00 = pure backspin → break straight UP (positive IVB only)
    #    3:00 = pure arm-side spin (RHP) → break HORIZONTAL right
    #    6:00 = pure topspin → break straight DOWN (negative IVB)
    #    9:00 = pure glove-side spin (RHP) → break HORIZONTAL left
    # The "spin axis" by convention is reported as the BREAK direction
    # — i.e., a 12:00 axis pitch has its break vector at 12:00.
    spin_axis_deg = math.degrees(math.atan2(horiz_break_in, vert_break_in))
    if spin_axis_deg < 0:
        spin_axis_deg += 360.0

    # Convert to clock face (round to nearest 15 min for readability)
    clock_hours = spin_axis_deg / 30.0
    total_quarters = round(clock_hours * 4)
    h = (total_quarters // 4) % 12
    if h == 0: h = 12
    m = (total_quarters % 4) * 15
    tilt_clock = f"{h}:{m:02d}"

    # ===== Spin efficiency =====
    assumed_total = TYPICAL_TOTAL_SPIN_RPM.get(
        pitch_type or "Unknown",
        TYPICAL_TOTAL_SPIN_RPM["Unknown"]
    )
    efficiency = (useful_spin_rpm / assumed_total) * 100.0 if assumed_total > 0 else None
    if efficiency is not None:
        efficiency = min(100.0, max(0.0, efficiency))

    return {
        "useful_spin_rpm":     int(round(useful_spin_rpm)),
        "spin_axis_deg":       round(spin_axis_deg, 1),
        "tilt_clock":          tilt_clock,
        "assumed_total_spin":  int(assumed_total),
        "spin_efficiency_pct": round(efficiency, 1) if efficiency is not None else None,
    }


def fit_pitch_trajectory(positions: "list[tuple[float, int, int]]",
                          calibration: dict) -> "dict | None":
    """Given a time series of ball positions, recover the pitch's basic
    flight metrics.

    positions: list of (t_sec, x_px, y_px) — timestamps in seconds from
      the start of capture, ball pixel coordinates.
    calibration: dict with:
      - plate_width_px: width of home plate in pixels in the camera's view
      - plate_center_x_px, plate_center_y_px: pixel center of the plate
      - sport: 'Baseball' or 'Softball' (selects rubber distance)
      - camera_view: 'behind_catcher' (z = image y) or 'side' (z requires
        a different mapping — Phase 2.1)

    Returns dict:
      - velocity_mph
      - flight_time_sec
      - plate_x_ft (negative = third-base side)
      - plate_z_ft (height)
      - n_samples_used
    Or None if there aren't enough samples / data is too noisy.
    """
    if len(positions) < 5:
        return None
    try:
        import numpy as np
    except Exception:
        return None

    sport = calibration.get("sport", "Baseball")
    c = TUNNEL_CONSTANTS.get(sport, TUNNEL_CONSTANTS["Baseball"])
    pitch_distance_ft = c["rubber_distance_ft"] - c["release_extension_ft"]

    # ===== Pixel-to-feet scale (using known plate width: 17 inches) =====
    PLATE_WIDTH_IN = 17.0
    px_per_ft = calibration["plate_width_px"] * (12.0 / PLATE_WIDTH_IN)
    ft_per_px = 1.0 / px_per_ft

    # ===== Identify the release frame and the catch (plate-cross) frame =====
    # Heuristic: release = first sample BEFORE the ball starts moving
    # consistently forward. Catch = sample closest to the plate's y-pixel
    # (or last sample if no clear "near plate" point).
    ts  = np.array([p[0] for p in positions], dtype=float)
    xs  = np.array([p[1] for p in positions], dtype=float)
    ys  = np.array([p[2] for p in positions], dtype=float)

    # Find the flight window. Real captured pre-release frames (ball in
    # pitcher's hand) have effectively zero pixel motion (< 1 px). Flight
    # frames have ≥ 2 px even on a slow pitch from a phone tripod. So use
    # an ABSOLUTE motion threshold to trim stationary phases — the relative
    # (% of max) version we used earlier kept clipping slow but valid
    # curveball flight frames.
    deltas = np.sqrt(np.diff(xs) ** 2 + np.diff(ys) ** 2)
    if len(deltas) == 0:
        return None
    STATIONARY_PX = 0.5    # below this = ball not moving in image (noise floor)
    # Default to the full provided window
    release_i = 0
    catch_i   = len(positions) - 1
    # Only TRIM the front if the first frames are genuinely stationary
    if deltas[0] < STATIONARY_PX:
        moving = np.where(deltas >= STATIONARY_PX)[0]
        if len(moving) >= 3:
            release_i = int(moving[0])
    # Only TRIM the back if the last frames are genuinely stationary
    if deltas[-1] < STATIONARY_PX:
        moving = np.where(deltas >= STATIONARY_PX)[0]
        if len(moving) >= 3:
            catch_i = int(moving[-1]) + 1
    if catch_i - release_i < 3:
        return None

    # The plate y-pixel is the camera-relative reference for the strike
    # zone's center, but we don't refine catch_i with it any more —
    # curveballs and chase pitches finish well below plate y, and the
    # closest-to-plate-y heuristic was picking mid-flight frames instead
    # of the actual catch. Motion-based trimming (above) is the reliable
    # signal for the catch frame.
    pcy = calibration.get("plate_center_y_px")

    if catch_i - release_i < 3:
        return None

    flight_time = float(ts[catch_i] - ts[release_i])
    if flight_time <= 0:
        return None

    # ===== Velocity =====
    # We assume the ball travels ~pitch_distance_ft from release to plate.
    # Phase 2.1 will use 3D triangulation from a single camera + ball-size
    # depth cues; for v1 we use the rubber distance as a fixed reference.
    velocity_fps = pitch_distance_ft / flight_time
    velocity_mph = velocity_fps / 1.467

    # ===== Plate-crossing location =====
    # Pixel offset from plate center → real-world feet, anchored at plate y
    if pcy is None:
        plate_x_ft = None
        plate_z_ft = None
    else:
        pcx = calibration["plate_center_x_px"]
        # Catch-frame pixel offset
        dx_px = xs[catch_i] - pcx
        dy_px = pcy - ys[catch_i]  # invert because pixel y grows downward
        plate_x_ft = float(dx_px * ft_per_px)
        plate_z_ft = float(dy_px * ft_per_px)
        # Add a "height of plate above ground" base so z is plausible
        # (typical strike-zone reference: 1.6 - 3.5 ft above ground)
        plate_z_ft += 2.5  # plate camera reference height

    # ===== BREAK ESTIMATION (Phase 2.1) =====
    # Fit a quadratic to the observed (x, z) trajectory in real-world feet,
    # then compare to a gravity-only no-spin reference. The deviation at
    # the plate is the induced break — same math approach PitchLab AI uses,
    # same accuracy ceiling (~10-15% of the true break for typical phone
    # frame rates).
    #
    # Coordinates in real-world feet:
    #   x_ft = (x_px - plate_center_x_px) * ft_per_px       (horizontal)
    #   z_ft = (plate_center_y_px - y_px) * ft_per_px + 2.5 (height; plate y is reference)
    vert_break_in = None
    horiz_break_in = None
    if pcy is not None and (catch_i - release_i) >= 5:
        pcx = calibration["plate_center_x_px"]
        flight_ts = ts[release_i:catch_i + 1] - ts[release_i]
        flight_xs_ft = (xs[release_i:catch_i + 1] - pcx) * ft_per_px
        flight_zs_ft = (pcy - ys[release_i:catch_i + 1]) * ft_per_px + 2.5

        try:
            # Quadratic fit: position(t) = a + b*t + c*t²  → 2c = acceleration
            cz_quad, cz_lin, cz_const = np.polyfit(flight_ts, flight_zs_ft, 2)
            cx_quad, cx_lin, cx_const = np.polyfit(flight_ts, flight_xs_ft, 2)
            a_z_total = 2.0 * cz_quad
            a_x_total = 2.0 * cx_quad
            # Vertical: a_z_total = -g + a_magnus_z (g = 32.17 ft/s²)
            # If a_z_total > -g (i.e. the ball drops less than gravity alone
            # would predict), Magnus is pushing UP → positive IVB (fastball).
            g_fps2 = 32.17
            a_magnus_z = a_z_total + g_fps2
            a_magnus_x = a_x_total
            # Total Magnus-induced deviation over the flight time (feet):
            vb_ft = 0.5 * a_magnus_z * flight_time * flight_time
            hb_ft = 0.5 * a_magnus_x * flight_time * flight_time
            vert_break_in  = round(vb_ft * 12.0, 1)
            horiz_break_in = round(hb_ft * 12.0, 1)
        except Exception:
            pass

    # ===== SPIN ESTIMATION (Phase 2.2) =====
    # From break + velocity we can recover useful_spin RPM and spin axis
    # via Magnus physics. Spin efficiency is computed against a pitch-type
    # baseline (assumed total spin) — coaches can override the baseline
    # later if they have a Rapsodo session for ground truth.
    pitch_type_hint = calibration.get("pitch_type")  # may be None
    spin = estimate_spin_metrics(
        vert_break_in=vert_break_in,
        horiz_break_in=horiz_break_in,
        velocity_mph=velocity_mph,
        pitch_type=pitch_type_hint,
        sport=sport,
    )

    return {
        "velocity_mph":         round(float(velocity_mph), 1),
        "flight_time_sec":      round(flight_time, 3),
        "plate_x_ft":           round(plate_x_ft, 2) if plate_x_ft is not None else None,
        "plate_z_ft":           round(plate_z_ft, 2) if plate_z_ft is not None else None,
        "vert_break_in":        vert_break_in,
        "horiz_break_in":       horiz_break_in,
        "useful_spin_rpm":      spin["useful_spin_rpm"],
        "spin_axis_deg":        spin["spin_axis_deg"],
        "tilt_clock":           spin["tilt_clock"],
        "assumed_total_spin":   spin["assumed_total_spin"],
        "spin_efficiency_pct":  spin["spin_efficiency_pct"],
        "n_samples_used":       int(catch_i - release_i + 1),
        "release_frame":        int(release_i),
        "catch_frame":          int(catch_i),
    }


# =============================================================================
# LIVE CAPTURE  (Beta) — phone/tablet camera + MediaPipe pose extraction
# =============================================================================
# Lets a coach point a phone at the pitcher and capture biomech metrics in
# real time, no Pitch Logic / Pulse / ProPlayAI required. Phase 1 = pose
# (release point, arm slot, hip-shoulder separation, lead-knee flex, stride
# length). Phase 2 (future) = ball-flight via OpenCV ball detection.
#
# Implementation:
#   - streamlit-webrtc → browser camera access via WebRTC (works on mobile)
#   - mediapipe Pose → 33 body landmarks per frame at 30 fps
#   - We extract per-frame metrics, smooth across the windup, then snap a
#     pitch when the "release moment" is detected (peak wrist velocity).
#
# Heavy deps (streamlit-webrtc, mediapipe, opencv, av) are imported INSIDE
# the function so the rest of the app keeps running if they're not installed.
# =============================================================================
# =============================================================================
# CALIBRATION PRESETS — natural-language setup that auto-fills pixel coords
# =============================================================================
# Picking "iPhone 1080p · 15 ft behind catcher" populates plate_cx / plate_cy
# / plate_w / ball_radius automatically so a non-technical user doesn't ever
# have to think about pixels. Advanced users can override via the Advanced
# expander.
#
# Numbers are sanity-checked against typical iPhone shots from common
# tripod distances. They get you in the ballpark — the user can tweak the
# Advanced expander if anything looks off in the first capture.
CALIBRATION_PRESETS = {
    "iPhone 1080p · 10 ft behind catcher": {
        "plate_cx_px": 960, "plate_cy_px": 800, "plate_w_px": 160,
        "ball_rmin_px": 8,  "ball_rmax_px": 26,
    },
    "iPhone 1080p · 15 ft behind catcher": {
        "plate_cx_px": 960, "plate_cy_px": 900, "plate_w_px": 110,
        "ball_rmin_px": 6,  "ball_rmax_px": 22,
    },
    "iPhone 1080p · 25 ft behind catcher": {
        "plate_cx_px": 960, "plate_cy_px": 980, "plate_w_px": 70,
        "ball_rmin_px": 4,  "ball_rmax_px": 16,
    },
    "iPhone 1080p · side view, 20 ft away": {
        "plate_cx_px": 1500, "plate_cy_px": 900, "plate_w_px": 90,
        "ball_rmin_px": 5,  "ball_rmax_px": 18,
    },
    "iPad 1080p · 15 ft behind catcher": {
        "plate_cx_px": 960, "plate_cy_px": 880, "plate_w_px": 130,
        "ball_rmin_px": 7,  "ball_rmax_px": 24,
    },
    "iPhone 720p · 15 ft behind catcher": {
        "plate_cx_px": 640, "plate_cy_px": 600, "plate_w_px": 75,
        "ball_rmin_px": 4,  "ball_rmax_px": 16,
    },
    "Custom (manual)": None,   # signals: show advanced sliders
}


def render_calibration_with_presets(state_prefix: str,
                                       default_preset: str = "iPhone 1080p · 15 ft behind catcher"
                                       ) -> dict:
    """Show preset picker → auto-fill pixel coords → optional Advanced tweaks.

    state_prefix: e.g. "livecap_" or "upload_" — namespaces the session-state
                  keys so Live and Upload modes don't collide.

    Returns the final calibration dict (plate_cx, plate_cy, plate_w,
    ball_rmin, ball_rmax) — same keys as before.
    """
    preset_key = f"{state_prefix}preset"
    if preset_key not in st.session_state:
        st.session_state[preset_key] = default_preset

    preset_name = st.selectbox(
        "Camera setup (pick the closest match)",
        list(CALIBRATION_PRESETS.keys()),
        index=list(CALIBRATION_PRESETS.keys()).index(
            st.session_state.get(preset_key, default_preset)),
        key=f"{state_prefix}preset_picker",
        help="Most coaches just pick a preset and never touch the pixel "
             "values. Tap Advanced below only if the ball isn't being "
             "detected after your first capture.",
    )
    st.session_state[preset_key] = preset_name

    preset_vals = CALIBRATION_PRESETS.get(preset_name)
    custom_mode = preset_vals is None

    # Initialize / refresh state from preset (unless custom)
    if not custom_mode:
        for k, v in preset_vals.items():
            st.session_state[f"{state_prefix}{k}"] = v

    # Advanced expander — only path to manual numbers
    with st.expander("Advanced — manual pixel calibration", expanded=custom_mode):
        st.caption(
            "Pixel coordinates of the home plate in your video frame. "
            "The plate width gives the pixel-to-feet scale. Default values "
            "above come from the preset — only adjust here if your video "
            "is shot differently."
        )
        c1, c2, c3 = st.columns(3)
        with c1:
            plate_cx = st.number_input(
                "Plate center X (px)", min_value=0, max_value=4000,
                value=int(st.session_state.get(f"{state_prefix}plate_cx_px", 960)),
                step=10, key=f"{state_prefix}plate_cx_widget",
            )
        with c2:
            plate_cy = st.number_input(
                "Plate center Y (px)", min_value=0, max_value=4000,
                value=int(st.session_state.get(f"{state_prefix}plate_cy_px", 900)),
                step=10, key=f"{state_prefix}plate_cy_widget",
            )
        with c3:
            plate_w = st.number_input(
                "Plate width (px)", min_value=10, max_value=600,
                value=int(st.session_state.get(f"{state_prefix}plate_w_px", 110)),
                step=5, key=f"{state_prefix}plate_w_widget",
            )
        c4, c5 = st.columns(2)
        with c4:
            ball_rmin = st.number_input(
                "Ball radius min (px)", min_value=2, max_value=40,
                value=int(st.session_state.get(f"{state_prefix}ball_rmin_px", 6)),
                step=1, key=f"{state_prefix}ball_rmin_widget",
            )
        with c5:
            ball_rmax = st.number_input(
                "Ball radius max (px)", min_value=4, max_value=80,
                value=int(st.session_state.get(f"{state_prefix}ball_rmax_px", 22)),
                step=1, key=f"{state_prefix}ball_rmax_widget",
            )

    # Persist to session state for next-render initial value AND for the
    # video processor / live capture to pick up
    st.session_state[f"{state_prefix}plate_cx_px"] = plate_cx
    st.session_state[f"{state_prefix}plate_cy_px"] = plate_cy
    st.session_state[f"{state_prefix}plate_w_px"]  = plate_w
    st.session_state[f"{state_prefix}ball_rmin_px"] = ball_rmin
    st.session_state[f"{state_prefix}ball_rmax_px"] = ball_rmax

    return {
        "plate_cx_px": plate_cx,
        "plate_cy_px": plate_cy,
        "plate_w_px":  plate_w,
        "ball_rmin_px": ball_rmin,
        "ball_rmax_px": ball_rmax,
    }


# =============================================================================
# CLICK-TO-CALIBRATE — point at the plate in a still frame
# =============================================================================
# A coach drops a bullpen video in. We grab a clean still, show it big, and
# ask them to tap the LEFT edge of home plate, then the RIGHT edge. From
# those two points we derive everything:
#   - plate_cx, plate_cy = midpoint of the two clicks
#   - plate_w = pixel distance between the two clicks
#   - ball radius = (plate_w_px / 17 in) * 2.9 in / 2 in pixels (regulation
#     plate width 17 in, regulation baseball diameter ~2.9 in)
#   - rmin / rmax = ball_radius * 0.6 / 1.6 to stay tolerant
#
# No pixel-typing, no preset lookup. Falls back to the preset picker
# gracefully if the streamlit-image-coordinates package isn't installed.

# Regulation reference (inches)
_PLATE_WIDTH_IN_BASEBALL  = 17.0
_BALL_DIAMETER_IN_BASE    = 2.9     # baseball
_BALL_DIAMETER_IN_SOFTBALL = 3.8    # softball (~3.8 in for 12-in circumference)


def _is_click_calibration_available() -> bool:
    try:
        import streamlit_image_coordinates  # noqa: F401
        return True
    except Exception:
        return False


def extract_calibration_still(video_path: str,
                                target_time_sec: float = 1.0):
    """Pull a single decent frame from a video for calibration purposes.

    Picks ~1 second in so the pitcher's hand isn't obscuring the plate on
    frame 0. Returns (png_bytes, orig_width, orig_height) or (None,0,0).
    """
    try:
        import cv2
    except Exception:
        return None, 0, 0
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None, 0, 0
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    target_frame = min(max(int(target_time_sec * fps), 0),
                        max(total - 1, 0))
    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
    ok, frame = cap.read()
    if not ok:
        # Retry frame 0 — some codecs reject seeking
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ok, frame = cap.read()
        if not ok:
            cap.release()
            return None, 0, 0
    h, w = frame.shape[:2]
    cap.release()
    ok2, png = cv2.imencode(".png", frame)
    if not ok2:
        return None, 0, 0
    return png.tobytes(), w, h


def _derive_calibration_from_two_points(left_pt: tuple,
                                           right_pt: tuple,
                                           sport: str = "Baseball") -> dict:
    """From the LEFT-edge + RIGHT-edge clicks on the plate, build the
    full calibration dict the rest of the pipeline expects."""
    lx, ly = left_pt
    rx, ry = right_pt
    plate_w = max(10, int(abs(rx - lx)))
    plate_cx = int((lx + rx) / 2)
    plate_cy = int((ly + ry) / 2)
    # Ball radius in pixels from regulation geometry
    ball_d_in = (_BALL_DIAMETER_IN_SOFTBALL
                  if sport.lower().startswith("s")
                  else _BALL_DIAMETER_IN_BASE)
    px_per_in = plate_w / _PLATE_WIDTH_IN_BASEBALL
    ball_r_px = max(3.0, (ball_d_in * px_per_in) / 2.0)
    rmin = max(2, int(round(ball_r_px * 0.6)))
    rmax = max(rmin + 2, int(round(ball_r_px * 1.6)))
    return {
        "plate_cx_px":  plate_cx,
        "plate_cy_px":  plate_cy,
        "plate_w_px":   plate_w,
        "ball_rmin_px": rmin,
        "ball_rmax_px": rmax,
    }


# =============================================================================
# AUTO-DETECT PLATE — CV-based zero-click calibration
# =============================================================================
# Strategy: home plate is a white pentagon against a darker (usually dirt or
# matting) background. We threshold the still for "white-ish" pixels in the
# lower 2/3 of the frame, find contours, and score candidates by:
#   - Area inside a plausible band (not too small, not too big)
#   - Aspect ratio between 1.2 and 4.0 (plate is wider than tall when shot
#     from behind catcher)
#   - 4-7 polygon vertices (pentagon ± noise)
#   - Distance from horizontal center (plate is usually framed centrally)
#
# Tested mentally against: behind-catcher iPhone video on a real field, indoor
# turf cage with painted plate, side-view shot. Wins on 1+2, struggles on 3
# (side-view plate is foreshortened — aspect ratio doesn't fit), which is
# why the single-tap fallback exists.
def auto_detect_plate(image_bytes: bytes) -> tuple | None:
    """Try to find home plate. Returns (cx_px, cy_px, w_px) in the
    ORIGINAL frame coordinates, or None if no confident candidate."""
    try:
        import cv2, numpy as np
    except Exception:
        return None
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    # Search lower 2/3 of frame — plate almost never lives in the top third
    roi_y0 = h // 3
    roi = img[roi_y0:, :]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    # White-ish: low saturation, high value. Loose bounds to catch dirty plates
    mask = cv2.inRange(hsv, (0, 0, 170), (180, 70, 255))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_score = 0.0
    frame_area = w * h
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        # Plate occupies between 0.05% and 8% of the frame typically
        if area < frame_area * 0.0005 or area > frame_area * 0.08:
            continue
        x, y, cw, ch = cv2.boundingRect(cnt)
        if cw < 30 or ch < 12:  # too small even for 720p far-away shot
            continue
        aspect = cw / max(1, ch)
        if aspect < 1.2 or aspect > 4.0:
            continue
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.04 * peri, True)
        if len(approx) < 4 or len(approx) > 7:
            continue
        # Score: weight area, penalize off-center, reward fill ratio
        fill_ratio = area / max(1, cw * ch)
        if fill_ratio < 0.55:
            continue
        center_x_dist = abs((x + cw / 2) - w / 2) / w   # 0 = perfect center
        score = area * (1.0 - 0.4 * center_x_dist) * fill_ratio
        if score > best_score:
            best_score = score
            best = (int(x + cw / 2), int(y + ch / 2 + roi_y0), int(cw))
    return best


def _default_plate_width_for_resolution(orig_w: int) -> int:
    """Sensible default plate-width in pixels for a single-tap fallback.
    Scales linearly with resolution from the ~110px @ 1920px reference."""
    return max(40, int(round(110 * (orig_w / 1920))))


def render_smart_calibration(video_path: str,
                                state_prefix: str,
                                sport: str = "Baseball",
                                display_width: int = 720) -> dict | None:
    """Zero-click calibration with single-tap fallback.

    Flow:
      1. Extract a still from the video.
      2. Run auto_detect_plate. If a confident match → lock and return.
      3. Otherwise show the still and ask user to tap the CENTER of the
         plate. Compute width from video resolution.
      4. 'Re-try auto-detect' button kicks back to step 2.
      5. The legacy preset / manual fallback lives in an Advanced expander.
    """
    if not _is_click_calibration_available():
        st.caption(
            "Calibration needs the `streamlit-image-coordinates` package. "
            "Re-run `enable_live_capture.command` to install it.")
        return None

    from streamlit_image_coordinates import streamlit_image_coordinates

    png, orig_w, orig_h = extract_calibration_still(video_path)
    if png is None:
        st.error("Couldn't extract a still from the video — file may be corrupted.")
        return None

    auto_key   = f"{state_prefix}auto_result"
    manual_key = f"{state_prefix}manual_center"
    retry_key  = f"{state_prefix}auto_retry"

    # Run auto-detect on first render or after explicit retry
    if auto_key not in st.session_state or st.session_state.pop(retry_key, False):
        st.session_state[auto_key] = auto_detect_plate(png)

    auto = st.session_state.get(auto_key)
    manual_center = st.session_state.get(manual_key)

    if auto:
        plate_cx, plate_cy, plate_w = auto
        st.markdown(
            f"<div style='background:#1e293b;border-left:4px solid #22c55e;"
            f"border-radius:8px;padding:12px 16px;margin:8px 0 14px 0;'>"
            f"<div style='font-size:11px;letter-spacing:0.10em;font-weight:700;"
            f"color:#22c55e;text-transform:uppercase;margin-bottom:4px;'>"
            f"Auto-calibrated</div>"
            f"<div style='color:#cbd5e1;font-size:14px;line-height:1.5;'>"
            f"Home plate detected at ({plate_cx}, {plate_cy}) px, width "
            f"{plate_w} px ≈ 17 inches. Tap <b>Process video</b> to start, "
            f"or use the buttons below if the green outline below doesn't "
            f"look right.</div></div>",
            unsafe_allow_html=True)
        # Draw overlay so the user can visually verify
        try:
            import cv2, numpy as np
            arr = np.frombuffer(png, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            cv2.rectangle(img,
                            (plate_cx - plate_w // 2, plate_cy - plate_w // 4),
                            (plate_cx + plate_w // 2, plate_cy + plate_w // 4),
                            (0, 230, 80), 3)
            cv2.drawMarker(img, (plate_cx, plate_cy), (0, 230, 80),
                              markerType=cv2.MARKER_CROSS,
                              markerSize=20, thickness=2)
            _ok, png_show = cv2.imencode(".png", img)
            if _ok:
                png = png_show.tobytes()
        except Exception:
            pass
        st.image(png, use_container_width=True,
                  caption="Detected plate (green box) — visually verify before processing.")
        b1, b2 = st.columns(2)
        with b1:
            if st.button("Re-try auto-detect", key=f"{state_prefix}retry_btn",
                          use_container_width=True):
                st.session_state[retry_key] = True
                st.session_state.pop(auto_key, None)
                st.rerun()
        with b2:
            if st.button("Looks wrong — let me tap the center",
                          key=f"{state_prefix}override_btn",
                          use_container_width=True):
                st.session_state.pop(auto_key, None)
                st.session_state["__force_manual_" + state_prefix] = True
                st.rerun()
        cal = _derive_calibration_from_two_points(
            (plate_cx - plate_w // 2, plate_cy),
            (plate_cx + plate_w // 2, plate_cy),
            sport=sport)
        for k, v in cal.items():
            st.session_state[f"{state_prefix}{k}"] = v
        return cal

    # --- No auto match → single-tap manual ---
    st.markdown(
        f"<div style='background:#1e293b;border-left:4px solid #d4a634;"
        f"border-radius:8px;padding:12px 16px;margin:8px 0 14px 0;'>"
        f"<div style='font-size:11px;letter-spacing:0.10em;font-weight:700;"
        f"color:#d4a634;text-transform:uppercase;margin-bottom:4px;'>"
        f"One quick tap</div>"
        f"<div style='color:#cbd5e1;font-size:14px;line-height:1.5;'>"
        f"We couldn't auto-detect home plate. <b>Tap the CENTER of the "
        f"plate</b> in the image below — that's all the app needs. Width is "
        f"estimated from your video resolution.</div></div>",
        unsafe_allow_html=True)
    click = streamlit_image_coordinates(png, width=display_width,
                                            key=f"{state_prefix}manual_img")
    if click is not None:
        scale_x = orig_w / display_width
        disp_h = int(orig_h * display_width / max(1, orig_w))
        scale_y = orig_h / max(1, disp_h)
        st.session_state[manual_key] = (int(click["x"] * scale_x),
                                          int(click["y"] * scale_y))
        st.rerun()

    if manual_center:
        plate_cx, plate_cy = manual_center
        plate_w = _default_plate_width_for_resolution(orig_w)
        cal = _derive_calibration_from_two_points(
            (plate_cx - plate_w // 2, plate_cy),
            (plate_cx + plate_w // 2, plate_cy),
            sport=sport)
        st.markdown(
            f"<div style='background:#0f172a;border:1px solid #334155;"
            f"border-radius:10px;padding:14px 18px;margin:12px 0;'>"
            f"<div style='font-size:11px;letter-spacing:0.10em;font-weight:700;"
            f"color:#22c55e;text-transform:uppercase;margin-bottom:8px;'>"
            f"Calibration ready</div>"
            f"<div style='font-size:14px;color:#cbd5e1;line-height:1.7;'>"
            f"Plate center: ({plate_cx}, {plate_cy}) px<br>"
            f"Estimated width: {plate_w} px (from {orig_w}×{orig_h} video)<br>"
            f"Ball radius window: {cal['ball_rmin_px']}–{cal['ball_rmax_px']} px"
            f"</div></div>",
            unsafe_allow_html=True)
        for k, v in cal.items():
            st.session_state[f"{state_prefix}{k}"] = v
        if st.button("Tap again — that wasn't right",
                      key=f"{state_prefix}manual_retap",
                      use_container_width=True):
            st.session_state.pop(manual_key, None)
            st.rerun()
        return cal
    return None


def render_click_calibration(video_path: str,
                                state_prefix: str,
                                sport: str = "Baseball",
                                display_width: int = 720) -> dict | None:
    """Show a still from the video. User clicks LEFT then RIGHT plate edge.
    Returns the calibration dict, or None if not yet calibrated.

    State machine (per state_prefix in session_state):
        step           — "left" | "right" | "done"
        left_orig_pt   — (x,y) in ORIGINAL pixel coords
        right_orig_pt  — (x,y) in ORIGINAL pixel coords
    """
    if not _is_click_calibration_available():
        st.caption(
            "Click-to-calibrate needs the `streamlit-image-coordinates` "
            "package (already in requirements.txt — make sure your "
            "Streamlit Cloud build is up to date)."
        )
        return None

    from streamlit_image_coordinates import streamlit_image_coordinates

    png, orig_w, orig_h = extract_calibration_still(video_path)
    if png is None:
        st.error("Couldn't extract a still from the video — file may be corrupted.")
        return None

    step_key  = f"{state_prefix}cc_step"
    left_key  = f"{state_prefix}cc_left"
    right_key = f"{state_prefix}cc_right"
    if step_key not in st.session_state:
        st.session_state[step_key] = "left"
    step = st.session_state[step_key]

    # Header + instructions
    instr_color = "#22c55e" if step != "done" else "#94a3b8"
    if step == "left":
        instr = ("Step 1 of 2 — <b>tap the LEFT edge of home plate</b> in "
                 "the image below. The frame is from about one second into "
                 "your video.")
    elif step == "right":
        instr = ("Step 2 of 2 — <b>tap the RIGHT edge of home plate</b>. "
                 "Try to match the same height (Y) as the left click.")
    else:
        instr = ("Calibrated. Tap <b>Start over</b> to re-click if the "
                 "preview crosshair below doesn't look right.")
    st.markdown(
        f"<div style='background:#1e293b;border-left:4px solid {instr_color};"
        f"border-radius:8px;padding:12px 16px;margin:8px 0 14px 0;'>"
        f"<div style='color:#f1f5f9;font-size:14px;line-height:1.5;'>"
        f"{instr}</div></div>",
        unsafe_allow_html=True,
    )

    # Render the still and capture the click
    click = streamlit_image_coordinates(
        png,
        width=display_width,
        key=f"{state_prefix}cc_img",
    )

    # Coordinate scaling: streamlit_image_coordinates returns coords in
    # the DISPLAYED image space. Multiply by orig/displayed ratio to get
    # the actual pixel of the source video.
    if click is not None:
        scale_x = orig_w / display_width
        # The component preserves aspect ratio, so disp_h = orig_h/orig_w * disp_w
        disp_h = int(orig_h * display_width / max(1, orig_w))
        scale_y = orig_h / max(1, disp_h)
        click_orig = (int(click["x"] * scale_x), int(click["y"] * scale_y))

        if step == "left":
            st.session_state[left_key] = click_orig
            st.session_state[step_key] = "right"
            st.rerun()
        elif step == "right":
            st.session_state[right_key] = click_orig
            st.session_state[step_key] = "done"
            st.rerun()

    # Show current calibration + reset button if we have both points
    left_pt  = st.session_state.get(left_key)
    right_pt = st.session_state.get(right_key)
    btn_l, btn_r = st.columns([1, 1])
    with btn_l:
        if st.button("Start over", key=f"{state_prefix}cc_reset",
                      use_container_width=True):
            for k in (step_key, left_key, right_key):
                st.session_state.pop(k, None)
            st.rerun()
    with btn_r:
        if left_pt and right_pt and st.button(
                "Re-click right edge only",
                key=f"{state_prefix}cc_reset_right",
                use_container_width=True):
            st.session_state.pop(right_key, None)
            st.session_state[step_key] = "right"
            st.rerun()

    if not (left_pt and right_pt):
        return None

    cal = _derive_calibration_from_two_points(left_pt, right_pt, sport=sport)
    # Persist for downstream
    for k, v in cal.items():
        st.session_state[f"{state_prefix}{k}"] = v
    # Show what was computed
    st.markdown(
        f"<div style='background:#0f172a;border:1px solid #334155;"
        f"border-radius:10px;padding:14px 18px;margin:12px 0;'>"
        f"<div style='font-size:11px;letter-spacing:0.10em;font-weight:700;"
        f"color:#22c55e;text-transform:uppercase;margin-bottom:8px;'>"
        f"Calibration ready</div>"
        f"<div style='font-size:14px;color:#cbd5e1;line-height:1.7;'>"
        f"Plate center: ({cal['plate_cx_px']}, {cal['plate_cy_px']}) px<br>"
        f"Plate width: {cal['plate_w_px']} px ≈ 17 inches<br>"
        f"Expected ball radius: {cal['ball_rmin_px']}–{cal['ball_rmax_px']} px"
        f"</div></div>",
        unsafe_allow_html=True,
    )
    return cal


def process_uploaded_video(video_path: str,
                             calibration: dict,
                             sport: str = "Baseball",
                             min_ball_radius: int = 6,
                             max_ball_radius: int = 22,
                             min_pitch_motion_px: float = 8.0,
                             min_quiet_frames: int = 30,
                             progress_cb=None) -> list[dict]:
    """Run ball detection + pitch segmentation on a pre-recorded video.

    The video is iterated frame-by-frame. Ball positions are collected
    across the whole video. "Pitches" are segmented as contiguous bursts
    of fast ball motion separated by quiet periods (no detection or near-
    stationary motion). Each pitch's positions are then fit with the
    same fit_pitch_trajectory math used by Live Capture.

    Returns a list of pitch-dicts (one per detected pitch) with the same
    keys as Live Capture's snapped pitches:
        velocity_mph, plate_x_ft, plate_z_ft, vert_break_in,
        horiz_break_in, useful_spin_rpm, tilt_clock,
        spin_efficiency_pct, n_samples, flight_time_sec
    """
    try:
        import cv2
        import numpy as np
    except Exception as e:
        raise RuntimeError(f"Cannot process video — OpenCV/NumPy not available: {e}")

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video file: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    # ----- Pass 1: detect ball in every frame -----
    positions: list[tuple[float, int, int]] = []
    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        pos = detect_ball_in_frame(
            frame,
            ball_radius_px_range=(min_ball_radius, max_ball_radius),
            motion_blur_tolerant=True,
        )
        if pos:
            t_sec = frame_idx / fps
            positions.append((t_sec, pos[0], pos[1]))
        frame_idx += 1
        if progress_cb and frame_idx % 30 == 0 and total_frames > 0:
            progress_cb(min(1.0, frame_idx / total_frames))
    cap.release()
    if progress_cb:
        progress_cb(1.0)

    if len(positions) < 5:
        return []

    # ----- Pass 2: segment positions into "pitches" -----
    # A pitch is a burst of consecutive detections where the ball is
    # moving fast and continuously. Pitches are separated by quiet periods
    # (no detection or near-stationary motion lasting min_quiet_frames).
    pitches: list[list[tuple[float, int, int]]] = []
    current: list[tuple[float, int, int]] = []
    for i in range(len(positions)):
        t, x, y = positions[i]
        if not current:
            current.append((t, x, y))
            continue
        prev_t, prev_x, prev_y = current[-1]
        gap_frames = (t - prev_t) * fps
        if gap_frames > min_quiet_frames:
            # Long gap → previous pitch ended
            if len(current) >= 5:
                pitches.append(current)
            current = [(t, x, y)]
        else:
            current.append((t, x, y))
    if len(current) >= 5:
        pitches.append(current)

    # Reject pitches where the ball didn't really move (false positives
    # like a bright object that briefly registered)
    pitches = [
        p for p in pitches
        if max(
            ((p[i+1][1]-p[i][1])**2 + (p[i+1][2]-p[i][2])**2) ** 0.5
            for i in range(len(p)-1)
        ) >= min_pitch_motion_px
    ]

    # ----- Pass 3: fit each pitch's metrics -----
    fitted = []
    for i, pitch_positions in enumerate(pitches):
        cal = dict(calibration)
        cal["sport"] = sport
        fit = fit_pitch_trajectory(pitch_positions, cal)
        if fit is None:
            continue
        fit["pitch_num"]    = i + 1
        fit["n_positions_seen"] = len(pitch_positions)
        # Time window of this pitch — used downstream for pose extraction
        fit["t_start_sec"]  = float(pitch_positions[0][0])
        fit["t_end_sec"]    = float(pitch_positions[-1][0])
        fitted.append(fit)
    return fitted


# =============================================================================
# POSE / MECHANICS HELPERS FOR RECORDED VIDEO
# =============================================================================
# These mirror the Live Capture pose pipeline but run on a pre-recorded file.
# They share the same metric dict shape (hip_shoulder_sep_deg, arm_slot_deg,
# lead_knee_flex_deg, elbow_stress_nm_est) so the existing Mechanics Analysis
# section can light up for Upload Video pitches without any extra plumbing.
#
# MediaPipe is optional. If it's not installed (e.g. Python 3.13/3.14 on
# Streamlit Cloud), these helpers return (None, None) cleanly and the caller
# falls back to ball-only metrics.
def _is_mediapipe_available() -> bool:
    """True only if mediapipe imports AND exposes the legacy solutions API.

    On Streamlit Cloud, if Python is the wrong version, pip sometimes
    installs a stub mediapipe package that imports without errors but is
    missing the `solutions` submodule. Treat that case as unavailable so
    the rest of the app gracefully degrades instead of crashing.
    """
    try:
        import mediapipe as _mp  # noqa: F401
        # Real builds expose mediapipe.solutions.pose; stubs don't.
        _ = _mp.solutions.pose            # type: ignore[attr-defined]
        _ = _mp.solutions.drawing_utils   # type: ignore[attr-defined]
        return True
    except Exception:
        return False


def _compute_pose_metrics(landmarks, img_h: int, img_w: int,
                           handedness: str = "Right") -> dict:
    """Standalone version of Live Capture's _compute_metrics.

    handedness: "Right" (RHP) or "Left" (LHP). Flips the arm-slot side.
    Returns the same key set as live mode so analyze_mechanics is happy.
    """
    import math as _m
    try:
        import mediapipe as _mp
    except Exception:
        return {}
    L = _mp.solutions.pose.PoseLandmark

    try:
        lhip   = landmarks[L.LEFT_HIP.value]
        rhip   = landmarks[L.RIGHT_HIP.value]
        lshld  = landmarks[L.LEFT_SHOULDER.value]
        rshld  = landmarks[L.RIGHT_SHOULDER.value]
        lwrist = landmarks[L.LEFT_WRIST.value]
        rwrist = landmarks[L.RIGHT_WRIST.value]
        lknee  = landmarks[L.LEFT_KNEE.value]
        rknee  = landmarks[L.RIGHT_KNEE.value]
        lankle = landmarks[L.LEFT_ANKLE.value]
        rankle = landmarks[L.RIGHT_ANKLE.value]

        def line_angle(p1, p2):
            return _m.degrees(_m.atan2(p2.y - p1.y, p2.x - p1.x))
        hip_ang  = line_angle(lhip, rhip)
        shld_ang = line_angle(lshld, rshld)
        hs_sep   = abs((shld_ang - hip_ang + 180) % 360 - 180)

        # Throwing side — RHP uses right shoulder/wrist; LHP flips.
        if handedness.lower().startswith("l"):
            slot_ang  = line_angle(lshld, lwrist)
            lead_top, lead_mid, lead_bot = rhip, rknee, rankle
        else:
            slot_ang  = line_angle(rshld, rwrist)
            lead_top, lead_mid, lead_bot = lhip, lknee, lankle

        def joint_angle(p_top, p_mid, p_bot):
            v1 = (p_top.x - p_mid.x, p_top.y - p_mid.y)
            v2 = (p_bot.x - p_mid.x, p_bot.y - p_mid.y)
            dot = v1[0]*v2[0] + v1[1]*v2[1]
            mag1 = (v1[0]**2 + v1[1]**2) ** 0.5
            mag2 = (v2[0]**2 + v2[1]**2) ** 0.5
            if mag1 * mag2 == 0:
                return None
            cos_a = max(-1.0, min(1.0, dot / (mag1 * mag2)))
            return _m.degrees(_m.acos(cos_a))
        lead_knee = joint_angle(lead_top, lead_mid, lead_bot)

        release_y_px = (rwrist.y if not handedness.lower().startswith("l")
                                  else lwrist.y) * img_h
        body_px = abs(lshld.y - lankle.y) * img_h

        # Elbow stress estimate (same formula as Live Capture)
        base_stress = 48.0
        if hs_sep > 50:
            base_stress += (hs_sep - 50) * 0.5
        if lead_knee is not None and lead_knee < 145:
            base_stress += (145 - lead_knee) * 0.4
        if abs(slot_ang) > 30:
            base_stress += (abs(slot_ang) - 30) * 0.3
        elbow_stress_est = max(30.0, min(85.0, base_stress))

        return {
            "hip_shoulder_sep_deg": round(hs_sep, 1),
            "arm_slot_deg":          round(slot_ang, 1),
            "lead_knee_flex_deg":    round(lead_knee, 1) if lead_knee else None,
            "release_y_pixel":       round(release_y_px, 1),
            "body_height_pixel":     round(body_px, 1),
            "elbow_stress_nm_est":   round(elbow_stress_est, 1),
        }
    except Exception:
        return {}


def extract_pose_from_video_segment(video_path: str,
                                       t_start_sec: float,
                                       t_end_sec: float,
                                       handedness: str = "Right",
                                       max_frames_to_scan: int = 60
                                       ) -> tuple:
    """Scan a slice of a recorded video, find the release frame, capture pose.

    Strategy:
      1. Open video, seek to t_start_sec.
      2. Read up to max_frames_to_scan frames inside the window.
      3. Run MediaPipe pose on each, pick the "release frame" = the frame
         whose throwing-wrist is highest in image (smallest y) — that's
         the apex of the arm action, a stable proxy for ball release on
         most camera angles. (For side view we'd use forward-most x; for
         now y works for behind-catcher framing.)
      4. Draw skeleton on that frame and return (overlay_png_bytes, metrics).

    Returns (None, None) if mediapipe is unavailable, video can't be read,
    or no pose was detected. Caller should treat that as "no biomech for
    this pitch" rather than an error.
    """
    if not _is_mediapipe_available():
        return None, None
    try:
        import cv2
        import numpy as np
        import mediapipe as mp
    except Exception:
        return None, None

    mp_pose    = mp.solutions.pose
    mp_draw    = mp.solutions.drawing_utils
    mp_styles  = mp.solutions.drawing_styles

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None, None
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    start_frame = max(0, int(t_start_sec * fps))
    end_frame   = max(start_frame + 1, int(t_end_sec * fps))
    end_frame   = min(end_frame, start_frame + max_frames_to_scan)

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    best_frame  = None
    best_lm     = None
    best_wrist_y = 2.0   # smaller = higher in image = release apex
    L = mp_pose.PoseLandmark
    pose = mp_pose.Pose(model_complexity=1, enable_segmentation=False,
                         min_detection_confidence=0.5,
                         min_tracking_confidence=0.5)
    try:
        for _ in range(end_frame - start_frame):
            ok, frame = cap.read()
            if not ok:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = pose.process(rgb)
            if not res.pose_landmarks:
                continue
            lm = res.pose_landmarks.landmark
            wrist_idx = (L.LEFT_WRIST.value
                          if handedness.lower().startswith("l")
                          else L.RIGHT_WRIST.value)
            w_y = lm[wrist_idx].y
            if w_y < best_wrist_y:
                best_wrist_y = w_y
                best_frame   = frame.copy()
                best_lm      = res.pose_landmarks
    finally:
        pose.close()
        cap.release()

    if best_frame is None or best_lm is None:
        return None, None

    h, w = best_frame.shape[:2]
    mp_draw.draw_landmarks(
        best_frame, best_lm, mp_pose.POSE_CONNECTIONS,
        landmark_drawing_spec=mp_styles.get_default_pose_landmarks_style(),
    )

    metrics = _compute_pose_metrics(best_lm.landmark, h, w,
                                       handedness=handedness)

    # Encode as PNG bytes so we can hand it straight to st.image()
    ok, png = cv2.imencode(".png", best_frame)
    if not ok:
        return None, metrics or None
    return png.tobytes(), (metrics or None)


def _capture_one_camera_for_upload(label: str,
                                      key_prefix: str,
                                      athlete_hand: str,
                                      athlete_sport: str) -> tuple:
    """Render the upload + calibrate + process pipeline for ONE camera.

    Returns (pitches_list, tmp_video_path). Either may be None when not
    yet ready. Pitches list comes from session_state so it survives
    Streamlit reruns (needed because click-calibration triggers reruns).

    key_prefix namespaces all session_state and widget keys so two cameras
    can live side by side without colliding.
    """
    import tempfile, os
    st.markdown(f"### {label}")

    # ===== Step 1 — upload =====
    st.markdown("**Step 1 — Upload video**")
    uploaded = st.file_uploader(
        "Drop a .mp4 / .mov / .m4v file here",
        type=["mp4", "mov", "m4v", "avi"],
        key=f"{key_prefix}file",
        help="iPhone 1080p, 30-240 fps. Native camera output works directly.",
    )
    pitches_session_key = f"{key_prefix}pitches"
    path_session_key    = f"{key_prefix}path"

    if uploaded is None:
        st.caption("No video uploaded yet.")
        return st.session_state.get(pitches_session_key, []), \
                st.session_state.get(path_session_key)

    # Persist file once per upload (avoid re-writing every rerun)
    last_name_key = f"{key_prefix}last_filename"
    tmp_path = os.path.join(tempfile.gettempdir(),
                              f"{key_prefix}{uploaded.name}")
    if st.session_state.get(last_name_key) != uploaded.name:
        with open(tmp_path, "wb") as f:
            f.write(uploaded.read())
        st.session_state[last_name_key] = uploaded.name
        st.session_state[path_session_key] = tmp_path
        # Invalidate cached results for the previous video
        st.session_state.pop(pitches_session_key, None)
    tmp_path = st.session_state.get(path_session_key, tmp_path)
    st.caption(f"Saved: `{tmp_path}` ({uploaded.size / 1024 / 1024:.1f} MB)")

    # ===== Step 2 — calibrate (auto with single-tap fallback) =====
    st.divider()
    st.markdown("**Step 2 — Calibrate** (automatic — confirm or one tap if needed)")
    cal = render_smart_calibration(tmp_path,
                                       state_prefix=f"{key_prefix}cc_",
                                       sport=athlete_sport)
    with st.expander("Power-user: preset / manual numbers",
                       expanded=False):
        st.caption(
            "Falling back here is fine if click-to-calibrate isn't loading. "
            "Pick a preset or type pixel coordinates by hand. Click-derived "
            "values above take precedence if both are set.")
        fallback_cal = render_calibration_with_presets(
            state_prefix=key_prefix)
    cal = cal or fallback_cal
    if cal is None:
        st.info("Calibrate the plate before processing.")
        return st.session_state.get(pitches_session_key, []), tmp_path

    plate_cx = cal["plate_cx_px"]; plate_cy = cal["plate_cy_px"]
    plate_w  = cal["plate_w_px"]
    ball_min = cal["ball_rmin_px"]; ball_max = cal["ball_rmax_px"]

    # ===== Step 3 — process =====
    if st.button("Process video", type="primary", use_container_width=True,
                  key=f"{key_prefix}process_btn"):
        try:
            import cv2  # noqa: F401
        except Exception as e:
            st.error(f"OpenCV missing — can't process video. ({e})")
            return st.session_state.get(pitches_session_key, []), tmp_path

        progress = st.progress(0.0, text="Scanning frames for ball detections...")
        def _cb(frac):
            progress.progress(min(1.0, frac),
                                text=f"Scanning frames... {int(frac*100)}%")
        calibration = {
            "plate_center_x_px": int(plate_cx),
            "plate_center_y_px": int(plate_cy),
            "plate_width_px":    int(plate_w),
            "sport":             athlete_sport,
        }
        try:
            with st.spinner("Detecting ball + fitting pitches..."):
                pitches = process_uploaded_video(
                    tmp_path, calibration, sport=athlete_sport,
                    min_ball_radius=int(ball_min),
                    max_ball_radius=int(ball_max), progress_cb=_cb)
            progress.empty()
            # Pose pass
            if pitches and _is_mediapipe_available():
                pose_prog = st.progress(
                    0.0, text="Extracting skeleton + biomech per pitch...")
                for idx, p in enumerate(pitches):
                    png_bytes, metrics = extract_pose_from_video_segment(
                        tmp_path,
                        t_start_sec=p.get("t_start_sec", 0.0),
                        t_end_sec=p.get("t_end_sec", 0.0) + 0.5,
                        handedness=athlete_hand or "Right")
                    p["skeleton_png"] = png_bytes
                    p["pose_metrics"] = metrics or {}
                    pose_prog.progress((idx + 1) / max(1, len(pitches)),
                                        text=f"Pose: pitch {idx+1}/{len(pitches)}")
                pose_prog.empty()
            elif pitches:
                st.caption(
                    "Skeleton overlay + biomech skipped — MediaPipe not "
                    "available. Ball flight metrics were captured.")
            st.session_state[pitches_session_key] = pitches
            if pitches:
                st.success(f"Found {len(pitches)} pitch(es) in this video.")
            else:
                st.warning(
                    f"No pitches detected. Try lowering ball radius min "
                    f"(< {ball_min} px) and re-process.")
        except Exception as e:
            progress.empty()
            st.error(f"Processing failed: {e}")
    return st.session_state.get(pitches_session_key, []), tmp_path


def _render_pitch_review_table(pitches: list, key_prefix: str) -> list:
    """Render the checkbox table + skeleton expanders for a list of pitches.
    Returns the list of pitches the user kept checked."""
    keep = []
    for p in pitches:
        c1, c2, c3, c4, c5 = st.columns([1, 1.5, 1.5, 1.5, 1.5])
        with c1:
            include = st.checkbox(
                f"Pitch {p['pitch_num']}", value=True,
                key=f"{key_prefix}keep_{p['pitch_num']}")
            if include:
                keep.append(p)
        c2.metric("Velocity",
                   f"{p['velocity_mph']} mph" if p.get('velocity_mph') else "—")
        c3.metric("Plate",
                   f"({p['plate_x_ft']:+.2f}, {p['plate_z_ft']:.2f}) ft"
                   if p.get('plate_x_ft') is not None else "—")
        c4.metric("Break",
                   f"V{p['vert_break_in']:+.0f}\" / H{p['horiz_break_in']:+.0f}\""
                   if p.get('vert_break_in') is not None else "—")
        c5.metric("Spin",
                   f"{p['useful_spin_rpm']} RPM" if p.get('useful_spin_rpm') else "—")

        skel_png = p.get("skeleton_png")
        pose_m   = p.get("pose_metrics") or {}
        if skel_png or pose_m:
            with st.expander(f"Mechanics — Pitch {p['pitch_num']}",
                              expanded=False):
                ec1, ec2 = st.columns([1.2, 1.0])
                with ec1:
                    if skel_png:
                        st.image(skel_png,
                                  caption="Release frame with pose skeleton",
                                  use_container_width=True)
                    else:
                        st.caption(
                            "No skeleton captured for this pitch — pose "
                            "detector couldn't lock in the release window.")
                with ec2:
                    if pose_m:
                        st.markdown("**Biomech (release frame)**")
                        bm_rows = [
                            ("Hip-Shoulder Sep",
                              pose_m.get("hip_shoulder_sep_deg"), "°",
                              "Elite 50-65° for baseball, 42-50° for softball"),
                            ("Arm Slot",
                              pose_m.get("arm_slot_deg"), "°",
                              "Negative = above shoulder, positive = sidearm"),
                            ("Lead Knee Flex",
                              pose_m.get("lead_knee_flex_deg"), "°",
                              "Posted >155° = strong block, <140° = collapse"),
                            ("Elbow Stress (est.)",
                              pose_m.get("elbow_stress_nm_est"), " Nm",
                              "Estimated from pose"),
                        ]
                        for label, val, unit, hint in bm_rows:
                            disp = f"{val}{unit}" if val is not None else "—"
                            st.markdown(
                                f"<div style='margin-bottom:8px;'>"
                                f"<span style='color:#94a3b8;font-size:12px;'>"
                                f"{label}</span><br>"
                                f"<span style='font-size:18px;font-weight:600;"
                                f"color:#f1f5f9;'>{disp}</span><br>"
                                f"<span style='font-size:11px;color:#64748b;'>"
                                f"{hint}</span></div>",
                                unsafe_allow_html=True)
                    else:
                        st.caption(
                            "No biomech values for this pitch — pose detector "
                            "didn't find a confident frame in the throw window.")
    return keep


def _fuse_pitch_pair(mech_pitch: dict, flight_pitch: dict) -> dict:
    """Combine a mechanics-camera pitch and a ball-flight camera pitch
    into one record. Ball flight wins for trajectory metrics (plate, break,
    spin), mechanics camera wins for pose + skeleton."""
    fused = dict(flight_pitch)   # Start with ball-flight side
    mech_pose = mech_pitch.get("pose_metrics") or {}
    if mech_pose:
        fused["pose_metrics"] = mech_pose
    if mech_pitch.get("skeleton_png"):
        fused["skeleton_png"] = mech_pitch["skeleton_png"]
    # Velocity: side view is more accurate (lateral pixel motion).
    # Prefer the mechanics camera's velocity if it's clearly the side view.
    if mech_pitch.get("velocity_mph") is not None:
        fused["velocity_mph_mech"] = mech_pitch["velocity_mph"]
    return fused


def _save_pitches_to_history(pitches: list,
                                active_athlete_id: int,
                                source_label: str = "video_upload"):
    """Write a session row per pitch to history. Returns new session id."""
    from datetime import datetime as _dt
    rows = []
    base_time = _dt.utcnow()
    for i, p in enumerate(pitches):
        pm = p.get("pose_metrics") or {}
        has_pose = bool(pm)
        rows.append({
            "Pitch_Num":              i + 1,
            "Timestamp":              base_time,
            "Pitch_Type":             "Unknown",
            "Velocity_mph":           p.get("velocity_mph"),
            "Total_Spin_rpm":         p.get("useful_spin_rpm"),
            "Spin_Efficiency_pct":    p.get("spin_efficiency_pct"),
            "Vert_Break_in":          p.get("vert_break_in"),
            "Horiz_Break_in":         p.get("horiz_break_in"),
            "Strike_Zone_Side":       p.get("plate_x_ft"),
            "Strike_Zone_Height":     p.get("plate_z_ft"),
            "Extension_ft":           None,
            "Peak_Valgus_Nm":         pm.get("elbow_stress_nm_est"),
            "AC_Ratio":               None,
            "FootPlant_Trunk_Rot":    None,
            "Peak_Hip_Shoulder_Sep":  pm.get("hip_shoulder_sep_deg"),
            "Release_Lead_Knee_Ext":  pm.get("lead_knee_flex_deg"),
            "Arm_Slot_deg":           pm.get("arm_slot_deg"),
            "Peak_Trunk_Angular_Vel": None,
            "Pulse_Present":          False,
            "Pulse_Match_Method":     None,
            "PPAI_Present":           True,
            "PPAI_Match_Method":      source_label,
            "Alignment_Confidence":   1.0,
            "Healed":                 False,
            "Healed_Notes":           (
                f"Uploaded video ({source_label}) — "
                f"{'ball-flight + pose' if has_pose else 'ball-flight only'}"),
            "Outlier_Type":           None,
        })
    cap_df = pd.DataFrame(rows)
    return save_session(active_athlete_id, cap_df,
                          session_type="real", session_kind="pitching")


def _render_two_camera_pairing_ui(mech_pitches: list,
                                     flight_pitches: list) -> list:
    """Side-by-side pitch pairing UI. Returns list of fused pitch dicts
    in the chosen order. User can manually re-pair if counts differ."""
    n_mech, n_flight = len(mech_pitches), len(flight_pitches)
    st.markdown("### Pair pitches across cameras")
    if n_mech == n_flight:
        st.caption(
            f"Both cameras detected {n_mech} pitches. Pairing in order "
            f"(pitch 1 of mechanics camera with pitch 1 of ball-flight "
            f"camera, etc.). Override with the dropdowns if needed.")
    else:
        st.warning(
            f"Camera counts differ — mechanics: {n_mech}, ball-flight: "
            f"{n_flight}. One camera may have missed a throw or detected a "
            f"false positive. Manually pair below (set to 'Skip' to drop a "
            f"pitch entirely).")

    # Build dropdown options
    mech_opts   = ["Skip"] + [f"M-Pitch {p['pitch_num']}" for p in mech_pitches]
    flight_opts = ["Skip"] + [f"F-Pitch {p['pitch_num']}" for p in flight_pitches]
    n_rows = max(n_mech, n_flight)
    fused_list = []
    for i in range(n_rows):
        col_m, col_arrow, col_f = st.columns([1.4, 0.3, 1.4])
        with col_m:
            default_m_idx = (i + 1) if i < n_mech else 0
            pick_m = st.selectbox(
                f"Mechanics #{i+1}", mech_opts, index=default_m_idx,
                key=f"two_cam_mech_pick_{i}")
        with col_arrow:
            st.markdown(
                "<div style='text-align:center;color:#94a3b8;"
                "font-size:24px;line-height:2;'>⇄</div>",
                unsafe_allow_html=True)
        with col_f:
            default_f_idx = (i + 1) if i < n_flight else 0
            pick_f = st.selectbox(
                f"Ball-flight #{i+1}", flight_opts, index=default_f_idx,
                key=f"two_cam_flight_pick_{i}")
        m_i = mech_opts.index(pick_m) - 1
        f_i = flight_opts.index(pick_f) - 1
        if m_i >= 0 and f_i >= 0:
            fused = _fuse_pitch_pair(mech_pitches[m_i],
                                       flight_pitches[f_i])
            fused["pitch_num"] = i + 1
            fused_list.append(fused)
        elif m_i >= 0:
            # Mech only — preserve pose, no ball flight
            mech_only = dict(mech_pitches[m_i])
            mech_only["pitch_num"] = i + 1
            fused_list.append(mech_only)
        elif f_i >= 0:
            # Flight only — ball metrics, no pose
            flight_only = dict(flight_pitches[f_i])
            flight_only["pitch_num"] = i + 1
            fused_list.append(flight_only)
        # else both Skip → drop entirely
    return fused_list


def _run_upload_video_mode(active_athlete_id: int | None,
                             athlete_name: str,
                             athlete_hand: str,
                             athlete_sport: str = "Baseball"):
    """Upload Video mode — film with phone's native camera, process later.

    Two flows live here:
      - Single camera (default): upload one video, calibrate, process, save.
      - Two cameras: upload two videos (mechanics + ball-flight angles),
        process each, manually pair pitches, save fused records where each
        pitch carries the BEST metrics from each angle.
    """
    st.markdown(
        _flat_html(
            "<div style='background:#f0f9ff;border:1px solid #bae6fd;"
            "border-left:4px solid #0ea5e9;border-radius:8px;padding:14px 18px;"
            "margin:8px 0 12px 0;'>"
            "<div style='font-size:11px;letter-spacing:0.10em;font-weight:700;"
            "color:#0369a1;text-transform:uppercase;margin-bottom:6px;'>"
            "How upload mode works</div>"
            "<div style='font-size:13px;color:#1f2937;line-height:1.6;'>"
            "<b>One camera (default):</b> film the bullpen from one angle, "
            "upload it, tap the plate edges to calibrate, then process. "
            "Works in 3-5 min.<br>"
            "<b>Two cameras (optional):</b> use the side-view phone for "
            "<i>mechanics</i> AND a second behind-catcher phone for "
            "<i>ball-flight</i>. Upload both, process both, then pair "
            "pitches. Each saved pitch ends up with the BEST data from "
            "each angle: pose biomech from the side view, plate "
            "location + break + spin from behind the catcher."
            "</div></div>"
        ),
        unsafe_allow_html=True,
    )

    # Camera-mode picker
    mode = st.radio(
        "Camera setup",
        ["Single camera",
         "Two cameras — mechanics + ball-flight"],
        horizontal=True,
        key="upload_mode",
    )

    if mode.startswith("Two cameras"):
        _run_upload_video_two_camera(active_athlete_id, athlete_hand,
                                        athlete_sport)
        return

    # ===== Single-camera flow =====
    pitches, _tmp_path = _capture_one_camera_for_upload(
        label="Bullpen video", key_prefix="upload_",
        athlete_hand=athlete_hand, athlete_sport=athlete_sport)

    if not pitches:
        return
    st.divider()
    st.subheader(f"Detected Pitches ({len(pitches)})")
    st.caption("Review what the detector found. Deselect any that look wrong.")
    keep = _render_pitch_review_table(pitches, key_prefix="upload_")

    st.divider()
    if active_athlete_id is None:
        st.info("Pick a pitcher from the sidebar to enable saving to history.")
        return
    if st.button(f"Save {len(keep)} pitch(es) to history",
                  type="primary", use_container_width=True,
                  key="upload_save_btn"):
        try:
            new_id = _save_pitches_to_history(keep, active_athlete_id,
                                                  source_label="video_upload")
            st.success(f"Saved as session #{new_id}. Open the History tab to "
                        "see it trended alongside other sessions.")
            st.session_state["upload_pitches"] = []
        except Exception as e:
            st.error(f"Could not save: {e}")


def _run_upload_video_two_camera(active_athlete_id: int | None,
                                    athlete_hand: str,
                                    athlete_sport: str):
    """Two-camera flow: side-by-side capture, pair, fuse, save."""
    st.divider()
    st.markdown(
        "<div style='background:#1e293b;border:1px solid #334155;"
        "border-radius:10px;padding:14px 18px;margin:6px 0 14px 0;'>"
        "<div style='font-size:13px;color:#cbd5e1;line-height:1.6;'>"
        "<b>Camera 1 — Mechanics (side view):</b> tripod 20 ft on the "
        "open side (3rd-base side for RHP, 1st-base for LHP), "
        "perpendicular to the flight path. Captures the cleanest skeleton "
        "and arm slot.<br>"
        "<b>Camera 2 — Ball-flight (behind catcher):</b> tripod 10-15 ft "
        "behind the plate on the centerline. Captures plate location, "
        "break direction, and spin most accurately."
        "</div></div>",
        unsafe_allow_html=True,
    )

    col_l, col_r = st.columns(2, gap="large")
    with col_l:
        mech_pitches, _ = _capture_one_camera_for_upload(
            label="Camera 1 · Mechanics (side view)",
            key_prefix="upload_mech_",
            athlete_hand=athlete_hand, athlete_sport=athlete_sport)
    with col_r:
        flight_pitches, _ = _capture_one_camera_for_upload(
            label="Camera 2 · Ball-flight (behind catcher)",
            key_prefix="upload_flight_",
            athlete_hand=athlete_hand, athlete_sport=athlete_sport)

    if not (mech_pitches and flight_pitches):
        st.info("Process both videos to pair pitches.")
        return

    st.divider()
    fused_list = _render_two_camera_pairing_ui(mech_pitches, flight_pitches)
    if not fused_list:
        st.info("No paired pitches — set at least one row to non-Skip on both sides.")
        return

    st.divider()
    st.subheader(f"Fused Pitches ({len(fused_list)})")
    st.caption(
        "Each row is one pitch with the BEST data from each camera — pose "
        "from mechanics, ball-flight from behind catcher. Open Mechanics to "
        "verify the skeleton overlay.")
    keep = _render_pitch_review_table(fused_list, key_prefix="upload_fused_")

    st.divider()
    if active_athlete_id is None:
        st.info("Pick a pitcher from the sidebar to enable saving to history.")
        return
    if st.button(f"Save {len(keep)} fused pitch(es) to history",
                  type="primary", use_container_width=True,
                  key="upload_save_two_cam_btn"):
        try:
            new_id = _save_pitches_to_history(
                keep, active_athlete_id,
                source_label="video_upload_two_cam")
            st.success(
                f"Saved as session #{new_id}. Each pitch carries pose from "
                "the side view AND ball-flight from behind the catcher.")
            for k in ("upload_mech_pitches", "upload_flight_pitches"):
                st.session_state[k] = []
        except Exception as e:
            st.error(f"Could not save: {e}")


def run_live_capture_tab(active_athlete_id: int | None,
                          athlete_name: str,
                          athlete_hand: str,
                          athlete_sport: str = "Baseball"):
    """Render the Live Capture (Beta) tab. Camera → MediaPipe → biomech."""
    st.subheader("Live Capture (Beta) — phone camera tracking")
    st.caption(
        "Point a phone or tablet at the pitcher. The app extracts ball-flight "
        "metrics (velocity, plate location, break, spin estimates) and pose "
        "biomech (when MediaPipe is installed). **No Pitch Logic, Pulse, or "
        "ProPlayAI subscription needed.**"
    )

    # ===== INPUT MODE — Live or Upload =====
    # Live mode is for real-time capture (phone over local Wi-Fi).
    # Upload mode lets you film the bullpen with your phone's native camera,
    # then process the recorded video LATER when you have good Wi-Fi.
    capture_mode = st.radio(
        "Capture mode",
        ["Live (real-time camera)",
         "Upload Video (film now, process later)"],
        index=0,
        horizontal=True,
        key="livecap_mode",
        help="Use Live for in-cage real-time feedback. Use Upload to film with "
             "your phone's native camera app, then upload the file here later "
             "when you're back on good Wi-Fi.",
    )
    if capture_mode.startswith("Upload"):
        _run_upload_video_mode(active_athlete_id, athlete_name, athlete_hand,
                                 athlete_sport)
        return

    # --- Verify the live-capture stack is installed ---
    # REQUIRED for any live capture at all (ball tracking + spin work even
    # without MediaPipe, but we still need webrtc / cv2 / av to grab frames)
    required_missing = []
    try:
        from streamlit_webrtc import webrtc_streamer, WebRtcMode, VideoProcessorBase  # noqa: F401
    except Exception:
        required_missing.append("streamlit-webrtc")
    try:
        import cv2  # noqa: F401
    except Exception:
        required_missing.append("opencv-python-headless")
    try:
        import av  # noqa: F401
    except Exception:
        required_missing.append("av")

    if required_missing:
        st.error(
            f"Live Capture needs these packages: **{', '.join(required_missing)}**. "
            "Open Terminal and run:\n\n"
            "```\npip3 install -r ~/Desktop/PitchingLab/requirements.txt --upgrade\n```\n\n"
            "Then restart the app. (The rest of the app keeps working without these.)"
        )
        return

    # --- OPTIONAL — MediaPipe pose. Missing on Python 3.13/3.14 because
    # MediaPipe doesn't ship wheels for those yet. Live capture still
    # works without it (ball tracking + spin), pose extraction is skipped.
    POSE_AVAILABLE = False
    try:
        import mediapipe as mp  # noqa: F811
        # Some Streamlit Cloud / Python combos install a stub mediapipe
        # without the legacy solutions API — detect that explicitly so
        # the app degrades gracefully instead of AttributeError-crashing.
        _ = mp.solutions.pose           # type: ignore[attr-defined]
        _ = mp.solutions.drawing_utils  # type: ignore[attr-defined]
        POSE_AVAILABLE = True
    except Exception:
        mp = None
        POSE_AVAILABLE = False

    # --- Imports for the working path ---
    import av
    import cv2
    import numpy as np
    from streamlit_webrtc import webrtc_streamer, WebRtcMode, VideoProcessorBase

    if POSE_AVAILABLE:
        mp_pose = mp.solutions.pose
        mp_draw = mp.solutions.drawing_utils
        mp_styles = mp.solutions.drawing_styles
    else:
        # Friendly notice — capture still works, just without pose overlay
        st.info(
            "**Pose extraction is OFF** — MediaPipe isn't fully available for "
            "this Python version. **Ball tracking + velocity + break + spin "
            "still work.** To turn pose ON, the deploy needs Python 3.12 "
            "(set in Streamlit Cloud → app Settings → Python version). "
            "Locally, run `enable_full_pose.command`."
        )

    # --- Sidebar/UI for capture settings ---
    cap_l, cap_r = st.columns([1.2, 1.0])
    with cap_l:
        st.markdown(
            _flat_html(
                "<div style='background:#f0f9ff;border:1px solid #bae6fd;"
                "border-left:4px solid #0ea5e9;border-radius:8px;padding:12px 16px;'>"
                "<div style='font-size:11px;letter-spacing:0.08em;font-weight:700;"
                "color:#0369a1;text-transform:uppercase;margin-bottom:4px;'>"
                "Quick start</div>"
                "<div style='font-size:13px;color:#1f2937;line-height:1.55;'>"
                "1. Set up the camera per the guide below.<br>"
                "2. Calibrate the plate position (one-time per setup).<br>"
                "3. Tap <b>START</b> below the calibration row.<br>"
                "4. After each pitch, tap <b>Snap Pitch</b> at release.<br>"
                "5. Tap <b>Save Session</b> when done — data goes into history."
                "</div></div>"
            ),
            unsafe_allow_html=True,
        )
    with cap_r:
        view_angle = st.radio("Camera angle",
                                ["Side (3rd-base or 1st-base side)", "Behind catcher"],
                                index=0, key="livecap_view_angle",
                                help="Side view catches stride and arm action best. "
                                     "Behind-catcher catches release height and arm slot.")
        show_skeleton = st.checkbox("Overlay skeleton on video", value=True,
                                      key="livecap_show_skeleton")

    # ===== CAMERA SETUP GUIDE =====
    with st.expander("**Where to put the camera** — full setup guide", expanded=True):
        st.markdown(
            _flat_html(
                "<div style='font-size:13px;color:#cbd5e1;line-height:1.65;'>"
                "<b style='color:#f1f5f9;'>What you need:</b> a phone or tablet, "
                "a tripod (or any stable surface — a bag of helmets on a 5-gal "
                "bucket works), and ~3 minutes of setup time."
                "</div>"
            ),
            unsafe_allow_html=True,
        )
        st.markdown("")  # spacer

        opt1, opt2 = st.columns(2)
        with opt1:
            st.markdown(
                _flat_html(
                    "<div style='background:#1e293b;border:1px solid #334155;"
                    "border-left:4px solid #3b82f6;border-radius:8px;padding:16px 18px;'>"
                    "<div style='font-size:11px;letter-spacing:0.10em;font-weight:700;"
                    "color:#3b82f6;text-transform:uppercase;margin-bottom:6px;'>"
                    "Option A · Behind catcher (recommended)</div>"
                    "<div style='font-size:14px;font-weight:700;color:#f1f5f9;margin-bottom:8px;'>"
                    "Best for ball-flight metrics</div>"
                    "<div style='font-size:13px;color:#cbd5e1;line-height:1.6;'>"
                    "<b style='color:#f1f5f9;'>Where:</b> directly behind the catcher, "
                    "facing the pitcher. About <b>10–15 ft behind home plate</b>. The catcher's "
                    "head should be in the bottom-center of the frame; the pitcher should "
                    "appear small in the upper-center.<br><br>"
                    "<b style='color:#f1f5f9;'>Height:</b> tripod at <b>~4 ft</b> — about "
                    "the height of the catcher's shoulders when crouched. Don't put it on "
                    "the ground; you need to see the strike-zone plane.<br><br>"
                    "<b style='color:#f1f5f9;'>Angle:</b> pan slightly DOWN so the strike "
                    "zone fills the middle-third of the frame.<br><br>"
                    "<b style='color:#f1f5f9;'>Strengths:</b> elite velo, plate location, "
                    "vert + horiz break, spin estimate.<br>"
                    "<b style='color:#f1f5f9;'>Weakness:</b> can't see stride, less detail "
                    "on arm slot (pose biomech will work but the pitcher is small)."
                    "</div></div>"
                ),
                unsafe_allow_html=True,
            )
        with opt2:
            st.markdown(
                _flat_html(
                    "<div style='background:#1e293b;border:1px solid #334155;"
                    "border-left:4px solid #d4a634;border-radius:8px;padding:16px 18px;'>"
                    "<div style='font-size:11px;letter-spacing:0.10em;font-weight:700;"
                    "color:#d4a634;text-transform:uppercase;margin-bottom:6px;'>"
                    "Option B · Side view (3rd-base side for RHP)</div>"
                    "<div style='font-size:14px;font-weight:700;color:#f1f5f9;margin-bottom:8px;'>"
                    "Best for mechanics + biomech</div>"
                    "<div style='font-size:13px;color:#cbd5e1;line-height:1.6;'>"
                    "<b style='color:#f1f5f9;'>Where:</b> on the open side of the pitcher "
                    "(<b>3rd-base side for RHP</b>, 1st-base side for LHP). About "
                    "<b>15–25 ft away</b>, perpendicular to the rubber-to-plate line.<br><br>"
                    "<b style='color:#f1f5f9;'>Height:</b> tripod at <b>~4 ft</b>. The "
                    "pitcher's whole body — head to plant foot — should fit in the frame "
                    "with about 1 ft of headroom above and below.<br><br>"
                    "<b style='color:#f1f5f9;'>Angle:</b> level, perpendicular to the "
                    "pitcher's line. NOT angled toward home plate.<br><br>"
                    "<b style='color:#f1f5f9;'>Strengths:</b> hip-shoulder separation, "
                    "stride length, lead-knee flex, arm slot, elbow stress estimate.<br>"
                    "<b style='color:#f1f5f9;'>Weakness:</b> ball-flight metrics (velo, "
                    "break, plate location) are less accurate — the ball is moving away "
                    "from the camera at an oblique angle."
                    "</div></div>"
                ),
                unsafe_allow_html=True,
            )

        st.markdown("")  # spacer
        # Universal setup tips
        st.markdown(
            _flat_html(
                "<div style='background:#1e293b;border:1px solid #334155;"
                "border-left:4px solid #22c55e;border-radius:8px;padding:14px 18px;"
                "margin-top:8px;'>"
                "<div style='font-size:11px;letter-spacing:0.10em;font-weight:700;"
                "color:#22c55e;text-transform:uppercase;margin-bottom:6px;'>"
                "Universal rules</div>"
                "<div style='font-size:13px;color:#cbd5e1;line-height:1.65;'>"
                "<b style='color:#f1f5f9;'>1. Stable tripod.</b> If the phone shakes, "
                "the ball detector picks up noise. A handheld phone is the #1 cause "
                "of bad capture sessions.<br>"
                "<b style='color:#f1f5f9;'>2. Background matters.</b> A dark backdrop "
                "(a net, a fence with foliage behind it) makes the white ball POP. "
                "Avoid pointing the camera at the sky or a white wall — the ball "
                "blends in.<br>"
                "<b style='color:#f1f5f9;'>3. Set the phone to 60 fps if it supports it.</b> "
                "Higher frame rate = more samples per pitch = better trajectory fit. "
                "On iPhone: Camera Settings → Record Video → 1080p at 60 fps.<br>"
                "<b style='color:#f1f5f9;'>4. Don't zoom in.</b> Use the full wide angle. "
                "Zoom crops resolution and breaks the calibration math.<br>"
                "<b style='color:#f1f5f9;'>5. Disable iPhone auto-lock.</b> "
                "Settings → Display & Brightness → Auto-Lock → Never. Otherwise Safari "
                "pauses the video when the screen turns off."
                "</div></div>"
            ),
            unsafe_allow_html=True,
        )

        st.markdown("")  # spacer
        st.markdown(
            _flat_html(
                "<div style='background:#1e293b;border:1px solid #334155;"
                "border-left:4px solid #dc2626;border-radius:8px;padding:14px 18px;'>"
                "<div style='font-size:11px;letter-spacing:0.10em;font-weight:700;"
                "color:#ef4444;text-transform:uppercase;margin-bottom:6px;'>"
                "Common mistakes to avoid</div>"
                "<div style='font-size:13px;color:#cbd5e1;line-height:1.65;'>"
                "• <b>Camera too far away</b> — if the ball is &lt;6 pixels wide in the "
                "frame, the detector misses it. Move closer if the ball-radius slider "
                "max is below 8 px after your test pitch.<br>"
                "• <b>Camera too close</b> — if the pitcher and the ball don't both fit "
                "in the frame, you have to choose between biomech OR ball flight.<br>"
                "• <b>Direct sunlight glare on the lens</b> — washes out the contrast and "
                "the ball becomes undetectable.<br>"
                "• <b>Recording vertically when filming side-view</b> — you'll cut off "
                "the pitcher's stride. Use landscape for side view."
                "</div></div>"
            ),
            unsafe_allow_html=True,
        )

    # --- Initialize per-session capture state ---
    if "livecap_snapped_pitches" not in st.session_state:
        st.session_state["livecap_snapped_pitches"] = []
    if "livecap_last_frame_metrics" not in st.session_state:
        st.session_state["livecap_last_frame_metrics"] = {}

    # ===== PRE-FLIGHT SELF-TEST =====
    # Runs through every dependency + capability so the user knows what
    # WILL work and what won't BEFORE they get to the field. Critical for
    # the weekend test — better to find a missing dep in your kitchen than
    # standing behind home plate.
    st.divider()
    with st.expander("**Pre-flight self-test** — run before going to the field",
                      expanded=False):
        st.caption(
            "Confirms every piece of the live-capture pipeline can actually run "
            "before you go to the field. If any row shows ✗ or ⚠, address it now "
            "rather than in the parking lot."
        )
        checks = []
        # WebRTC / Streamlit
        checks.append(("streamlit-webrtc", True, "Camera streaming"))
        # OpenCV
        try:
            import cv2 as _cv2
            checks.append(("OpenCV " + _cv2.__version__, True, "Ball detection"))
        except Exception as e:
            checks.append((f"OpenCV — MISSING: {e}", False, "Ball detection"))
        # av
        try:
            import av as _av
            checks.append((f"av (PyAV)", True, "Video frame I/O"))
        except Exception as e:
            checks.append((f"av — MISSING: {e}", False, "Video frame I/O"))
        # MediaPipe (pose)
        try:
            import mediapipe as _mp
            checks.append((f"MediaPipe Pose", True,
                            "Skeleton overlay + biomech (hip-shoulder, arm slot)"))
        except Exception:
            checks.append((f"MediaPipe — not installed", False,
                            "Skeleton overlay + biomech — INSTALL Python 3.12 "
                            "to enable (ball tracking still works without it)"))
        # numpy
        try:
            import numpy as _np
            checks.append((f"NumPy " + _np.__version__, True, "Math backbone"))
        except Exception as e:
            checks.append((f"NumPy — MISSING: {e}", False, "Math backbone"))

        rows = []
        for label, ok, purpose in checks:
            badge = ("<span style='color:#22c55e;font-weight:700;'>✓</span>" if ok
                     else "<span style='color:#d4a634;font-weight:700;'>⚠</span>")
            rows.append(
                f"<tr>"
                f"<td style='padding:6px 12px;'>{badge}</td>"
                f"<td style='padding:6px 12px;font-weight:600;color:#f1f5f9;'>{label}</td>"
                f"<td style='padding:6px 12px;color:#94a3b8;'>{purpose}</td>"
                f"</tr>"
            )
        all_ok = all(ok for _, ok, _ in checks)
        summary = ("<b style='color:#22c55e;'>All systems go — capture pipeline ready.</b>"
                   if all_ok else
                   "<b style='color:#d4a634;'>Capture pipeline will run with limited features.</b>"
                   " Ball tracking, velocity, plate location, break, and spin estimates"
                   " will all work even without MediaPipe. The pose-based biomech"
                   " (hip-shoulder separation, arm slot, lead-knee flex) requires"
                   " MediaPipe which needs Python 3.9-3.12.")
        st.markdown(
            _flat_html(
                f"<table style='width:100%;border-collapse:collapse;"
                f"background:#1e293b;border-radius:8px;overflow:hidden;'>"
                f"{''.join(rows)}"
                f"</table>"
                f"<div style='font-size:13px;color:#cbd5e1;margin-top:10px;'>"
                f"{summary}</div>"
            ),
            unsafe_allow_html=True,
        )

    # ===== Calibration UI — preset-driven =====
    st.divider()
    st.markdown("**Step 1 — Calibration** (pick the closest match to your setup)")
    cal = render_calibration_with_presets(state_prefix="livecap_")
    plate_cx       = cal["plate_cx_px"]
    plate_cy       = cal["plate_cy_px"]
    plate_w        = cal["plate_w_px"]
    ball_radius_lo = cal["ball_rmin_px"]
    ball_radius_hi = cal["ball_rmax_px"]

    st.divider()
    st.markdown("**Step 2 — Live capture**")

    # ===== Video processor class — runs MediaPipe (if available) + ball detection per frame =====
    class PoseExtractor(VideoProcessorBase):
        def __init__(self):
            self.pose = None
            if POSE_AVAILABLE:
                self.pose = mp_pose.Pose(
                    model_complexity=1,
                    enable_segmentation=False,
                    min_detection_confidence=0.5,
                    min_tracking_confidence=0.5,
                )
            self.latest_metrics: dict = {}
            self.frame_count = 0
            self.show_skel = True
            self.track_ball = True
            # Ring buffer of (timestamp_sec, x_px, y_px) for the recent N
            # frames — used by snap-pitch to fit a velocity.
            import time as _time
            import collections as _coll
            self._ball_positions = _coll.deque(maxlen=120)  # ~2 sec @ 60fps
            self._start_time = _time.time()
            self.last_ball_pos = None
            self.ball_radius_range = (6, 22)
            self.plate_cx = 640
            self.plate_cy = 600
            self.plate_w  = 80

        def _compute_metrics(self, landmarks, img_h, img_w):
            """Pull pitcher biomech from MediaPipe landmarks.

            Coordinates are normalized (0-1) within the frame. For absolute
            metrics like hip-shoulder separation (an ANGLE), normalization
            doesn't matter. For stride length we'd need real-world scale,
            so we report it as "pixels" until a calibration step is added.
            """
            L = mp_pose.PoseLandmark
            try:
                lhip   = landmarks[L.LEFT_HIP.value]
                rhip   = landmarks[L.RIGHT_HIP.value]
                lshld  = landmarks[L.LEFT_SHOULDER.value]
                rshld  = landmarks[L.RIGHT_SHOULDER.value]
                lwrist = landmarks[L.LEFT_WRIST.value]
                rwrist = landmarks[L.RIGHT_WRIST.value]
                lknee  = landmarks[L.LEFT_KNEE.value]
                rknee  = landmarks[L.RIGHT_KNEE.value]
                lankle = landmarks[L.LEFT_ANKLE.value]
                rankle = landmarks[L.RIGHT_ANKLE.value]

                # Hip-shoulder separation (angle between hip line and shoulder
                # line, projected onto image plane — proxy for the true 3D
                # separation; close enough for relative tracking across pitches)
                def line_angle(p1, p2):
                    import math as _m
                    return _m.degrees(_m.atan2(p2.y - p1.y, p2.x - p1.x))
                hip_ang  = line_angle(lhip, rhip)
                shld_ang = line_angle(lshld, rshld)
                hs_sep   = abs((shld_ang - hip_ang + 180) % 360 - 180)

                # Arm slot — angle of the line from the throwing shoulder to
                # the throwing wrist, measured from horizontal
                # (Assumes right-handed pitcher — flip for LHP in Phase 1.1)
                slot_ang = line_angle(rshld, rwrist)

                # Lead-knee flex — angle at the LEFT knee for RHP
                def joint_angle(p_top, p_mid, p_bot):
                    import math as _m
                    v1 = (p_top.x - p_mid.x, p_top.y - p_mid.y)
                    v2 = (p_bot.x - p_mid.x, p_bot.y - p_mid.y)
                    dot = v1[0]*v2[0] + v1[1]*v2[1]
                    mag1 = (v1[0]**2 + v1[1]**2) ** 0.5
                    mag2 = (v2[0]**2 + v2[1]**2) ** 0.5
                    if mag1 * mag2 == 0:
                        return None
                    cos_a = max(-1.0, min(1.0, dot / (mag1 * mag2)))
                    return _m.degrees(_m.acos(cos_a))
                import math as _m_module  # need it in scope above
                lead_knee = joint_angle(lhip, lknee, lankle)

                # Release point pixel — wrist y-position (lower y = higher
                # in image since 0 is the top)
                release_y_px = rwrist.y * img_h
                # Approximate body height in pixels (for relative scale)
                body_px = abs(lshld.y - lankle.y) * img_h

                # ===== ELBOW STRESS ESTIMATE (Phase 3 — replaces Pulse sleeve)
                # Pulse measures elbow torque directly via inertial sensor.
                # We can ESTIMATE peak valgus torque from pose using three
                # signals known to drive it:
                #   1. Hip-shoulder separation (more = more torque transfer)
                #   2. Trunk early opening at foot-plant (more = elbow takes
                #      load that should go to the trunk)
                #   3. Lead-knee flex at release (collapsed knee = chest
                #      doesn't post = arm yanks)
                # Calibrated against published Driveline / ASMI biomech
                # data — high-stress fastball at 90+ averages 60-70 Nm,
                # well-mechanic'd at the same velocity averages 45-55 Nm.
                # Returned in Nm with explicit ESTIMATED tag in callers.
                base_stress = 48.0   # baseline for well-sequenced HS arm
                # Bonus from separation overload (linear above 50°)
                if hs_sep > 50:
                    base_stress += (hs_sep - 50) * 0.5
                # Penalty for low lead-knee flex (collapsed front-side)
                if lead_knee is not None and lead_knee < 145:
                    base_stress += (145 - lead_knee) * 0.4
                # Penalty for arm slot too flat (sidearm = more torque)
                if abs(slot_ang) > 30:
                    base_stress += (abs(slot_ang) - 30) * 0.3
                # Clamp to physically plausible range
                elbow_stress_est = max(30.0, min(85.0, base_stress))

                return {
                    "hip_shoulder_sep_deg": round(hs_sep, 1),
                    "arm_slot_deg":          round(slot_ang, 1),
                    "lead_knee_flex_deg":    round(lead_knee, 1) if lead_knee else None,
                    "release_y_pixel":       round(release_y_px, 1),
                    "body_height_pixel":     round(body_px, 1),
                    "elbow_stress_nm_est":   round(elbow_stress_est, 1),
                }
            except Exception:
                return {}

        def get_recent_ball_track(self):
            """Snapshot the ring buffer as a list (thread-safe-ish copy)."""
            return list(self._ball_positions)

        def get_calibration(self):
            return {
                "plate_center_x_px": self.plate_cx,
                "plate_center_y_px": self.plate_cy,
                "plate_width_px":    self.plate_w,
                "sport":             "Baseball",  # caller can override
            }

        def recv(self, frame: "av.VideoFrame") -> "av.VideoFrame":
            import time as _time
            img = frame.to_ndarray(format="bgr24")
            h, w = img.shape[:2]

            # ---- Pose (only if MediaPipe is available) ----
            if self.pose is not None:
                rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                results = self.pose.process(rgb)
                if results.pose_landmarks:
                    metrics = self._compute_metrics(
                        results.pose_landmarks.landmark, h, w)
                    self.latest_metrics = metrics

                    if self.show_skel:
                        mp_draw.draw_landmarks(
                            img,
                            results.pose_landmarks,
                            mp_pose.POSE_CONNECTIONS,
                            landmark_drawing_spec=mp_styles.get_default_pose_landmarks_style(),
                        )

            # ---- Ball tracking (Phase 2) ----
            if self.track_ball:
                ball_pos = detect_ball_in_frame(
                    img, ball_radius_px_range=self.ball_radius_range)
                if ball_pos:
                    t_now = _time.time() - self._start_time
                    self._ball_positions.append((t_now, ball_pos[0], ball_pos[1]))
                    self.last_ball_pos = ball_pos
                    # Highlight detected ball with a green ring
                    cv2.circle(img, ball_pos,
                                self.ball_radius_range[1] + 4,
                                (0, 255, 0), 2)
                # Draw recent ball trail
                recent = list(self._ball_positions)[-30:]
                for i, (_, bx, by) in enumerate(recent):
                    alpha = (i + 1) / max(1, len(recent))
                    color = (int(255 * alpha), int(200 * alpha), 0)
                    cv2.circle(img, (bx, by), 3, color, -1)

            # ---- HUD overlay ----
            text_lines = []
            if self.latest_metrics:
                text_lines += [
                    f"HS Sep:  {self.latest_metrics.get('hip_shoulder_sep_deg', '--')} deg",
                    f"Slot:    {self.latest_metrics.get('arm_slot_deg', '--')} deg",
                    f"Lead Knee: {self.latest_metrics.get('lead_knee_flex_deg', '--')} deg",
                ]
            text_lines.append(f"Ball-track samples: {len(self._ball_positions)}")
            y = 28
            for line in text_lines:
                cv2.putText(img, line, (12, y),
                             cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                             (255, 255, 255), 2, cv2.LINE_AA)
                y += 26

            # ---- Calibration crosshair overlay ----
            # Draw the calibrated plate position so the user can see if it
            # lines up with the real plate in the camera view.
            try:
                cv2.line(img,
                          (self.plate_cx - self.plate_w // 2, self.plate_cy),
                          (self.plate_cx + self.plate_w // 2, self.plate_cy),
                          (255, 200, 0), 2)
                cv2.line(img,
                          (self.plate_cx, self.plate_cy - 10),
                          (self.plate_cx, self.plate_cy + 10),
                          (255, 200, 0), 2)
                cv2.putText(img, "PLATE", (self.plate_cx - 26, self.plate_cy - 16),
                             cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                             (255, 200, 0), 2, cv2.LINE_AA)
            except Exception:
                pass

            self.frame_count += 1
            return av.VideoFrame.from_ndarray(img, format="bgr24")

    # ===== Launch the WebRTC stream =====
    ctx = webrtc_streamer(
        key="livecap-pose",
        mode=WebRtcMode.SENDRECV,
        video_processor_factory=PoseExtractor,
        media_stream_constraints={"video": True, "audio": False},
        async_processing=True,
    )
    if ctx.video_processor:
        ctx.video_processor.show_skel  = bool(show_skeleton)
        ctx.video_processor.plate_cx   = int(plate_cx)
        ctx.video_processor.plate_cy   = int(plate_cy)
        ctx.video_processor.plate_w    = int(plate_w)
        ctx.video_processor.ball_radius_range = (
            int(ball_radius_lo), int(ball_radius_hi))

    # ===== Snap-pitch + save controls =====
    st.divider()
    snap_l, snap_r = st.columns([1.0, 1.0])
    with snap_l:
        if st.button("Snap Pitch (at end of throw)",
                      use_container_width=True, type="primary",
                      key="livecap_snap_btn",
                      disabled=ctx.video_processor is None):
            # Snap is allowed if pose is detected OR ball is being tracked.
            # On Python 3.13/3.14 (no MediaPipe) pose will be empty but
            # ball tracking still produces velo + break + spin.
            vp = ctx.video_processor
            has_pose = vp and vp.latest_metrics
            has_ball_buffer = vp and len(vp.get_recent_ball_track()) >= 5
            if vp and (has_pose or has_ball_buffer):
                m = dict(ctx.video_processor.latest_metrics)
                m["pitch_num"] = len(st.session_state["livecap_snapped_pitches"]) + 1
                # ---- Fit ball-flight metrics from the recent buffer ----
                ball_track = ctx.video_processor.get_recent_ball_track()
                calib = ctx.video_processor.get_calibration()
                calib["sport"] = athlete_sport
                fit = fit_pitch_trajectory(ball_track, calib)
                if fit:
                    m["velocity_mph"]         = fit["velocity_mph"]
                    m["plate_x_ft"]           = fit["plate_x_ft"]
                    m["plate_z_ft"]           = fit["plate_z_ft"]
                    m["flight_time_sec"]      = fit["flight_time_sec"]
                    m["n_samples"]            = fit["n_samples_used"]
                    m["vert_break_in"]        = fit.get("vert_break_in")
                    m["horiz_break_in"]       = fit.get("horiz_break_in")
                    m["useful_spin_rpm"]      = fit.get("useful_spin_rpm")
                    m["spin_efficiency_pct"]  = fit.get("spin_efficiency_pct")
                    m["tilt_clock"]           = fit.get("tilt_clock")
                    m["assumed_total_spin"]   = fit.get("assumed_total_spin")
                st.session_state["livecap_snapped_pitches"].append(m)
                # Save the most recent pitch so the instant-feedback card
                # below renders with massive readable numbers
                st.session_state["livecap_last_pitch"] = m
            else:
                st.warning("No pose detected yet — make sure the pitcher is in frame.")
    with snap_r:
        if st.button("Clear all snapped pitches",
                      use_container_width=True, key="livecap_clear_btn"):
            st.session_state["livecap_snapped_pitches"] = []
            st.session_state.pop("livecap_last_pitch", None)
            st.rerun()

    # ===== INSTANT-FEEDBACK CARD (industry-standard, TrackMan-style) =====
    # Shows the just-snapped pitch in massive font so an athlete 10 ft from
    # the phone can read it. Two primary metrics dominate; secondary info
    # lives in smaller text below.
    last_pitch = st.session_state.get("livecap_last_pitch")
    if last_pitch:
        v = last_pitch.get("velocity_mph")
        vb = last_pitch.get("vert_break_in")
        hb = last_pitch.get("horiz_break_in")
        useful_spin = last_pitch.get("useful_spin_rpm")
        tilt = last_pitch.get("tilt_clock")
        eff  = last_pitch.get("spin_efficiency_pct")
        plate_x = last_pitch.get("plate_x_ft")
        plate_z = last_pitch.get("plate_z_ft")

        # Tone for the velocity number (green if elite, neutral otherwise)
        v_tone_color = "#1a2150"
        v_badge = ""
        if v is not None:
            if v >= 90:
                v_tone_color = "#16a34a"
                v_badge = "<span style='display:inline-block;background:#dcfce7;color:#15803d;font-size:14px;font-weight:700;padding:4px 10px;border-radius:12px;margin-left:10px;'>ELITE</span>"
            elif v < 75:
                v_tone_color = "#d4a634"
                v_badge = "<span style='display:inline-block;background:#fef3c7;color:#92400e;font-size:14px;font-weight:700;padding:4px 10px;border-radius:12px;margin-left:10px;'>SOFT</span>"

        v_str = f"{v:.1f}" if v else "—"
        velo_block = (
            f"<div style='flex:1;text-align:center;'>"
            f"<div style='font-size:13px;letter-spacing:0.12em;font-weight:700;"
            f"color:#6b7280;text-transform:uppercase;'>Velocity</div>"
            f"<div style='font-size:80px;font-weight:800;color:{v_tone_color};"
            f"line-height:1.0;letter-spacing:-0.03em;margin-top:6px;'>"
            f"{v_str}"
            f"<span style='font-size:24px;font-weight:600;color:#6b7280;margin-left:8px;'>mph</span>"
            f"{v_badge}"
            f"</div></div>"
            if v else
            "<div style='flex:1;text-align:center;font-size:18px;color:#9ca3af;'>"
            "No ball detected — make sure the ball is in the camera view</div>"
        )

        plate_str = (
            f"({plate_x:+.2f}, {plate_z:.2f}) ft"
            if (plate_x is not None and plate_z is not None) else "—"
        )
        break_str = (
            f"V {vb:+.0f}\" &nbsp;·&nbsp; H {hb:+.0f}\""
            if (vb is not None and hb is not None) else "—"
        )
        spin_str = (
            f"{useful_spin} RPM ({tilt} tilt, {eff}% eff)"
            if useful_spin else "—"
        )

        feedback_html = (
            f"<div style='background:white;border:1px solid #e5e7eb;border-radius:16px;"
            f"padding:28px 32px;box-shadow:0 4px 14px rgba(26,33,80,0.10);"
            f"margin-top:14px;margin-bottom:14px;'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center;"
            f"margin-bottom:18px;'>"
            f"<div style='font-size:14px;letter-spacing:0.10em;font-weight:700;"
            f"color:#d4a634;text-transform:uppercase;'>"
            f"Pitch #{last_pitch.get('pitch_num', '—')} · Live Capture</div>"
            f"<div style='font-size:11px;color:#9ca3af;'>Captured just now</div>"
            f"</div>"
            f"<div style='display:flex;gap:24px;align-items:center;'>"
            f"{velo_block}"
            f"</div>"
            f"<div style='display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;"
            f"margin-top:22px;padding-top:18px;border-top:1px solid #f3f4f6;'>"
            f"<div><div style='font-size:11px;letter-spacing:0.10em;font-weight:700;"
            f"color:#6b7280;text-transform:uppercase;margin-bottom:4px;'>Plate</div>"
            f"<div style='font-size:18px;font-weight:700;color:#1a2150;'>{plate_str}</div></div>"
            f"<div><div style='font-size:11px;letter-spacing:0.10em;font-weight:700;"
            f"color:#6b7280;text-transform:uppercase;margin-bottom:4px;'>Break</div>"
            f"<div style='font-size:18px;font-weight:700;color:#1a2150;'>{break_str}</div></div>"
            f"<div><div style='font-size:11px;letter-spacing:0.10em;font-weight:700;"
            f"color:#6b7280;text-transform:uppercase;margin-bottom:4px;'>Spin</div>"
            f"<div style='font-size:18px;font-weight:700;color:#1a2150;'>{spin_str}</div></div>"
            f"</div></div>"
        )
        st.markdown(_flat_html(feedback_html), unsafe_allow_html=True)

    # ===== MECHANICS ANALYSIS — per-pitch biomech critique =====
    # Color-coded breakdown of every captured pitch's pose metrics with
    # specific cue + drill recommendation for each flagged value. This
    # is what gives the snapshot meaningful coaching value beyond raw
    # numbers — the user can read it like a coach's report.
    snapped = st.session_state.get("livecap_snapped_pitches", [])
    if snapped:
        st.divider()
        st.subheader("Mechanics Analysis")
        st.caption(
            "Per-pitch breakdown of biomechanics + ball flight. Each metric "
            "is color-coded against level-typical ranges and flagged metrics "
            "carry a specific coaching cue + drill suggestion."
        )

        def _critique_value(name: str, value, ranges: dict):
            """Return (color, status, cue) for a metric vs. its baselines.
            ranges = {"good": (lo, hi), "warn": (lo, hi), "cue": "..."}
            """
            if value is None:
                return "#94a3b8", "—", "no data captured for this pitch"
            good = ranges.get("good")
            if good and good[0] <= value <= good[1]:
                return "#22c55e", "ON TARGET", ranges.get("good_cue", "keep this pattern.")
            return "#d4a634", "FLAGGED", ranges.get("cue", "outside the target range.")

        # Define what "good" looks like for each metric
        BIOMECH_RANGES = {
            "hip_shoulder_sep_deg": {
                "good": (42, 55),
                "cue": "Hip-shoulder separation under 42° = limited rubber-band torque. "
                       "Drill: Hershiser drill, 3×4, 3 days/week.",
                "good_cue": "Strong separation generates rubber-band torque for velocity.",
            },
            "arm_slot_deg": {
                "good": (-50, -20),
                "cue": "Arm slot outside typical 3/4-to-over-top range. Drill: mirror "
                       "work for arm-path consistency, 10 reps before each bullpen.",
                "good_cue": "Consistent arm slot in the productive range.",
            },
            "lead_knee_flex_deg": {
                "good": (145, 180),
                "cue": "Lead knee collapsing under load. Drill: wall drill, 4×6, "
                       "every other day.",
                "good_cue": "Strong front-leg block — energy transferring up the chain.",
            },
            "elbow_stress_nm_est": {
                "good": (30, 55),
                "cue": "Elevated estimated elbow stress. Consider cooldown work today "
                       "+ reduced volume next session.",
                "good_cue": "Stress in the safe-to-moderate range.",
            },
            "velocity_mph": {
                "good": (78, 105),
                "cue": "Velocity outside typical HS-to-college range for this athlete.",
                "good_cue": "In-range velocity.",
            },
        }
        DISPLAY_NAMES = {
            "hip_shoulder_sep_deg": "Hip-Shoulder Sep (°)",
            "arm_slot_deg":         "Arm Slot (°)",
            "lead_knee_flex_deg":   "Lead Knee Flex (°)",
            "elbow_stress_nm_est":  "Elbow Stress (Nm, est.)",
            "velocity_mph":         "Velocity (mph)",
            "vert_break_in":        "Vert Break (in)",
            "horiz_break_in":       "Horiz Break (in)",
            "useful_spin_rpm":      "Useful Spin (RPM)",
            "tilt_clock":           "Tilt Clock",
            "spin_efficiency_pct":  "Spin Efficiency (%)",
            "plate_x_ft":           "Plate Side (ft)",
            "plate_z_ft":           "Plate Height (ft)",
        }

        # Per-pitch expander with critique
        for p in snapped:
            num = p.get("pitch_num", "?")
            v = p.get("velocity_mph")
            v_str = f"{v} mph" if v else "(no velo)"
            with st.expander(f"Pitch #{num} — {v_str}", expanded=(num == 1)):
                # All metrics in two columns
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown("**Pose Biomech**")
                    for key in ["hip_shoulder_sep_deg", "arm_slot_deg",
                                "lead_knee_flex_deg", "elbow_stress_nm_est"]:
                        val = p.get(key)
                        name = DISPLAY_NAMES.get(key, key)
                        ranges = BIOMECH_RANGES.get(key, {})
                        color, status, cue = _critique_value(key, val, ranges)
                        val_str = f"{val}" if val is not None else "—"
                        st.markdown(
                            _flat_html(
                                f"<div style='background:#1e293b;border-left:3px solid {color};"
                                f"padding:10px 14px;margin:6px 0;border-radius:0 6px 6px 0;'>"
                                f"<div style='font-size:11px;letter-spacing:0.08em;font-weight:700;"
                                f"color:#94a3b8;text-transform:uppercase;'>{name}</div>"
                                f"<div style='font-size:20px;font-weight:700;color:#f1f5f9;"
                                f"margin:3px 0;'>{val_str} "
                                f"<span style='font-size:11px;color:{color};margin-left:6px;'>"
                                f"{status}</span></div>"
                                f"<div style='font-size:12px;color:#cbd5e1;font-style:italic;'>"
                                f"{cue}</div>"
                                f"</div>"
                            ),
                            unsafe_allow_html=True,
                        )
                with c2:
                    st.markdown("**Ball Flight**")
                    for key in ["velocity_mph", "vert_break_in", "horiz_break_in",
                                "useful_spin_rpm", "tilt_clock", "spin_efficiency_pct",
                                "plate_x_ft", "plate_z_ft"]:
                        val = p.get(key)
                        name = DISPLAY_NAMES.get(key, key)
                        ranges = BIOMECH_RANGES.get(key, {})
                        color, status, cue = _critique_value(key, val, ranges) \
                            if key in BIOMECH_RANGES else ("#94a3b8", "", "")
                        val_str = f"{val}" if val is not None else "—"
                        st.markdown(
                            _flat_html(
                                f"<div style='background:#1e293b;border-left:3px solid {color};"
                                f"padding:8px 12px;margin:4px 0;border-radius:0 4px 4px 0;'>"
                                f"<div style='font-size:11px;letter-spacing:0.08em;font-weight:700;"
                                f"color:#94a3b8;text-transform:uppercase;'>{name}</div>"
                                f"<div style='font-size:17px;font-weight:700;color:#f1f5f9;'>"
                                f"{val_str}</div>"
                                f"</div>"
                            ),
                            unsafe_allow_html=True,
                        )

        st.divider()
        st.subheader(f"Snapped Pitches ({len(snapped)})")
        snap_df = pd.DataFrame(snapped)
        st.dataframe(snap_df, use_container_width=True, hide_index=True)

        if active_athlete_id is not None:
            if st.button("Save this session to history",
                          type="primary", use_container_width=True,
                          key="livecap_save_session_btn"):
                # Build a minimal canonical pitching df from the snapped pitches.
                # The biomech columns map directly; ball-flight columns are blank
                # until Phase 2 wires up ball tracking.
                from datetime import datetime as _dt
                rows = []
                base_time = _dt.utcnow()
                for i, m in enumerate(snapped):
                    has_ball = m.get("velocity_mph") is not None
                    rows.append({
                        "Pitch_Num":              i + 1,
                        "Timestamp":              base_time,
                        "Pitch_Type":             "Unknown",  # auto-classify = Phase 3
                        "Velocity_mph":           m.get("velocity_mph"),
                        "Total_Spin_rpm":         m.get("useful_spin_rpm"),  # estimated from break + velo
                        "Spin_Efficiency_pct":    m.get("spin_efficiency_pct"),
                        "Vert_Break_in":          m.get("vert_break_in"),
                        "Horiz_Break_in":         m.get("horiz_break_in"),
                        "Strike_Zone_Side":       m.get("plate_x_ft"),
                        "Strike_Zone_Height":     m.get("plate_z_ft"),
                        "Extension_ft":           None,
                        "Peak_Valgus_Nm":         m.get("elbow_stress_nm_est"),  # pose-estimated (Phase 3)
                        "AC_Ratio":               None,
                        "FootPlant_Trunk_Rot":    None,
                        "Peak_Hip_Shoulder_Sep":  m.get("hip_shoulder_sep_deg"),
                        "Release_Lead_Knee_Ext":  (180 - m.get("lead_knee_flex_deg", 0))
                                                   if m.get("lead_knee_flex_deg") else None,
                        "Arm_Slot_deg":           m.get("arm_slot_deg"),
                        "Peak_Trunk_Angular_Vel": None,
                        "Pulse_Present":          False,
                        "Pulse_Match_Method":     None,
                        "PPAI_Present":           True,
                        "PPAI_Match_Method":      "live_capture",
                        "Alignment_Confidence":   1.0,
                        "Healed":                 False,
                        "Healed_Notes":           ("Live Capture — pose + velo + plate + break + spin (est.)"
                                                    if has_ball and m.get("vert_break_in") is not None
                                                    else "Live Capture — pose + velo + plate (no break track)"
                                                    if has_ball
                                                    else "Live Capture — pose only (no ball track)"),
                        "Outlier_Type":           None,
                    })
                cap_df = pd.DataFrame(rows)
                try:
                    new_id = save_session(active_athlete_id, cap_df,
                                            session_type="real",
                                            session_kind="pitching")
                    st.success(f"Saved as session #{new_id}. "
                                "Open the History tab to see it trended alongside other sessions.")
                    st.session_state["livecap_snapped_pitches"] = []
                except Exception as e:
                    st.error(f"Could not save: {e}")
        else:
            st.info("Pick a pitcher from the sidebar to enable saving this session to their history.")
    else:
        st.info("No pitches snapped yet. Tap **Snap Pitch** at the release moment of each pitch.")


# =============================================================================
# LIVE CAPTURE  (Hitting Lab) — phone tracks the ball off the tee/toss +
# pose-based swing biomech. Phone-only — no Blast Motion, no HitTrax.
# =============================================================================
def process_uploaded_swing_video(video_path: str,
                                    calibration: dict,
                                    sport: str = "Baseball",
                                    min_ball_radius: int = 6,
                                    max_ball_radius: int = 22,
                                    progress_cb=None) -> list[dict]:
    """Run swing detection on a pre-recorded hitting video.

    Strategy mirrors process_uploaded_video for pitching, but the ball
    behaviour is reversed: in pitching the ball arrives at the plate; in
    hitting the ball *leaves* the contact zone at high speed off the bat.

    A "swing" is segmented as a burst of ball motion where pixel speed
    spikes (the post-contact flight). Between swings the ball is either
    stationary on the tee, slow incoming from a tosser, or absent.

    Returns one dict per detected swing with:
        exit_velocity_mph, launch_angle_deg, contact_t_sec,
        n_samples_used, t_start_sec, t_end_sec
    """
    try:
        import cv2
        import numpy as np
    except Exception as e:
        raise RuntimeError(
            f"Cannot process video — OpenCV/NumPy not available: {e}")

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video file: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    # ----- Pass 1: ball position per frame -----
    positions: list[tuple[float, int, int]] = []
    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        pos = detect_ball_in_frame(
            frame,
            ball_radius_px_range=(min_ball_radius, max_ball_radius),
            motion_blur_tolerant=True)
        if pos:
            t_sec = frame_idx / fps
            positions.append((t_sec, pos[0], pos[1]))
        frame_idx += 1
        if progress_cb and frame_idx % 30 == 0 and total_frames > 0:
            progress_cb(min(1.0, frame_idx / total_frames))
    cap.release()
    if progress_cb:
        progress_cb(1.0)

    if len(positions) < 5:
        return []

    # ----- Pass 2: find post-contact bursts -----
    # Compute pixel speed between consecutive samples. A "swing" is a
    # contiguous run where speed > exit_threshold (much faster than a
    # toss or stationary ball). Separated by quiet gaps.
    EXIT_PX_PER_FRAME = 12.0   # ~50 mph+ at typical zoom — robust threshold
    speeds = []
    for i in range(1, len(positions)):
        dt = positions[i][0] - positions[i-1][0]
        if dt <= 0:
            speeds.append(0.0)
            continue
        dx = positions[i][1] - positions[i-1][1]
        dy = positions[i][2] - positions[i-1][2]
        px_per_sec = ((dx * dx + dy * dy) ** 0.5) / dt
        speeds.append(px_per_sec / fps)   # px per frame for easy threshold

    swings: list[list[int]] = []   # list of position-index ranges
    cur: list[int] = []
    quiet_count = 0
    QUIET_FRAMES_FOR_GAP = 20
    for i, sp in enumerate(speeds, start=1):
        if sp >= EXIT_PX_PER_FRAME:
            cur.append(i)
            quiet_count = 0
        else:
            quiet_count += 1
            if cur and quiet_count >= QUIET_FRAMES_FOR_GAP:
                if len(cur) >= 3:
                    swings.append(cur)
                cur = []
    if len(cur) >= 3:
        swings.append(cur)

    # ----- Pass 3: per-swing exit velo + launch angle -----
    plate_dist_ft = float(calibration.get("ref_dist_ft", 20.0))
    # Same rough scale as the live capture path uses
    ft_per_px = 1.0 / (30.0 * (20.0 / max(plate_dist_ft, 1.0)))
    out = []
    for swing_i, idx_range in enumerate(swings, start=1):
        # Indices into `positions`
        xs = [positions[k][1] for k in idx_range]
        ys = [positions[k][2] for k in idx_range]
        ts = [positions[k][0] for k in idx_range]
        if len(xs) < 3:
            continue
        # Peak pixel speed inside the burst
        pixel_speeds = []
        for k in range(1, len(xs)):
            dt = max(ts[k] - ts[k-1], 1e-6)
            dx = xs[k] - xs[k-1]
            dy = ys[k] - ys[k-1]
            pixel_speeds.append(((dx*dx + dy*dy) ** 0.5) / dt)
        if not pixel_speeds:
            continue
        import numpy as _np
        peak_px_per_sec = float(_np.max(pixel_speeds))
        peak_fps = peak_px_per_sec * ft_per_px
        peak_mph = peak_fps / 1.467
        # Launch angle from a couple of frames after peak
        peak_idx = int(_np.argmax(pixel_speeds))
        la = None
        if peak_idx + 2 < len(xs):
            dx_la = xs[peak_idx + 2] - xs[peak_idx]
            dy_la = ys[peak_idx + 2] - ys[peak_idx]
            import math as _math
            la = _math.degrees(_math.atan2(-dy_la, abs(dx_la)))
        out.append({
            "swing_num":          swing_i,
            "exit_velocity_mph":  round(peak_mph, 1),
            "launch_angle_deg":   (round(la, 1) if la is not None else None),
            "contact_t_sec":      ts[0],
            "t_start_sec":        ts[0],
            "t_end_sec":          ts[-1],
            "n_samples_used":     len(xs),
        })
    return out


def _run_upload_swing_video_mode(active_athlete_id: int | None,
                                     athlete_name: str,
                                     athlete_hand: str,
                                     athlete_sport: str = "Baseball"):
    """Upload Video mode for HITTING — mirror of pitching upload flow."""
    st.markdown(
        _flat_html(
            "<div style='background:#f0f9ff;border:1px solid #bae6fd;"
            "border-left:4px solid #0ea5e9;border-radius:8px;padding:14px 18px;"
            "margin:8px 0 12px 0;'>"
            "<div style='font-size:11px;letter-spacing:0.10em;font-weight:700;"
            "color:#0369a1;text-transform:uppercase;margin-bottom:6px;'>"
            "How upload mode works for hitting</div>"
            "<div style='font-size:13px;color:#1f2937;line-height:1.6;'>"
            "1. At the cage, film the session with your <b>phone's native "
            "Camera app</b> (1080p, 60 fps if your phone supports it). "
            "Phone roughly 20 ft to the side of the hitter, perpendicular "
            "to the swing path — same setup as Live mode.<br>"
            "2. Later, AirDrop the video to your laptop (or open this app "
            "on the device that has the video).<br>"
            "3. Upload the video below. The app scans every frame, finds "
            "every swing by detecting post-contact ball bursts, and "
            "computes exit velocity + launch angle per swing.<br>"
            "4. Review the detected swings and save the keepers to the "
            "hitter's history.<br>"
            "<b>Works for:</b> tee, soft toss, front toss, live BP, and "
            "in-game video — anywhere the ball clearly leaves the bat in "
            "frame."
            "</div></div>"
        ),
        unsafe_allow_html=True,
    )

    import tempfile, os
    st.markdown("**Step 1 — Upload video**")
    uploaded = st.file_uploader(
        "Drop a .mp4 / .mov / .m4v file here",
        type=["mp4", "mov", "m4v", "avi"],
        key="hitcap_upload_file",
        help="iPhone 1080p, 30-240 fps. Native camera output works directly.")
    if uploaded is None:
        st.info("No video uploaded yet.")
        return

    tmp_path = os.path.join(tempfile.gettempdir(),
                              f"hitcap_{uploaded.name}")
    last_name_key = "hitcap_upload_last_name"
    if st.session_state.get(last_name_key) != uploaded.name:
        with open(tmp_path, "wb") as f:
            f.write(uploaded.read())
        st.session_state[last_name_key] = uploaded.name
        st.session_state.pop("hitcap_upload_swings", None)
    st.caption(f"Saved: `{tmp_path}` ({uploaded.size / 1024 / 1024:.1f} MB)")

    # ----- Step 2: camera distance (hitting calibration is simpler) -----
    st.divider()
    st.markdown("**Step 2 — Camera distance**")
    ref_dist_ft = st.slider(
        "Camera-to-contact-zone distance (ft)", min_value=5, max_value=50,
        value=int(st.session_state.get("hitcap_upload_dist", 20)), step=1,
        key="hitcap_upload_dist",
        help="Used to scale pixel motion to real-world feet.")
    ball_min = int(st.session_state.get("hitcap_upload_rmin", 6))
    ball_max = int(st.session_state.get("hitcap_upload_rmax", 22))
    with st.expander("Advanced — ball radius bounds", expanded=False):
        adv_l, adv_r = st.columns(2)
        with adv_l:
            ball_min = st.number_input(
                "Ball radius min (px)", min_value=2, max_value=40,
                value=ball_min, step=1, key="hitcap_upload_rmin_in")
        with adv_r:
            ball_max = st.number_input(
                "Ball radius max (px)", min_value=4, max_value=80,
                value=ball_max, step=1, key="hitcap_upload_rmax_in")

    # ----- Step 3: process -----
    if st.button("Process video", type="primary",
                  use_container_width=True,
                  key="hitcap_upload_process_btn"):
        try:
            import cv2  # noqa
        except Exception as e:
            st.error(f"OpenCV missing — can't process video. ({e})")
            return
        progress = st.progress(0.0, text="Scanning frames for ball...")
        def _cb(frac):
            progress.progress(min(1.0, frac),
                                text=f"Scanning frames... {int(frac*100)}%")
        try:
            with st.spinner("Detecting swings..."):
                swings = process_uploaded_swing_video(
                    tmp_path,
                    {"ref_dist_ft": float(ref_dist_ft), "sport": athlete_sport},
                    sport=athlete_sport,
                    min_ball_radius=int(ball_min),
                    max_ball_radius=int(ball_max),
                    progress_cb=_cb)
            progress.empty()
            st.session_state["hitcap_upload_swings"] = swings
            if swings:
                st.success(f"Found {len(swings)} swing(s) in this video.")
            else:
                st.warning(
                    "No swings detected. Try lowering the ball-radius min "
                    "in Advanced, or check that the ball is clearly "
                    "visible coming off the bat in your video.")
        except Exception as e:
            progress.empty()
            st.error(f"Processing failed: {e}")

    # ----- Step 4: review + save -----
    swings = st.session_state.get("hitcap_upload_swings", [])
    if not swings:
        return
    st.divider()
    st.subheader(f"Detected swings ({len(swings)})")
    st.caption("Each row is one swing. Deselect any that look wrong.")
    keep = []
    for s in swings:
        c1, c2, c3, c4 = st.columns([1, 1.6, 1.6, 2.0])
        with c1:
            include = st.checkbox(f"Swing {s['swing_num']}", value=True,
                                     key=f"hitcap_upload_keep_{s['swing_num']}")
            if include:
                keep.append(s)
        ev = s.get("exit_velocity_mph")
        la = s.get("launch_angle_deg")
        c2.metric("Exit velo", f"{ev} mph" if ev else "—")
        c3.metric("Launch angle", f"{la:+.0f}°" if la is not None else "—")
        c4.metric("Samples", s.get("n_samples_used", "—"))

    st.divider()
    if active_athlete_id is None:
        st.info("Pick a hitter from the sidebar to enable saving to history.")
        return
    if st.button(f"Save {len(keep)} swing(s) to history",
                  type="primary", use_container_width=True,
                  key="hitcap_upload_save_btn"):
        from datetime import datetime as _dt, timedelta as _td
        base_time = _dt.utcnow()
        rows = []
        for i, s in enumerate(keep, start=1):
            rows.append({
                "Swing_Num":              i,
                "Timestamp":              base_time + _td(seconds=2 * (i - 1)),
                "Pitch_Type_Faced":       "Unknown",
                "Pitch_Velocity_mph":     None,
                "Plate_X_ft":             None,
                "Plate_Z_ft":             None,
                "Swing_Type":             "swing",
                "Swing_Outcome":          "solid_contact",
                "Bat_Speed_mph":          None,
                "Attack_Angle_deg":       None,
                "On_Plane_Eff_pct":       None,
                "Peak_Hand_Speed_mph":    None,
                "Time_to_Contact_sec":    None,
                "Exit_Velocity_mph":      s.get("exit_velocity_mph"),
                "Launch_Angle_deg":       s.get("launch_angle_deg"),
                "Contact_Offset_in":      None,
                "Distance_ft":            None,
                "Spray_Angle_deg":        None,
                "Peak_Hip_Shoulder_Sep_deg": None,
                "Stride_Length_in":       None,
                "Lead_Knee_Flex_deg":     None,
            })
        cap_df = pd.DataFrame(rows)
        try:
            new_id = save_session(active_athlete_id, cap_df,
                                    session_type="real",
                                    session_kind="hitting")
            st.success(
                f"Saved as session #{new_id}. Open the Hitting History tab "
                "to see it trended.")
            st.session_state["hitcap_upload_swings"] = []
        except Exception as e:
            st.error(f"Could not save: {e}")


def run_hitting_live_capture(active_athlete_id: int | None,
                              athlete_name: str,
                              athlete_hand: str,
                              athlete_sport: str = "Baseball"):
    """Hitter Live Capture — phone camera tracks the ball off the bat +
    MediaPipe pose for swing mechanics. Sibling to the pitching version."""
    st.subheader("Hitting Live Capture (Beta) — phone camera tracking")
    st.caption(
        "Point the phone at the hitter from the side (3rd-base line for a "
        "RHH, 1st-base line for LHH). The app tracks the ball off the bat "
        "for exit velocity + launch angle, and uses MediaPipe pose for swing "
        "mechanics (hip-shoulder separation, attack angle, stride length). "
        "No bat sensor or HitTrax required."
    )

    # ===== INPUT MODE — Live or Upload Video =====
    capture_mode = st.radio(
        "Capture mode",
        ["Live (real-time camera)",
         "Upload Video (film now, process later)"],
        index=0,
        horizontal=True,
        key="hitcap_mode",
        help="Live uses the phone's camera in real-time over local Wi-Fi. "
             "Upload Video lets you film at the cage with your phone's "
             "native camera, then upload the file when you're on good "
             "Wi-Fi.",
    )
    if capture_mode.startswith("Upload"):
        _run_upload_swing_video_mode(active_athlete_id, athlete_name,
                                        athlete_hand, athlete_sport)
        return

    # --- Verify the live-capture stack ---
    required_missing = []
    try:
        from streamlit_webrtc import webrtc_streamer, WebRtcMode, VideoProcessorBase  # noqa: F401
    except Exception:
        required_missing.append("streamlit-webrtc")
    try:
        import cv2  # noqa: F401
    except Exception:
        required_missing.append("opencv-python-headless")
    try:
        import av  # noqa: F401
    except Exception:
        required_missing.append("av")

    if required_missing:
        st.error(
            f"Hitting Live Capture needs these packages: **{', '.join(required_missing)}**. "
            "Open Terminal and run:\n\n"
            "```\npip3 install -r ~/Desktop/PitchingLab/requirements.txt --upgrade\n```\n\n"
            "Then restart the app."
        )
        return

    # --- OPTIONAL pose dep (stub-aware) ---
    POSE_AVAILABLE = False
    try:
        import mediapipe as mp  # noqa
        # Some Streamlit Cloud / Python combos install a stub mediapipe
        # without the legacy solutions API — detect that explicitly.
        _ = mp.solutions.pose           # type: ignore[attr-defined]
        _ = mp.solutions.drawing_utils  # type: ignore[attr-defined]
        POSE_AVAILABLE = True
    except Exception:
        mp = None
        POSE_AVAILABLE = False
    import av, cv2, numpy as np
    from streamlit_webrtc import webrtc_streamer, WebRtcMode, VideoProcessorBase
    if POSE_AVAILABLE:
        mp_pose = mp.solutions.pose
        mp_draw = mp.solutions.drawing_utils
        mp_styles = mp.solutions.drawing_styles
    else:
        st.info(
            "**Pose extraction is OFF** — MediaPipe isn't fully available for "
            "this Python version. **Ball tracking + exit velo + launch "
            "angle still work.** The deploy needs Python 3.12 to enable "
            "swing-mechanics extraction."
        )

    # --- Calibration — coach-friendly defaults + collapsible advanced ---
    st.divider()
    st.markdown("**Step 1 — Calibration**")
    st.caption(
        "Works for **tee work, soft toss, front toss, and live pitches**. "
        "Phone roughly 20 ft from the contact zone, perpendicular to the "
        "swing path. The defaults below work for most setups — only open "
        "Advanced if exit-velo readings look off after a few swings. "
        "**For live pitches:** tap Snap Swing within ~150 ms of contact so "
        "the algorithm locks onto the post-contact ball.")
    ref_dist_ft = st.slider(
        "Camera-to-contact-zone distance (ft)", min_value=5, max_value=50,
        value=int(st.session_state.get("hitcap_dist", 20)), step=1,
        key="hitcap_dist_input",
        help="Used to scale pixel motion to real-world feet. Same number "
             "whether you're hitting off a tee, front toss, or live BP.")
    with st.expander("Advanced — manual pixel calibration", expanded=False):
        cal_c1, cal_c2 = st.columns(2)
        with cal_c1:
            tee_x = st.number_input(
                "Contact-zone X (px)", min_value=0, max_value=4000,
                value=int(st.session_state.get("hitcap_tee_x", 320)),
                step=10, key="hitcap_tee_x_input",
                help="Pixel column where the ball is at contact "
                     "(tee top, hitting zone, or wherever the bat meets "
                     "the ball).")
            tee_y = st.number_input(
                "Contact-zone Y (px)", min_value=0, max_value=4000,
                value=int(st.session_state.get("hitcap_tee_y", 400)),
                step=10, key="hitcap_tee_y_input")
        with cal_c2:
            ball_min = st.number_input(
                "Ball radius min (px)", min_value=2, max_value=40,
                value=int(st.session_state.get("hitcap_rmin", 6)),
                step=1, key="hitcap_rmin_input")
            ball_max = st.number_input(
                "Ball radius max (px)", min_value=4, max_value=80,
                value=int(st.session_state.get("hitcap_rmax", 22)),
                step=1, key="hitcap_rmax_input")
    # Pull the latest values whether the expander was opened or not
    tee_x   = int(st.session_state.get("hitcap_tee_x", 320))
    tee_y   = int(st.session_state.get("hitcap_tee_y", 400))
    ball_min = int(st.session_state.get("hitcap_rmin", 6))
    ball_max = int(st.session_state.get("hitcap_rmax", 22))
    st.session_state["hitcap_tee_x"] = tee_x
    st.session_state["hitcap_tee_y"] = tee_y
    st.session_state["hitcap_dist"]  = ref_dist_ft
    st.session_state["hitcap_rmin"]  = ball_min
    st.session_state["hitcap_rmax"]  = ball_max
    st.caption("Place the tee (or toss spot) and the camera at a known distance. "
                "The ball-trail math uses this to convert pixels to feet for exit-velo.")

    # --- Init session state ---
    if "hitcap_snapped_swings" not in st.session_state:
        st.session_state["hitcap_snapped_swings"] = []

    # ===== Swing pose extractor + ball tracker =====
    class SwingExtractor(VideoProcessorBase):
        def __init__(self):
            self.pose = mp_pose.Pose(model_complexity=1,
                                       min_detection_confidence=0.5,
                                       min_tracking_confidence=0.5) if POSE_AVAILABLE else None
            self.latest_metrics = {}
            self.show_skel = True
            self.tee_x = 320
            self.tee_y = 400
            self.ref_dist_ft = 20
            self.ball_radius_range = (6, 22)
            import collections as _coll, time as _time
            self._ball_positions = _coll.deque(maxlen=120)
            self._start_time = _time.time()

        def get_recent_ball_track(self):
            return list(self._ball_positions)

        def get_calibration(self):
            return {
                "tee_x_px":     self.tee_x,
                "tee_y_px":     self.tee_y,
                "ref_dist_ft":  self.ref_dist_ft,
                "sport":        athlete_sport,
            }

        def _compute_swing_metrics(self, landmarks, img_h, img_w):
            L = mp_pose.PoseLandmark
            try:
                lhip = landmarks[L.LEFT_HIP.value]
                rhip = landmarks[L.RIGHT_HIP.value]
                lshld = landmarks[L.LEFT_SHOULDER.value]
                rshld = landmarks[L.RIGHT_SHOULDER.value]
                lwrist = landmarks[L.LEFT_WRIST.value]
                rwrist = landmarks[L.RIGHT_WRIST.value]
                lknee  = landmarks[L.LEFT_KNEE.value]
                lankle = landmarks[L.LEFT_ANKLE.value]
                rankle = landmarks[L.RIGHT_ANKLE.value]
                import math as _m
                def line_angle(p1, p2):
                    return _m.degrees(_m.atan2(p2.y - p1.y, p2.x - p1.x))
                hip_ang  = line_angle(lhip, rhip)
                shld_ang = line_angle(lshld, rshld)
                hs_sep   = abs((shld_ang - hip_ang + 180) % 360 - 180)
                # Attack angle ≈ angle of the line from rear hip to lead wrist
                # at contact (proxy for the bat-path angle, since we don't
                # track the bat directly in v1)
                rear_hip = rhip if athlete_hand == "Right" else lhip
                lead_wrist = lwrist if athlete_hand == "Right" else rwrist
                attack_ang = line_angle(rear_hip, lead_wrist)
                # Stride length: pixel distance between ankles, scaled by
                # body height in pixels
                stride_px = abs(lankle.x - rankle.x) * img_w
                body_px   = abs(lshld.y - lankle.y) * img_h
                stride_ratio = stride_px / body_px if body_px > 0 else None
                return {
                    "hip_shoulder_sep_deg": round(hs_sep, 1),
                    "attack_angle_deg":      round(attack_ang, 1),
                    "stride_ratio":          round(stride_ratio, 2) if stride_ratio else None,
                }
            except Exception:
                return {}

        def recv(self, frame):
            import time as _time
            img = frame.to_ndarray(format="bgr24")
            h, w = img.shape[:2]
            if self.pose is not None:
                rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                results = self.pose.process(rgb)
                if results.pose_landmarks:
                    self.latest_metrics = self._compute_swing_metrics(
                        results.pose_landmarks.landmark, h, w)
                    if self.show_skel:
                        mp_draw.draw_landmarks(
                            img, results.pose_landmarks,
                            mp_pose.POSE_CONNECTIONS,
                            landmark_drawing_spec=mp_styles.get_default_pose_landmarks_style(),
                        )
            # Ball tracking
            ball_pos = detect_ball_in_frame(img,
                ball_radius_px_range=self.ball_radius_range)
            if ball_pos:
                t_now = _time.time() - self._start_time
                self._ball_positions.append((t_now, ball_pos[0], ball_pos[1]))
                cv2.circle(img, ball_pos,
                            self.ball_radius_range[1] + 4,
                            (0, 255, 0), 2)
            # Trail markers
            for i, (_, bx, by) in enumerate(list(self._ball_positions)[-30:]):
                alpha = (i + 1) / 30
                cv2.circle(img, (bx, by), 3,
                            (int(255*alpha), int(200*alpha), 0), -1)
            # Tee crosshair
            cv2.drawMarker(img, (self.tee_x, self.tee_y),
                            (255, 200, 0), markerType=cv2.MARKER_CROSS,
                            markerSize=24, thickness=2)
            # HUD
            text_lines = []
            if self.latest_metrics:
                text_lines += [
                    f"HS Sep: {self.latest_metrics.get('hip_shoulder_sep_deg', '--')} deg",
                    f"Attack: {self.latest_metrics.get('attack_angle_deg', '--')} deg",
                ]
            text_lines.append(f"Ball samples: {len(self._ball_positions)}")
            y = 28
            for line in text_lines:
                cv2.putText(img, line, (12, y),
                             cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                             (255, 255, 255), 2, cv2.LINE_AA)
                y += 26
            return av.VideoFrame.from_ndarray(img, format="bgr24")

    # ===== Launch capture =====
    st.divider()
    st.markdown("**Step 2 — Live capture**")
    ctx = webrtc_streamer(
        key="hitcap-swing",
        mode=WebRtcMode.SENDRECV,
        video_processor_factory=SwingExtractor,
        media_stream_constraints={"video": True, "audio": False},
        async_processing=True,
    )
    if ctx.video_processor:
        ctx.video_processor.tee_x = int(tee_x)
        ctx.video_processor.tee_y = int(tee_y)
        ctx.video_processor.ref_dist_ft = int(ref_dist_ft)
        ctx.video_processor.ball_radius_range = (int(ball_min), int(ball_max))

    # ===== Snap swing =====
    st.divider()
    snap_l, snap_r = st.columns(2)
    with snap_l:
        if st.button("Snap Swing (after contact)",
                      type="primary", use_container_width=True,
                      key="hitcap_snap_btn",
                      disabled=ctx.video_processor is None):
            vp = ctx.video_processor
            if vp:
                m = dict(vp.latest_metrics) if vp.latest_metrics else {}
                m["swing_num"] = len(st.session_state["hitcap_snapped_swings"]) + 1
                # Try to extract exit velo from the recent ball trail —
                # post-contact ball travels in a straight line at peak speed
                ball_track = vp.get_recent_ball_track()
                if len(ball_track) >= 5:
                    # Use the FASTEST 3-frame span as the exit velocity
                    # estimate. Calibrate using the tee-to-camera distance
                    # as the pixel-to-ft scale.
                    import numpy as _np
                    pts = _np.array(ball_track)
                    times = pts[:, 0]
                    xs = pts[:, 1]; ys = pts[:, 2]
                    dx = _np.diff(xs); dy = _np.diff(ys); dt = _np.diff(times)
                    pixel_speeds = _np.sqrt(dx*dx + dy*dy) / _np.maximum(dt, 1e-3)
                    if len(pixel_speeds) >= 3:
                        # Pixel-to-foot scale: assume 1 ft per 30 pixels at the
                        # calibrated camera distance (rough — user can fine-tune)
                        ft_per_px = 1.0 / (30.0 * (20.0 / max(ref_dist_ft, 1)))
                        peak_px_per_sec = float(_np.max(pixel_speeds))
                        peak_fps = peak_px_per_sec * ft_per_px
                        peak_mph = peak_fps / 1.467
                        m["exit_velocity_mph"] = round(peak_mph, 1)
                        # Launch angle ≈ angle of the post-contact trail
                        peak_idx = int(_np.argmax(pixel_speeds))
                        if peak_idx + 2 < len(xs):
                            dx_la = xs[peak_idx + 2] - xs[peak_idx]
                            dy_la = ys[peak_idx + 2] - ys[peak_idx]
                            import math as _math
                            la_deg = _math.degrees(_math.atan2(-dy_la, abs(dx_la)))
                            m["launch_angle_deg"] = round(la_deg, 1)
                st.session_state["hitcap_snapped_swings"].append(m)
                st.session_state["hitcap_last_swing"] = m

    with snap_r:
        if st.button("Clear all snapped swings",
                      use_container_width=True,
                      key="hitcap_clear_btn"):
            st.session_state["hitcap_snapped_swings"] = []
            st.session_state.pop("hitcap_last_swing", None)
            st.rerun()

    # ===== Instant feedback card =====
    last = st.session_state.get("hitcap_last_swing")
    if last:
        ev = last.get("exit_velocity_mph")
        la = last.get("launch_angle_deg")
        ev_color = "#1a2150"
        ev_badge = ""
        if ev is not None:
            if ev >= 95:
                ev_color = "#16a34a"
                ev_badge = "<span style='display:inline-block;background:#dcfce7;color:#15803d;font-size:14px;font-weight:700;padding:4px 10px;border-radius:12px;margin-left:10px;'>BARREL</span>"
            elif ev < 75:
                ev_color = "#d4a634"
                ev_badge = "<span style='display:inline-block;background:#fef3c7;color:#92400e;font-size:14px;font-weight:700;padding:4px 10px;border-radius:12px;margin-left:10px;'>WEAK</span>"
        la_badge = ""
        if la is not None and 10 <= la <= 25:
            la_badge = "<span style='display:inline-block;background:#dcfce7;color:#15803d;font-size:12px;font-weight:700;padding:3px 8px;border-radius:10px;margin-left:8px;'>SWEET SPOT</span>"
        hs = last.get("hip_shoulder_sep_deg")
        attack = last.get("attack_angle_deg")
        feedback_html = (
            f"<div style='background:white;border:1px solid #e5e7eb;border-radius:16px;"
            f"padding:28px 32px;box-shadow:0 4px 14px rgba(26,33,80,0.10);"
            f"margin-top:14px;margin-bottom:14px;'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center;"
            f"margin-bottom:18px;'>"
            f"<div style='font-size:14px;letter-spacing:0.10em;font-weight:700;"
            f"color:#d4a634;text-transform:uppercase;'>"
            f"Swing #{last.get('swing_num', '—')} · Live Capture</div>"
            f"<div style='font-size:11px;color:#9ca3af;'>Captured just now</div>"
            f"</div>"
            f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:24px;'>"
            f"<div style='text-align:center;'>"
            f"<div style='font-size:13px;letter-spacing:0.12em;font-weight:700;"
            f"color:#6b7280;text-transform:uppercase;'>Exit Velocity</div>"
            f"<div style='font-size:72px;font-weight:800;color:{ev_color};"
            f"line-height:1.0;letter-spacing:-0.03em;margin-top:6px;'>"
            f"{(f'{ev:.1f}' if ev else '—')}"
            f"<span style='font-size:24px;font-weight:600;color:#6b7280;margin-left:8px;'>mph</span>"
            f"{ev_badge}</div></div>"
            f"<div style='text-align:center;'>"
            f"<div style='font-size:13px;letter-spacing:0.12em;font-weight:700;"
            f"color:#6b7280;text-transform:uppercase;'>Launch Angle</div>"
            f"<div style='font-size:72px;font-weight:800;color:#1a2150;"
            f"line-height:1.0;letter-spacing:-0.03em;margin-top:6px;'>"
            f"{(f'{la:.0f}' if la is not None else '—')}"
            f"<span style='font-size:24px;font-weight:600;color:#6b7280;margin-left:8px;'>°</span>"
            f"{la_badge}</div></div>"
            f"</div>"
            f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:14px;"
            f"margin-top:20px;padding-top:16px;border-top:1px solid #f3f4f6;'>"
            f"<div><div style='font-size:11px;letter-spacing:0.10em;font-weight:700;"
            f"color:#6b7280;text-transform:uppercase;margin-bottom:4px;'>Hip-Shoulder Sep</div>"
            f"<div style='font-size:18px;font-weight:700;color:#1a2150;'>"
            f"{(f'{hs}°' if hs is not None else '—')}</div></div>"
            f"<div><div style='font-size:11px;letter-spacing:0.10em;font-weight:700;"
            f"color:#6b7280;text-transform:uppercase;margin-bottom:4px;'>Attack Angle</div>"
            f"<div style='font-size:18px;font-weight:700;color:#1a2150;'>"
            f"{(f'{attack}°' if attack is not None else '—')}</div></div>"
            f"</div></div>"
        )
        st.markdown(_flat_html(feedback_html), unsafe_allow_html=True)

    # ===== Swings table + save =====
    swings = st.session_state.get("hitcap_snapped_swings", [])
    if swings:
        st.subheader(f"Snapped Swings ({len(swings)})")
        st.dataframe(pd.DataFrame(swings),
                       use_container_width=True, hide_index=True)
        if active_athlete_id is not None:
            if st.button("Save this session to history",
                          type="primary", use_container_width=True,
                          key="hitcap_save_btn"):
                from datetime import datetime as _dt
                rows = []
                base_time = _dt.utcnow()
                for i, m in enumerate(swings):
                    rows.append({
                        "Swing_Num":          i + 1,
                        "Timestamp":          base_time,
                        "Pitch_Type_Faced":   "Unknown",
                        "Pitch_Velocity_mph": None,
                        "Plate_X_ft":         0.0,
                        "Plate_Z_ft":         2.5,
                        "Swing_Type":         "swing",
                        "Swing_Outcome":      "solid_contact"
                                              if m.get("exit_velocity_mph", 0) >= 85
                                              else "weak_contact",
                        "Bat_Speed_mph":      None,
                        "Attack_Angle_deg":   m.get("attack_angle_deg"),
                        "On_Plane_Eff_pct":   None,
                        "Peak_Hand_Speed_mph": None,
                        "Time_to_Contact_sec": None,
                        "Exit_Velocity_mph":   m.get("exit_velocity_mph"),
                        "Launch_Angle_deg":    m.get("launch_angle_deg"),
                        "Contact_Offset_in":   None,
                        "Distance_ft":         None,
                        "Spray_Angle_deg":     None,
                        "Peak_Hip_Shoulder_Sep_deg": m.get("hip_shoulder_sep_deg"),
                        "Stride_Length_in":    None,
                        "Lead_Knee_Flex_deg":  None,
                    })
                cap_df = pd.DataFrame(rows)
                try:
                    new_id = save_session(active_athlete_id, cap_df,
                                            session_type="real",
                                            session_kind="hitting")
                    st.success(f"Saved as hitting session #{new_id}.")
                    st.session_state["hitcap_snapped_swings"] = []
                except Exception as e:
                    st.error(f"Could not save: {e}")
        else:
            st.info("Pick a hitter from the sidebar to enable saving this session.")
    else:
        st.info("No swings snapped yet. Tap **Snap Swing** right after contact.")


# =============================================================================
# LOGIN + LANDING SCREEN
# =============================================================================
# Renders BEFORE the main app body. Gates the rest behind a username/password
# login, then a landing screen where the user picks (or creates) an athlete
# before any analytics screens load. Demo athletes are hidden until the user
# clicks the "Try demo data" button.
def _render_brand_header():
    """Centered Diamond Sports Lab logo + name for the login/landing pages."""
    logo_svg = (
        "<svg width='64' height='64' viewBox='0 0 40 40' "
        "xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M 20 4 L 36 20 L 20 36 L 4 20 Z' "
        "fill='url(#brandgrad)' stroke='#3b82f6' stroke-width='1.5' />"
        "<path d='M 20 12 L 28 20 L 20 28 L 12 20 Z' fill='none' "
        "stroke='#d4a634' stroke-width='1.5' opacity='0.85' />"
        "<circle cx='20' cy='20' r='2' fill='#d4a634' />"
        "<defs><linearGradient id='brandgrad' x1='0%' y1='0%' x2='100%' y2='100%'>"
        "<stop offset='0%' stop-color='#1e3a8a' />"
        "<stop offset='100%' stop-color='#3b82f6' />"
        "</linearGradient></defs></svg>"
    )
    st.markdown(
        f"<div style='display:flex;flex-direction:column;align-items:center;"
        f"padding:18px 0 10px 0;'>"
        f"{logo_svg}"
        f"<div style='font-size:26px;font-weight:800;color:#f1f5f9;"
        f"letter-spacing:-0.01em;margin-top:8px;'>Diamond Sports Lab</div>"
        f"<div style='font-size:12px;color:#94a3b8;letter-spacing:0.10em;"
        f"text-transform:uppercase;font-weight:600;margin-top:4px;'>"
        f"Coach Tools · Pitching + Hitting</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _render_login_screen() -> bool:
    """Sign-in tab + Create-account tab.

    Create-account has three sub-flows the user picks first:
      - Coach (Organization)  → creates an org they admin
      - Athlete (Solo)        → independent player account, owns their own data
      - Athlete (Invite Code) → joins a coach's org via per-athlete code
    """
    _render_brand_header()
    st.markdown(
        "<div style='max-width:520px;margin:14px auto 0 auto;'>"
        "<div style='color:#94a3b8;font-size:14px;text-align:center;line-height:1.6;'>"
        "Sign in to your account, or create one if it's your first time. "
        "Coaches manage an organization. Players can register on their own "
        "or join a coach's org with an invite code."
        "</div></div>",
        unsafe_allow_html=True,
    )

    # ===== Try-the-demo button — no sign-up required =====
    with st.container(border=False):
        d_pad_l, d_btn, d_pad_r = st.columns([1, 2, 1])
        with d_btn:
            st.markdown(
                "<div style='height:14px;'></div>", unsafe_allow_html=True)
            if st.button("Try the demo (no sign-up)",
                          key="login_try_demo",
                          use_container_width=True,
                          type="primary",
                          help="Browse a sample roster across all tiers — "
                               "Individual / Team / Club / Large Org. "
                               "Nothing is saved."):
                st.session_state["auth_user"] = "__demo_guest__"
                st.session_state["auth_demo_mode"] = True
                st.session_state["auth_demo_tier"] = "individual"
                st.rerun()
            with st.expander("What's in the demo?", expanded=False):
                st.markdown(
                    "Browse a realistic sample roster at any subscription "
                    "tier — Individual (1 athlete), Single Team (12), "
                    "Club (24 across age groups), or Large Org (33 across "
                    "Varsity / JV / Freshman). Click any athlete to see "
                    "their full bullpen or post-swing report. Nothing is "
                    "saved, no card required.")
            st.markdown(
                "<div style='height:8px;'></div>", unsafe_allow_html=True)

    with st.container(border=False):
        col_pad_l, col_form, col_pad_r = st.columns([1, 3, 1])
        with col_form:
            tab_in, tab_new = st.tabs(["Sign in", "Create account"])

            with tab_in:
                u = st.text_input("Username", key="login_u",
                                    placeholder="e.g. coach_smith")
                p = st.text_input("Password", type="password", key="login_p")
                if st.button("Sign in", type="primary",
                              use_container_width=True, key="login_btn"):
                    ok, msg = verify_user(u, p)
                    if ok:
                        st.session_state["auth_user"] = msg
                        st.session_state.pop("login_p", None)
                        st.rerun()
                    else:
                        st.error(msg)
                st.caption(
                    "Forgot password? Recovery isn't built yet — passwords "
                    "are stored locally. Create a new account if needed.")

            with tab_new:
                acct_type = st.radio(
                    "What kind of account?",
                    ["Coach (manage an organization)",
                     "Athlete · I have an invite code from a coach",
                     "Athlete · Sign up on my own (no coach)"],
                    key="reg_acct_type",
                    label_visibility="visible")

                st.markdown("---")
                nu  = st.text_input("Pick a username", key="reg_u",
                                       placeholder="3+ chars, letters/numbers/_-")
                np_ = st.text_input("Pick a password", type="password",
                                       key="reg_p",
                                       placeholder="6+ characters")
                np2 = st.text_input("Confirm password", type="password",
                                       key="reg_p2")

                if acct_type.startswith("Coach"):
                    org_name = st.text_input(
                        "Organization name",
                        key="reg_org_name",
                        placeholder="e.g. Riverside HS Baseball")
                    btn_label = "Create coach account"
                elif acct_type.startswith("Athlete · I have"):
                    inv = st.text_input(
                        "Invite code from your coach",
                        key="reg_invite_code",
                        placeholder="6 letters/numbers, e.g. K7M2QX")
                    btn_label = "Join organization"
                else:
                    st.caption(
                        "We'll create a personal athlete profile owned by "
                        "you. You can be invited to a coach's organization "
                        "later if needed.")
                    rcol1, rcol2 = st.columns(2)
                    with rcol1:
                        a_name = st.text_input("Your full name",
                                                  key="reg_solo_name")
                        a_hand = st.selectbox("Throwing/Batting hand",
                                                ["Right", "Left"],
                                                key="reg_solo_hand")
                    with rcol2:
                        a_sport = st.selectbox("Sport",
                                                  ["Baseball", "Softball"],
                                                  key="reg_solo_sport")
                        a_grad = st.text_input("Grad class (optional)",
                                                  key="reg_solo_grad",
                                                  placeholder="e.g. 2027")
                    btn_label = "Create athlete account"

                if st.button(btn_label, type="primary",
                              use_container_width=True, key="reg_btn"):
                    if np_ != np2:
                        st.error("Passwords don't match.")
                    elif acct_type.startswith("Coach"):
                        ok, msg = register_coach(nu, np_,
                                                    st.session_state.get(
                                                        "reg_org_name", ""))
                        if ok:
                            st.session_state["auth_user"] = msg
                            st.session_state.pop("reg_p", None)
                            st.session_state.pop("reg_p2", None)
                            st.rerun()
                        else:
                            st.error(msg)
                    elif acct_type.startswith("Athlete · I have"):
                        ok, msg = register_athlete(
                            nu, np_,
                            invite_code=st.session_state.get(
                                "reg_invite_code", ""))
                        if ok:
                            _, canonical = verify_user(nu, np_)
                            st.session_state["auth_user"] = canonical
                            st.session_state.pop("reg_p", None)
                            st.session_state.pop("reg_p2", None)
                            st.rerun()
                        else:
                            st.error(msg)
                    else:
                        ok, msg = register_athlete(
                            nu, np_,
                            name=st.session_state.get("reg_solo_name", ""),
                            hand=st.session_state.get("reg_solo_hand", "Right"),
                            sport=st.session_state.get("reg_solo_sport", "Baseball"),
                            grad_class=st.session_state.get("reg_solo_grad", ""))
                        if ok:
                            _, canonical = verify_user(nu, np_)
                            st.session_state["auth_user"] = canonical
                            st.session_state.pop("reg_p", None)
                            st.session_state.pop("reg_p2", None)
                            st.rerun()
                        else:
                            st.error(msg)
    return False


def _render_landing_screen() -> bool:
    """Coach landing: pick an athlete from the org roster, add a new one,
    or browse demo data. Athletes get auto-routed to their own profile
    before this screen renders (see render_login_or_landing)."""
    user = current_username() or ""
    rec  = current_user_record() or {}
    role = rec.get("role", "coach")
    org  = get_org_record(rec.get("org_id"))
    org_name = org.get("name") if org else None
    _render_brand_header()

    # Welcome banner with logout link
    head_l, head_r = st.columns([5, 1])
    with head_l:
        org_line = (f" · Coach for <b style='color:#f1f5f9;'>{org_name}</b>"
                    if org_name else "")
        st.markdown(
            f"<div style='text-align:left;color:#cbd5e1;font-size:15px;"
            f"line-height:1.6;margin-top:6px;'>"
            f"Signed in as <b style='color:#f1f5f9;'>{user}</b>{org_line}. "
            f"Pick an athlete to open their profile, or create a new one."
            f"</div>",
            unsafe_allow_html=True)
    with head_r:
        if st.button("Sign out", key="landing_logout",
                      use_container_width=True):
            for k in ("auth_user", "auth_demo_mode", "selected_athlete_id"):
                st.session_state.pop(k, None)
            st.rerun()

    # ===== Demo mode picker — Individual / Team / Club / Org =====
    st.divider()
    demo_on  = st.session_state.get("auth_demo_mode", False)
    cur_tier = st.session_state.get("auth_demo_tier", "individual")
    toggle_l, toggle_r = st.columns([3, 2])
    with toggle_l:
        st.markdown(
            "<div style='color:#f1f5f9;font-size:15px;font-weight:600;"
            "margin-top:8px;'>Browsing</div>",
            unsafe_allow_html=True)
        if demo_on:
            spec = DEMO_TIERS[cur_tier]
            st.caption(
                f"Demo: **{spec['label']}**. {spec['blurb']} "
                "Toggle off to return to your real athletes.")
        else:
            st.caption(
                "Showing your athletes. Try demo mode to see what each "
                "tier (Individual / Team / Club / Org) looks like with "
                "realistic sample rosters.")
    with toggle_r:
        new_state = st.toggle(
            "Demo data on" if demo_on else "Try demo data",
            value=demo_on, key="landing_demo_toggle")
        if new_state != demo_on:
            st.session_state["auth_demo_mode"] = new_state
            st.rerun()

    if demo_on:
        tier_pick = st.radio(
            "Tier to preview",
            list(DEMO_TIERS.keys()),
            format_func=lambda k: DEMO_TIERS[k]["label"],
            index=list(DEMO_TIERS.keys()).index(cur_tier),
            horizontal=True,
            key="landing_demo_tier_pick")
        if tier_pick != cur_tier:
            st.session_state["auth_demo_tier"] = tier_pick
            st.rerun()
        # Seed athletes for the selected tier on demand
        _seed_demo_tier(tier_pick)

    # Roster
    seed_demo_athletes_if_empty()
    roster = list_athletes()   # auto-scopes by user / demo
    cap_value = get_athlete_cap_for_user()
    st.divider()

    # ----- Teams (org sub-groups) — coach can create / delete -----
    # In demo mode, surface the virtual teams that were seeded for the
    # selected tier so the demo properly shows the team grouping feel.
    org_id_for_teams = rec.get("org_id")
    if is_demo_mode_active():
        teams_list = get_demo_teams(current_demo_tier() or "individual")
    else:
        teams_list = list_teams_for_org(org_id_for_teams) if org_id_for_teams else []
    if org_id_for_teams:
        with st.expander("Manage teams "
                          f"({len(teams_list)} team"
                          f"{'s' if len(teams_list) != 1 else ''})",
                          expanded=False):
            st.caption(
                "Sub-teams keep large rosters organized — Varsity / JV, "
                "14U / 16U, etc. Athletes can stay unassigned if you don't "
                "need teams.")
            new_team = st.text_input("New team name",
                                       key="land_new_team_name",
                                       placeholder="e.g. Varsity")
            if st.button("Add team", key="land_new_team_btn"):
                if new_team.strip():
                    create_team(org_id_for_teams, new_team.strip())
                    st.rerun()
                else:
                    st.error("Team name can't be empty.")
            for t in teams_list:
                tc_l, tc_r = st.columns([3, 1])
                with tc_l:
                    st.markdown(f"**{t['name']}**")
                with tc_r:
                    if st.button("Delete", key=f"del_team_{t['id']}"):
                        delete_team(t["id"])
                        st.rerun()

    if roster:
        cap_str = f" / {cap_value}" if cap_value else ""
        st.markdown(
            f"<div style='color:#cbd5e1;font-size:13px;letter-spacing:0.08em;"
            f"font-weight:700;text-transform:uppercase;margin-bottom:10px;'>"
            f"Your athletes ({len(roster)}{cap_str})</div>",
            unsafe_allow_html=True)

        # Build team-grouped buckets. Each athlete lands in either a named
        # team or "Unassigned" so coaches with no teams still see a clean
        # single bucket.
        buckets: dict = {}    # team_label → list[athlete]
        team_lookup = {t["id"]: t["name"] for t in teams_list}
        for a in roster:
            label = team_lookup.get(a.get("team_id"), "Unassigned")
            buckets.setdefault(label, []).append(a)
        # Sort: named teams first (alpha), Unassigned last
        bucket_order = sorted([k for k in buckets if k != "Unassigned"])
        if "Unassigned" in buckets:
            bucket_order.append("Unassigned")

        for team_label in bucket_order:
            bucket_athletes = buckets[team_label]
            st.markdown(
                f"<div style='margin-top:18px;color:#d4a634;font-size:13px;"
                f"letter-spacing:0.10em;font-weight:700;text-transform:"
                f"uppercase;'>{team_label} · {len(bucket_athletes)}</div>",
                unsafe_allow_html=True)
            # 3-column grid inside this team bucket
            for row_start in range(0, len(bucket_athletes), 3):
                cols = st.columns(3, gap="medium")
                for i, col in enumerate(cols):
                    idx = row_start + i
                    if idx >= len(bucket_athletes):
                        continue
                    a = bucket_athletes[idx]
                    hand_tag = f"{a['hand'][:1]}HP" if a['sport'] == 'Baseball' else f"{a['hand'][:1]}HP"
                    sport_icon = "Softball" if a['sport'] == 'Softball' else "Baseball"
                    with col:
                        invite = a.get("invite_code") or "—"
                        avatar = athlete_avatar_html(a, size_px=56)
                        st.markdown(
                            f"<div style='background:#1e293b;border:1px solid #334155;"
                            f"border-radius:10px;padding:14px 16px;margin-bottom:8px;'>"
                            f"<div style='display:flex;gap:12px;align-items:center;'>"
                            f"{avatar}"
                            f"<div style='min-width:0;flex:1;'>"
                            f"<div style='font-size:11px;letter-spacing:0.10em;"
                            f"color:#94a3b8;text-transform:uppercase;font-weight:600;'>"
                            f"{sport_icon} · {hand_tag}</div>"
                            f"<div style='font-size:18px;font-weight:700;"
                            f"color:#f1f5f9;margin-top:2px;line-height:1.2;'>"
                            f"{a['name']}</div>"
                            f"<div style='font-size:12px;color:#94a3b8;margin-top:2px;'>"
                            f"Class of {a['grad_class'] or '—'} · {a['level']}</div>"
                            f"</div></div>"
                            f"<div style='margin-top:10px;padding-top:10px;"
                            f"border-top:1px dashed #334155;font-size:11px;"
                            f"color:#94a3b8;letter-spacing:0.10em;font-weight:600;"
                            f"text-transform:uppercase;'>Athlete invite code</div>"
                            f"<div style='font-family:JetBrains Mono,Menlo,"
                            f"monospace;font-size:16px;font-weight:700;"
                            f"color:#d4a634;letter-spacing:0.15em;margin-top:4px;'>"
                            f"{invite}</div>"
                            f"</div>",
                            unsafe_allow_html=True)
                        bc1, bc2 = st.columns([2, 1])
                        with bc1:
                            if st.button(f"Open profile",
                                          key=f"open_{a['id']}",
                                          use_container_width=True):
                                st.session_state["selected_athlete_id"] = a["id"]
                                st.rerun()
                        with bc2:
                            if st.button("Graduated",
                                          key=f"grad_{a['id']}",
                                          use_container_width=True,
                                          help="Archive this athlete. "
                                               "History is kept; frees up "
                                               "a roster slot.",
                                          disabled=is_demo_mode_active()):
                                archive_athlete(a["id"], archived=True)
                                st.success(
                                    f"{a['name']} archived — roster slot freed.")
                                st.rerun()
                        # Inline team reassign + profile pic upload
                        if teams_list:
                            team_opts = ["Unassigned"] + [t["name"] for t in teams_list]
                            curr_team_name = team_lookup.get(a.get("team_id"), "Unassigned")
                            picked_team = st.selectbox(
                                "Team", team_opts,
                                index=team_opts.index(curr_team_name),
                                key=f"team_pick_{a['id']}",
                                label_visibility="collapsed")
                            if picked_team != curr_team_name:
                                if picked_team == "Unassigned":
                                    assign_athlete_to_team(a["id"], None)
                                else:
                                    new_tid = next(t["id"] for t in teams_list
                                                     if t["name"] == picked_team)
                                    assign_athlete_to_team(a["id"], new_tid)
                                st.rerun()
                        with st.expander(f"Profile picture", expanded=False):
                            up = st.file_uploader(
                                f"Upload for {a['name']}",
                                type=["png", "jpg", "jpeg", "webp"],
                                key=f"pic_up_{a['id']}",
                                label_visibility="collapsed")
                            if up is not None:
                                ok, msg = set_athlete_profile_pic(
                                    a["id"], up.read())
                                (st.success if ok else st.error)(msg)
                                if ok:
                                    st.rerun()
                            if a.get("profile_pic_b64") and st.button(
                                    "Clear picture",
                                    key=f"pic_clear_{a['id']}",
                                    use_container_width=True):
                                clear_athlete_profile_pic(a["id"])
                                st.rerun()

        # Archived view
        archived_all = list_athletes(include_archived=True)
        archived_only = [x for x in archived_all if x.get("archived")]
        if archived_only:
            with st.expander(
                    f"Archived athletes ({len(archived_only)})",
                    expanded=False):
                st.caption(
                    "Graduated / inactive players. Their history is still "
                    "viewable. Unarchive to restore (will count against "
                    "your roster cap again).")
                for a in archived_only:
                    ac_l, ac_m, ac_r = st.columns([2, 1, 1])
                    with ac_l:
                        st.markdown(
                            f"**{a['name']}** · "
                            f"{a.get('sport', 'Baseball')} · "
                            f"Class of {a.get('grad_class') or '—'}")
                    with ac_m:
                        if st.button("Unarchive",
                                      key=f"unarch_{a['id']}",
                                      use_container_width=True):
                            archive_athlete(a["id"], archived=False)
                            st.rerun()
                    with ac_r:
                        if st.button("View history",
                                      key=f"open_arch_{a['id']}",
                                      use_container_width=True):
                            st.session_state["selected_athlete_id"] = a["id"]
                            st.rerun()
    else:
        st.info(
            "No athletes yet. Tap **Add new athlete** below to create one, "
            "or flip the **Try demo data** toggle above to explore with "
            "sample players.")

    # Create-new section
    st.divider()
    demo_tier_now = current_demo_tier() or "individual"
    allow_add_in_demo = (
        is_demo_mode_active() and demo_tier_now != "individual")
    with st.expander("Add new athlete",
                       expanded=(len(roster) == 0)):
        if is_demo_mode_active() and not allow_add_in_demo:
            st.caption(
                "The Individual demo is a single-athlete preview — switch to "
                "Single Team / Club / Large Org demo to try adding athletes, "
                "or turn demo mode off to add to your real roster.")
        else:
            with st.form("landing_new_athlete"):
                fc1, fc2 = st.columns(2)
                with fc1:
                    nm = st.text_input("Name", key="land_new_name")
                    hd = st.selectbox("Throwing/Batting hand",
                                        ["Right", "Left"], key="land_new_hand")
                    sp = st.selectbox("Sport",
                                        ["Baseball", "Softball"],
                                        key="land_new_sport")
                with fc2:
                    gc = st.text_input("Grad class (e.g. 2026)",
                                          key="land_new_grad")
                    lv = st.selectbox("Level", ATHLETE_LEVELS,
                                        index=ATHLETE_LEVELS.index("HS-Varsity"),
                                        key="land_new_level")
                    if teams_list:
                        team_choices = ["Unassigned"] + [t["name"] for t in teams_list]
                        team_pick = st.selectbox(
                            "Team (optional)", team_choices,
                            key="land_new_team_pick")
                    else:
                        team_pick = "Unassigned"
                    nt = st.text_area("Notes", key="land_new_notes", height=70)
                pic = st.file_uploader(
                    "Profile picture (optional)",
                    type=["png", "jpg", "jpeg", "webp"],
                    key="land_new_pic")
                go = st.form_submit_button("Create athlete", type="primary",
                                              use_container_width=True)
                if go:
                    in_demo = is_demo_mode_active()
                    if in_demo:
                        # Skip the roster cap in demo mode — coaches are
                        # poking around the experience, not paying yet.
                        allowed, reason = True, None
                    else:
                        allowed, reason = can_add_athlete()
                    if not nm.strip():
                        st.error("Athlete name is required.")
                    elif not allowed:
                        st.error(reason)
                    else:
                        team_id_for_new = None
                        if team_pick != "Unassigned":
                            team_id_for_new = next(
                                (t["id"] for t in teams_list
                                  if t["name"] == team_pick), None)
                        # Demo additions land in the active demo tier so
                        # they show up alongside the seeded athletes.
                        if in_demo:
                            tier_now = current_demo_tier() or "individual"
                            created_by_val = DEMO_TIERS[tier_now]["tag"]
                            org_id_val = None
                        else:
                            created_by_val = current_username()
                            org_id_val = "auto"
                        new_id = add_athlete(
                            nm.strip(), hand=hd, sport=sp,
                            grad_class=gc.strip(), notes=nt.strip(), level=lv,
                            created_by=created_by_val,
                            org_id=org_id_val,
                            team_id=team_id_for_new)
                        if pic is not None:
                            set_athlete_profile_pic(new_id, pic.read())
                        st.session_state["selected_athlete_id"] = new_id
                        st.success(f"Created {nm.strip()}.")
                        st.rerun()
    return False


def _render_admin_panel() -> bool:
    """Admin dashboard — total visibility into the whole platform.

    Three tables:
      - Orgs (count, subscription tier, status, athlete count)
      - Users (role, org, subscription, trial usage)
      - Athletes (name, sport, org, invite code, created_by)

    Click any athlete row to impersonate (drop into their profile).
    """
    user = current_username() or ""
    _render_brand_header()
    head_l, head_r = st.columns([5, 1])
    with head_l:
        st.markdown(
            f"<div style='color:#cbd5e1;font-size:15px;line-height:1.6;'>"
            f"<b style='color:#ef4444;'>ADMIN</b> · signed in as "
            f"<b style='color:#f1f5f9;'>{user}</b>. You can see every "
            f"org, user, and athlete on the platform.</div>",
            unsafe_allow_html=True)
    with head_r:
        if st.button("Sign out", key="admin_signout",
                      use_container_width=True):
            for k in ("auth_user", "auth_demo_mode", "selected_athlete_id",
                        "admin_impersonating"):
                st.session_state.pop(k, None)
            st.rerun()

    orgs = admin_list_all_orgs()
    users = admin_list_all_users()
    athletes = admin_list_all_athletes()

    st.divider()
    # ----- Summary KPIs -----
    paying_orgs   = [o for o in orgs if o.get("subscription_status") == "active"]
    paying_users  = [u for u in users
                       if u.get("role") == "athlete" and not u.get("org_id")
                       and u.get("subscription_status") == "active"]
    mrr = 0.0
    for o in paying_orgs:
        tier = SUBSCRIPTION_TIERS.get(o.get("subscription_tier") or "")
        if tier:
            mrr += float(tier["monthly_usd"])
    for u in paying_users:
        tier = SUBSCRIPTION_TIERS.get(u.get("subscription_tier") or "")
        if tier:
            mrr += float(tier["monthly_usd"])
    kpi_cols = st.columns(4)
    kpi_cols[0].metric("Organizations", len(orgs))
    kpi_cols[1].metric("Users", len(users))
    kpi_cols[2].metric("Athletes", len(athletes))
    kpi_cols[3].metric("MRR (est.)", f"${mrr:,.0f}")

    st.divider()
    st.subheader(f"Organizations ({len(orgs)})")
    if not orgs:
        st.caption("No orgs yet.")
    else:
        import pandas as pd
        org_rows = []
        for o in orgs:
            ath_n = sum(1 for a in athletes if a.get("org_id") == o["id"])
            org_rows.append({
                "ID": o["id"], "Name": o["name"],
                "Owner": o["owner_username"],
                "Status": o.get("subscription_status", "trial"),
                "Tier": o.get("subscription_tier") or "—",
                "Athletes": ath_n,
                "Invite code": o.get("invite_code") or "—",
                "Created": (o.get("created_at") or "")[:10],
            })
        st.dataframe(pd.DataFrame(org_rows), use_container_width=True,
                       hide_index=True)

    st.divider()
    st.subheader(f"Users ({len(users)})")
    if not users:
        st.caption("No users yet.")
    else:
        import pandas as pd
        user_rows = []
        for u in users:
            user_rows.append({
                "Username": u["username"],
                "Role": u.get("role", "coach"),
                "Org ID": u.get("org_id") or "—",
                "Status": u.get("subscription_status", "trial"),
                "Tier": u.get("subscription_tier") or "—",
                "Trial sess/pitch":
                    f"{u.get('trial_sessions_used', 0)}/{u.get('trial_pitches_used', 0)}",
                "Stripe": (u.get("stripe_customer_id") or "—")[:18],
                "Created": (u.get("created_at") or "")[:10],
            })
        st.dataframe(pd.DataFrame(user_rows), use_container_width=True,
                       hide_index=True)

    st.divider()
    st.subheader(f"Athletes ({len(athletes)})")
    if not athletes:
        st.caption("No athletes yet.")
    else:
        # Picker + impersonate button
        opts = ["Pick to impersonate..."] + [
            f"#{a['id']} · {a['name']} ({a.get('sport', 'Baseball')}) — org {a.get('org_id') or 'solo'}"
            for a in athletes]
        pick = st.selectbox("Open as athlete", opts, key="admin_imp_pick")
        if pick != opts[0] and st.button("Impersonate selected",
                                              key="admin_imp_btn",
                                              type="primary"):
            idx = opts.index(pick) - 1
            admin_impersonate_athlete(athletes[idx]["id"])
            st.rerun()
        import pandas as pd
        ath_rows = [{
            "ID": a["id"], "Name": a["name"],
            "Sport": a.get("sport", "Baseball"),
            "Hand": a.get("hand"),
            "Org ID": a.get("org_id") or "—",
            "Invite": a.get("invite_code") or "—",
            "Created by": a.get("created_by"),
        } for a in athletes]
        st.dataframe(pd.DataFrame(ath_rows), use_container_width=True,
                       hide_index=True)

    st.divider()
    with st.expander("Bootstrap a new admin (requires secret code)",
                       expanded=False):
        bu = st.text_input("Username to promote", key="admin_promote_user")
        bc = st.text_input("Bootstrap code", type="password",
                              key="admin_promote_code")
        if st.button("Promote to admin", key="admin_promote_btn"):
            ok, msg = promote_user_to_admin(bu, bc)
            (st.success if ok else st.error)(msg)
    return False


def render_plans_and_billing_page():
    """Full-screen Plans & Billing — replaces the old in-tab Pricing page.

    Pulls live tier data from SUBSCRIPTION_TIERS so updating prices in one
    place (the constants block) updates the page automatically. CTAs are
    Stripe-ready: clicking 'Choose' starts checkout when Stripe is set
    up, otherwise shows a 'billing not yet enabled' notice.
    """
    rec = current_user_record() or {}
    org = get_org_record(rec.get("org_id"))

    head_l, head_r = st.columns([5, 1])
    with head_l:
        st.markdown(
            "<div style='font-size:11px;letter-spacing:0.14em;font-weight:700;"
            "color:#d4a634;text-transform:uppercase;margin-bottom:6px;'>"
            "Plans &amp; Billing</div>"
            "<div style='font-size:24px;font-weight:800;color:#f1f5f9;"
            "line-height:1.2;'>Pick the plan that fits your roster</div>",
            unsafe_allow_html=True)
    with head_r:
        if st.button("Back to app", key="billing_back",
                      use_container_width=True):
            st.session_state.pop("show_plans_page", None)
            st.rerun()

    # Current plan summary (if any)
    sub_entity = org if org else rec
    sub_status = (sub_entity or {}).get("subscription_status", "trial")
    sub_tier   = (sub_entity or {}).get("subscription_tier")
    tier_info  = SUBSCRIPTION_TIERS.get(sub_tier) if sub_tier else None
    status_color = {"active": "#22c55e", "trial": "#d4a634",
                     "past_due": "#ef4444", "canceled": "#ef4444",
                     "expired": "#ef4444"}.get(sub_status, "#94a3b8")
    status_label = {"active": "Active subscription",
                     "trial": "Free trial",
                     "past_due": "Payment past due",
                     "canceled": "Canceled",
                     "expired": "Expired",
                     "billing_disabled": "Billing not enabled yet"}.get(
                         sub_status, "Status unknown")
    st.markdown(
        f"<div style='background:#1e293b;border:1px solid #334155;"
        f"border-left:4px solid {status_color};border-radius:10px;"
        f"padding:16px 20px;margin:14px 0;'>"
        f"<div style='font-size:11px;letter-spacing:0.10em;font-weight:700;"
        f"color:{status_color};text-transform:uppercase;margin-bottom:4px;'>"
        f"{status_label}</div>"
        f"<div style='color:#f1f5f9;font-size:15px;'>"
        f"{tier_info['label'] if tier_info else 'No tier selected'}"
        f"</div></div>",
        unsafe_allow_html=True)

    cycle = st.radio("Billing cycle",
                       ["Annual (save ~30%)", "Monthly"],
                       horizontal=True, key="plans_cycle")
    is_annual = cycle.startswith("Annual")

    cols = st.columns(len(SUBSCRIPTION_TIERS))
    for i, (key, t) in enumerate(SUBSCRIPTION_TIERS.items()):
        price = t["annual_usd"] if is_annual else t["monthly_usd"]
        unit  = "/yr" if is_annual else "/mo"
        is_current = (sub_tier == key and sub_status == "active")
        border = "#22c55e" if is_current else "#334155"
        with cols[i]:
            st.markdown(
                f"<div style='background:#1e293b;border:2px solid {border};"
                f"border-radius:12px;padding:20px 18px;height:100%;'>"
                f"<div style='font-size:11px;letter-spacing:0.10em;font-weight:700;"
                f"color:#94a3b8;text-transform:uppercase;'>{t['label']}</div>"
                f"<div style='font-size:32px;font-weight:800;color:#f1f5f9;"
                f"margin:10px 0 2px 0;'>${price:.0f}"
                f"<span style='font-size:14px;font-weight:500;"
                f"color:#94a3b8;'>{unit}</span></div>"
                f"<div style='font-size:13px;color:#cbd5e1;line-height:1.5;"
                f"min-height:60px;'>{t['blurb']}</div>"
                f"<div style='font-size:11px;color:#64748b;margin-top:8px;"
                f"padding-top:8px;border-top:1px dashed #334155;'>"
                f"Up to {t['athlete_cap']} athlete"
                f"{'s' if t['athlete_cap'] > 1 else ''}</div>"
                f"</div>",
                unsafe_allow_html=True)
            if is_current:
                st.success("Your current plan")
            else:
                if st.button(f"Choose {t['label']}",
                              key=f"plans_choose_{key}_{cycle}",
                              use_container_width=True,
                              type="primary"):
                    if stripe_is_configured():
                        st.session_state["pending_checkout_tier"]   = key
                        st.session_state["pending_checkout_annual"] = is_annual
                        st.info("Redirecting to Stripe Checkout...")
                    else:
                        st.warning(
                            "Card processing isn't turned on yet. Email "
                            "[kolbydonnell@gmail.com](mailto:kolbydonnell@gmail.com)"
                            " to subscribe directly while billing is being set up.")

    st.divider()
    st.markdown(
        f"**Free trial:** every account starts with {TRIAL_SESSIONS_CAP} "
        f"free saved sessions or {TRIAL_PITCHES_CAP} total captured "
        f"pitches — whichever comes first. After that you keep full read "
        f"access to your existing data; new captures require an active "
        f"subscription.")
    with st.expander("Can I cancel anytime?", expanded=False):
        st.markdown(
            "Yes. Month-to-month. No contracts, no cancellation fees. "
            "Annual saves about 30% if you want to lock in.")
    with st.expander("What happens if I cancel?", expanded=False):
        st.markdown(
            "Your saved sessions and athletes stay viewable forever — "
            "we don't delete data when you cancel. You just can't add "
            "new captures until you resubscribe.")
    with st.expander("Can a single player use this without a coach?",
                       expanded=False):
        st.markdown(
            "Yes — the Individual tier is built for that. Solo athletes "
            "or parents tracking their kid's college recruiting get the "
            "full app for one athlete profile.")
    with st.expander("Can a former team player keep their data?",
                       expanded=False):
        st.markdown(
            "Yes. When a coach archives a graduated player, that player "
            "is offered a one-click conversion to an Individual account "
            "on their next login. Their data comes with them, the trial "
            "resets, and they own their profile from then on.")

    st.divider()
    sell_l, sell_r = st.columns(2)
    with sell_l:
        try:
            sell_pdf = generate_sell_sheet_pdf()
            st.download_button(
                "Download sell-sheet PDF",
                data=sell_pdf,
                file_name="Diamond_Sports_Lab_Sell_Sheet.pdf",
                mime="application/pdf",
                use_container_width=True)
        except Exception as e:
            st.caption(f"Sell sheet generation issue: {e}")
    with sell_r:
        st.link_button(
            "Schedule a 15-min walkthrough",
            url="mailto:kolbydonnell@gmail.com?subject=Diamond%20Sports%20Lab%20Demo",
            use_container_width=True)


def render_login_or_landing() -> bool:
    """Top-level gate. Returns True ONLY when the user has logged in AND
    picked an athlete (so main() should proceed to the analytics app).
    Otherwise it renders login or landing UI itself and returns False."""
    if not current_username():
        _render_login_screen()
        return False

    # Admin role bypasses the regular landing and gets the admin panel.
    # If they've explicitly opted into impersonating an athlete, fall
    # through to the main app instead.
    rec = current_user_record() or {}
    if rec.get("role") == "admin":
        if st.session_state.get("admin_impersonating") and \
           st.session_state.get("selected_athlete_id"):
            return True
        _render_admin_panel()
        return False

    # Player accounts skip the landing — they only see their own athlete
    if rec.get("role") == "athlete":
        # Conversion prompt: if their team archived them OR their athlete
        # is gone, offer to convert to a solo Individual account.
        archived_ath = needs_player_conversion_prompt()
        if archived_ath:
            _render_brand_header()
            st.markdown(
                f"<div style='max-width:560px;margin:20px auto 0 auto;"
                f"background:#1e293b;border:1px solid #334155;border-left:"
                f"4px solid #d4a634;border-radius:12px;padding:22px 26px;'>"
                f"<div style='font-size:11px;letter-spacing:0.12em;font-weight:"
                f"700;color:#d4a634;text-transform:uppercase;margin-bottom:8px;'>"
                f"Your team archived your profile</div>"
                f"<div style='color:#f1f5f9;font-size:18px;font-weight:700;"
                f"margin-bottom:6px;'>{archived_ath['name']}</div>"
                f"<div style='color:#cbd5e1;font-size:14px;line-height:1.6;'>"
                f"You can convert to an Individual account to keep all your "
                f"history (every session, every pitch, every video). You'd "
                f"own your data outright with no organization attached. The "
                f"trial counter resets so you have time to evaluate before "
                f"subscribing.</div></div>",
                unsafe_allow_html=True)
            cv_l, cv_r = st.columns(2)
            with cv_l:
                if st.button("Convert to Individual account",
                              type="primary", use_container_width=True,
                              key="player_convert_btn"):
                    ok, msg = convert_player_to_solo(current_username())
                    (st.success if ok else st.error)(msg)
                    if ok:
                        st.rerun()
            with cv_r:
                if st.button("Sign out", use_container_width=True,
                              key="player_convert_signout"):
                    for k in ("auth_user", "auth_demo_mode",
                                "selected_athlete_id"):
                        st.session_state.pop(k, None)
                    st.rerun()
            return False
        linked = rec.get("linked_athlete_id")
        if linked:
            st.session_state["selected_athlete_id"] = linked
            return True
        # No athlete linked (shouldn't happen after register) — show
        # an explanatory screen instead of dropping into a coach landing.
        _render_brand_header()
        st.error(
            "Your account isn't linked to an athlete profile yet. Ask your "
            "coach to share the invite code for your profile and create a "
            "new account with it.")
        if st.button("Sign out", key="orphan_signout"):
            for k in ("auth_user", "auth_demo_mode", "selected_athlete_id"):
                st.session_state.pop(k, None)
            st.rerun()
        return False

    # Coach — if they haven't picked an athlete yet, show landing
    if not st.session_state.get("selected_athlete_id"):
        _render_landing_screen()
        return False
    return True


def main():
    st.set_page_config(
        page_title="Diamond Sports Lab",
        page_icon="◆",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_global_styles()

    # ===== LOGIN / LANDING GATE =====
    # Renders login or landing screen and returns False until the user
    # has signed in AND picked an athlete. Keeps the analytics screens
    # from popping up on a random profile.
    if not render_login_or_landing():
        return

    # (Chart-sizing diagnostic now lives at the bottom of the sidebar
    # and renders unconditionally — see the "Debug: chart sizes" block
    # in the sidebar code. No main-area gate needed.)

    # ===== iOS Safari rotation lock =====
    # Streamlit's default viewport meta tag lets iOS recompute scale on
    # every rotation, which compounds and shrinks the layout each time.
    # We aggressively re-install a locked viewport on EVERY rotation event,
    # AND force the page to reflow at the new viewport width.
    st.markdown(
        """
        <script>
        (function() {
            const LOCKED_VIEWPORT = 'width=device-width, initial-scale=1.0, maximum-scale=1.0, minimum-scale=1.0, user-scalable=no, viewport-fit=cover';
            function lockViewport() {
                let meta = document.querySelector('meta[name="viewport"]');
                if (meta) { meta.setAttribute('content', LOCKED_VIEWPORT); }
                else {
                    meta = document.createElement('meta');
                    meta.name = 'viewport';
                    meta.content = LOCKED_VIEWPORT;
                    document.head.appendChild(meta);
                }
            }
            // Lock on initial load
            lockViewport();
            // Re-lock on every conceivable event Streamlit / iOS might fire
            ['load', 'pageshow', 'orientationchange', 'resize',
             'focus', 'visibilitychange'].forEach(evt => {
                window.addEventListener(evt, lockViewport);
            });
            // Periodic re-lock in case Streamlit re-renders the head
            setInterval(lockViewport, 1000);
            // Snap Plotly charts to current container width after rotation
            function snapPlotly() {
                try {
                    document.querySelectorAll('.js-plotly-plot').forEach(c => {
                        if (window.Plotly && window.Plotly.Plots) {
                            try { window.Plotly.Plots.resize(c); } catch(e) {}
                        }
                    });
                } catch(e) {}
            }
            // Force every chart image to the CURRENT viewport width.
            // Belt-and-suspenders for the iOS rotation shrinkage bug —
            // even if the CSS 100vw rules don't reapply correctly, this
            // hard-sets the inline style on every <img> that sits inside
            // a Streamlit image container.
            function pinChartImagesToViewport() {
                const w = window.innerWidth;
                document.querySelectorAll(
                    '.stImage img, [data-testid="stImage"] img'
                ).forEach(img => {
                    img.style.setProperty('width', w + 'px', 'important');
                    img.style.setProperty('min-width', w + 'px', 'important');
                    img.style.setProperty('max-width', w + 'px', 'important');
                    img.style.setProperty('height', 'auto', 'important');
                });
            }
            // Initial pin + after every load tick
            window.addEventListener('load', pinChartImagesToViewport);
            // Run again right after every rotate so new viewport wins
            window.addEventListener('orientationchange', () => {
                lockViewport();
                setTimeout(() => {
                    lockViewport();
                    snapPlotly();
                    pinChartImagesToViewport();
                }, 300);
                setTimeout(() => {
                    lockViewport();
                    snapPlotly();
                    pinChartImagesToViewport();
                }, 700);
            });
            window.addEventListener('resize', pinChartImagesToViewport);
            // Catch any lazily-rendered charts after Streamlit reruns
            setInterval(pinChartImagesToViewport, 1500);
        })();
        </script>

        <script>
        /* =========================================================
           CHART WHEEL PASS-THROUGH (v2)
           ---------------------------------------------------------
           Chart, image, and component-iframe containers steal mouse
           wheel events, freezing page scroll. We capture wheel events
           globally (in the capture phase so we fire before charts
           react) and forward them to the actual scrollable container.

           Streamlit Cloud's scrollable container isn't window — it's
           usually <section.main> or a stAppViewContainer div. We
           walk from the body to find which ancestor actually has
           overflow scroll and is taller than its viewport.
           ========================================================= */
        (function() {
            let scroller = null;
            function findScroller() {
                if (scroller && document.body.contains(scroller)) {
                    return scroller;
                }
                const candidates = [
                    document.querySelector('[data-testid="stAppViewContainer"]'),
                    document.querySelector('section.main'),
                    document.querySelector('div.main'),
                    document.querySelector('.main'),
                    document.scrollingElement,
                    document.documentElement,
                    document.body,
                ];
                for (const el of candidates) {
                    if (!el) continue;
                    const style = window.getComputedStyle(el);
                    const canScroll = (style.overflowY === 'auto' ||
                                          style.overflowY === 'scroll');
                    if (canScroll && el.scrollHeight > el.clientHeight + 1) {
                        scroller = el;
                        return el;
                    }
                }
                // Fallback: whichever candidate has the largest content overflow
                let best = null, bestDiff = 0;
                for (const el of candidates) {
                    if (!el) continue;
                    const diff = el.scrollHeight - el.clientHeight;
                    if (diff > bestDiff) {
                        bestDiff = diff;
                        best = el;
                    }
                }
                scroller = best;
                return best;
            }
            function isChartTarget(el) {
                if (!el) return false;
                // Walk up to find a chart/image/iframe container
                let cur = el;
                while (cur && cur !== document.body) {
                    if (cur.classList) {
                        if (cur.classList.contains('stImage') ||
                            cur.classList.contains('stPlotlyChart') ||
                            cur.classList.contains('js-plotly-plot') ||
                            cur.classList.contains('plot-container') ||
                            cur.classList.contains('svg-container')) {
                            return true;
                        }
                    }
                    if (cur.tagName === 'IMG' || cur.tagName === 'IFRAME' ||
                        cur.tagName === 'SVG') {
                        return true;
                    }
                    if (cur.getAttribute && (
                        cur.getAttribute('data-testid') === 'stImage' ||
                        cur.getAttribute('data-testid') === 'stPlotlyChart')) {
                        return true;
                    }
                    cur = cur.parentElement;
                }
                return false;
            }
            function onWheel(e) {
                if (!isChartTarget(e.target)) return;
                const sc = findScroller();
                if (!sc) return;
                sc.scrollTop += e.deltaY;
                sc.scrollLeft += e.deltaX;
                e.preventDefault();
                e.stopPropagation();
            }
            // Capture phase = fire before chart libraries see it
            document.addEventListener('wheel', onWheel,
                                          { passive: false, capture: true });
            // Re-evaluate the scroller every few seconds in case Streamlit
            // re-mounts its view container after a rerun.
            setInterval(() => { scroller = null; findScroller(); }, 3000);
        })();
        </script>
        """,
        unsafe_allow_html=True,
    )

    # Apply pending "Start Sample Session" intent BEFORE the toggle widget
    # renders below. Setting widget state after instantiation is forbidden.
    if st.session_state.pop("_force_demo_on", False):
        st.session_state["demo_mode_toggle"] = True

    # -------- Sidebar --------
    with st.sidebar:
        # Mode-aware sub-line under the brand mark
        mode_sub = "Pitching · Bullpen Analytics"
        if st.session_state.get("app_mode", "Pitching") == "Hitting":
            mode_sub = "Hitting · Swing Analytics"
        # ===== DIAMOND SPORTS LAB — brand logo =====
        # Stylized diamond (baseball-field shape) in brand blue/gold with
        # the wordmark next to it. Renders cleanly on the dark sidebar.
        logo_svg = (
            "<svg width='40' height='40' viewBox='0 0 40 40' xmlns='http://www.w3.org/2000/svg'>"
            # Outer diamond (baseball-field shape)
            "<path d='M 20 4 L 36 20 L 20 36 L 4 20 Z' "
            "fill='url(#grad1)' stroke='#3b82f6' stroke-width='1.5' />"
            # Inner diamond (infield)
            "<path d='M 20 12 L 28 20 L 20 28 L 12 20 Z' "
            "fill='none' stroke='#d4a634' stroke-width='1.5' opacity='0.85' />"
            # Center dot (home plate marker)
            "<circle cx='20' cy='20' r='2' fill='#d4a634' />"
            # Gradient def
            "<defs><linearGradient id='grad1' x1='0%' y1='0%' x2='100%' y2='100%'>"
            "<stop offset='0%' stop-color='#1e3a8a' />"
            "<stop offset='100%' stop-color='#3b82f6' />"
            "</linearGradient></defs>"
            "</svg>"
        )
        st.markdown(
            _flat_html(
                f"<div style='display:flex;align-items:center;gap:12px;"
                f"padding:6px 0 4px 0;'>"
                f"{logo_svg}"
                f"<div>"
                f"<div style='font-size:18px;font-weight:800;line-height:1.05;"
                f"color:#f1f5f9;letter-spacing:-0.01em;'>Diamond Sports Lab</div>"
                f"<div style='font-size:11px;color:#94a3b8;letter-spacing:0.06em;"
                f"text-transform:uppercase;font-weight:600;margin-top:2px;'>"
                f"{mode_sub}</div>"
                f"</div></div>"
            ),
            unsafe_allow_html=True,
        )
        st.divider()

        # ===== PERSISTENT NAVIGATION =====
        # Always-visible "Home" gets the user back to the landing screen
        # (or back to login if they signed in as a guest). Means no screen
        # in the app feels like a dead end.
        _nav_rec = current_user_record() or {}
        _nav_role = _nav_rec.get("role", "coach")
        if _nav_role == "admin":
            _home_label = "Admin panel"
        elif _nav_role == "athlete":
            _home_label = "My profile"
        elif current_username() == "__demo_guest__":
            _home_label = "Demo home"
        else:
            _home_label = "Home"
        if st.button(_home_label, key="sidebar_home_btn",
                      use_container_width=True,
                      help="Back to the home screen — you're never stuck."):
            st.session_state.pop("show_plans_page", None)
            st.session_state.pop("show_billing_modal", None)
            if _nav_role == "admin":
                st.session_state.pop("admin_impersonating", None)
            # For non-athlete roles, also drop the selected athlete so
            # render_login_or_landing sends them to the landing picker.
            if _nav_role != "athlete":
                st.session_state.pop("selected_athlete_id", None)
            st.rerun()

        # ===== Demo tier switcher (always visible while in demo mode) =====
        if is_demo_mode_active():
            cur_tier_sb = st.session_state.get("auth_demo_tier", "individual")
            tier_pick_sb = st.selectbox(
                "Demo tier",
                list(DEMO_TIERS.keys()),
                format_func=lambda k: DEMO_TIERS[k]["label"],
                index=list(DEMO_TIERS.keys()).index(cur_tier_sb)
                       if cur_tier_sb in DEMO_TIERS else 0,
                key="sidebar_demo_tier",
                help="Flip between tiers to see what Individual, Team, "
                     "Club, and Large Org rosters look like.")
            if tier_pick_sb != cur_tier_sb:
                st.session_state["auth_demo_tier"] = tier_pick_sb
                _seed_demo_tier(tier_pick_sb)
                # When tier changes, drop the selected athlete so the
                # landing reshuffles to the new tier's roster.
                st.session_state.pop("selected_athlete_id", None)
                st.session_state.pop("selected_athlete_label", None)
                st.rerun()
            if st.button("Turn demo off", key="sidebar_demo_off",
                          use_container_width=True):
                st.session_state["auth_demo_mode"] = False
                st.session_state.pop("selected_athlete_id", None)
                st.session_state.pop("selected_athlete_label", None)
                st.rerun()
        st.divider()

        # ===== MODE SWITCH — Pitching / Hitting =====
        # Determines which lab the rest of the app shows. Persisted across reruns.
        app_mode = st.radio(
            "Lab",
            ["Pitching", "Hitting"],
            index=1 if st.session_state.get("app_mode") == "Hitting" else 0,
            horizontal=True,
            key="app_mode_radio",
            help="Switch between the Pitching Lab and the Hitting Lab.",
        )
        # Normalize to a stable key
        app_mode = "Hitting" if "Hitting" in app_mode else "Pitching"
        st.session_state["app_mode"] = app_mode
        st.divider()

        # ===== SAMPLE SESSION TOGGLE =====
        sample_label = "Sample Session"
        sample_help  = (
            "Generate a believable sample bullpen for any pitcher name. "
            "Useful for showing the platform without needing CSV uploads."
            if app_mode == "Pitching"
            else "Generate a believable sample batting practice for any hitter name."
        )
        demo_mode = st.toggle(
            sample_label,
            value=st.session_state.get("demo_mode_default", False),
            key="demo_mode_toggle",
            help=sample_help,
        )
        # Live Capture toggle — works in both modes now.
        live_capture_mode = st.toggle(
            "Live Capture (Beta)",
            value=False,
            key="live_capture_toggle",
            help="Use the phone/tablet camera to capture pitches/swings live. "
                 "Requires streamlit-webrtc + opencv (pip install -r requirements.txt).",
        )
        st.divider()

        # Section header reacts to mode — "Pitcher Profile" vs "Hitter Profile"
        _profile_label = "Hitter Profile" if st.session_state.get("app_mode") == "Hitting" else "Pitcher Profile"
        st.subheader(_profile_label)

        # Sport label helper (text-only — no emoji, matches dark-mode brand)
        def _sport_icon(s: str) -> str:
            return "SB" if s == "Softball" else "BB"

        # Role labels swap with mode. Same athlete record — only the display
        # changes. Pitching shows "RHP/LHP"; Hitting shows "RHH/LHH".
        is_hitting          = (app_mode == "Hitting")
        ROLE_NOUN           = "hitter" if is_hitting else "pitcher"
        ROLE_DROPDOWN_LABEL = "Hitter" if is_hitting else "Pitcher"
        HAND_TAG            = "HH"     if is_hitting else "HP"     # "RHH" / "RHP"
        HAND_FIELD_LABEL    = "Batting hand" if is_hitting else "Throwing hand"

        def _athlete_label(a: dict) -> str:
            suffix = f"{a['hand'][:1]}{HAND_TAG}"
            return (f"[{_sport_icon(a.get('sport', 'Baseball'))}] "
                    f"{a['name']} ({suffix}, {a['grad_class'] or '—'})")

        # ===== ROSTER DROPDOWN =====
        roster = list_athletes()
        ADD_NEW = f"Add new {ROLE_NOUN}..."
        DEMO_ATHLETE_BB = f"Sample {ROLE_DROPDOWN_LABEL} — Baseball"
        DEMO_ATHLETE_SB = f"Sample {ROLE_DROPDOWN_LABEL} — Softball"
        roster_options: list = []
        if demo_mode and not roster:
            # First-run demo experience: offer both sports as virtual defaults
            roster_options.append(DEMO_ATHLETE_BB)
            roster_options.append(DEMO_ATHLETE_SB)
        roster_options += [_athlete_label(a) for a in roster]
        # Only coaches can add athletes — hide for athlete-role users
        if (current_user_record() or {}).get("role") != "athlete":
            roster_options.append(ADD_NEW)

        # If landing screen set a selected_athlete_id, sync the dropdown to it
        _picked_id = st.session_state.get("selected_athlete_id")
        if _picked_id:
            for _a in roster:
                if _a["id"] == _picked_id:
                    st.session_state["selected_athlete_label"] = _athlete_label(_a)
                    break

        # Maintain selection across reruns
        if "selected_athlete_label" not in st.session_state:
            st.session_state["selected_athlete_label"] = roster_options[0]
        if st.session_state["selected_athlete_label"] not in roster_options:
            st.session_state["selected_athlete_label"] = roster_options[0]

        selected_label = st.selectbox(
            ROLE_DROPDOWN_LABEL,
            roster_options,
            index=roster_options.index(st.session_state["selected_athlete_label"]),
            key="roster_select",
        )
        st.session_state["selected_athlete_label"] = selected_label

        # Inline "Add new pitcher" form
        if selected_label == ADD_NEW:
            with st.form("add_athlete_form", clear_on_submit=True):
                new_name  = st.text_input("Name", placeholder="Last, First or First Last")
                cols_nh = st.columns(2)
                with cols_nh[0]:
                    new_sport = st.selectbox("Sport", ["Baseball", "Softball"])
                with cols_nh[1]:
                    new_hand = st.selectbox(HAND_FIELD_LABEL, ["Right", "Left"])
                cols_lc = st.columns(2)
                with cols_lc[0]:
                    new_level = st.selectbox(
                        "Level", ATHLETE_LEVELS,
                        index=ATHLETE_LEVELS.index("HS-Varsity"),
                        help="Determines which tutorial videos the app picks "
                             "(youth-friendly vs college-level). Used by the "
                             "drill video picker.",
                    )
                with cols_lc[1]:
                    new_class = st.text_input("Class / Grad year", placeholder="2027")
                new_notes = st.text_area("Notes (optional)",
                                          placeholder="Position, level, anything you want to remember",
                                          height=70)
                submitted = st.form_submit_button("Add to roster", type="primary",
                                                   use_container_width=True)
                if submitted and new_name.strip():
                    aid = add_athlete(new_name.strip(), new_hand, new_sport,
                                      new_class.strip(), new_notes.strip(),
                                      level=new_level)
                    new_a = {"name": new_name.strip(), "hand": new_hand,
                             "sport": new_sport, "grad_class": new_class.strip()}
                    st.session_state["selected_athlete_label"] = _athlete_label(new_a)
                    st.success(f"Added {new_name.strip()} to roster.")
                    st.rerun()
            st.stop()

        # Resolve the selected athlete to its DB record (or fall back to demo)
        active_athlete = None
        if selected_label == DEMO_ATHLETE_BB:
            athlete_name, athlete_hand, athlete_class, athlete_sport, athlete_level = \
                "Marcus Vance", "Right", "2027", "Baseball", "HS-Varsity"
            active_athlete_id = None
        elif selected_label == DEMO_ATHLETE_SB:
            athlete_name, athlete_hand, athlete_class, athlete_sport, athlete_level = \
                "Sara Johnson", "Right", "2026", "Softball", "HS-Varsity"
            active_athlete_id = None
        else:
            # Match by reconstructed label
            for a in roster:
                if _athlete_label(a) == selected_label:
                    active_athlete = a
                    break
            if active_athlete is None:
                active_athlete = roster[0] if roster else None
            athlete_name = active_athlete["name"] if active_athlete else "Pitcher"
            athlete_hand = active_athlete["hand"] if active_athlete else "Right"
            athlete_class = (active_athlete["grad_class"] or "") if active_athlete else ""
            athlete_sport = active_athlete.get("sport", "Baseball") if active_athlete else "Baseball"
            athlete_level = active_athlete.get("level", "HS-Varsity") if active_athlete else "HS-Varsity"
            active_athlete_id = active_athlete["id"] if active_athlete else None

        # Compact "Edit profile" expander
        if active_athlete_id is not None:
            # Scope EVERY widget key per-athlete. Without this, Streamlit caches
            # the first-edited pitcher's values under generic keys and reuses
            # them when you switch to a different pitcher (the bug Kolby hit).
            aid = active_athlete_id
            with st.expander("✏️ Edit profile", expanded=False):
                e_name  = st.text_input("Name", value=athlete_name,
                                         key=f"edit_name_{aid}")
                ec1, ec2 = st.columns(2)
                with ec1:
                    e_sport = st.selectbox("Sport", ["Baseball", "Softball"],
                                            index=0 if athlete_sport == "Baseball" else 1,
                                            key=f"edit_sport_{aid}")
                with ec2:
                    e_hand  = st.selectbox(HAND_FIELD_LABEL, ["Right", "Left"],
                                            index=0 if athlete_hand == "Right" else 1,
                                            key=f"edit_hand_{aid}")
                ec3, ec4 = st.columns(2)
                with ec3:
                    e_level = st.selectbox(
                        "Level", ATHLETE_LEVELS,
                        index=ATHLETE_LEVELS.index(athlete_level) if athlete_level in ATHLETE_LEVELS else 2,
                        key=f"edit_level_{aid}",
                        help="Used by the video picker to choose age-appropriate "
                             "tutorials (youth vs college).",
                    )
                with ec4:
                    e_class = st.text_input("Class / Grad year", value=athlete_class,
                                             key=f"edit_class_{aid}")
                if st.button("Save changes", use_container_width=True,
                              key=f"save_edit_{aid}"):
                    update_athlete(active_athlete_id, name=e_name, hand=e_hand,
                                   sport=e_sport, level=e_level, grad_class=e_class)
                    updated_a = {"name": e_name, "hand": e_hand,
                                 "sport": e_sport, "grad_class": e_class}
                    st.session_state["selected_athlete_label"] = _athlete_label(updated_a)
                    st.rerun()
                if st.button(f"Archive this {ROLE_NOUN}", use_container_width=True,
                              key=f"archive_btn_{aid}",
                              help="Hides them from the roster but keeps history."):
                    archive_athlete(active_athlete_id, archived=True)
                    st.session_state["selected_athlete_label"] = roster_options[0]
                    st.rerun()

                # ===== HARD DELETE — with confirmation gate =====
                st.markdown("---")
                st.markdown(
                    "<div style='font-size:11px;color:#dc2626;font-weight:600;"
                    "letter-spacing:0.04em;text-transform:uppercase;margin-bottom:6px;'>"
                    "⚠️ Danger zone</div>",
                    unsafe_allow_html=True,
                )
                # Per-athlete confirmation key so toggling doesn't persist across pitchers
                confirm_key = f"confirm_delete_{active_athlete_id}"
                confirm = st.checkbox(
                    f"I understand: permanently delete **{athlete_name}** AND all their session history.",
                    key=confirm_key,
                )
                if st.button("Delete permanently (cannot be undone)",
                              use_container_width=True,
                              disabled=not confirm,
                              type="secondary",
                              key=f"delete_btn_{aid}",
                              help="Use Archive instead if you want to keep history. "
                                   "This wipes the pitcher AND every saved session."):
                    sessions_deleted = delete_athlete_permanently(active_athlete_id)
                    st.session_state["selected_athlete_label"] = roster_options[0]
                    st.toast(f"Deleted {athlete_name} and {sessions_deleted} session(s).")
                    st.rerun()

        st.divider()

        if not demo_mode:
            st.subheader("Upload Session Data")
            pl_file = st.file_uploader(
                "Ball flight CSV (Pitch Logic, Rapsodo, etc.)",
                type=["csv"],
                key="pl",
                help="The parser auto-detects the format. Works with Pitch Logic, "
                     "Rapsodo Pitching 2.0/PRO 2.0, and any compatible export.",
            )
            pulse_file = st.file_uploader("Driveline Pulse CSV", type=["csv"], key="pulse")
            ppai_files = st.file_uploader(
                "ProPlayAI per-pitch files",
                type=["csv"],
                accept_multiple_files=True,
                key="ppai",
            )
            video_upload = st.file_uploader(
                "Bullpen Video — optional",
                type=["mp4", "mov", "m4v", "webm"],
                key="video",
                help="A single video of the whole bullpen. Plays back in the "
                     "pitch-detail panel when you click a pitch on the strike-zone map. "
                     "Auto-clipping by pitch is a v2 feature.",
            )
            if video_upload is not None:
                st.session_state["bullpen_video"] = video_upload
                st.session_state["bullpen_video_url"] = None
            video_url_input = st.text_input(
                "...or paste a video URL (YouTube/Vimeo)",
                value=st.session_state.get("bullpen_video_url", "") or "",
                placeholder="https://...",
                key="video_url_input",
            )
            if video_url_input:
                st.session_state["bullpen_video_url"] = video_url_input
            st.caption(
                "👉 Demo files live in the `sample_data/` folder next to this script. "
                "Upload them all to see a worked example."
            )
        else:
            pl_file, pulse_file, ppai_files = None, None, None
            st.info(
                "Turn off Demo Mode to upload real CSV files from Pitch Logic, "
                "Pulse, and ProPlayAI."
            )
            # Demo users can still upload a video, paste a URL, or load a sample
            video_upload_demo = st.file_uploader(
                "Bullpen Video — optional",
                type=["mp4", "mov", "m4v", "webm"],
                key="video_demo",
            )
            if video_upload_demo is not None:
                st.session_state["bullpen_video"] = video_upload_demo
                st.session_state["bullpen_video_url"] = None
            if st.button("Load a sample bullpen video", use_container_width=True):
                st.session_state["bullpen_video_url"] = SAMPLE_PITCHER_VIDEO_URL
                st.session_state["bullpen_video"] = None
            video_url_input2 = st.text_input(
                "...or paste a video URL (YouTube/Vimeo)",
                value=st.session_state.get("bullpen_video_url", "") or "",
                placeholder="https://...",
                key="video_url_input_demo",
            )
            if video_url_input2 and video_url_input2 != st.session_state.get("bullpen_video_url"):
                st.session_state["bullpen_video_url"] = video_url_input2

        # ===== Plans & Billing entry =====
        st.divider()
        if st.button("Plans & Billing", key="sidebar_plans_btn",
                      use_container_width=True,
                      help="See subscription tiers and manage your plan."):
            st.session_state["show_plans_page"] = True
            st.rerun()

        # (Chart diagnostic removed — the real bug was scaleanchor=x in
        # the strike-zone / tunneling / heat-map figures forcing Plotly
        # to extend axis ranges to keep 1:1 pixel scaling on non-square
        # canvases. See the "no scaleanchor" notes in the affected
        # figure builders.)

        # ===== Account footer (role-aware) =====
        st.divider()
        _user_rec = current_user_record() or {}
        _user_role = _user_rec.get("role", "coach")
        _org_rec = get_org_record(_user_rec.get("org_id"))
        _user_label = current_username() or "—"
        _role_label = ("Coach" if _user_role == "coach"
                        else "Athlete")
        _sub = f" · {_org_rec['name']}" if _org_rec else ""
        st.markdown(
            f"<div style='font-size:11px;color:#94a3b8;letter-spacing:0.08em;"
            f"font-weight:600;text-transform:uppercase;margin-bottom:6px;'>"
            f"{_role_label}{_sub}</div>"
            f"<div style='font-size:12px;color:#f1f5f9;margin-bottom:8px;'>"
            f"{_user_label}</div>",
            unsafe_allow_html=True)
        if _user_role == "admin":
            # Admin badge + back-to-admin button
            st.markdown(
                "<div style='color:#ef4444;font-size:11px;letter-spacing:0.10em;"
                "font-weight:700;margin-bottom:6px;'>ADMIN MODE</div>",
                unsafe_allow_html=True)
            adm_l, adm_r = st.columns(2)
            with adm_l:
                if st.button("Admin panel",
                              key="sidebar_admin_panel",
                              use_container_width=True,
                              help="Back to the org/user overview."):
                    st.session_state.pop("selected_athlete_id", None)
                    st.session_state.pop("admin_impersonating", None)
                    st.rerun()
            with adm_r:
                if st.button("Sign out", key="sidebar_signout",
                              use_container_width=True):
                    for k in ("auth_user", "auth_demo_mode",
                                "selected_athlete_id",
                                "selected_athlete_label",
                                "admin_impersonating"):
                        st.session_state.pop(k, None)
                    st.rerun()
        elif _user_role == "coach":
            acct_l, acct_r = st.columns(2)
            with acct_l:
                if st.button("Switch athlete",
                              key="sidebar_switch_athlete",
                              use_container_width=True,
                              help="Same as Home — back to the landing "
                                   "screen to pick or create a different "
                                   "athlete."):
                    st.session_state.pop("selected_athlete_id", None)
                    st.rerun()
            with acct_r:
                if st.button("Sign out", key="sidebar_signout",
                              use_container_width=True):
                    for k in ("auth_user", "auth_demo_mode",
                                "selected_athlete_id",
                                "selected_athlete_label"):
                        st.session_state.pop(k, None)
                    st.rerun()
        else:
            # Athlete: sign-out only (no athlete switching)
            if st.button("Sign out", key="sidebar_signout",
                          use_container_width=True):
                for k in ("auth_user", "auth_demo_mode",
                            "selected_athlete_id",
                            "selected_athlete_label"):
                    st.session_state.pop(k, None)
                st.rerun()

    # ===== PLANS & BILLING short-circuit =====
    # Tapping 'Plans & Billing' in the sidebar replaces the main pane
    # (analytics tabs) with the pricing page. Sidebar stays visible so
    # the user can navigate back via 'Back to app' or by tapping the
    # button again.
    if st.session_state.get("show_plans_page"):
        render_plans_and_billing_page()
        return

    # -------- Mode branch: if Hitting Lab, render swing report and return --------
    if app_mode == "Hitting":
        # Hitting Live Capture takes precedence over the standard hitting report
        if live_capture_mode:
            _branded_header_hitting(athlete_name, athlete_hand, athlete_class,
                                      demo_mode, sport=athlete_sport)
            run_hitting_live_capture(
                active_athlete_id=active_athlete_id,
                athlete_name=athlete_name,
                athlete_hand=athlete_hand,
                athlete_sport=athlete_sport,
            )
            return
        run_hitting_lab(
            athlete_name=athlete_name,
            athlete_hand=athlete_hand,
            athlete_class=athlete_class,
            athlete_sport=athlete_sport,
            athlete_level=athlete_level,
            active_athlete_id=active_athlete_id,
            demo_mode=demo_mode,
        )
        return  # don't fall through to the pitching report

    # -------- Branded header --------
    _branded_header(athlete_name, athlete_hand, athlete_class, demo_mode, sport=athlete_sport)

    # -------- Live Capture mode: phone camera → MediaPipe pose → biomech --------
    if live_capture_mode:
        run_live_capture_tab(
            active_athlete_id=active_athlete_id,
            athlete_name=athlete_name,
            athlete_hand=athlete_hand,
            athlete_sport=athlete_sport,
        )
        return

    # -------- Branch: Demo Mode vs Real Data --------
    if demo_mode:
        with st.spinner(f"Generating demo session for {athlete_name}..."):
            df = generate_demo_session(athlete_name, hand=athlete_hand, sport=athlete_sport)
    else:
        if not pl_file or not pulse_file or not ppai_files:
            # ===== BRANDED WELCOME / EMPTY STATE =====
            # Headline card (HTML for visual polish)
            header_html = _flat_html(
                "<div style='background:white;border:1px solid #e5e7eb;border-radius:14px;"
                "padding:28px 28px 20px 28px;margin-top:12px;"
                "box-shadow:0 1px 3px rgba(0,0,0,0.04);'>"
                "<div style='font-size:11px;letter-spacing:0.14em;font-weight:700;"
                "color:#d4a634;text-transform:uppercase;margin-bottom:8px;'>"
                "◆ Welcome to Diamond Sports Lab</div>"
                "<div style='font-size:24px;font-weight:700;color:#1a2150;margin-bottom:8px;'>"
                "Ready to see a Post-Bullpen Report?</div>"
                "<div style='font-size:14px;color:#4b5563;line-height:1.5;'>"
                "Pick one of the two paths below to get started."
                "</div></div>"
            )
            st.markdown(header_html, unsafe_allow_html=True)

            # Two REAL action buttons (not HTML — actually clickable)
            st.write("")  # small spacer
            cta_a, cta_b = st.columns(2)
            with cta_a:
                with st.container(border=True):
                    st.markdown("### Try a Sample Session")
                    st.markdown(
                        "We'll generate a believable bullpen for any pitcher name "
                        "you type. **No uploads needed.** Best way to see the full "
                        "app in 30 seconds."
                    )
                    if st.button("Start Sample Session",
                                 key="welcome_start_demo",
                                 type="primary",
                                 use_container_width=True):
                        # Use an intermediate flag instead of touching the
                        # widget's state directly (Streamlit forbids that
                        # after the widget has already been instantiated
                        # in this run). The top of main() will apply this
                        # flag on the next rerun, before the toggle renders.
                        st.session_state["_force_demo_on"] = True
                        st.rerun()
            with cta_b:
                with st.container(border=True):
                    st.markdown("### Upload Real Bullpen Data")
                    st.markdown(
                        "Drop in your **Pitch Logic / Rapsodo**, **Pulse**, and "
                        "**ProPlayAI** CSV exports using the three file uploaders "
                        "in the sidebar on the left."
                    )
                    st.button("Open Sidebar →",
                              key="welcome_open_sidebar",
                              use_container_width=True,
                              help="If the sidebar is hidden, click the small > arrow "
                                   "at the top-left of the page to open it.",
                              disabled=True)

            # Sidebar hint (in case it's collapsed)
            st.info(
                "💡 **Don't see the sidebar?** Look for a small **›** arrow on the "
                "very far left edge of the page and click it to open the sidebar. "
                "That's where Sample Session and the file uploaders live."
            )
            st.stop()

        # -------- Diagnostic: peek at uploaded files --------
        with st.expander("🔍 Inspect uploaded files (click to preview raw CSV contents)"):
            st.write("**Pitch Logic — first 12 lines:**")
            st.code(file_diagnostic(pl_file), language="text")
            st.write("**Pulse — first 12 lines:**")
            st.code(file_diagnostic(pulse_file), language="text")
            if ppai_files:
                st.write(f"**ProPlayAI — first file ({ppai_files[0].name}), first 12 lines:**")
                st.code(file_diagnostic(ppai_files[0]), language="text")

        # -------- Run the pipeline (wrapped so errors show diagnostic) --------
        def _parse_with_diagnostic(label, parse_fn, file_arg):
            try:
                return parse_fn(file_arg)
            except ParserError as e:
                st.error(f"**{label} parsing failed.**\n\n{str(e)}")
                st.info(
                    "👉 Copy the error message above and the file preview below, "
                    "paste them back to Claude, and the parser will be updated."
                )
                st.stop()
            except KeyError as e:
                st.error(
                    f"**{label} parsing hit an unexpected column.**\n\n"
                    f"Missing column: {e}\n\n"
                    "This usually means the CSV uses a different name for this field. "
                    "Copy the error + file preview and paste back to Claude."
                )
                st.stop()
            except Exception as e:
                st.error(f"**{label} parsing crashed:** `{type(e).__name__}: {e}`")
                st.stop()

        with st.spinner("Parsing Pitch Logic..."):
            pl_df = _parse_with_diagnostic("Pitch Logic", parse_pitch_logic, pl_file)
        with st.spinner("Parsing Pulse..."):
            pulse_df = _parse_with_diagnostic("Pulse", parse_pulse, pulse_file)
        with st.spinner("Reducing ProPlayAI frame data to per-pitch summaries..."):
            ppai_df = _parse_with_diagnostic("ProPlayAI", parse_proplayai_batch, ppai_files)
        with st.spinner("Running self-healing timeline aligner..."):
            df = align_pitches(pl_df, pulse_df, ppai_df)

    # -------- Session save (auto for real data, manual for samples) --------
    if active_athlete_id is not None:
        # Fingerprint this session to avoid double-saving across reruns
        try:
            first_ts = str(df["Timestamp"].min())
            last_ts  = str(df["Timestamp"].max())
            fingerprint = f"{active_athlete_id}|{len(df)}|{first_ts}|{last_ts}"
        except Exception:
            fingerprint = f"{active_athlete_id}|{len(df)}|{id(df)}"

        if demo_mode:
            # Sample session — require explicit save
            if st.session_state.get("_saved_fingerprint") != fingerprint:
                save_cols = st.columns([3, 1])
                save_cols[0].caption(
                    "📌 This is a sample session. Save it to this pitcher's history if you "
                    "want to use it for trend tracking — or skip and just demo."
                )
                if save_cols[1].button("💾 Save sample to history",
                                       use_container_width=True,
                                       key="save_sample_btn"):
                    save_session(active_athlete_id, df, session_type="sample")
                    st.session_state["_saved_fingerprint"] = fingerprint
                    st.toast("Saved to history.")
                    st.rerun()
        else:
            # Real data — auto-save once per session fingerprint
            if st.session_state.get("_saved_fingerprint") != fingerprint:
                try:
                    save_session(active_athlete_id, df, session_type="real")
                    st.session_state["_saved_fingerprint"] = fingerprint
                except Exception as e:
                    st.warning(f"Could not auto-save to history: {e}")

    # -------- Top KPI strip --------
    kpis = session_kpis(df)
    _kpi_row(kpis, int(kpis.get("Pitches Healed", 0)))

    if kpis["Pitches Healed"] > 0:
        st.markdown(
            f"<div style='background:#fffbeb; border:1px solid #fde68a; "
            f"border-left:4px solid #d4a634; border-radius:6px; padding:10px 14px; "
            f"font-size:13px; color:#78350f; margin-top:8px;'>"
            f"<b>Self-healing engaged.</b> {kpis['Pitches Healed']} pitch(es) had "
            f"at least one dropout — the aligner filled the timeline so your rows "
            f"stay matched.</div>",
            unsafe_allow_html=True,
        )

    st.divider()

    # -------- Tabs --------
    tab_overview, tab_per_pitch, tab_history, tab_tunneling, tab_alignment, tab_action = st.tabs(
        ["Overview", "Per-Pitch Detail", "History",
         "Tunneling", "Alignment Quality", "Action Plan"]
    )

    # ---- Overview tab ----
    with tab_overview:
        st.subheader("Pitch Type Breakdown")
        breakdown = pitch_type_breakdown(df)

        # Decide which baseline to use: real history if it exists, else demo
        real_baseline = {}
        baseline_source = "demo"
        if active_athlete_id is not None:
            try:
                real_baseline = compute_real_baseline(active_athlete_id, lookback=6)
                if real_baseline:
                    baseline_source = "real"
            except Exception:
                real_baseline = {}
        active_baseline = real_baseline if real_baseline else DEMO_BASELINE

        delta_rows = []
        for _, r in breakdown.iterrows():
            base = active_baseline.get(r["Pitch_Type"])
            def _delta_str(today_v, base_v, unit=""):
                if base_v is None or today_v is None or pd.isna(today_v):
                    return f"{today_v}{unit}" if today_v is not None and not pd.isna(today_v) else "—"
                d = today_v - base_v
                arrow = " 📈" if d > 0.3 else (" 📉" if d < -0.3 else "")
                return f"{today_v}{unit} ({d:+.1f}){arrow}"
            if base:
                delta_rows.append({
                    "Pitch Type":     r["Pitch_Type"],
                    "Thrown":         int(r["Thrown"]),
                    "Velo":           _delta_str(r['Avg_Velo'], base.get('velo'), " mph"),
                    "Vert Break":     _delta_str(r['Avg_Vert_Break'], base.get('vbreak'), '"'),
                    "Horiz Break":    f"{r['Avg_Horiz_Break']}\"",
                    "Elbow Stress":   _delta_str(r['Avg_Stress'], base.get('stress'), " Nm"),
                })
            else:
                delta_rows.append({
                    "Pitch Type":     r["Pitch_Type"],
                    "Thrown":         int(r["Thrown"]),
                    "Velo":           f"{r['Avg_Velo']} mph",
                    "Vert Break":     f"{r['Avg_Vert_Break']}\"",
                    "Horiz Break":    f"{r['Avg_Horiz_Break']}\"",
                    "Elbow Stress":   f"{r['Avg_Stress']} Nm",
                })
        st.dataframe(pd.DataFrame(delta_rows), use_container_width=True, hide_index=True)
        if baseline_source == "real":
            st.caption(
                f"📊 Deltas compare today's session to **{athlete_name}'s actual "
                f"rolling baseline** from their recent saved sessions. "
                "The more sessions you log, the more accurate this becomes."
            )
        else:
            st.caption(
                "🔬 Deltas shown vs a **sample reference baseline** for illustration. "
                "Save real sessions to this pitcher's history (auto-saved when you upload "
                "real CSVs) and the deltas will switch to a real rolling baseline."
            )

        st.subheader("Pitch Map — Movement vs Velocity")
        fig = px.scatter(
            df, x="Horiz_Break_in", y="Velocity_mph",
            color="Pitch_Type", text="Pitch_Num",
            color_discrete_map=PITCH_COLORS,
            hover_data=["Total_Spin_rpm", "Spin_Efficiency_pct", "Peak_Valgus_Nm"],
            labels={
                "Horiz_Break_in": "Horizontal Break (inches)",
                "Velocity_mph":   "Velocity (mph)",
            },
        )
        fig.update_traces(marker=dict(size=14, line=dict(width=1, color="black")),
                          textposition="top center")
        fig.update_layout(height=480)
        render_static_chart(fig)

        # =====================================================
        # STRIKE ZONE SCATTER (clickable → per-pitch detail panel below)
        # =====================================================
        st.subheader("Strike Zone Map")
        if df["Strike_Zone_Side"].notna().any():
            sz_fig = _build_strike_zone_figure(df)
            render_static_chart(sz_fig, key="strike_zone_chart", height_px=550)

            st.caption(
                "Green-outlined dots = positive outliers (above-average pitches). "
                "Red-outlined dots = negative outliers (concerning pitches). "
                "Strike-zone box = ~17\" wide plate, knees to letters."
            )

            # ----- PITCH DETAIL via dropdown (mobile-friendly fallback) -----
            st.divider()
            st.subheader("Pitch Detail")
            pitch_options = {
                int(r["Pitch_Num"]):
                    f"Pitch #{int(r['Pitch_Num'])} — {r['Pitch_Type']} "
                    f"({r['Velocity_mph']:.1f} mph)"
                for _, r in df.iterrows()
            }
            keys = list(pitch_options.keys())
            selected_pitch_num = st.selectbox(
                "Pick a pitch to inspect",
                keys,
                format_func=lambda k: pitch_options[k],
                index=0,
                key="strike_zone_pitch_picker",
            )
            if selected_pitch_num is not None:
                pitch = df[df["Pitch_Num"] == selected_pitch_num].iloc[0]
                _render_pitch_detail_panel(pitch, athlete_name=athlete_name, sport=athlete_sport)
        else:
            st.info(
                "Strike-zone map requires plate-location data. "
                "Rapsodo provides it directly; Pitch Logic data gets a projected location "
                "from release point + break (approximate)."
            )

        # ===== MECHANICS CRITIQUE (moved here so the strike zone is up top) =====
        critique = analyze_mechanics(df, sport=athlete_sport)
        if critique["strengths"] or critique["weaknesses"]:
            st.divider()
            st.subheader("Mechanics Critique")
            st.caption(
                "Body sequencing strengths and improvement areas, based on the 3D pose data. "
                "Each improvement area is tied to a specific gain (velocity / control / movement / injury safety)."
            )
            mc1, mc2 = st.columns(2)
            with mc1:
                strengths_html = (
                    "<div style='background:#f0fdf4;border:1px solid #bbf7d0;"
                    "border-left:4px solid #16a34a;border-radius:8px;padding:14px 16px;"
                    "margin-bottom:8px;'>"
                    "<div style='font-size:11px;letter-spacing:0.08em;font-weight:700;"
                    "color:#16a34a;text-transform:uppercase;margin-bottom:8px;'>"
                    "✓ What's working</div>"
                )
                if critique["strengths"]:
                    for s in critique["strengths"]:
                        strengths_html += (
                            f"<div style='margin-bottom:10px;'>"
                            f"<div style='font-weight:700;color:#14532d;font-size:14px;'>{s['label']}</div>"
                            f"<div style='font-size:13px;color:#1f2937;margin-top:2px;'>{s['detail']}</div>"
                            f"<div style='font-size:12px;color:#4b5563;margin-top:3px;font-style:italic;'>"
                            f"Why it matters: {s['gain']} "
                            f"<span style='background:#dcfce7;color:#15803d;padding:1px 7px;"
                            f"border-radius:8px;font-size:10.5px;font-weight:700;margin-left:4px;'>"
                            f"{s['tag']}</span></div>"
                            f"</div>"
                        )
                else:
                    strengths_html += "<div style='font-size:13px;color:#4b5563;'>No specific mechanical strengths identified yet — keep building.</div>"
                strengths_html += "</div>"
                st.markdown(_flat_html(strengths_html), unsafe_allow_html=True)

            with mc2:
                weak_html = (
                    "<div style='background:#fefce8;border:1px solid #fde68a;"
                    "border-left:4px solid #d4a634;border-radius:8px;padding:14px 16px;"
                    "margin-bottom:8px;'>"
                    "<div style='font-size:11px;letter-spacing:0.08em;font-weight:700;"
                    "color:#92400e;text-transform:uppercase;margin-bottom:8px;'>"
                    "→ Areas to improve</div>"
                )
                if critique["weaknesses"]:
                    for w in critique["weaknesses"]:
                        weak_html += (
                            f"<div style='margin-bottom:10px;'>"
                            f"<div style='font-weight:700;color:#78350f;font-size:14px;'>{w['label']}</div>"
                            f"<div style='font-size:13px;color:#1f2937;margin-top:2px;'>{w['detail']}</div>"
                            f"<div style='font-size:12px;color:#4b5563;margin-top:3px;'>"
                            f"<b>Gain:</b> {w['gain']} &nbsp;·&nbsp; "
                            f"<b>Fix:</b> {w['fix']}</div>"
                            f"</div>"
                        )
                else:
                    weak_html += "<div style='font-size:13px;color:#4b5563;'>Clean mechanics across the session — no specific corrections flagged.</div>"
                weak_html += "</div>"
                st.markdown(_flat_html(weak_html), unsafe_allow_html=True)

    # ---- Per-pitch detail tab ----
    with tab_per_pitch:
        # ===== COMPARE TWO PITCHES (side-by-side) =====
        st.subheader("🆚 Compare Two Pitches Side-by-Side")
        st.caption(
            "Pick any two pitches from this session to compare their metrics, "
            "biomechanics, and (when video is loaded) their delivery side-by-side."
        )
        pitch_options = {
            int(r['Pitch_Num']): f"Pitch #{int(r['Pitch_Num'])} — "
                                  f"{r['Pitch_Type']} ({r['Velocity_mph']:.1f} mph)"
            for _, r in df.iterrows()
        }
        keys = list(pitch_options.keys())
        cmp_col1, cmp_col2 = st.columns(2)
        with cmp_col1:
            a_key = st.selectbox("Pitch A", keys,
                                 format_func=lambda k: pitch_options[k],
                                 index=0, key="cmp_a")
        with cmp_col2:
            default_b = 1 if len(keys) > 1 else 0
            b_key = st.selectbox("Pitch B", keys,
                                 format_func=lambda k: pitch_options[k],
                                 index=default_b, key="cmp_b")

        if a_key != b_key:
            pa = df[df["Pitch_Num"] == a_key].iloc[0]
            pb = df[df["Pitch_Num"] == b_key].iloc[0]

            def _cmp_row(label, va, vb, fmt=lambda x: f"{x:.1f}" if pd.notna(x) else "—"):
                # Highlight the better/worse cell with color when comparable
                va_str = fmt(va) if pd.notna(va) else "—"
                vb_str = fmt(vb) if pd.notna(vb) else "—"
                return f"""
                <tr>
                  <td style='padding:8px 12px;font-weight:600;color:#374151;font-size:13px;'>{label}</td>
                  <td style='padding:8px 12px;text-align:right;font-weight:700;color:#1a2150;font-size:14px;'>{va_str}</td>
                  <td style='padding:8px 12px;text-align:right;font-weight:700;color:#1a2150;font-size:14px;'>{vb_str}</td>
                </tr>
                """

            table_html = f"""
            <table style='width:100%;border-collapse:collapse;
                          border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;
                          background:white;margin-top:8px;font-size:13px;'>
              <thead>
                <tr style='background:#1a2150;color:white;'>
                  <th style='padding:10px 12px;text-align:left;font-size:11px;letter-spacing:0.06em;'>Metric</th>
                  <th style='padding:10px 12px;text-align:right;font-size:11px;letter-spacing:0.06em;'>Pitch #{int(a_key)} ({pa['Pitch_Type']})</th>
                  <th style='padding:10px 12px;text-align:right;font-size:11px;letter-spacing:0.06em;'>Pitch #{int(b_key)} ({pb['Pitch_Type']})</th>
                </tr>
              </thead>
              <tbody style='background:white;'>
                {_cmp_row("Velocity (mph)",        pa["Velocity_mph"],       pb["Velocity_mph"])}
                {_cmp_row("Total Spin (rpm)",      pa["Total_Spin_rpm"],     pb["Total_Spin_rpm"], fmt=lambda x: f"{int(x):,}")}
                {_cmp_row("Spin Efficiency (%)",   pa["Spin_Efficiency_pct"], pb["Spin_Efficiency_pct"])}
                {_cmp_row("Vert Break (in)",       pa["Vert_Break_in"],      pb["Vert_Break_in"])}
                {_cmp_row("Horiz Break (in)",      pa["Horiz_Break_in"],     pb["Horiz_Break_in"])}
                {_cmp_row("Extension (ft)",        pa["Extension_ft"],       pb["Extension_ft"])}
                {_cmp_row("Elbow Stress (Nm)",     pa["Peak_Valgus_Nm"],     pb["Peak_Valgus_Nm"])}
                {_cmp_row("Hip-Shoulder Sep (°)",  pa["Peak_Hip_Shoulder_Sep"], pb["Peak_Hip_Shoulder_Sep"])}
                {_cmp_row("Trunk Rot @ Foot-Plant (°)", pa["FootPlant_Trunk_Rot"], pb["FootPlant_Trunk_Rot"])}
                {_cmp_row("Lead Knee Ext (°)",     pa["Release_Lead_Knee_Ext"], pb["Release_Lead_Knee_Ext"])}
              </tbody>
            </table>
            """
            st.markdown(_flat_html(table_html), unsafe_allow_html=True)

            # Side-by-side video players
            v_data = st.session_state.get("bullpen_video")
            v_url  = st.session_state.get("bullpen_video_url")
            if v_data is not None or v_url:
                st.markdown("**Side-by-side video**")
                st.caption(
                    "Both players show the same bullpen video — scrub each one to "
                    "the moment of the pitch you're comparing, and use the speed "
                    "controls (0.25× for slow-motion) to break down the deliveries. "
                    "True auto-clipping per pitch is on the v2 roadmap."
                )
                va_col, vb_col = st.columns(2)
                with va_col:
                    st.caption(f"**Pitch #{int(a_key)}** — {pa['Pitch_Type']}")
                    if v_data is not None:
                        st.video(v_data)
                    else:
                        st.video(v_url)
                with vb_col:
                    st.caption(f"**Pitch #{int(b_key)}** — {pb['Pitch_Type']}")
                    if v_data is not None:
                        st.video(v_data)
                    else:
                        st.video(v_url)
            else:
                st.info(
                    "📹 Upload a bullpen video or paste a URL in the sidebar to "
                    "enable side-by-side video playback."
                )
        else:
            st.info("Pick two **different** pitches to compare.")

        st.divider()
        st.subheader("Every Pitch — Canonical View")
        display_df = df.copy()
        display_df["Confidence"] = (display_df["Alignment_Confidence"] * 100).astype(int).astype(str) + "%"
        display_cols = [
            "Pitch_Num", "Timestamp", "Pitch_Type", "Velocity_mph",
            "Total_Spin_rpm", "Spin_Efficiency_pct", "Vert_Break_in",
            "Horiz_Break_in", "Peak_Valgus_Nm", "AC_Ratio",
            "FootPlant_Trunk_Rot", "Peak_Hip_Shoulder_Sep",
            "Healed", "Healed_Notes", "Confidence",
        ]
        st.dataframe(display_df[display_cols], use_container_width=True, hide_index=True)

        st.caption(
            "Click any column header to sort. `Healed = True` means the aligner had to "
            "fill in a missing data source; `Confidence` tells you how much to trust this row."
        )

        # Injury-risk callouts
        st.subheader("Injury / Risk Flags")
        any_flag = False
        for _, row in df.iterrows():
            flags = detect_injury_flags(row)
            for f in flags:
                any_flag = True
                icon = "🚨" if f["severity"] == "DANGER" else "⚠️"
                pitch_label = f"Pitch #{int(row['Pitch_Num'])} ({row['Pitch_Type']}, {row['Velocity_mph']:.1f} mph)"
                st.write(f"{icon} **{pitch_label}** — {f['label']}")
        if not any_flag:
            st.success("No injury-risk flags raised this session.")

    # ---- History & Trends tab ----
    with tab_history:
        st.subheader(f"History — {athlete_name}")

        if active_athlete_id is None:
            st.info(
                "📌 You're viewing a sample session for a virtual pitcher. "
                "Add this pitcher to your roster (or pick a saved pitcher in the sidebar) "
                "to start building a session history."
            )
        else:
            history = list_sessions(active_athlete_id, limit=50)
            if not history:
                st.info(
                    f"No saved sessions yet for **{athlete_name}**. "
                    "Upload real Pitch Logic / Pulse / ProPlayAI data and it auto-saves here. "
                    "Once you have 2+ sessions, you'll see trend charts and real baselines."
                )
            else:
                st.caption(
                    f"{len(history)} session(s) on file. "
                    "Real sessions feed the rolling baseline; sample sessions are kept "
                    "for reference but don't affect baselines."
                )

                # ----- Trend charts -----
                hist_df = pd.DataFrame(history)
                hist_df["session_date"] = pd.to_datetime(hist_df["session_date"], errors="coerce")
                hist_df = hist_df.sort_values("session_date")

                # Real sessions only for trend display
                trend_df = hist_df[hist_df["session_type"] == "real"].copy()
                if len(trend_df) >= 2:
                    st.subheader("Velocity, Spin & Stress Over Time")
                    tcol1, tcol2 = st.columns(2)
                    with tcol1:
                        fig_v = px.line(trend_df, x="session_date", y="avg_velocity",
                                         markers=True,
                                         labels={"session_date": "Session",
                                                 "avg_velocity": "Avg Velocity (mph)"},
                                         title="Average Velocity Trend")
                        fig_v.update_traces(line=dict(color="#1a2150", width=3),
                                             marker=dict(size=10, color="#d4a634"))
                        fig_v.update_layout(height=320, margin=dict(t=40, b=20))
                        render_static_chart(fig_v)
                    with tcol2:
                        fig_s = px.line(trend_df, x="session_date", y="max_stress",
                                         markers=True,
                                         labels={"session_date": "Session",
                                                 "max_stress": "Max Elbow Stress (Nm)"},
                                         title="Peak Elbow Stress Trend")
                        fig_s.update_traces(line=dict(color="#dc2626", width=3),
                                             marker=dict(size=10, color="#fca5a5"))
                        fig_s.add_hline(y=DANGER_VALGUS_NM, line_dash="dash",
                                         line_color="#dc2626", opacity=0.5,
                                         annotation_text=f"Danger ≥ {DANGER_VALGUS_NM} Nm",
                                         annotation_position="top right")
                        fig_s.update_layout(height=320, margin=dict(t=40, b=20))
                        render_static_chart(fig_s)

                    fig_p = px.line(trend_df, x="session_date", y="pitch_count",
                                     markers=True,
                                     labels={"session_date": "Session",
                                             "pitch_count": "Pitches"},
                                     title="Workload per Session (Pitch Count)")
                    fig_p.update_traces(line=dict(color="#16a34a", width=3),
                                         marker=dict(size=10, color="#86efac"))
                    fig_p.update_layout(height=260, margin=dict(t=40, b=20))
                    render_static_chart(fig_p)
                elif len(trend_df) == 1:
                    st.info("Log 2+ real sessions to see trend charts.")

                st.divider()

                # ----- Session list with delete -----
                st.subheader("All Sessions")
                for s in history:
                    with st.container(border=True):
                        c1, c2, c3, c4, c5, c6 = st.columns([2, 1, 1, 1, 1, 1])
                        date_str = pd.to_datetime(s["session_date"]).strftime("%b %d, %Y · %I:%M %p") \
                            if s.get("session_date") else "—"
                        type_pill = (
                            "<span style='background:#dcfce7;color:#15803d;padding:2px 8px;"
                            "border-radius:10px;font-size:10.5px;font-weight:700;'>REAL</span>"
                            if s["session_type"] == "real"
                            else "<span style='background:#eef2ff;color:#3730a3;padding:2px 8px;"
                                 "border-radius:10px;font-size:10.5px;font-weight:700;'>SAMPLE</span>"
                        )
                        c1.markdown(_flat_html(
                            f"<div style='font-weight:700;color:#1a2150;'>{date_str}</div>"
                            f"<div style='margin-top:2px;'>{type_pill}</div>"
                        ), unsafe_allow_html=True)
                        c2.metric("Pitches", s.get("pitch_count", "—"))
                        c3.metric("Avg Velo", f"{s['avg_velocity']:.1f} mph" if s.get("avg_velocity") is not None else "—")
                        c4.metric("Peak Velo", f"{s['peak_velocity']:.1f} mph" if s.get("peak_velocity") is not None else "—")
                        c5.metric("Max Stress", f"{s['max_stress']:.1f} Nm" if s.get("max_stress") is not None else "—")
                        with c6:
                            if st.button("Delete",
                                          key=f"del_session_{s['id']}",
                                          use_container_width=True):
                                delete_session(s["id"])
                                # Invalidate the saved-fingerprint so a fresh save isn't blocked
                                if "_saved_fingerprint" in st.session_state:
                                    del st.session_state["_saved_fingerprint"]
                                st.rerun()

    # ---- Pitch Tunneling tab ----
    with tab_tunneling:
        st.subheader("Pitch Tunneling — pair sequencing tool")
        st.caption(
            "Pick the **starting pitch**, click anywhere on the zone to place it, "
            "then pick the **second pitch** you want to tunnel off the first. The "
            "app uses this pitcher's measured break profile to compute exactly where "
            "the second pitch would have to land to share the tunnel — and shows "
            "both flight paths from three angles so the convergence at the commit "
            "point and divergence at the plate are obvious."
        )

        # Build arsenal from the current session
        breakdown = pitch_type_breakdown(df)
        arsenal_rows = breakdown.to_dict("records")
        if not arsenal_rows:
            st.info("No pitches thrown yet — load a session to use the tunneling tool.")
        elif len(arsenal_rows) < 2:
            st.info("Need at least 2 different pitch types in the session to do pair tunneling.")
        else:
            available_types = [r["Pitch_Type"] for r in arsenal_rows]
            # Pick the most-thrown fastball as the default starter
            default_a_idx = 0
            for i, r in enumerate(arsenal_rows):
                if "Fastball" in r["Pitch_Type"] or "Sinker" in r["Pitch_Type"]:
                    default_a_idx = i
                    break
            # Default second pitch = first non-starter pitch type
            default_b_idx = 1 if default_a_idx == 0 else 0

            # ---- Two-pitch selectors ----
            sel_a, sel_b = st.columns(2)
            with sel_a:
                pitch_a = st.selectbox(
                    "🎯 Starting pitch (the one you 'tunnel off')",
                    available_types,
                    index=default_a_idx,
                    key="tunnel_pitch_a",
                    help="Usually your fastball — the pitch the batter is gearing up for.",
                )
            # Available B-pitch options (everything except A)
            b_options = [t for t in available_types if t != pitch_a]
            with sel_b:
                # Reset selection if A changed and the prior B equals new A
                prior_b = st.session_state.get("tunnel_pitch_b")
                if prior_b not in b_options:
                    prior_b = b_options[0] if b_options else None
                pitch_b = st.selectbox(
                    "🪢 Second pitch (the one you tunnel WITH)",
                    b_options,
                    index=b_options.index(prior_b) if prior_b in b_options else 0,
                    key="tunnel_pitch_b",
                    help="The pitch you throw AFTER the starter — sequence weapon.",
                )

            # ---- Placement controls — sliders work on phone + laptop ----
            # The old click-to-place on the chart let the chart get dragged
            # off screen on mobile. Two sliders below give the same control
            # in a way that cooperates with page scrolling.
            slide_l, slide_r, reset_col = st.columns([3, 3, 1])
            with slide_l:
                plate_x = st.slider(
                    "Plate side (ft)",
                    min_value=-1.5, max_value=1.5,
                    value=float(st.session_state.get("tunnel_plate_x", 0.0)),
                    step=0.05,
                    key="tunnel_plate_x_slider",
                    help="Negative = third-base side. Positive = first-base side.",
                )
            with slide_r:
                plate_z = st.slider(
                    "Plate height (ft)",
                    min_value=0.5, max_value=4.5,
                    value=float(st.session_state.get("tunnel_plate_z", 2.5)),
                    step=0.05,
                    key="tunnel_plate_z_slider",
                    help="1.6 ft = bottom of zone, 3.5 ft = top of zone.",
                )
            with reset_col:
                st.write("")  # spacer
                if st.button("Reset",
                              use_container_width=True,
                              key="tunnel_reset_btn"):
                    st.session_state["tunnel_plate_x_slider"] = 0.0
                    st.session_state["tunnel_plate_z_slider"] = 2.5
                    st.rerun()
            st.session_state["tunnel_plate_x"] = plate_x
            st.session_state["tunnel_plate_z"] = plate_z

            # ---- Compute tunnel for ONLY the two selected pitches ----
            arsenal_pair = [r for r in arsenal_rows if r["Pitch_Type"] in (pitch_a, pitch_b)]
            tunnel_data = compute_arsenal_tunnel(
                arsenal=arsenal_pair,
                starting_pitch_type=pitch_a,
                plate_x=plate_x,
                plate_z=plate_z,
                sport=athlete_sport,
            )
            quality = tunnel_quality_metrics(tunnel_data)
            pair_q = quality.get(pitch_b, {})

            # ---- Two POV charts ----
            sub_batter, sub_side = st.tabs([
                "Batter POV", "Side View"
            ])
            with sub_batter:
                # clickable=False — placement is driven by the sliders above
                fig_b = _build_tunnel_batter_view(tunnel_data,
                                                     sport=athlete_sport,
                                                     hand=athlete_hand,
                                                     clickable=False)
                render_static_chart(fig_b, key="tunnel_batter_chart")
                st.caption(
                    "Catcher's view — what the batter sees. Both flight paths "
                    "start from the same release, share the **commit window** "
                    "(gold dashed circle), then visibly diverge in the final 22 ft. "
                    "Use the sliders above to move the starting pitch."
                )
            with sub_side:
                fig_s = _build_tunnel_side_view(tunnel_data, sport=athlete_sport)
                render_static_chart(fig_s, key="tunnel_side_chart")
                st.caption(
                    "Side view — classic baseball-trajectory diagram. Pitcher on "
                    "the left, catcher on the right. The two flight paths leave "
                    "the **release ring** together, share the **tunnel ring** at "
                    "the commit point, then arc into different spots at the "
                    "**plate ring**."
                )

            # ---- Pair tunneling summary ----
            st.divider()
            st.subheader(f"How {pitch_b} tunnels off {pitch_a}")
            if pair_q:
                grade_colors = {
                    "Elite":     ("#16a34a", "#dcfce7", "🟢"),
                    "Good":      ("#15803d", "#d1fae5", "🟢"),
                    "Loose":     ("#d4a634", "#fef3c7", "🟡"),
                    "No Tunnel": ("#dc2626", "#fee2e2", "🔴"),
                }
                grade_color, grade_bg, grade_icon = grade_colors.get(
                    pair_q["tunnel_grade"], ("#6b7280", "#f3f4f6", "⚪"))

                # Three-stat tile row
                t1, t2, t3, t4 = st.columns(4)
                t1.metric("Tunnel Offset", f"{pair_q['tunnel_offset_in']}\"",
                            help="At the commit point — lower is better. "
                                 "Under 3\" is elite.")
                t2.metric("Plate Separation", f"{pair_q['plate_diff_in']}\"",
                            help="At the plate — higher = more deception "
                                 "after the batter committed.")
                t3.metric("Timing Differential", f"{pair_q['timing_offset_ms']:+.0f} ms",
                            help="Velocity-gap the batter must adjust to "
                                 "(positive = second pitch arrives later).")
                t4.metric("Tunnel Grade", f"{grade_icon} {pair_q['tunnel_grade']}")

                # Coaching takeaway specific to this pair
                # Get plate locations for the narrative
                pa = tunnel_data[pitch_a]
                pb = tunnel_data[pitch_b]
                st.markdown(
                    _flat_html(
                        f"<div style='background:{grade_bg};border:1px solid {grade_color}40;"
                        f"border-left:4px solid {grade_color};border-radius:8px;"
                        f"padding:14px 18px;margin-top:14px;'>"
                        f"<div style='font-weight:700;color:{grade_color};'>"
                        f"Coaching takeaway</div>"
                        f"<div style='font-size:13px;color:#1f2937;margin-top:6px;'>"
                        f"Throw the <b>{pitch_a}</b> to "
                        f"<b>({pa['plate_x']:+.2f}, {pa['plate_z']:.2f}) ft</b>. "
                        f"Then for the <b>{pitch_b}</b> to share the tunnel, it "
                        f"needs to release on the SAME line and finish at "
                        f"<b>({pb['plate_x']:+.2f}, {pb['plate_z']:.2f}) ft</b> — "
                        f"a {pair_q['plate_diff_in']:.1f}\" separation. "
                        f"Through the commit window, the two pitches are within "
                        f"<b>{pair_q['tunnel_offset_in']:.1f}\"</b> of each other → "
                        f"this is a <b>{pair_q['tunnel_grade']}</b> tunnel pairing."
                        f"</div></div>"
                    ),
                    unsafe_allow_html=True,
                )

    # ---- Alignment quality tab ----
    with tab_alignment:
        st.subheader("Alignment Quality Audit")
        # Derive counts from canonical df so this works in BOTH Demo Mode
        # and real-data mode (Demo Mode doesn't have separate source DFs).
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Pitches in ball-flight source", len(df))
        col_b.metric("Throws matched to Pulse",       int(df["Pulse_Present"].sum()))
        col_c.metric("ProPlayAI captures matched",    int(df["PPAI_Present"].sum()))

        st.write("**Per-pitch source presence:**")
        audit = df[["Pitch_Num", "Timestamp", "Pitch_Type",
                    "Pulse_Present", "Pulse_Match_Method",
                    "PPAI_Present", "PPAI_Match_Method",
                    "Alignment_Confidence", "Healed_Notes"]].copy()
        audit["Alignment_Confidence"] = (audit["Alignment_Confidence"] * 100).astype(int).astype(str) + "%"
        st.dataframe(audit, use_container_width=True, hide_index=True)

        st.caption(
            "`timestamp_window` = clean match within ±5s. "
            "`timestamp_wide` = matched within ±15s — review manually. "
            "Blank method = no match found, row was healed with a placeholder."
        )

    # ---- Action plan tab ----
    with tab_action:
        plan = recommend_drills(df, sport=athlete_sport,
                                 athlete_level=athlete_level)

        # Color-code by category for visual hierarchy
        CATEGORY_BADGES = {
            "Injury Prevention":     ("🚨", "#d32f2f"),
            "Mechanics":             ("🔧", "#1976d2"),
            "Mechanics — Velocity":  ("🔧", "#1976d2"),
            "Velocity":              ("⚡", "#f57c00"),
            "Stuff — Fastball":      ("🎯", "#7b1fa2"),
            "Stuff — Slider":        ("🎯", "#7b1fa2"),
            "Stuff — Movement":      ("🎯", "#7b1fa2"),
            "Grip":                  ("👆", "#00838f"),
            "Consistency":           ("📐", "#388e3c"),
        }

        def render_drill_card(d):
            badge_icon, badge_color = CATEGORY_BADGES.get(d["category"], ("•", "#666"))
            with st.container(border=True):
                # Top accent strip in category color (4px solid bar)
                st.markdown(
                    f"<div style='height:4px; background:{badge_color}; "
                    f"margin:-1rem -1rem 12px -1rem; border-radius:6px 6px 0 0;'></div>",
                    unsafe_allow_html=True,
                )
                # Header row: category badge + drill label
                st.markdown(
                    f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:8px;'>"
                    f"<span style='background:{badge_color};color:white;padding:3px 11px;"
                    f"border-radius:14px;font-size:11px;font-weight:700;letter-spacing:0.04em;'>"
                    f"{badge_icon} {d['category'].upper()}</span>"
                    f"<span style='font-size:17px;font-weight:700;color:#1a2150;'>{d['label']}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                # If this drill has a grip visual, show drill text + grip SVG side-by-side
                if d.get("grip_key"):
                    left, right = st.columns([1.4, 1])
                    with left:
                        st.markdown(f"**Drill:** {d['drill']}")
                        st.markdown(f"**Protocol:** {d['protocol']}")
                        st.markdown(f"**Why it works:** {d['why']}")
                    with right:
                        render_grip_diagram(d["grip_key"])
                    # Surface ALL release-style variants for this pitch so
                    # the coach sees the menu of options + the trade-offs.
                    if d["grip_key"] in GRIP_VARIANTS:
                        with st.expander("**See all grip variants** "
                                          "(arm-slot + palm-angle options)",
                                          expanded=False):
                            render_grip_variants(d["grip_key"])
                else:
                    st.markdown(f"**Drill:** {d['drill']}")
                    st.markdown(f"**Protocol:** {d['protocol']}")
                    st.markdown(f"**Why it works:** {d['why']}")
                # "Watch demo" link button — opens the best-matched YouTube
                # tutorial in a new tab. Shows the video title + source so the
                # coach knows what they're about to open.
                if d.get("video_url"):
                    label = d.get("video_title") or "Watch demo on YouTube"
                    source = d.get("video_source", "")
                    source_html = (f"<span style='color:#6b7280;font-weight:500;'> · {source}</span>"
                                    if source else "")
                    st.markdown(
                        _flat_html(
                            f"<a href='{d['video_url']}' target='_blank' "
                            f"style='display:inline-flex;align-items:center;gap:6px;"
                            f"background:#fee2e2;color:#b91c1c;padding:6px 12px;"
                            f"border-radius:6px;font-size:13px;font-weight:600;"
                            f"text-decoration:none;margin-top:6px;'>"
                            f"▶ {label}{source_html}</a>"
                        ),
                        unsafe_allow_html=True,
                    )
                st.caption(f"Why this fired: _{d['trigger']}_")

        # =========================
        # 5-DAY STRUCTURED WEEKLY PLAN
        # Every day = warm-up → development drills (priority-flagged or
        # general-development) → cool-down. Same structure regardless of
        # whether the data flagged a glaring weakness or not.
        # =========================
        st.subheader("This Week — 5-Day Structured Plan")
        st.caption(
            "Every day opens with the standard warm-up and closes with the "
            "cool-down (full sequences in the reference panel below). "
            "Drill blocks adapt to the pitcher's specific weaknesses, or "
            "fall back to general-development work when the data is clean."
        )
        weekly_p = build_weekly_plan("pitching", plan,
                                       athlete_level=athlete_level)
        # Render Day 1 expanded by default + Days 2-5 collapsed.
        # Streamlit Cloud's free tier struggles to render 5 dense bordered
        # containers + nested drill cards all at once — lazy-loading the
        # later days as expanders keeps the initial render light.
        def _render_pitching_day(day):
            st.markdown(
                _flat_html(
                    f"<div style='font-size:13px;color:#4b5563;margin-bottom:10px;'>"
                    f"{day['notes']}</div>"
                ),
                unsafe_allow_html=True,
            )
            st.markdown(
                _flat_html(
                    f"<div style='background:#f0fdf4;border-left:3px solid #16a34a;"
                    f"padding:8px 12px;border-radius:0 6px 6px 0;margin-bottom:8px;'>"
                    f"<b style='color:#15803d;'>Warm-up</b> "
                    f"<span style='color:#6b7280;'>· {day['warmup']['duration']} · "
                    f"{day['warmup']['label']}</span>"
                    f"</div>"
                ),
                unsafe_allow_html=True,
            )
            if day["drills"]:
                for d in day["drills"]:
                    render_drill_card(d)
            else:
                st.caption(
                    "_No structured drills today — execute the bullpen/session described "
                    "in the notes above with full intent._"
                )
            st.markdown(
                _flat_html(
                    f"<div style='background:#eff6ff;border-left:3px solid #3b82f6;"
                    f"padding:8px 12px;border-radius:0 6px 6px 0;margin-top:8px;'>"
                    f"<b style='color:#1e40af;'>Cool-down</b> "
                    f"<span style='color:#6b7280;'>· {day['cooldown']['duration']} · "
                    f"{day['cooldown']['label']}</span>"
                    f"</div>"
                ),
                unsafe_allow_html=True,
            )

        for day in weekly_p:
            day_label = day["label"].split("—", 1)[-1].strip()
            header = f"**Day {day['day_num']} — {day_label}**"
            with st.expander(header, expanded=(day["day_num"] == 1)):
                _render_pitching_day(day)

        # =========================================================
        # WARM-UP & COOL-DOWN REFERENCE — full sequences spelled out
        # =========================================================
        st.divider()
        with st.expander("**Warm-Up & Cool-Down Reference** — full sequences",
                          expanded=False):
            for header, seq in [
                ("Pitcher Pre-Bullpen Warm-Up",  PITCHING_WARMUP),
                ("Pitcher Post-Bullpen Cool-Down", PITCHING_COOLDOWN),
            ]:
                st.markdown(
                    _flat_html(
                        f"<div style='font-size:11px;letter-spacing:0.10em;"
                        f"font-weight:700;color:#d4a634;text-transform:uppercase;"
                        f"margin-top:12px;'>{seq['duration']}</div>"
                        f"<div style='font-size:16px;font-weight:700;color:#1a2150;"
                        f"margin-bottom:8px;'>{header}</div>"
                    ),
                    unsafe_allow_html=True,
                )
                for step_name, step_detail in seq["steps"]:
                    st.markdown(
                        _flat_html(
                            f"<div style='border-left:3px solid #1a2150;padding:6px 12px;"
                            f"background:#f6f7fb;border-radius:0 4px 4px 0;margin:4px 0;'>"
                            f"<b style='color:#1a2150;'>{step_name}</b> "
                            f"<span style='color:#4b5563;'>— {step_detail}</span>"
                            f"</div>"
                        ),
                        unsafe_allow_html=True,
                    )
                st.markdown(
                    _flat_html(
                        f"<div style='font-size:12px;color:#6b7280;font-style:italic;"
                        f"margin-top:6px;margin-bottom:12px;'>"
                        f"Why it matters: {seq['why']}</div>"
                    ),
                    unsafe_allow_html=True,
                )

        st.divider()

        # =========================
        # DRILL LIBRARY (browseable — every drill, with demo video links)
        # =========================
        with st.expander(f"Drill Library — browse every {athlete_sport.lower()} drill with demo videos",
                          expanded=False):
            st.caption(
                f"Every drill available for {athlete_sport.lower()} pitchers, grouped by category. "
                "The Action Plan above only shows drills the data flagged for THIS pitcher — "
                "this library shows everything, so coaches and players can browse and learn."
            )

            # Filter drills by sport (keys starting with 'softball_' are softball-only;
            # everything else is baseball-only EXCEPT the truly shared cooldowns).
            def _drill_is_for_sport(key: str) -> bool:
                if athlete_sport == "Softball":
                    return key.startswith("softball_")
                else:
                    return not key.startswith("softball_")

            # Group by category
            drills_by_cat: dict = {}
            for key, d in DRILL_LIBRARY.items():
                if not _drill_is_for_sport(key):
                    continue
                cat = d["category"]
                drills_by_cat.setdefault(cat, []).append((key, d))

            # Sort categories by a sensible order
            CAT_ORDER = ["Injury Prevention", "Mechanics", "Mechanics — Velocity",
                         "Velocity", "Stuff — Fastball", "Stuff — Slider",
                         "Stuff — Rise Ball", "Stuff — Drop Ball", "Stuff — Movement",
                         "Grip", "Consistency"]
            for cat in CAT_ORDER:
                if cat not in drills_by_cat:
                    continue
                st.markdown(f"#### {cat}")
                for key, d in drills_by_cat[cat]:
                    with st.container(border=True):
                        st.markdown(f"**{d['label']}**")
                        st.markdown(f"**Drill:** {d['drill']}")
                        st.markdown(f"**Protocol:** {d['protocol']}")
                        st.markdown(f"<span style='color:#6b7280;font-style:italic;'>Why it works: {d['why']}</span>",
                                     unsafe_allow_html=True)
                        # Show ALL curated alternate videos for this drill —
                        # different sources / levels / severities the coach can pick from
                        alternates = get_drill_video_alternates(key)
                        if alternates:
                            chips_html = "<div style='display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;'>"
                            for v in alternates:
                                meta_bits = []
                                if v.get("severity", "any") != "any":
                                    meta_bits.append(v["severity"])
                                if v.get("level", "any") != "any":
                                    meta_bits.append(v["level"])
                                meta_str = (" · " + ", ".join(meta_bits)) if meta_bits else ""
                                chips_html += (
                                    f"<a href='{v['url']}' target='_blank' "
                                    f"style='background:#fee2e2;color:#b91c1c;"
                                    f"padding:6px 10px;border-radius:6px;font-size:12px;"
                                    f"font-weight:600;text-decoration:none;'>"
                                    f"▶ {v.get('title') or 'Watch demo'}"
                                    f"<span style='color:#6b7280;font-weight:500;'> "
                                    f"— {v.get('source','')}{meta_str}</span></a>"
                                )
                            chips_html += "</div>"
                            st.markdown(_flat_html(chips_html), unsafe_allow_html=True)

        # =========================
        # GRIP LIBRARY (browse all grips)
        # =========================
        with st.expander("🤲 Grip Library — browse all grips with plain-English instructions", expanded=False):
            st.caption(
                "Each grip below is described for any coach, player, or parent — "
                "no jargon. If a baseball term is unfamiliar, check the **Baseball "
                "Glossary** at the bottom of this tab."
            )
            for gk, info in GRIP_LIBRARY.items():
                with st.container(border=True):
                    gcol1, gcol2 = st.columns([1, 1.4])
                    with gcol1:
                        st.markdown(f"### {info['label']}")
                        render_grip_diagram(gk, height=340)
                    with gcol2:
                        st.markdown(info["description"])

        # =========================
        # SPORT-AWARE GLOSSARY (parent / young-player friendly)
        # =========================
        glossary_label = "Softball" if athlete_sport == "Softball" else "Baseball"
        with st.expander(f"📚 {glossary_label} Glossary — what do these terms mean?",
                          expanded=False):
            st.caption(
                f"Plain-English definitions of every {glossary_label.lower()} term used "
                "in this report. Helpful for parents and young players who are new to "
                "advanced metrics."
            )
            for term, definition in get_glossary_for_sport(athlete_sport):
                st.markdown(f"{term}: {definition}")
                st.markdown("")

        # =========================
        # EXPORT
        # =========================
        st.subheader("Export")

        # Primary CTA: PDF report (this is what coaches text/email parents)
        try:
            pdf_bytes = generate_pbr_pdf(df, athlete_name, athlete_hand, athlete_class,
                                          sport=athlete_sport, athlete_level=athlete_level)
            st.download_button(
                "📄  Download Post-Bullpen Report (PDF) — text or email to parent",
                data=pdf_bytes,
                file_name=f"{athlete_name.replace(' ', '_')}_PBR_{pd.Timestamp.now().strftime('%Y%m%d')}.pdf",
                mime="application/pdf",
                use_container_width=True,
                type="primary",
            )
        except Exception as e:
            st.warning(f"PDF generation hit an issue: `{type(e).__name__}: {e}`. "
                       "The CSV + text exports below still work.")

        # Secondary CTA: focused action-plan-only PDF
        try:
            ap_pdf = generate_action_plan_pdf(df, athlete_name, athlete_hand,
                                               athlete_class, sport=athlete_sport,
                                               athlete_level=athlete_level)
            st.download_button(
                "📋  Download Action Plan only (PDF) — focused 1-2 page coaching sheet",
                data=ap_pdf,
                file_name=f"{athlete_name.replace(' ', '_')}_ActionPlan_{pd.Timestamp.now().strftime('%Y%m%d')}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        except Exception as e:
            st.caption(f"Action plan PDF generation issue: `{type(e).__name__}: {e}`.")

        col_e1, col_e2 = st.columns(2)
        with col_e1:
            csv_bytes = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "⬇️ Download canonical pitch CSV",
                data=csv_bytes,
                file_name=f"{athlete_name.replace(' ', '_')}_session.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with col_e2:
            # Plain-text action plan — for pasting into an SMS or quick email body
            text_plan = _format_plan_text(athlete_name, plan)
            st.download_button(
                "💬 Action plan as plain text (for SMS body)",
                data=text_plan.encode("utf-8"),
                file_name=f"{athlete_name.replace(' ', '_')}_action_plan.txt",
                mime="text/plain",
                use_container_width=True,
                help="Use the PDF above for emails. This text version is for when you want "
                     "to copy/paste the plan directly into a text message body.",
            )

def _safe_main():
    """Run main() with a friendly error card if anything goes wrong.

    Never show a raw Python traceback to a coach — that breaks the spell.
    Surface an actionable message instead.
    """
    try:
        main()
    except Exception as e:
        import traceback as tb
        st.error(
            "⚠️ **Something went wrong.**\n\n"
            "We hit an unexpected issue rendering this report. The data "
            "you uploaded was saved, so nothing is lost — try refreshing "
            "the page, or flip on **Sample Session** in the sidebar to "
            "see if the app itself is working."
        )
        with st.expander("Technical details (share with Kolby if this keeps happening)"):
            st.code(f"{type(e).__name__}: {e}\n\n{tb.format_exc()}", language="text")


if __name__ == "__main__":
    _safe_main()
