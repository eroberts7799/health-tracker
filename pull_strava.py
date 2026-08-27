"""Pull recent activities from Strava into the workouts table.

Auth model: Strava gives long-lived *refresh* tokens and short-lived (6h)
*access* tokens. Each run trades the refresh token for a fresh access token.
Strava may hand back a NEW refresh token in that exchange — if so we save it
to .env immediately, because the old one may stop working. The same token is
shared with the ai-workout-dj Vercel cron, so a rotation gets loudly flagged.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

import db

ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH)


def refresh_access_token() -> str:
    res = requests.post(
        "https://www.strava.com/oauth/token",
        json={
            "client_id": os.environ["STRAVA_CLIENT_ID"],
            "client_secret": os.environ["STRAVA_CLIENT_SECRET"],
            "refresh_token": os.environ["STRAVA_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if not res.ok:
        sys.exit(f"Strava token refresh failed: HTTP {res.status_code} — {res.text}")
    data = res.json()

    new_refresh = data.get("refresh_token")
    if new_refresh and new_refresh != os.environ["STRAVA_REFRESH_TOKEN"]:
        text = ENV_PATH.read_text().replace(os.environ["STRAVA_REFRESH_TOKEN"], new_refresh)
        ENV_PATH.write_text(text)
        print("⚠️  Strava rotated the refresh token. Saved the new one to .env.")
        print("⚠️  Update STRAVA_REFRESH_TOKEN in the ai-workout-dj Vercel project too,")
        print(f"⚠️  or its daily sync may break. New token: {new_refresh}")

    return data["access_token"]


def parse_activity(a: dict) -> dict:
    start_local = a.get("start_date_local") or a.get("start_date") or ""
    return {
        "source": "strava",
        "external_id": str(a["id"]),
        "date": start_local[:10],
        "start_time": start_local,
        "type": a.get("sport_type") or a.get("type"),
        "name": a.get("name"),
        "duration_s": a.get("moving_time"),
        "distance_m": a.get("distance"),
        "avg_hr": a.get("average_heartrate"),
        "max_hr": a.get("max_heartrate"),
        "elev_gain_m": a.get("total_elevation_gain"),
        "effort": a.get("suffer_score"),  # Strava's "relative effort"
        "raw": a,
    }


def pull(days: int = 14) -> int:
    token = refresh_access_token()
    after = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    res = requests.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers={"Authorization": f"Bearer {token}"},
        params={"after": after, "per_page": 100},
        timeout=30,
    )
    if not res.ok:
        sys.exit(f"Strava activities fetch failed: HTTP {res.status_code} — {res.text}")
    activities = res.json()

    conn = db.connect()
    with conn:
        for a in activities:
            db.upsert_workout(conn, parse_activity(a))
    return len(activities)


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    n = pull(days)
    print(f"Strava: upserted {n} activities from the last {days} days.")
