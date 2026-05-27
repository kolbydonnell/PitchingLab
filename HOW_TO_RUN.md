# How to Run the Pitching Lab

Four steps. Total time: 10 minutes the first time, 5 seconds every time after.

---

## Step 1 — Move the folder to your Desktop

Find the folder with all these files in it. Drag the whole folder onto your **Desktop** so you can find it easily.

---

## Step 2 — Set things up (do this ONCE)

1. Open the folder on your Desktop.
2. Find the file called **`setup.command`**.
3. **Double-click it.**
4. A Terminal window opens and starts installing stuff. You'll see lots of text scroll by — that's normal.
5. It will ask for your Mac password at some point. Type it (you won't see letters appear — normal) and press Return.
6. Wait until you see **"✅ SETUP COMPLETE"**. About 5 minutes.
7. Close the Terminal window.

> 🟡 **First-time warning:** macOS may say *"setup.command can't be opened because it is from an unidentified developer."*
> If you see that: open **System Settings → Privacy & Security**, scroll down, click **"Open Anyway"** next to `setup.command`. Then double-click it again.

---

## Step 3 — Start the app (every time you want to use it)

1. **Double-click `start.command`** in the same folder.
2. Wait a few seconds.
3. Your web browser opens automatically to the Pitching Lab.

(Same first-time security warning may appear — same fix: System Settings → Privacy & Security → Open Anyway.)

---

## Step 4 — Try the sample data

When the app is open in your browser:

1. Look at the **left sidebar**.
2. Under **"Pitch Logic CSV"** → click **Browse files** → open `sample_data/pitch_logic_sample.csv`.
3. Under **"Driveline Pulse CSV"** → click **Browse files** → open `sample_data/pulse_sample.csv`.
4. Under **"ProPlayAI per-pitch files"** → click **Browse files**, then **hold Command** and click every file that starts with `proplayai_pitch_` (there are 9 of them). Click **Open**.
5. The report appears. Click through the four tabs at the top.

---

## To stop the app

Close the Terminal window that opened when you double-clicked `start.command`. That's it.

---

## If something goes wrong

Copy the error message (red or yellow text), paste it back to me, and I'll fix it.

---

## Live Capture (Beta) — phone-camera pitch tracking

The Live Capture tab uses the phone or laptop camera to track the ball in real time — no Pitch Logic ball, no Pulse sleeve, no ProPlayAI subscription required. The tab works in two modes:

**Full mode** — ball tracking + pose extraction. Requires Python 3.9 through 3.12.

**Ball-tracking-only mode** — works on any Python version (including 3.13 and 3.14). Captures velocity, break, plate location, and estimated spin metrics. Pose biomech (hip-shoulder separation, arm slot, lead knee) is skipped.

### If you see "Failed building wheel" for pillow / pandas / streamlit

That happens when your Python version (e.g. 3.14) is newer than the pinned package version in `requirements.txt`. The old requirements.txt used `==` pins which forced pip to compile from source — and compiling needs system libraries that aren't always there.

**Fix:** the requirements.txt has been updated to use `>=` pins. Run:

```
pip3 install --upgrade pip
pip3 install -r ~/Desktop/PitchingLab/requirements.txt --upgrade
```

If it still fails on one specific package, copy that package's error line and send it to me. The most common culprit is `pillow` — explicitly upgrade it with:

```
pip3 install --upgrade pillow
```

### If `pip install mediapipe` failed with "no matching distribution"

That means your Python version is too new for MediaPipe (it currently supports 3.9-3.12). You have two options:

**Option A — Use Live Capture without pose** (easiest). The capture still gives you velocity, break, plate location, and spin. Just leave `mediapipe` commented out in `requirements.txt` and re-run:

```
pip3 install -r ~/Desktop/PitchingLab/requirements.txt --upgrade
```

**Option B — Install Python 3.12 alongside your current version** (full feature mode). One-time setup:

```
brew install python@3.12
/opt/homebrew/bin/python3.12 -m pip install -r ~/Desktop/PitchingLab/requirements.txt --upgrade
```

Then uncomment the `mediapipe>=0.10.14` line in `requirements.txt`. The app's `start.command` will pick up the Python 3.12 env automatically.

### Setup checklist for the Live Capture tab

1. Phone or tablet on a tripod, behind the catcher OR 15-30 ft to the side.
2. Open the Live Capture toggle in the sidebar.
3. Mark home plate's pixel position in the calibration row (one-time per camera setup).
4. Set ball radius range (typical 6-22 px for a phone 30 ft away).
5. Tap **START**, allow camera access in the browser.
6. After each pitch, tap **📌 Snap Pitch** at the end of the throw.
7. Tap **💾 Save Session** when done — the captured pitches flow into the athlete's history exactly like a CSV upload.
