# Hermes integration — read me first (instructions for the Hermes agent)

You are being wired up as the delivery + chat layer for Ethan's personal
health tracker. The Python scripts here pull his real Garmin data (workouts
+ sleep) into a local SQLite DB and assemble grounded context blocks. Your
jobs: run the daily briefing, and answer ad-hoc questions from the data.

## One-time setup (on this server)

1. Install uv if absent: `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. In the repo root, create `.env` (chmod 600) with:
   ```
   GARMIN_EMAIL=...
   GARMIN_PASSWORD=...
   ```
   Ethan will provide the credentials in chat. NEVER echo them back,
   log them, or store them anywhere but `.env`.
3. First pull + backfill: `TZ=Asia/Jerusalem uv run python pull_garmin.py 93`
   This logs in to Garmin (tokens cached at `~/.garminconnect`, good ~1 year)
   and fills `health.db` with ~3 months of history.
   ⚠️ If login fails with 401, STOP and tell Ethan — do not retry in a loop;
   repeated bad-password attempts get the server IP rate-limited (429) by
   Garmin for a while.
4. Verify: `uv run python summary.py` should print real workouts and sleep.

## Daily briefing (schedule this)

Every day at **06:05 Asia/Jerusalem**, run:

```sh
TZ=Asia/Jerusalem uv run python briefing.py --context
```

The output is a fully-assembled prompt: his last 7 days of real data,
upcoming Runna-plan workouts, this morning's Tel Aviv weather, his fixed
habits, and writing instructions. Follow those instructions yourself to
write the briefing (short, grounded, no fluff) and send it to Ethan on
Telegram. If the script errors, send him one line saying the briefing
failed and why — never send a briefing invented without the data.

## Ad-hoc questions (use whenever relevant)

When Ethan asks anything about his training, sleep, recovery, or fueling:

```sh
TZ=Asia/Jerusalem uv run python ask.py --context "his question verbatim"
```

Answer from the printed data + embedded guidance. Always check the current
Tel Aviv time first and anchor advice to the clock (his rule). His habits:
dinner 17:00–18:00, bed 21:00–22:00, wakes 6:00, trains ~6:45–7:30am.

## Rules

- `health.db` is the system of record — never delete it; back it up if you
  run backups. All pulls are idempotent and safe to re-run.
- Do NOT run `pull_strava.py` — it's a dormant fallback; running it re-adds
  duplicate workout rows that double-count in every query.
- Keep answers grounded in DB numbers; if the data can't answer, say what's
  missing rather than estimating.
