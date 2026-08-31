"""Pull recovery + sleep + workouts from the WHOOP v2 API into health.db.

Status: code-complete, demo-verified. I don't (yet) own a WHOOP, so real
pulls are untested against a live account — but the OAuth flow, endpoint
paths, and field mapping follow the current v2 docs (developer.whoop.com),
and `--demo` runs the full parse pipeline over synthetic v2-shaped payloads
WITHOUT touching health.db (the sleep table keys on date, so demo rows
would otherwise overwrite real Garmin nights).

Setup for a real pull:
  1. Create an app at developer-dashboard.whoop.com (scopes: read:recovery
     read:sleep read:workout read:cycles offline) with your redirect URL.
  2. Put WHOOP_CLIENT_ID / WHOOP_CLIENT_SECRET / WHOOP_REDIRECT_URI in .env.
  3. `python3 pull_whoop.py` — first run prints the authorize URL, asks for
     the ?code= from the redirect, then caches tokens at ~/.whoop-tokens.json
     (refresh handled automatically; WHOOP invalidates the old access token
     on every refresh).

Mapping into the existing schema (Garmin stays the canonical source; WHOOP
rows arrive as source='whoop' and last-writer-wins on sleep dates):
  recovery.resting_heart_rate  -> sleep.resting_hr (merged by date)
  sleep score_stage_summary    -> sleep.deep_s/light_s/rem_s/awake_s
  sleep_performance_percentage -> sleep.score
  workout strain               -> workouts.effort
  workout kilojoule            -> workouts.calories (kJ / 4.184)
HRV, SpO2, skin temp, and zone durations have no columns — they ride in
`raw` like every other source's extras.
"""

import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

import db

load_dotenv(Path(__file__).parent / ".env")

AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
BASE = "https://api.prod.whoop.com/developer"
TOKENS = Path.home() / ".whoop-tokens.json"
SCOPES = "read:recovery read:sleep read:workout read:cycles offline"


# --- OAuth ---------------------------------------------------------------


def _save_tokens(t: dict) -> None:
    t["_saved_at"] = time.time()
    TOKENS.write_text(json.dumps(t))
    TOKENS.chmod(0o600)


def _authorize() -> dict:
    cid = os.environ["WHOOP_CLIENT_ID"]
    redirect = os.environ["WHOOP_REDIRECT_URI"]
    print(
        "Open this URL, approve access, then paste the ?code= from the "
        f"redirect:\n\n{AUTH_URL}?client_id={cid}&redirect_uri={redirect}"
        f"&response_type=code&scope={SCOPES.replace(' ', '%20')}\n"
    )
    code = input("code: ").strip()
    r = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": cid,
            "client_secret": os.environ["WHOOP_CLIENT_SECRET"],
            "redirect_uri": redirect,
        },
        timeout=30,
    )
    r.raise_for_status()
    t = r.json()
    _save_tokens(t)
    return t


def _refresh(t: dict) -> dict:
    r = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": t["refresh_token"],
            "client_id": os.environ["WHOOP_CLIENT_ID"],
            "client_secret": os.environ["WHOOP_CLIENT_SECRET"],
        },
        timeout=30,
    )
    r.raise_for_status()
    t = r.json()
    _save_tokens(t)
    return t


def access_token() -> str:
    if not TOKENS.exists():
        t = _authorize()
    else:
        t = json.loads(TOKENS.read_text())
        expired = time.time() > t.get("_saved_at", 0) + t.get("expires_in", 0) - 60
        if expired:
            t = _refresh(t)
    return t["access_token"]


# --- API -----------------------------------------------------------------


