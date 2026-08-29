# health-tracker

Personal training + sleep database. Pulls workouts from Strava and sleep from
Garmin Connect into a local SQLite file (`health.db`), so questions about
training load and recovery can be answered from real accumulated history.

## Usage

```sh
uv run python pull_garmin.py 14   # workouts + sleep, last 14 days (idempotent)
uv run python summary.py          # weekly mileage, sleep, RHR at a glance
uv run python ask.py "question"   # grounded answer via claude -p
```

## Data flow

- **Everything ← Garmin Connect** via `python-garminconnect` (unofficial).
  Workouts (with calories, sweat estimate, training effect, training load —
  richer than Strava's list API) and sleep, one login. First run prompts for
  Garmin login + MFA, then caches tokens at `~/.garminconnect`.
- `pull_strava.py` is a kept-as-fallback pull of the same workouts via the
  ai-workout-dj Strava app (client 274279; refresh token in `.env` shared
  with the AWDJ Vercel env — heed the rotation warning if it fires). Not run
  routinely since 2026-08-29; running it re-adds `source='strava'` rows,
  which double-counts workouts in summary/ask until deleted.
- Each metric has exactly one source — no cross-source merging or deduping.
- `raw` columns keep each source's full JSON; parsed columns are a view on
  top, so a schema drift upstream never loses data.

`.env` holds all credentials and is gitignored. `health.db` is the system of
record and is also gitignored — back it up if the history starts mattering.
