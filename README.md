# health-tracker

Personal training + sleep database. Pulls workouts from Strava and sleep from
Garmin Connect into a local SQLite file (`health.db`), so questions about
training load and recovery can be answered from real accumulated history.

## Usage

```sh
uv run python pull_strava.py 14   # workouts, last 14 days (idempotent)
uv run python pull_garmin.py 14   # sleep + resting HR, last 14 days
uv run python summary.py          # weekly mileage, sleep, RHR at a glance
```

## Data flow

- **Workouts ← Strava.** Everything the Garmin watch records auto-syncs to
  Strava, so Strava covers runs, lifts, and rides. Auth: refresh token in
  `.env` (shared with the ai-workout-dj Strava app, client 274279 — if the
  pull script warns about a rotated token, update the AWDJ Vercel env too).
- **Sleep ← Garmin Connect** via `python-garminconnect` (unofficial; Strava
  carries no sleep data). First run prompts for Garmin login + MFA, then
  caches tokens at `~/.garminconnect`.
- Each metric has exactly one source — no cross-source merging or deduping.
- `raw` columns keep each source's full JSON; parsed columns are a view on
  top, so a schema drift upstream never loses data.

`.env` holds all credentials and is gitignored. `health.db` is the system of
record and is also gitignored — back it up if the history starts mattering.
