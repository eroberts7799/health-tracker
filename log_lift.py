"""Log a lift's top set into lifts.jsonl — the muscle-retention early warning.

    uv run python log_lift.py deadlift --top 140x3 --backoff "3x3 @130"
    uv run python log_lift.py bench --top 100x5 --date 2026-09-07
    uv run python log_lift.py --history deadlift

Why this exists: Garmin records reps and sets for strength sessions but
writes maxWeight 0 on every one, so the doctrine's "regressing lifts"
tripwire has no load data to read. Without it, nothing in the system would
catch muscle loss during the deficit until the monthly photos showed it.

Deliberately minimal — TOP SET ONLY, one main lift per session. Top set is
what regresses first in a deficit, and a logger that asks for every set is
a logger that gets abandoned. Backoff sets are free text, recorded but not
graded.

`lifts.jsonl` is append-only, same contract as meals.jsonl: to correct a
line, add "superseded": true to it and append a new one. Never delete.
"""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

LIFTS = Path(__file__).resolve().parent / "lifts.jsonl"


def parse_set(s: str) -> tuple[float, int]:
    """'140x3' -> (140.0, 3)"""
    weight, _, reps = s.lower().partition("x")
    if not reps:
        raise argparse.ArgumentTypeError(f"expected WEIGHTxREPS, got {s!r}")
    return float(weight), int(reps)


def epley_1rm(weight: float, reps: int) -> float:
    """Estimated 1RM. Epley; reasonable to ~10 reps, drifts high beyond that."""
    return round(weight * (1 + reps / 30), 1)


def read_all() -> list[dict]:
    if not LIFTS.exists():
        return []
    return [json.loads(l) for l in LIFTS.read_text().splitlines() if l.strip()]


def history(lift: str) -> None:
    rows = [r for r in read_all()
            if r["lift"] == lift.lower() and not r.get("superseded")]
    if not rows:
        print(f"no entries for {lift}")
        return
    for r in rows:
        print(f"{r['date']}  {r['top_kg']:g}kg x{r['top_reps']}"
              f"  e1RM {r['e1rm_kg']:g}kg"
              + (f"  [{r['backoff']}]" if r.get("backoff") else ""))
    first, last = rows[0], rows[-1]
    delta = last["e1rm_kg"] - first["e1rm_kg"]
    print(f"\n{len(rows)} sessions, e1RM {first['e1rm_kg']:g} -> "
          f"{last['e1rm_kg']:g}kg ({delta:+.1f})")


def main():
    p = argparse.ArgumentParser(description="Log a lift's top set")
    p.add_argument("lift", nargs="?", help="deadlift, squat, bench, press, row ...")
    p.add_argument("--top", type=parse_set, help="top set as WEIGHTxREPS, e.g. 140x3")
    p.add_argument("--backoff", default=None, help="free text, e.g. '3x3 @130'")
    p.add_argument("--date", default=None, help="YYYY-MM-DD, default today")
    p.add_argument("--time", default=None, help="HH:MM local, default now")
    p.add_argument("--notes", default=None)
    p.add_argument("--history", metavar="LIFT", help="print history for a lift and exit")
    args = p.parse_args()

    if args.history:
        return history(args.history)
    if not args.lift or not args.top:
        p.error("need a lift name and --top WEIGHTxREPS (or --history LIFT)")

    weight, reps = args.top
    row = {
        "date": args.date or date.today().isoformat(),
        "time": args.time or datetime.now().strftime("%H:%M"),
        "lift": args.lift.lower(),
        "top_kg": weight,
        "top_reps": reps,
        "backoff": args.backoff,
        "e1rm_kg": epley_1rm(weight, reps),
        "notes": args.notes,
        "logged_at": datetime.now().isoformat(timespec="seconds"),
    }
    with LIFTS.open("a") as f:
        f.write(json.dumps(row) + "\n")
    print(f"Logged {row['lift']}: {weight:g}kg x{reps} on {row['date']} "
          f"(e1RM {row['e1rm_kg']:g}kg)")


if __name__ == "__main__":
    sys.exit(main())
