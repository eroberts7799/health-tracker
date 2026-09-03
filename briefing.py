"""Morning briefing: pull fresh data and assemble today's full picture.

Context assembled: last night's sleep, recent training, upcoming planned
workouts (Runna plan via Garmin calendar), this morning's weather, and
Ethan's fixed habits.

Two modes:
  --context   pull fresh data, print the assembled prompt, and exit — for
              Hermes, which writes the briefing itself and messages Ethan
  (default)   generate via local `claude -p` and send via notify.py — the
              standalone fallback; `--dry` prints instead of sending
"""

import os
import subprocess
import sys
from datetime import date, timedelta

import requests

import db
import doctrine_eval
import notify
import pull_garmin

HABITS = """Fixed habits: wakes 6:00, trains ~6:45-7:30am, dinner 17:00-18:00,
bed 21:00-22:00. Runs 3x/week (Runna plan) + lifts 4x/week (leg/pull/push +
kettlebell). Goals: strength/muscle, endurance, leanness, recovery. Has LMNT,
whey, Greek yogurt, cottage cheese, oats on hand. Coffee = cortado."""

LAT, LON, TZ = 32.08, 34.78, "Asia/Jerusalem"  # Tel Aviv


def weather_today() -> str:
    try:
        d = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": LAT, "longitude": LON, "timezone": TZ,
                "hourly": "temperature_2m,apparent_temperature,relative_humidity_2m",
                "start_date": date.today().isoformat(),
                "end_date": date.today().isoformat(),
            },
            timeout=15,
        ).json()["hourly"]
    except Exception as e:
        return f"(weather fetch failed: {e})"
    lines = []
    for i, t in enumerate(d["time"]):
        if 6 <= int(t[11:13]) <= 9:
            lines.append(
                f"{t[11:16]}: {d['temperature_2m'][i]}°C "
                f"(feels {d['apparent_temperature'][i]}°C), "
                f"{d['relative_humidity_2m'][i]}% humidity"
            )
    return "\n".join(lines)


def planned_workouts(api) -> str:
    today = date.today()
    items = []
    months = {(today.year, today.month), ((today + timedelta(days=7)).year, (today + timedelta(days=7)).month)}
    for y, m in months:
        try:
            cal = api.get_scheduled_workouts(y, m)
            items += [i for i in cal.get("calendarItems", []) if i.get("itemType") == "workout"]
        except Exception as e:
            return f"(plan fetch failed: {e})"
    unique = {i["id"]: i for i in items if i.get("date") and i["date"] >= today.isoformat()}
    upcoming = sorted(unique.values(), key=lambda i: i["date"])[:4]
    if not upcoming:
        return "(no scheduled workouts found)"
    return "\n".join(f"{i['date']}: {i['title']}" for i in upcoming)


def recent_data() -> str:
    conn = db.connect()
    lines = ["Last 7 days of workouts (date | type | mi | min | avgHR | kcal | sweat_ml | load):"]
    for r in conn.execute(
        """SELECT date, type, ROUND(distance_m/1609.34,1) mi, duration_s/60 mins,
                  avg_hr, calories, sweat_ml, ROUND(effort) load
           FROM workouts WHERE date >= date('now','-7 days') ORDER BY date"""
    ):
        lines.append(f"{r['date']} | {r['type']} | {r['mi'] or '-'} | {r['mins']} | "
                     f"{r['avg_hr'] or '-'} | {r['calories'] or '-'} | {r['sweat_ml'] or '-'} | {r['load'] or '-'}")
    lines.append("\nLast 7 nights (date | hrs | deep_h | rem_h | RHR | score):")
    for r in conn.execute(
        """SELECT date, ROUND(duration_s/3600.0,1) h, ROUND(deep_s/3600.0,1) dh,
                  ROUND(rem_s/3600.0,1) rh, resting_hr, score
           FROM sleep WHERE date >= date('now','-7 days') ORDER BY date"""
    ):
        lines.append(f"{r['date']} | {r['h']} | {r['dh']} | {r['rh']} | {r['resting_hr']} | {r['score']}")
    return "\n".join(lines)


