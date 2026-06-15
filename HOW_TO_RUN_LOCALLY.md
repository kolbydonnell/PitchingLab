# How to Run Diamond Sports Lab on Your Mac

No terminal commands. No memorizing Docker syntax. Just double-click.

## Daily Workflow

| Action | What to do |
|---|---|
| **Start the app** | Double-click `Start_App.command` |
| **Stop the app** | Double-click `Stop_App.command` |
| **Rebuild after changing requirements.txt or Dockerfile** | Double-click `Rebuild_App.command` |

That's it. The browser opens automatically when the app is ready (about 5 seconds after you double-click Start).

## What These Scripts Do

### `Start_App.command`

When you double-click:

1. Checks Docker Desktop is running (tells you if it isn't)
2. Builds the Docker image if it doesn't exist yet (first run only — 5 min)
3. Starts the container with your code, secrets, and test videos mounted in
4. Waits for the app to be ready
5. Opens `http://localhost:8501` in your default browser
6. Tells you everything's good

**Code changes to `pitching_lab.py` are picked up automatically** — just save the file and refresh the browser. No need to stop/start.

### `Stop_App.command`

Stops the running container so you can free up resources. Use this when you're done testing for the day.

### `Rebuild_App.command`

Only needed when you change `requirements.txt`, `Dockerfile`, or `entrypoint.sh`. For everyday code changes you don't need this.

## First-Time Setup

**Step 1 — Make sure Docker Desktop is installed and running:**

- Download Docker Desktop from https://www.docker.com/products/docker-desktop/ (free)
- Install it (drag-and-drop into Applications)
- Launch Docker Desktop from Applications
- Wait ~60 seconds — the whale icon in your menu bar should be solid (not animated)

**Step 2 — Double-click `Start_App.command`**

The first time, this takes ~5 minutes (building the Docker image). Subsequent starts take ~5 seconds.

## Daily Workflow After Setup

1. Open Docker Desktop (if not already running)
2. Double-click `Start_App.command` — browser opens in ~5 seconds
3. Use the app, edit `pitching_lab.py` as needed — saves are picked up automatically
4. Double-click `Stop_App.command` when done

## When Something Goes Wrong

### "Docker Desktop isn't running"

Open Docker Desktop from Applications, wait for the whale icon to be solid in your menu bar, then double-click Start_App.command again.

### The browser doesn't open

Open it yourself and go to: http://localhost:8501

### The page shows "This site can't be reached"

The container is still starting up. Wait 30 seconds and refresh. If it still doesn't work, check Docker Desktop — there might be an error in the container.

### See what's happening inside the container

Open Terminal and run:

```bash
docker logs -f diamond-sports-lab-dev
```

This shows Streamlit's live logs. Press `Ctrl+C` when done watching.

### Reset everything and start fresh

1. Double-click `Stop_App.command`
2. Double-click `Rebuild_App.command`
3. Double-click `Start_App.command`

## Why Local Instead of Render?

Local Docker on your Mac:
- Full CPU and RAM (no limits)
- No request timeouts
- No build quotas
- No "exceeded memory" emails
- Faster iteration (no push-and-wait deploy cycle)
- Same container as Render — when you eventually deploy, it works identically

Use Render when you want a public URL to share. Use local Docker while you're actively developing and testing.
