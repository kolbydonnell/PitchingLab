# Deploying Diamond Sports Lab to Render

## Why we moved

Streamlit Community Cloud kept producing PermissionError on MediaPipe's bundled
model files, with no fix from our side. Render with Docker means we control
the entire environment — same Python version, system packages, file
permissions every time. No more "wait, the deploy broke again" cycles.

## One-time setup (about 30 minutes)

### Step 1 — Test the Docker build locally

Before pushing to GitHub, make sure the container builds and runs on your Mac.
This is the fastest way to catch issues.

```bash
cd ~/Desktop/PitchingLab
docker build -t diamond-sports-lab .
docker run -p 8501:8501 diamond-sports-lab
```

Open http://localhost:8501 in your browser. You should see the app login
screen. If it works locally, it'll work on Render.

If `docker` isn't installed, get Docker Desktop:
https://www.docker.com/products/docker-desktop/

### Step 2 — Push the new files to GitHub

We added four new files: `Dockerfile`, `entrypoint.sh`, `.dockerignore`, and
`render.yaml`. Commit and push them:

```bash
cd ~/Desktop/PitchingLab
git add Dockerfile entrypoint.sh .dockerignore render.yaml DEPLOY_TO_RENDER.md
git commit -m "Add Render Docker deploy config"
git push
```

### Step 3 — Sign up for Render

1. Go to https://render.com
2. Sign in with your GitHub account (easier than email — Render needs repo access)
3. Authorize the Render GitHub app on your `pitchinglab` repo

### Step 4 — Create the web service

1. From the Render dashboard, click **"New +"** → **"Blueprint"**
2. Select your `pitchinglab` repo
3. Render reads `render.yaml` and pre-fills everything
4. Click **"Apply"** to create the service
5. Wait ~5 minutes for the first build to complete

### Step 5 — Add secrets

Once the service exists but BEFORE it starts serving traffic, configure secrets.

**5a — Add the secrets.toml as a Secret File:**

1. Service dashboard → **Environment** tab
2. Scroll to **Secret Files** → **Add Secret File**
3. Filename: `secrets.toml`
4. Contents: copy/paste the entire contents of your local
   `.streamlit/secrets.toml` file (Stripe keys, email config, etc.)
5. Save

The container's `entrypoint.sh` script automatically copies this file into the
right place at startup, so Streamlit reads it without any code changes.

**5b — Trigger a redeploy:**

After adding the secret file, click **"Manual Deploy"** → **"Deploy latest commit"**
so the new container picks up the secrets.

### Step 6 — Verify

When the deploy finishes (status: "Live"), visit the URL Render assigned
(something like `https://diamond-sports-lab.onrender.com`).

Test:
- Login screen loads
- Stripe checkout flow works (use Stripe test mode keys)
- MediaPipe-dependent features work (Live Capture, Upload Video pose
  detection, behind-pitcher mode)
- Email features work (password reset)

If anything fails, check **Logs** tab in Render dashboard — the entrypoint
script prints diagnostic info on startup.

## Day-to-day workflow

Same as Streamlit Cloud:

```bash
# Make changes locally
# Test locally if you want
git add -A
git commit -m "Whatever you changed"
git push
```

Render auto-deploys on every push to main. New version is live in 60-90
seconds.

## Free tier limits

| Limit | Detail |
|---|---|
| **750 hours / month** | Roughly 31 days × 24 hrs = always-on if it's your only app. You're fine. |
| **15 min idle → spin down** | First visit after idle takes 30-60 sec cold start. Then snappy. |
| **0.1 CPU / 512 MB RAM** | Plenty for Streamlit. Video processing may be slow on free tier. |
| **No persistent disk** | SQLite database wipes on every restart. See "Database persistence" below. |

## Database persistence

The current SQLite database (`diamond_sports_lab.db`) lives in the container
filesystem, which Render wipes on every restart. For a passion project in
testing this is fine — your user accounts and athlete data reset on each
deploy.

When you want real persistence:

**Option A — Render persistent disk ($1/month minimum)**
Uncomment the `disk:` block in `render.yaml`, redeploy. Database survives
restarts.

**Option B — Free Postgres elsewhere (most popular)**
Supabase, Neon, or Render's own Postgres free tier (90 days then $7/mo).
Requires modifying the app to use SQLAlchemy with a Postgres connection
string instead of raw sqlite3. About 2-3 hours of code work.

For now, leaving it as-is is fine. Tackle persistence when you have real
users you don't want to lose.

## Troubleshooting

**"Build failed: ERROR: failed to solve: ..."**
The Docker build itself failed — usually a Python dep won't install on the
target architecture. Check the Logs tab. Most fixes are version-pinning a
package in `requirements.txt`.

**Build succeeds but app crashes on startup**
Check Logs for the Python traceback. Usually a secrets issue (missing
secret file) or a module import that worked locally but not in the
container.

**App is slow / video processing times out**
Free tier has limited CPU. For testing this is fine — for production
video processing, you'd want at least Starter tier ($7/mo) for the
extra CPU headroom.

**Cold start is annoying**
The 15-min idle spin-down is the free tier's main downside. Two options:
1. Upgrade to Starter ($7/mo) — always-on, no cold start
2. Ping the URL every 10 min with a free uptime monitor like UptimeRobot
   — keeps the app awake for free, at the cost of using ~744 of your 750
   free hours per month

## Switching back

The `Dockerfile` doesn't lock you to Render — it's portable. The same
container runs on:
- Fly.io (similar free tier, slightly different setup)
- Google Cloud Run (more complex but 2M free requests/month)
- Hugging Face Spaces (always-on free tier, ML-focused)
- AWS / GCP / Azure (when you eventually need scale)

If Render disappoints, the migration to any of these is ~30 minutes max
because the container is already built.