def verdicts_section() -> str:
    """Run yesterday's doctrine evals and format verdicts + 14-day history."""
    conn = db.connect()
    conn.executescript(doctrine_eval.VERDICTS_SCHEMA)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with conn:
        doctrine_eval.evaluate(conn, yesterday)

    lines = [f"Yesterday ({yesterday}), one line per rule (status | margin | detail):"]
    for r in conn.execute(
        "SELECT * FROM verdicts WHERE date=? ORDER BY status DESC, rule_id", (yesterday,)
    ):
        m = f"{r['margin']:+.0f}" if r["margin"] is not None else "-"
        lines.append(f"{r['rule_id']} | {r['status']} | {m} | {r['detail'] or ''}")

    lines.append("\n14-day adherence per rule (PASS/FAIL counts, NO_DATA excluded):")
    since = (date.today() - timedelta(days=14)).isoformat()
    for r in conn.execute(
        """SELECT rule_id, SUM(status='PASS') p, SUM(status='FAIL') f
           FROM verdicts WHERE date >= ? GROUP BY rule_id HAVING p + f > 0
           ORDER BY rule_id""", (since,)
    ):
        lines.append(f"{r['rule_id']}: {r['p']} PASS / {r['f']} FAIL")

    lines.append("\n14-day FAIL details (the only admissible amendment evidence):")
    fails = conn.execute(
        "SELECT date, rule_id, detail FROM verdicts WHERE date >= ? AND status='FAIL' ORDER BY date",
        (since,),
    ).fetchall()
    lines += [f"{r['date']} | {r['rule_id']} | {r['detail']}" for r in fails] or ["(none)"]
    return "\n".join(lines)


def build_prompt() -> str:
    api = pull_garmin.client()
    pull_garmin.pull(3)

    return f"""Write Ethan's morning briefing as his coach. It is {date.today().isoformat()},
~06:05 in Tel Aviv. He reads this on his phone at wake-up.

{HABITS}

## His real data (Garmin)
{recent_data()}

## Doctrine verdicts (computed by doctrine_eval.py — do not recompute)
{verdicts_section()}

## Planned workouts (Runna plan; days sometimes shift)
{planned_workouts(api)}

## This morning's weather (Tel Aviv)
{weather_today()}

Write a SHORT briefing (under 150 words, plain text, no markdown headers):
1. One line on last night's sleep and what it means for today.
2. Today's workout (planned or inferred) with a concrete pre-fuel
   instruction — what to eat and when, given his 6:45-7am start.
3. Hydration/electrolyte call keyed to the actual weather.
4. DOCTRINE line: yesterday's verdicts, compact ("protein +21 · kcal PASS ·
   fiber FAIL -12"). Copy statuses/margins from the verdicts section
   verbatim — never recompute. Skip NO_DATA rules unless the gap itself
   needs flagging (e.g. logging stopped). Name any fired tripwire plainly.
5. AMENDMENT (most days: none): only if the 14-day FAIL DETAILS show 3+
   failures whose details are attributable to the exact rule text you want
   to change. Cite the failing verdicts (dates + details) in the proposal,
   and propose exactly ONE amendment as a two-line diff ("- old rule" /
   "+ proposed rule"). A FAIL on a different sub-rule, or FAILs that
   predate the rule they broke, are NOT evidence — when in doubt, propose
   nothing. Rejected precedent (2026-09-02): a pre-RUN carb amendment was
   proposed off pre-LIFT cap failures; evidence must match the rule being
   amended.
Ground every number in the data. No generic filler, no motivational fluff."""


def build_briefing() -> str:
    result = subprocess.run(
        [os.environ.get("CLAUDE_BIN", "claude"), "-p"], input=build_prompt(), capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout).strip()
        sys.exit(f"claude -p failed: {err}")
    return result.stdout.strip()


if __name__ == "__main__":
    if "--context" in sys.argv:
        print(build_prompt())
    elif "--dry" in sys.argv:
        print("\n--- briefing (not sent) ---\n" + build_briefing())
    else:
        text = build_briefing()
        notify.send(text)
        print("briefing sent")
