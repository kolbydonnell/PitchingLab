# Grip Photos

Drop a photo into this folder named after the grip key and it will
automatically appear in the Grip Library — no code changes needed.

## Filename → grip mapping

Save each photo with this exact filename (any of `.jpg`, `.jpeg`, `.png`,
`.webp` work).

### Baseball

| Grip                    | Filename                          |
|-------------------------|-----------------------------------|
| Four-Seam Fastball      | `four_seam_fastball.jpg`          |
| Two-Seam Sinker         | `two_seam_fastball.jpg`           |
| Sinker (Heavy 2-Seam)   | `sinker.jpg`                      |
| Standard Slider         | `slider_standard.jpg`             |
| Spike-Seam Slider       | `slider_spike_seam.jpg`           |
| Sweeper                 | `sweeper.jpg`                     |
| Curveball (12-6)        | `curveball.jpg`                   |
| Circle Changeup         | `changeup_circle.jpg`             |
| Three-Finger Changeup   | `three_finger_change.jpg`         |
| Fork Changeup (Horns)   | `fork_change.jpg`                 |
| Cutter                  | `cutter.jpg`                      |
| Splitter                | `splitter.jpg`                    |
| Knuckleball             | `knuckleball.jpg`                 |
| Knuckle Curve           | `knuckle_curve.jpg`               |
| Vulcan Changeup         | `vulcan_changeup.jpg`             |
| Slurve                  | `slurve.jpg`                      |
| Eephus                  | `eephus.jpg`                      |

### Softball

| Grip                | Filename                          |
|---------------------|-----------------------------------|
| Softball Fastball   | `softball_fastball.jpg`           |
| Rise Ball           | `softball_rise.jpg`               |
| Drop Ball (Peel)    | `softball_drop.jpg`               |
| Drop Ball (Roll)    | `softball_drop_roll.jpg`          |
| Curveball           | `softball_curve.jpg`              |
| Screwball           | `softball_screw.jpg`              |
| Changeup            | `softball_changeup.jpg`           |
| Backhand Change     | `softball_change.jpg`             |
| Knuckle Change      | `softball_offspeed_knuckle.jpg`   |

## Image guidelines

- **Aspect ratio:** roughly square or slightly wider than tall works best
- **Resolution:** 800×800 px or higher
- **Background:** plain wood, white, or neutral — the ball + hand should pop
- **Angle:** front-facing (release perspective) — fingers visible on the ball
- **License:** make sure you own the photo or it's licensed for use
  (Wikimedia Commons, your own pitcher's photos, etc.). Do **not** use
  copyrighted stock images without permission.

## How it works in code

`render_grip_diagram(grip_key)` checks for a matching photo here first.
If found, it renders the photo via `st.image`. If not, it falls back to
the abstract SVG diagram with finger-position markers.
