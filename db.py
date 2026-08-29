"""SQLite layer — one file on disk (health.db), three tables.

Every row keeps a `raw` column with the source's full JSON so nothing is
lost while the parsed columns evolve. Upserts key on (source, external_id)
for workouts and date for sleep, so re-running a pull never duplicates.

Meals are append-only and their source of truth is meals.jsonl (one JSON
object per line, git-tracked) — NOT the meals table, which is rebuilt from
the file on every connect(). This is what lets multiple agents (Hermes on
the server, Claude Code on the Mac) share one food log through git while
health.db itself stays local and gitignored. Row ids are therefore not
stable across rebuilds; treat (logged_at) as the durable identity.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "health.db"
MEALS_PATH = Path(__file__).parent / "meals.jsonl"

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
  effort      REAL,                   -- Strava relative effort / Garmin training load
  calories    REAL,                   -- active kcal (Garmin: calories - bmrCalories)
  sweat_ml    REAL,                   -- Garmin estimated sweat loss
  aerobic_te  REAL,                   -- Garmin aerobic training effect 0-5
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

CREATE TABLE IF NOT EXISTS meals (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  date        TEXT NOT NULL,          -- YYYY-MM-DD, local
  time        TEXT,                   -- HH:MM local, if known
  description TEXT NOT NULL,          -- free text: "eggs, greek yogurt, cottage cheese, oats"
  calories    REAL,                   -- optional, only if estimated/known
  protein_g   REAL,
  carbs_g     REAL,
  fat_g       REAL,
  notes       TEXT,                   -- e.g. "pre-run", "rest day", context
  logged_at   TEXT NOT NULL           -- full timestamp this row was inserted, for audit
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for col in ("calories REAL", "sweat_ml REAL", "aerobic_te REAL"):
        try:
            conn.execute(f"ALTER TABLE workouts ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # column already exists
    _rebuild_meals(conn)
    return conn


def _rebuild_meals(conn: sqlite3.Connection) -> None:
    """The meals table is a cache of meals.jsonl — wipe and reload it so
    every reader sees whatever the last `git pull` brought in."""
    if not MEALS_PATH.exists():
        return
    rows = []
    for line in MEALS_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"warning: skipping malformed meals.jsonl line: {line[:80]}")
    with conn:
        conn.execute("DELETE FROM meals")
        for m in rows:
            conn.execute(
                """INSERT INTO meals (date, time, description, calories, protein_g,
                                      carbs_g, fat_g, notes, logged_at)
                   VALUES (:date, :time, :description, :calories, :protein_g,
                           :carbs_g, :fat_g, :notes, :logged_at)""",
                {k: m.get(k) for k in ("date", "time", "description", "calories",
                                       "protein_g", "carbs_g", "fat_g", "notes", "logged_at")},
            )


def upsert_workout(conn: sqlite3.Connection, w: dict) -> None:
    w = {**w, "raw": json.dumps(w.pop("raw", None))}
    for optional in ("calories", "sweat_ml", "aerobic_te"):
        w.setdefault(optional, None)
    conn.execute(
        """INSERT INTO workouts (source, external_id, date, start_time, type, name,
                                 duration_s, distance_m, avg_hr, max_hr, elev_gain_m, effort,
                                 calories, sweat_ml, aerobic_te, raw)
           VALUES (:source, :external_id, :date, :start_time, :type, :name,
                   :duration_s, :distance_m, :avg_hr, :max_hr, :elev_gain_m, :effort,
                   :calories, :sweat_ml, :aerobic_te, :raw)
           ON CONFLICT(source, external_id) DO UPDATE SET
             date=excluded.date, start_time=excluded.start_time, type=excluded.type,
             name=excluded.name, duration_s=excluded.duration_s, distance_m=excluded.distance_m,
             avg_hr=excluded.avg_hr, max_hr=excluded.max_hr, elev_gain_m=excluded.elev_gain_m,
             effort=excluded.effort, calories=excluded.calories, sweat_ml=excluded.sweat_ml,
             aerobic_te=excluded.aerobic_te, raw=excluded.raw""",
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


def add_meal(conn: sqlite3.Connection, m: dict) -> int:
    """Append one meal to meals.jsonl (the git-tracked source of truth) and
    refresh the table cache. Only `date` and `description` are required.
    Remember to commit+push meals.jsonl so other agents see it."""
    m = dict(m)
    for optional in ("time", "calories", "protein_g", "carbs_g", "fat_g", "notes"):
        m.setdefault(optional, None)
    m["logged_at"] = datetime.now().isoformat(timespec="seconds")
    with MEALS_PATH.open("a") as f:
        f.write(json.dumps(m, ensure_ascii=False) + "\n")
    _rebuild_meals(conn)
    return conn.execute("SELECT MAX(id) FROM meals").fetchone()[0]
