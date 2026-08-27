"""Print a quick proof-of-life summary straight from health.db."""

import sqlite3

import db


def main() -> None:
    conn = db.connect()

    print("=== Workouts by week ===")
    for r in conn.execute(
        """SELECT strftime('%Y-W%W', date) wk,
                  COUNT(*) n,
                  ROUND(SUM(CASE WHEN type LIKE '%Run%' THEN distance_m ELSE 0 END) / 1609.34, 1) run_mi,
                  ROUND(SUM(duration_s) / 3600.0, 1) hours
           FROM workouts GROUP BY wk ORDER BY wk DESC LIMIT 4"""
    ):
        print(f"  {r['wk']}: {r['n']} workouts, {r['run_mi']} run miles, {r['hours']} total hrs")

    print("\n=== Recent workouts ===")
    for r in conn.execute(
        """SELECT date, type, name, ROUND(distance_m/1609.34,1) mi,
                  duration_s/60 mins, avg_hr
           FROM workouts ORDER BY date DESC LIMIT 10"""
    ):
        dist = f"{r['mi']}mi " if r["mi"] else ""
        hr = f"avg HR {round(r['avg_hr'])}" if r["avg_hr"] else "no HR"
        print(f"  {r['date']}  {r['type']:<16} {dist}{r['mins']}min  {hr}  — {r['name']}")

    print("\n=== Sleep ===")
    row = conn.execute(
        "SELECT ROUND(AVG(duration_s)/3600.0,1) avg_hrs, ROUND(AVG(resting_hr),1) avg_rhr, COUNT(*) n FROM sleep"
    ).fetchone()
    if row["n"]:
        print(f"  {row['n']} nights: avg {row['avg_hrs']} hrs, avg resting HR {row['avg_rhr']}")
        for r in conn.execute(
            "SELECT date, ROUND(duration_s/3600.0,1) hrs, resting_hr, score FROM sleep ORDER BY date DESC LIMIT 7"
        ):
            print(f"  {r['date']}: {r['hrs']} hrs, RHR {r['resting_hr']}, score {r['score']}")
    else:
        print("  no sleep data yet — run pull_garmin.py")


if __name__ == "__main__":
    main()
