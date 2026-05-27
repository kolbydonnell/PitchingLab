# How to deploy Diamond Sports Lab to the web (and your iPhone)

Goal: get the app running at a public URL so you can pull it up on your iPhone for beta-testing. About 30 minutes the first time, 30 seconds for every future update.

You'll use two free services:
1. **GitHub** — stores your app's code (free account)
2. **Streamlit Cloud** — runs your app on a public URL (free tier, no credit card)

---

## Step 1 — Create a GitHub account (5 min)

1. Go to <https://github.com/join> and create a free account if you don't already have one.
2. Verify your email when prompted.

---

## Step 2 — Install GitHub Desktop (5 min)

This is the easiest way to push code without using the terminal.

1. Download GitHub Desktop: <https://desktop.github.com/>
2. Open the installer, drag GitHub Desktop into your Applications folder.
3. Open it and sign in with the GitHub account you just made.

---

## Step 3 — Put your app code into a GitHub repo (5 min)

1. Open **GitHub Desktop** → click **File → Add Local Repository**.
2. Click **Choose...** and select the `~/Desktop/PitchingLab` folder.
3. GitHub Desktop will say "this directory does not appear to be a Git repository" — click **create a repository** in that message.
4. Leave the defaults (name: `PitchingLab`, description optional). Click **Create Repository**.
5. At the top, click **Publish repository**.
6. **Uncheck** "Keep this code private" if you're fine with the code being public. (Free Streamlit Cloud needs the repo to be public — if you want it private, you can do that later by upgrading the Streamlit Cloud plan.)
7. Click **Publish Repository**.

The code is now on GitHub at `https://github.com/YOUR-USERNAME/PitchingLab`.

---

## Step 4 — Deploy to Streamlit Cloud (5 min)

1. Go to <https://streamlit.io/cloud> and click **Sign in with GitHub**.
2. Authorize Streamlit to read your GitHub repos.
3. Click **New app** in the top-right.
4. Select:
   - **Repository:** `YOUR-USERNAME/PitchingLab`
   - **Branch:** `main` (or `master`)
   - **Main file path:** `pitching_lab.py`
   - **App URL:** Pick something memorable like `diamond-sports-lab` (final URL will be `https://diamond-sports-lab.streamlit.app`)
5. Click **Deploy**.

Watch the build log. The first build takes 3-5 minutes (it installs every package from `requirements.txt`). When it's done, your app is live at the URL you picked.

---

## Step 5 — Add to iPhone Home Screen (1 min)

1. On your iPhone, open **Safari** and go to your Streamlit Cloud URL.
2. Tap the **Share** button (the square with the arrow at the bottom).
3. Scroll down and tap **Add to Home Screen**.
4. Name it "Diamond Lab" or whatever you like.
5. Tap **Add**.

Now there's an icon on your home screen. Tap it — the app opens full-screen, no browser chrome, looks like a real native app.

---

## Step 6 — Push updates (every future change)

When you (or I) change the code:

1. Open **GitHub Desktop**.
2. You'll see the changed files listed.
3. Type a short message in the bottom-left (e.g., "polish KPI layout").
4. Click **Commit to main**.
5. Click **Push origin** at the top.

Streamlit Cloud detects the push automatically and re-deploys in ~30 seconds. Refresh the page on your iPhone and the new version is live.

---

## Live Capture on iPhone — known limits

The Live Capture tab uses your phone's camera through Safari's WebRTC. It WILL work, but expect:

- **First-time camera prompt** — Safari will ask permission. Tap Allow.
- **Frame rate** — limited to ~24-30 fps in Safari (vs. native 60 fps). Velocity estimates are still solid.
- **Background runs** — Safari pauses video when the screen locks. Keep the screen on during a session, or use the iPhone's screen-timeout setting to set "Never."
- **Pose extraction** — works on Python 3.12 only (Streamlit Cloud uses 3.12 by default, so this should be fine — much better than your local 3.14 setup).

---

## If the deploy fails

The most common reason is a package that doesn't install cleanly on the Streamlit Cloud build environment. Look at the build log:

- **"No matching distribution"** for some package → the package version in `requirements.txt` is too tight. Loosen it from `==` to `>=`.
- **"Failed building wheel"** → same fix; the cloud is on Python 3.12 by default which has wheels for everything we use.
- **App boots but live capture doesn't work** → that's the WebRTC limitation in Safari. The rest of the app should be fine.

Copy the build log error and send it to me — quick to fix.

---

## What this costs you

- **GitHub:** Free for public repos. Private repos cost $4/month (Pro tier) but you don't need that for now.
- **Streamlit Cloud:** Free for the first community-tier app. If you want custom domain (`pitchinglab.com` instead of `*.streamlit.app`), Streamlit Cloud Teams is $250/month — overkill until you have paying customers.
- **Apple Developer Program ($99/year):** NOT needed for "Add to Home Screen" — that's just a Safari bookmark. Only needed if you ever want to ship a TRUE native iOS app to the App Store (Path 2 in the strategy doc).

So: **$0 to get on your iPhone for beta-testing.** That's the right answer for right now.
