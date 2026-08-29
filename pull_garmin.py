"""Pull sleep + resting HR from Garmin Connect into the sleep table.

Uses python-garminconnect (unofficial — mimics the official mobile app's
login). First run asks for your Garmin email/password (and MFA code if your
account has it) and caches OAuth tokens at ~/.garminconnect; later runs are
silent. Garmin has broken this login flow before (see the garth deprecation),
so if auth ever 401s persistently, check the library's issue tracker first.

Field parsing is defensive: Garmin's JSON shapes drift, so every field is a
.get() with the full payload kept in `raw` — a parse miss loses a column,
never the data.
"""

import os
import sys
from datetime import date, timedelta
from getpass import getpass
from pathlib import Path

from dotenv import load_dotenv
from garminconnect import Garmin

import db

load_dotenv(Path(__file__).parent / ".env")
TOKENSTORE = os.path.expanduser("~/.garminconnect")


def client() -> Garmin:
    api = Garmin(
        email=os.getenv("GARMIN_EMAIL") or input("Garmin email: "),
        password=os.getenv("GARMIN_PASSWORD") or getpass("Garmin password: "),
        prompt_mfa=lambda: input("Garmin MFA code: "),
    )
    api.login(tokenstore=TOKENSTORE)
    return api


def parse_sleep(day: str, data: dict) -> dict | None:
    dto = data.get("dailySleepDTO") or {}
    if not dto.get("sleepTimeSeconds"):
        return None  # no sleep recorded that night (watch off, etc.)
    scores = dto.get("sleepScores") or {}
    return {
        "date": day,
        "duration_s": dto.get("sleepTimeSeconds"),
        "deep_s": dto.get("deepSleepSeconds"),
        "light_s": dto.get("lightSleepSeconds"),
        "rem_s": dto.get("remSleepSeconds"),
        "awake_s": dto.get("awakeSleepSeconds"),
        "resting_hr": data.get("restingHeartRate"),
        "score": (scores.get("overall") or {}).get("value"),
        "source": "garmin",
        "raw": data,
    }


def parse_activity(a: dict) -> dict:
    start_local = a.get("startTimeLocal") or ""  # "2026-08-29 06:53:37"
    calories, bmr = a.get("calories"), a.get("bmrCalories")
    return {
        "source": "garmin",
        "external_id": str(a["activityId"]),
        "date": start_local[:10],
        "start_time": start_local,
        "type": (a.get("activityType") or {}).get("typeKey"),
        "name": a.get("activityName"),
        "duration_s": round(a["duration"]) if a.get("duration") else None,
        "distance_m": a.get("distance"),
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "elev_gain_m": a.get("elevationGain"),
        "effort": a.get("activityTrainingLoad"),
        "calories": (calories - bmr) if calories is not None and bmr is not None else calories,
        "sweat_ml": a.get("waterEstimated"),
        "aerobic_te": a.get("aerobicTrainingEffect"),
        "raw": a,
    }


def pull(days: int = 14) -> tuple[int, int]:
    api = client()
    conn = db.connect()
    start = (date.today() - timedelta(days=days - 1)).isoformat()

    activities = api.get_activities_by_date(start, date.today().isoformat())
    with conn:
        for a in activities:
            db.upsert_workout(conn, parse_activity(a))

    slept = 0
    with conn:
        for i in range(days):
            day = (date.today() - timedelta(days=i)).isoformat()
            try:
                data = api.get_sleep_data(day)
            except Exception as e:
                print(f"  {day}: fetch failed ({e}), skipping")
                continue
            row = parse_sleep(day, data or {})
            if row:
                db.upsert_sleep(conn, row)
                slept += 1
    return len(activities), slept


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    n_act, n_sleep = pull(days)
    print(f"Garmin: upserted {n_act} workouts and {n_sleep} nights of sleep from the last {days} days.")
