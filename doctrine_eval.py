"""Doctrine eval engine — grades every doctrine rule against health.db.

    uv run python doctrine_eval.py                 # evaluate yesterday (cron mode)
    uv run python doctrine_eval.py --date 2026-09-01
    uv run python doctrine_eval.py --backfill 90   # retrospective baseline

Verdicts land in the `verdicts` table (upsert — safe to re-run). Statuses:
PASS / FAIL / NO_DATA. NO_DATA means the data can't answer (unlabeled meals,
missing sleep row) and is EXCLUDED from adherence percentages — the doctrine
forbids guessing macros, so the evaluator refuses to guess too.

Dates before DOCTRINE_START are graded against today's doctrine and tagged
`retrospective` — context, not adherence judgment (the rules didn't exist).
Nutrition rules are never evaluated before MEAL_LOG_START.
"""

import argparse
import sys
from datetime import date, datetime, timedelta

import db
import doctrine_rules as R

VERDICTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS verdicts (
  rule_id    TEXT NOT NULL,
  subject_id TEXT NOT NULL,   -- date | workout external_id | window-end date
  date       TEXT NOT NULL,
  period     TEXT NOT NULL,   -- day | run | window
  status     TEXT NOT NULL,   -- PASS | FAIL | NO_DATA
  margin     REAL,
  detail     TEXT,
  UNIQUE (rule_id, subject_id, period)
);
"""


def upsert(conn, rule_id, subject_id, d, period, status, margin=None, detail=None, retro=False):
    if retro:
        detail = f"retrospective; {detail}" if detail else "retrospective"
    conn.execute(
        """INSERT INTO verdicts (rule_id, subject_id, date, period, status, margin, detail)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(rule_id, subject_id, period) DO UPDATE SET
             date=excluded.date, status=excluded.status,
             margin=excluded.margin, detail=excluded.detail""",
        (rule_id, subject_id, d, period, status, margin, detail),
    )


# ---------- classification ----------

def runs_on(conn, d):
    rows = conn.execute(
        "SELECT * FROM workouts WHERE date=? AND type LIKE '%run%'", (d,)
    ).fetchall()
    return [r for r in rows if (r["duration_s"] or 0) >= R.MIN_RUN_MINUTES * 60]


def classify_run(r) -> str:
    name = (r["name"] or "").lower()
    if any(w in name for w in R.QUALITY_TITLE_WORDS):
        return "quality"
    if any(w in name for w in R.EASY_TITLE_WORDS):
        return "easy"
    mins = (r["duration_s"] or 0) / 60
    hr = r["avg_hr"] or 0
    return "easy" if hr < R.EASY_RUN_HR_CEILING and mins < R.EASY_FALLBACK_MAX_MIN else "quality"


def day_type(conn, d) -> str:
    for r in runs_on(conn, d):
        if classify_run(r) == "quality" or (r["duration_s"] or 0) > 75 * 60:
            return "big"
    return "standard"


def meals_on(conn, d):
    return conn.execute("SELECT * FROM meals WHERE date=? ORDER BY time", (d,)).fetchall()


def macro_total(meals, field):
    """(total, complete) — complete=False if any meal lacks the field."""
    total, complete = 0.0, True
    for m in meals:
        v = m[field]
        if v is None:
            complete = False
        else:
            total += v
    return total, complete


# ---------- daily rules ----------

def eval_day(conn, d, retro):
    meals = meals_on(conn, d)

    if d >= R.MEAL_LOG_START:
        _eval_macro_floor(conn, d, meals, "protein_floor", "protein_g", R.PROTEIN_FLOOR_G, retro)
        _eval_macro_floor(conn, d, meals, "fiber_floor", "fiber_g", R.FIBER_FLOOR_G, retro)
        _eval_produce(conn, d, meals, retro)
        _eval_kcal(conn, d, meals, retro)
        _eval_pre_training(conn, d, meals, retro)

    _eval_sleep(conn, d, retro)

    for r in runs_on(conn, d):
        if classify_run(r) == "easy":
            hr = r["avg_hr"]
            if hr is None:
                upsert(conn, "easy_run_hr", r["external_id"], d, "run", "NO_DATA",
                       detail="no HR data", retro=retro)
            else:
                status = "PASS" if hr <= R.EASY_RUN_HR_CEILING else "FAIL"
                upsert(conn, "easy_run_hr", r["external_id"], d, "run", status,
                       margin=R.EASY_RUN_HR_CEILING - hr,
                       detail=f"avg {hr:.0f} vs ceiling {R.EASY_RUN_HR_CEILING}", retro=retro)


def _eval_macro_floor(conn, d, meals, rule_id, field, floor, retro):
    if not meals:
        upsert(conn, rule_id, d, d, "day", "NO_DATA", detail="no meals logged", retro=retro)
        return
    total, complete = macro_total(meals, field)
    if total >= floor:  # already cleared even with gaps — that's a PASS, not NO_DATA
        upsert(conn, rule_id, d, d, "day", "PASS", margin=total - floor,
               detail=f"{total:.0f}g logged", retro=retro)
    elif not complete:
        upsert(conn, rule_id, d, d, "day", "NO_DATA", margin=total - floor,
               detail=f"{total:.0f}g labeled, but unlabeled meals present", retro=retro)
    else:
        upsert(conn, rule_id, d, d, "day", "FAIL", margin=total - floor,
               detail=f"{total:.0f}g vs floor {floor}g", retro=retro)


def _eval_produce(conn, d, meals, retro):
    if not meals:
        upsert(conn, "produce", d, d, "day", "NO_DATA", detail="no meals logged", retro=retro)
        return
    hits = sum(1 for m in meals if any(w in (m["description"] or "").lower() for w in R.PRODUCE_WORDS))
    status = "PASS" if hits >= R.PRODUCE_MEALS_REQUIRED else "FAIL"
    upsert(conn, "produce", d, d, "day", status, margin=hits - R.PRODUCE_MEALS_REQUIRED,
           detail=f"{hits} meals with produce", retro=retro)


def _eval_kcal(conn, d, meals, retro):
    if not meals:
        upsert(conn, "kcal_template", d, d, "day", "NO_DATA", detail="no meals logged", retro=retro)
        return
    total, complete = macro_total(meals, "calories")
    dt = day_type(conn, d)
    lo, hi = R.KCAL_BAND[dt]
    if not complete:
        upsert(conn, "kcal_template", d, d, "day", "NO_DATA", margin=None,
               detail=f"{dt} day; {total:.0f} kcal labeled, unlabeled meals present", retro=retro)
        return
    status = "PASS" if lo <= total <= hi else "FAIL"
    margin = min(total - lo, hi - total)
    upsert(conn, "kcal_template", d, d, "day", status, margin=margin,
           detail=f"{total:.0f} kcal vs {dt} band {lo}-{hi}", retro=retro)


def _first_training(conn, d):
    rows = conn.execute(
        "SELECT * FROM workouts WHERE date=? AND start_time IS NOT NULL ORDER BY start_time", (d,)
    ).fetchall()
    return rows[0] if rows else None


def _eval_pre_training(conn, d, meals, retro):
    first = _first_training(conn, d)
    if first is None:
        return  # rest day — rule doesn't apply
    quality_run_day = any(classify_run(r) == "quality" for r in runs_on(conn, d))
    is_lift_day = "strength" in (first["type"] or "") or not quality_run_day
    start_hhmm = (first["start_time"] or "")[11:16]
    if not start_hhmm:
        return
    window_start = f"{max(0, int(start_hhmm[:2]) - R.PRE_MEAL_WINDOW_H):02d}{start_hhmm[2:]}"
    pre = [m for m in meals if m["time"] and window_start <= m["time"] < start_hhmm]
    if quality_run_day:
        if not pre:
            upsert(conn, "pre_training", d, d, "day", "FAIL", margin=-R.PRE_RUN_CARBS_G[0],
                   detail="quality run day, no pre-run meal logged", retro=retro)
            return
        carbs, complete = macro_total(pre, "carbs_g")
        if not complete and carbs < R.PRE_RUN_CARBS_G[0]:
            upsert(conn, "pre_training", d, d, "day", "NO_DATA",
                   detail="pre-run meal unlabeled", retro=retro)
            return
        lo, hi = R.PRE_RUN_CARBS_G
        status = "PASS" if carbs >= lo else "FAIL"
        upsert(conn, "pre_training", d, d, "day", status, margin=carbs - lo,
               detail=f"pre-run {carbs:.0f}g carbs vs {lo}-{hi}g", retro=retro)
    elif is_lift_day:
        if not pre:
            return  # fasted lift is allowed; sizing rule only grades what was eaten
        kcal, complete = macro_total(pre, "calories")
        if not complete:
            upsert(conn, "pre_training", d, d, "day", "NO_DATA",
                   detail="pre-lift meal unlabeled", retro=retro)
            return
        status = "PASS" if kcal <= R.PRE_LIFT_MAX_KCAL else "FAIL"
        upsert(conn, "pre_training", d, d, "day", status, margin=R.PRE_LIFT_MAX_KCAL - kcal,
               detail=f"pre-lift {kcal:.0f} kcal vs cap {R.PRE_LIFT_MAX_KCAL}", retro=retro)


def _eval_sleep(conn, d, retro):
    row = conn.execute("SELECT * FROM sleep WHERE date=?", (d,)).fetchone()
    if row is None or row["duration_s"] is None:
        upsert(conn, "sleep_floor", d, d, "day", "NO_DATA", detail="no sleep row", retro=retro)
        return
    hrs = row["duration_s"] / 3600
    status = "PASS" if row["duration_s"] >= R.SLEEP_FLOOR_S else "FAIL"
    upsert(conn, "sleep_floor", d, d, "day", status,
           margin=round(hrs - R.SLEEP_FLOOR_S / 3600, 2),
           detail=f"{hrs:.1f}h" + (f", score {row['score']:.0f}" if row["score"] else ""), retro=retro)


# ---------- windowed rules (window ends on d) ----------

def eval_windows(conn, d, retro):
    d_obj = date.fromisoformat(d)

    def span(days_back, end=d_obj):
        return ((end - timedelta(days=days_back - 1)).isoformat(), end.isoformat())

    if d >= R.MEAL_LOG_START:
        lo, hi = span(7)
        tuna = conn.execute(
            "SELECT COUNT(*) FROM meals WHERE date BETWEEN ? AND ? AND lower(description) LIKE '%tuna%'",
            (lo, hi)).fetchone()[0]
        upsert(conn, "tuna_cap", d, d, "window",
               "PASS" if tuna <= R.TUNA_CAP_PER_7D else "FAIL",
               margin=R.TUNA_CAP_PER_7D - tuna, detail=f"{tuna} tuna meals in 7d", retro=retro)

        salmon = conn.execute(
            "SELECT COUNT(*) FROM meals WHERE date BETWEEN ? AND ? AND lower(description) LIKE '%salmon%' AND time >= '16:00'",
            (lo, hi)).fetchone()[0]
        upsert(conn, "salmon_cap", d, d, "window",
               "PASS" if salmon <= R.SALMON_DINNERS_PER_7D else "FAIL",
               margin=R.SALMON_DINNERS_PER_7D - salmon, detail=f"{salmon} salmon dinners in 7d", retro=retro)

        # protein streak tripwire
        statuses = []
        for i in range(R.PROTEIN_STREAK_DAYS):
            day_i = (d_obj - timedelta(days=i)).isoformat()
            meals = meals_on(conn, day_i)
            total, complete = macro_total(meals, "protein_g")
            if not meals or (not complete and total < R.PROTEIN_FLOOR_G):
                statuses.append(None)
            else:
                statuses.append(total < R.PROTEIN_FLOOR_G)
        if None in statuses:
            upsert(conn, "tw_protein", d, d, "window", "NO_DATA",
                   detail="incomplete macro data in window", retro=retro)
        else:
            fired = all(statuses)
            upsert(conn, "tw_protein", d, d, "window", "FAIL" if fired else "PASS",
                   detail=f"{sum(statuses)}/{R.PROTEIN_STREAK_DAYS} recent days under floor", retro=retro)

        # logging-gap tripwire
        gap = all(
            not meals_on(conn, (d_obj - timedelta(days=i)).isoformat())
            for i in range(R.NO_LOG_STREAK_DAYS)
        )
        upsert(conn, "tw_no_logs", d, d, "window", "FAIL" if gap else "PASS",
               detail=f"last {R.NO_LOG_STREAK_DAYS} days", retro=retro)

    # HR-creep tripwire
    def easy_hrs(lo, hi):
        rows = conn.execute(
            "SELECT * FROM workouts WHERE date BETWEEN ? AND ? AND type LIKE '%run%'", (lo, hi)).fetchall()
        return [r["avg_hr"] for r in rows
                if (r["duration_s"] or 0) >= R.MIN_RUN_MINUTES * 60
                and classify_run(r) == "easy" and r["avg_hr"]]
    recent = easy_hrs(*span(R.HR_CREEP_WINDOW_D))
    prior = easy_hrs(*span(R.HR_CREEP_WINDOW_D, d_obj - timedelta(days=R.HR_CREEP_WINDOW_D)))
    if len(recent) < R.HR_CREEP_MIN_RUNS or len(prior) < R.HR_CREEP_MIN_RUNS:
        upsert(conn, "tw_hr_creep", d, d, "window", "NO_DATA",
               detail=f"{len(recent)}/{len(prior)} easy runs in windows", retro=retro)
    else:
        delta = sum(recent) / len(recent) - sum(prior) / len(prior)
        upsert(conn, "tw_hr_creep", d, d, "window",
               "FAIL" if delta >= R.HR_CREEP_DELTA_BPM else "PASS",
               margin=R.HR_CREEP_DELTA_BPM - delta, detail=f"easy-run HR delta {delta:+.1f} bpm", retro=retro)

    # sleep-score decline tripwire
    def scores(lo, hi):
        return [r["score"] for r in conn.execute(
            "SELECT score FROM sleep WHERE date BETWEEN ? AND ? AND score IS NOT NULL", (lo, hi))]
    recent_s = scores(*span(R.SLEEP_DECLINE_WINDOW_D))
    prior_s = scores(*span(R.SLEEP_DECLINE_WINDOW_D, d_obj - timedelta(days=R.SLEEP_DECLINE_WINDOW_D)))
    if len(recent_s) < 4 or len(prior_s) < 4:
        upsert(conn, "tw_sleep", d, d, "window", "NO_DATA",
               detail=f"{len(recent_s)}/{len(prior_s)} scored nights", retro=retro)
    else:
        drop = sum(prior_s) / len(prior_s) - sum(recent_s) / len(recent_s)
        upsert(conn, "tw_sleep", d, d, "window",
               "FAIL" if drop >= R.SLEEP_DECLINE_POINTS else "PASS",
               margin=R.SLEEP_DECLINE_POINTS - drop, detail=f"sleep score delta {-drop:+.1f}", retro=retro)


# ---------- driver ----------

def evaluate(conn, d):
    retro = d < R.DOCTRINE_START
    eval_day(conn, d, retro)
    eval_windows(conn, d, retro)


def main():
    p = argparse.ArgumentParser(description="Evaluate doctrine rules")
    p.add_argument("--date", default=None, help="YYYY-MM-DD (default: yesterday)")
    p.add_argument("--backfill", type=int, default=None, help="evaluate the last N days")
    args = p.parse_args()

    conn = db.connect()
    conn.executescript(VERDICTS_SCHEMA)

    if args.backfill:
        days = [(date.today() - timedelta(days=i)).isoformat() for i in range(1, args.backfill + 1)]
    else:
        days = [args.date or (date.today() - timedelta(days=1)).isoformat()]

    with conn:
        for d in days:
            evaluate(conn, d)

    lo, hi = min(days), max(days)
    for row in conn.execute(
        """SELECT status, COUNT(*) FROM verdicts WHERE date BETWEEN ? AND ?
           GROUP BY status ORDER BY status""", (lo, hi)):
        print(f"{row[0]}: {row[1]}")
    print(f"evaluated {len(days)} day(s) [{lo} .. {hi}]")


if __name__ == "__main__":
    sys.exit(main())
