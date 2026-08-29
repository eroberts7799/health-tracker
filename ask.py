"""Ask a plain-English question against your real training data.

    uv run python ask.py "how did I sleep before my last three long runs?"

How it works: dumps the recent DB contents (workouts + sleep, compact text)
into a prompt and runs it through `claude -p` — headless Claude Code, billed
to the Max subscription, no API key needed. The dataset is small enough to
inline whole, so every answer is grounded in the actual numbers rather than
a retrieval guess. Revisit if the DB outgrows ~a year of history.
"""

import subprocess
import sys

import db

CONTEXT_DAYS = 90


def build_context(conn) -> str:
    lines = ["## Workouts (last %d days)" % CONTEXT_DAYS,
             "date | type | name | duration_min | distance_mi | avg_hr | max_hr | effort"]
    for r in conn.execute(
        """SELECT date, type, name, duration_s/60 mins,
                  ROUND(distance_m/1609.34,2) mi, avg_hr, max_hr, effort
           FROM workouts WHERE date >= date('now', ?) ORDER BY date""",
        (f"-{CONTEXT_DAYS} days",),
    ):
        lines.append(f"{r['date']} | {r['type']} | {r['name']} | {r['mins']} | "
                     f"{r['mi'] or ''} | {r['avg_hr'] or ''} | {r['max_hr'] or ''} | {r['effort'] or ''}")

    lines += ["", "## Sleep (last %d days; date = morning the night ended)" % CONTEXT_DAYS,
              "date | hours | deep_h | rem_h | resting_hr | garmin_score"]
    for r in conn.execute(
        """SELECT date, ROUND(duration_s/3600.0,1) h, ROUND(deep_s/3600.0,1) dh,
                  ROUND(rem_s/3600.0,1) rh, resting_hr, score
           FROM sleep WHERE date >= date('now', ?) ORDER BY date""",
        (f"-{CONTEXT_DAYS} days",),
    ):
        lines.append(f"{r['date']} | {r['h']} | {r['dh']} | {r['rh']} | {r['resting_hr']} | {r['score']}")
    return "\n".join(lines)


def ask(question: str) -> str:
    context = build_context(db.connect())
    prompt = f"""You are analyzing a 26-year-old athlete's real training and sleep data
(runs 3x/week on a Runna plan — typically one interval session, one easy run,
one long run, but the days shift week to week — plus 4x/week lifting; goals:
strength, endurance, leanness, recovery). The data does NOT label runs as
easy/intervals/long: infer type from HR, pace, and distance, and never assume
a high-HR run was a failed easy run — it may simply have been a workout day.
Answer using ONLY the data below. Cite the specific dates and numbers behind
every claim. If the data can't answer it, say what's missing — never estimate.

{context}

## Question
{question}"""
    result = subprocess.run(
        ["claude", "-p"], input=prompt, capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout).strip()
        if any(s in err.lower() for s in ("log in", "login", "authenticat", "api key", "credential")):
            sys.exit("The claude CLI isn't logged in — run `claude login` in a terminal, then retry.")
        sys.exit(f"claude -p failed: {err}")
    return result.stdout.strip()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--context"]
    if not args:
        sys.exit('usage: uv run python ask.py [--context] "your question"')
    question = " ".join(args)
    if "--context" in sys.argv:
        # For Hermes: print data + question; the calling agent answers itself.
        context = build_context(db.connect())
        print(f"{context}\n\n## Question\n{question}\n\n"
              "Answer using ONLY the data above; cite dates and numbers. "
              "Runs are not labeled easy/interval/long — infer from HR and pace; "
              "a high-HR day may simply have been a workout day.")
    else:
        print(ask(question))
