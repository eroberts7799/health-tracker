"""SQLite layer — one file on disk (health.db), two tables.

Every row keeps a `raw` column with the source's full JSON so nothing is
lost while the parsed columns evolve. Upserts key on (source, external_id)
for workouts and date for sleep, so re-running a pull never duplicates.
"""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "health.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS workouts (
  source      TEXT NOT NULL,          -- 'strava' | 'garmin'
  external_id TEXT NOT NULL,          -- the source's activity id
  date        TEXT NOT NULL,          -- YYYY-MM-DD (local start date)
  start_time  TEXT,                   -- full local timestamp
  type        TEXT,                   -- Run, WeightTraining, ...
  name        TEXT,
  duration_s  INTEGER,                -- moving time
  distance_m  REAL,
  avg_hr      REAL,
  max_hr      REAL,
  elev_gain_m REAL,
  effort      REAL,                   -- Strava relative effort, if present
  raw         TEXT,
  PRIMARY KEY (source, external_id)
);

CREATE TABLE IF NOT EXISTS sleep (
  date        TEXT PRIMARY KEY,       -- YYYY-MM-DD (the night ending this morning)
  duration_s  INTEGER,
  deep_s      INTEGER,
  light_s     INTEGER,
  rem_s       INTEGER,
  awake_s     INTEGER,
  resting_hr  REAL,
  score       REAL,                   -- Garmin sleep score 0-100, if present
  source      TEXT,
  raw         TEXT
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_workout(conn: sqlite3.Connection, w: dict) -> None:
    w = {**w, "raw": json.dumps(w.pop("raw", None))}
    conn.execute(
        """INSERT INTO workouts (source, external_id, date, start_time, type, name,
                                 duration_s, distance_m, avg_hr, max_hr, elev_gain_m, effort, raw)
           VALUES (:source, :external_id, :date, :start_time, :type, :name,
                   :duration_s, :distance_m, :avg_hr, :max_hr, :elev_gain_m, :effort, :raw)
           ON CONFLICT(source, external_id) DO UPDATE SET
             date=excluded.date, start_time=excluded.start_time, type=excluded.type,
             name=excluded.name, duration_s=excluded.duration_s, distance_m=excluded.distance_m,
             avg_hr=excluded.avg_hr, max_hr=excluded.max_hr, elev_gain_m=excluded.elev_gain_m,
             effort=excluded.effort, raw=excluded.raw""",
        w,
    )


def upsert_sleep(conn: sqlite3.Connection, s: dict) -> None:
    s = {**s, "raw": json.dumps(s.pop("raw", None))}
    conn.execute(
        """INSERT INTO sleep (date, duration_s, deep_s, light_s, rem_s, awake_s,
                              resting_hr, score, source, raw)
           VALUES (:date, :duration_s, :deep_s, :light_s, :rem_s, :awake_s,
                   :resting_hr, :score, :source, :raw)
           ON CONFLICT(date) DO UPDATE SET
             duration_s=excluded.duration_s, deep_s=excluded.deep_s, light_s=excluded.light_s,
             rem_s=excluded.rem_s, awake_s=excluded.awake_s, resting_hr=excluded.resting_hr,
             score=excluded.score, source=excluded.source, raw=excluded.raw""",
        s,
    )
