"""Log a meal into health.db — quick CLI for manual entry.

    uv run python log_meal.py "eggs, greek yogurt, cottage cheese, oats"
    uv run python log_meal.py "chicken, rice, broccoli" --time 13:30 --notes "post-run"
    uv run python log_meal.py "protein shake" --calories 180 --protein 30

Date defaults to today (local). No macro estimation happens here —
macros are optional and only recorded if you (or Hermes, from a stated
estimate) provide them explicitly. This keeps the DB honest: a meal with
no macros just means "this was eaten," not "we know the numbers."
"""

import argparse
import sys
from datetime import date, datetime

import db


def main():
    p = argparse.ArgumentParser(description="Log a meal")
    p.add_argument("description", help="what was eaten, free text")
    p.add_argument("--date", default=None, help="YYYY-MM-DD, default today")
    p.add_argument("--time", default=None, help="HH:MM local, default now")
    p.add_argument("--calories", type=float, default=None)
    p.add_argument("--protein", type=float, default=None, help="grams")
    p.add_argument("--carbs", type=float, default=None, help="grams")
    p.add_argument("--fat", type=float, default=None, help="grams")
    p.add_argument("--notes", default=None, help="e.g. 'pre-run', 'rest day'")
    args = p.parse_args()

    meal_date = args.date or date.today().isoformat()
    meal_time = args.time or datetime.now().strftime("%H:%M")

    conn = db.connect()
    row_id = db.add_meal(conn, {
        "date": meal_date,
        "time": meal_time,
        "description": args.description,
        "calories": args.calories,
        "protein_g": args.protein,
        "carbs_g": args.carbs,
        "fat_g": args.fat,
        "notes": args.notes,
    })
    print(f"Logged meal #{row_id}: {meal_date} {meal_time} — {args.description}")


if __name__ == "__main__":
    sys.exit(main())
