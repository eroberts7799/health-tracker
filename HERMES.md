# Hermes integration — read me first (instructions for the Hermes agent)

You are being wired up as the delivery + chat layer for Ethan's personal
health tracker. The Python scripts here pull his real Garmin data (workouts
+ sleep) into a local SQLite DB and assemble grounded context blocks. Your
jobs: run the daily briefing, and answer ad-hoc questions from the data.

## One-time setup (on this server)

1. Install uv if absent: `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. In the repo root, create `.env` (chmod 600) with:
   ```
   GARMIN_EMAIL=...
   GARMIN_PASSWORD=...
   ```
   Ethan will provide the credentials in chat. NEVER echo them back,
   log them, or store them anywhere but `.env`.
3. First pull + backfill: `TZ=Asia/Jerusalem uv run python pull_garmin.py 93`
   This logs in to Garmin (tokens cached at `~/.garminconnect`, good ~1 year)
   and fills `health.db` with ~3 months of history.
   ⚠️ If login fails with 401, STOP and tell Ethan — do not retry in a loop;
   repeated bad-password attempts get the server IP rate-limited (429) by
   Garmin for a while.
4. Verify: `uv run python summary.py` should print real workouts and sleep.

## Model billing setup (one-time, on this server) — added 2026-08-31

**Why:** generating briefings/answers with an API-keyed model burns Ethan's
paid API credits. All heavy generation must go through the `claude` CLI
authenticated with his Max subscription instead.

**Status 2026-08-31: already done.** `claude` is installed on this server
and authed to Ethan's Max subscription via the OAuth browser flow
(authMethod claude.ai, subscriptionType max). Nothing to set up.

If auth ever breaks: re-run the OAuth browser flow (`claude` login) with
Ethan — **never accept a token/API key pasted in chat** (a secret in chat
history is a leak; Hermes correctly refused this once already). And keep
`ANTHROPIC_API_KEY` unset in the environment where `claude` runs — if both
auths are present the API key silently wins and bills API credits. Check
with `env | grep -o 'ANTHROPIC_API_KEY'` (name only — never print values).
Verify health with `echo "say ok" | claude -p`.

Known billing map (Hermes self-reported 2026-08-31): the Hermes agent loop
itself (Telegram routing/reasoning) runs on Nous Portal inference billed to
Ethan's Nous credits; everything piped through `claude -p` bills his Max
subscription. Keep heavy generation on `claude -p` so Nous credits only pay
for the light routing layer.

**Rule:** never write a briefing or data-grounded answer with your own
API-billed model. Assemble the prompt with the scripts, pipe it to
`claude -p`, deliver the output. If `claude -p` errors, send Ethan one line
saying so — do NOT quietly fall back to writing it yourself.

## Daily briefing (schedule this)

Every day at **06:05 Asia/Jerusalem**, run:

```sh
TZ=Asia/Jerusalem uv run python briefing.py --context | claude -p
```

`briefing.py --context` prints a fully-assembled prompt: his last 7 days of
real data, upcoming Runna-plan workouts, this morning's Tel Aviv weather,
his fixed habits, and writing instructions. Piping it to `claude -p` writes
the briefing on Ethan's subscription (see Model billing setup). Send the
output to Ethan on Telegram verbatim. If either the script or `claude -p`
errors, send him one line saying the briefing failed and why — never write
the briefing yourself and never send one invented without the data.

## Ad-hoc questions (use whenever relevant)

When Ethan asks anything about his training, sleep, recovery, or fueling:

```sh
TZ=Asia/Jerusalem uv run python ask.py --context "his question verbatim" | claude -p
```

Deliver the output verbatim — the assembled prompt already contains the data
and guidance, and `claude -p` bills his subscription, not API credits. If
the question needs expert grounding (see Expert grounding below), do the
search yourself first and append your findings to the question text you pass
to `ask.py`, so the searched positions reach the model writing the answer.
Always check the current Tel Aviv time first and anchor advice to the clock
(his rule). His habits: dinner 17:00–18:00, bed 21:00–22:00, wakes 6:00,
trains ~6:45–7:30am.

## Logging meals

When Ethan tells you what he ate — **in this chat, in Claude Code on his Mac,
or any other tool that has access to this repo** — log it. The flow is
pull → log → push, because meals sync between machines through git:

```sh
git pull --rebase
TZ=Asia/Jerusalem uv run python log_meal.py "eggs, greek yogurt, cottage cheese, oats" --notes "rest day breakfast"
git add meals.jsonl && git commit -m "log meal" && git push
```

**`meals.jsonl` (git-tracked, append-only) is the source of truth for food**
— the `meals` table in health.db is just a cache rebuilt from the file on
every connect. Never edit the table directly; never resolve a meals.jsonl
merge conflict by deleting lines (union both sides — every line is a meal
someone logged). Also `git pull` before answering nutrition questions so
you see meals logged from other machines.

Only `description` is required. Add `--calories`/`--protein`/`--carbs`/`--fat`
ONLY if you actually know/estimated them — never guess macros just to fill
the fields; an unlabeled meal (just the description) is still useful data.
Meals then show up automatically in `ask.py --context` output for future
questions, so nutrition answers get grounded in what he actually ate, not
just a one-off in-chat estimate that's gone once the conversation scrolls.

**This applies regardless of which agent/tool you are.** If he mentions food
to you, log it here so every other tool sees it too.

**Migration note (one-time, server):** if your local health.db `meals` table
has rows that predate meals.jsonl, re-log any that are missing from the file
(the 2026-08-29 meals — breakfast, 14:30 snack, sushi dinner — are ALREADY in
the file; do not re-add them, your old local sushi row is superseded).

## Meal doctrine (agreed with Ethan 2026-08-30)

- **Standard days** (lifts / easy runs / rest): big post-training breakfast
  (~700-900 kcal, 40g+ protein) + optional small ~14:30 snack + dinner
  17:00-18:00 (~700-800, 40g+ protein). ~1,900-2,200 kcal total — the
  built-in ~500 deficit is his fat-loss mechanism; don't add meals to it.
- **Big run days** (long runs, hard intervals): three real meals + pre-run
  carbs (50-80g) + in-run fuel past ~75 min. Deficit deliberately shrinks
  to ~maintenance — he does not cut on hard days.
- **Protein: 150g floor, 160g target, every day, both templates** (set
  2026-08-30 from Helms 2.3-3.1g/kg LBM for lean trainees in a deficit;
  Ethan self-reported 168lb @ ~15-17% BF → ~63-64kg LBM). Carbs flex with
  training; protein never does. In practice: breakfast 45-50g, the 14:30
  snack is a mandatory ~40-45g protein feed (whey + cottage cheese — his
  hunger peaks mid-afternoon, observed 2026-08-30), dinner 50g+. Pre-bed
  cottage cheese is CONDITIONAL, not ritual: only if dinner ran light or
  he's genuinely hungry at 20:30 — never force-fed.
- Progress metric: AESTHETIC only — he has no scale and doesn't want one;
  don't suggest weigh-ins. Monthly same-light front/side photos are the
  measure. Weight (168lb @ ~15-17% BF) is a self-reported 2026-08-30
  estimate; treat derived numbers (LBM, protein floor, TDEE) as ±10% and
  lean on the DB tripwires (easy-run HR creep, sleep-score decline,
  regressing lifts) to catch a mis-sized deficit instead.
- Grade his days against these templates in briefings and answers.

## Goal hierarchy (Ethan, 2026-08-30)

**Health / feeling / training / recovery FIRST; best possible look subject
to that.** Target: 12% BF (~8-12lb fat loss, ~12-16 wks) — chosen because
it's the leanest point that costs nothing on the primary goals; going below
is off the table. When look and health conflict, health wins automatically.
Consequences: tripwires (easy-run HR creep, week of declining sleep scores,
regressing lifts) trigger +200-300 kcal on standard days that same week, no
debate; a maintenance-week diet break every 6-8 weeks is mandatory;
vegetables/fruit at both real meals; sleep consistency outranks every
nutrition detail.

## Coaching tone (Ethan's explicit request, 2026-08-29)

Be ruthless — strictly optimization against his goals (strength/muscle,
endurance, leanness, recovery). State the optimal call first, grade his
actual/proposed choice against it with numbers, name the cost of the gap.
No reassurance padding, no "that's fine, enjoy!" framing. Direct ≠ hostile:
stay accurate and evidence-based, and if a choice IS optimal, say so in one
line and stop — never invent shortfalls.

## Expert grounding (per the `ethan-health-advisor` Hermes skill)

When a question calls for a specific protocol/threshold/named recommendation
(sodium, protein/lb, volume landmarks, carb timing, recovery), search for
that expert's actual published position before citing them — never answer
"as" an expert from memory alone. If no clear public position exists, say so
and cite the general evidence instead. Default panel: Attia (longevity/
biomarkers), Huberman (sleep/stress/recovery), Sisson (ancestral nutrition),
Jeukendrup (in-exercise fueling/sodium), Daniels/Magness (endurance
structure/VO2max), Israetel (strength/hypertrophy volume). Combine both
grounding sources in one answer: his real DB numbers (what's actually
happening) + a searched expert citation (what the optimal call is) — this
is what "state the optimal call first" above should be grounded in, not
memory alone.

## Rules

- `health.db` is the system of record — never delete it; back it up if you
  run backups. All pulls are idempotent and safe to re-run.
- Do NOT run `pull_strava.py` — it's a dormant fallback; running it re-adds
  duplicate workout rows that double-count in every query.
- Keep answers grounded in DB numbers; if the data can't answer, say what's
  missing rather than estimating.