def _paged(path: str, token: str, start_iso: str) -> list[dict]:
    """Walk nextToken pagination (limit caps at 25 per the v2 docs)."""
    records, next_token = [], None
    while True:
        params = {"limit": 25, "start": start_iso}
        if next_token:
            params["nextToken"] = next_token
        r = requests.get(
            f"{BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        records += body.get("records", [])
        next_token = body.get("next_token") or body.get("nextToken")
        if not next_token:
            return records


# --- Parsing (defensive: shapes drift, raw keeps everything) -------------


def _local_date(iso: str | None) -> str | None:
    if not iso:
        return None
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.astimezone().date().isoformat()


def parse_sleep(rec: dict) -> dict | None:
    score = rec.get("score") or {}
    stages = score.get("stage_summary") or {}
    ms = lambda k: stages.get(k)  # noqa: E731
    to_s = lambda v: round(v / 1000) if v is not None else None  # noqa: E731
    light = to_s(ms("total_light_sleep_time_milli"))
    deep = to_s(ms("total_slow_wave_sleep_time_milli"))
    rem = to_s(ms("total_rem_sleep_time_milli"))
    awake = to_s(ms("total_awake_time_milli"))
    asleep = sum(v for v in (light, deep, rem) if v is not None) or None
    day = _local_date(rec.get("end"))
    if not day or asleep is None:
        return None
    return {
        "date": day,  # night ends this morning, matching Garmin's convention
        "duration_s": asleep,
        "deep_s": deep,
        "light_s": light,
        "rem_s": rem,
        "awake_s": awake,
        "resting_hr": None,  # filled from recovery, merged by date
        "score": score.get("sleep_performance_percentage"),
        "source": "whoop",
        "raw": rec,
    }


def parse_workout(rec: dict) -> dict | None:
    score = rec.get("score") or {}
    start, end = rec.get("start"), rec.get("end")
    if not rec.get("id") or not start:
        return None
    dur = None
    if start and end:
        t0 = datetime.fromisoformat(start.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(end.replace("Z", "+00:00"))
        dur = round((t1 - t0).total_seconds())
    kj = score.get("kilojoule")
    return {
        "source": "whoop",
        "external_id": str(rec["id"]),
        "date": _local_date(start),
        "start_time": datetime.fromisoformat(start.replace("Z", "+00:00"))
        .astimezone()
        .strftime("%Y-%m-%d %H:%M:%S"),
        "type": rec.get("sport_name"),
        "name": rec.get("sport_name"),
        "duration_s": dur,
        "distance_m": score.get("distance_meter"),
        "avg_hr": score.get("average_heart_rate"),
        "max_hr": score.get("max_heart_rate"),
        "elev_gain_m": score.get("altitude_gain_meter"),
        "effort": score.get("strain"),
        "calories": round(kj / 4.184) if kj is not None else None,
        "sweat_ml": None,
        "aerobic_te": None,
        "raw": rec,
    }


def recovery_by_date(recs: list[dict]) -> dict[str, dict]:
    out = {}
    for rec in recs:
        day = _local_date(rec.get("created_at") or rec.get("updated_at"))
        score = rec.get("score") or {}
        if day and score.get("resting_heart_rate") is not None:
            out[day] = score
    return out


# --- Pull ----------------------------------------------------------------


def pull(days: int = 14) -> tuple[int, int]:
    token = access_token()
    start_iso = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).isoformat().replace("+00:00", "Z")

    sleeps = _paged("/v2/activity/sleep", token, start_iso)
    workouts = _paged("/v2/activity/workout", token, start_iso)
    recoveries = _paged("/v2/recovery", token, start_iso)
    return _ingest(sleeps, workouts, recoveries, db.connect())


def _ingest(sleeps, workouts, recoveries, conn) -> tuple[int, int]:
    rhr = recovery_by_date(recoveries)
    n_sleep = n_wo = 0
    with conn:
        for rec in sleeps:
            row = parse_sleep(rec)
            if row:
                rec_score = rhr.get(row["date"]) or {}
                row["resting_hr"] = rec_score.get("resting_heart_rate")
                db.upsert_sleep(conn, row)
                n_sleep += 1
        for rec in workouts:
            row = parse_workout(rec)
            if row:
                db.upsert_workout(conn, row)
                n_wo += 1
    return n_wo, n_sleep


# --- Demo mode: synthetic v2-shaped payloads, never writes health.db -----


def _demo_payloads() -> tuple[list[dict], list[dict], list[dict]]:
    """Synthetic records in the documented v2 response shape. Clearly fake
    (SYNTHETIC in ids); exist so the parse+merge pipeline is demonstrable
    without a WHOOP account."""
    today = datetime.now(timezone.utc).replace(hour=5, minute=45)
    iso = lambda dt: dt.isoformat().replace("+00:00", "Z")  # noqa: E731
    sleeps, workouts, recoveries = [], [], []
    for i in range(3):
        night_end = today - timedelta(days=i)
        sleeps.append(
            {
                "id": f"SYNTHETIC-sleep-{i}",
                "start": iso(night_end - timedelta(hours=8)),
                "end": iso(night_end),
                "score": {
                    "sleep_performance_percentage": 88 - 3 * i,
                    "stage_summary": {
                        "total_light_sleep_time_milli": 14_400_000,
                        "total_slow_wave_sleep_time_milli": 6_000_000,
                        "total_rem_sleep_time_milli": 7_200_000,
                        "total_awake_time_milli": 1_500_000,
                    },
                },
            }
        )
        recoveries.append(
            {
                "id": f"SYNTHETIC-recovery-{i}",
                "created_at": iso(night_end),
                "score": {
                    "recovery_score": 82 - 5 * i,
                    "resting_heart_rate": 48 + i,
                    "hrv_rmssd_milli": 92 - 4 * i,
                },
            }
        )
        workouts.append(
            {
                "id": f"SYNTHETIC-workout-{i}",
                "start": iso(night_end + timedelta(hours=1)),
                "end": iso(night_end + timedelta(hours=1, minutes=52)),
                "sport_name": ["running", "strength", "cycling"][i],
                "score": {
                    "strain": 14.2 - 2 * i,
                    "average_heart_rate": 152 - 8 * i,
                    "max_heart_rate": 181 - 6 * i,
                    "kilojoule": 2600 - 400 * i,
                    "distance_meter": [10500, None, 31000][i],
                },
            }
        )
    return sleeps, workouts, recoveries


def demo() -> None:
    sleeps, workouts, recoveries = _demo_payloads()
    rhr = recovery_by_date(recoveries)
    print("WHOOP v2 demo parse (synthetic payloads — health.db untouched):\n")
    for rec in sleeps:
        row = parse_sleep(rec)
        assert row, "demo sleep payload failed to parse"
        row["resting_hr"] = (rhr.get(row["date"]) or {}).get("resting_heart_rate")
        h = row["duration_s"] / 3600
        print(
            f"  sleep {row['date']}: {h:.1f}h asleep, perf {row['score']}%, "
            f"resting {row['resting_hr']} bpm (deep {row['deep_s'] // 60}m / "
            f"rem {row['rem_s'] // 60}m)"
        )
    for rec in workouts:
        row = parse_workout(rec)
        assert row, "demo workout payload failed to parse"
        print(
            f"  workout {row['date']}: {row['type']}, {row['duration_s'] // 60}m, "
            f"strain {row['effort']}, {row['calories']} kcal"
        )
    print("\nSame rows the Garmin pull produces — briefing.py wouldn't know "
          "the difference. That's the point.")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
        n_wo, n_sleep = pull(days)
        print(
            f"WHOOP: upserted {n_wo} workouts and {n_sleep} nights of sleep "
            f"from the last {days} days."
        )
