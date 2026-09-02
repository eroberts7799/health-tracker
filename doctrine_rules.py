"""The doctrine, formalized — rule definitions and tunable parameters.

This file is the machine-checkable half of the doctrine; HERMES.md remains
the agent-facing prose. When a rule changes (an amendment is merged), it
changes HERE and in HERMES.md together, in one commit — the git history of
these two files is the doctrine's changelog.

Amendments 2026-09-02 (formalization): sleep floor 7h and the easy-run HR
ceiling 150 are NEW numbers set at formalization — the prose doctrine only
implied them. Tune with evidence, not vibes.
"""

DOCTRINE_START = "2026-08-30"   # nutrition rules exist from here
MEAL_LOG_START = "2026-08-29"   # no nutrition claims possible before this

PROTEIN_FLOOR_G = 150
FIBER_FLOOR_G = 30
SLEEP_FLOOR_S = 7 * 3600            # NEW at formalization
EASY_RUN_HR_CEILING = 150           # NEW at formalization; also the easy/quality fallback threshold
MIN_RUN_MINUTES = 10                # ignore warmup fragments

# Day templates (kcal bands). Standard carries the built-in ~500 deficit;
# big days deliberately shrink toward maintenance.
KCAL_BAND = {"standard": (1500, 2250), "big": (1800, 2700)}

# A run is "quality" if its Runna title says so; unplanned runs fall back to
# HR/duration. Big day = any quality run or any run > 75 min.
QUALITY_TITLE_WORDS = ("tempo", "interval", "long run", "800", "repeats", "pyramid")
EASY_TITLE_WORDS = ("easy",)
EASY_FALLBACK_MAX_MIN = 75

# Pre-training feed sizing (pre-lift is a snack, pre-run is a meal)
PRE_LIFT_MAX_KCAL = 330
PRE_RUN_CARBS_G = (50, 80)
PRE_MEAL_WINDOW_H = 3               # meal within 3h before first training start

# Weekly caps (rolling 7-day windows)
TUNA_CAP_PER_7D = 3
SALMON_DINNERS_PER_7D = 2

# Produce keyword net for the veg/fruit rule (lowercase substring match)
PRODUCE_WORDS = (
    "spinach", "pepper", "cucumber", "zucchini", "salad", "broccoli",
    "tomato", "carrot", "kale", "greens", "vegetable", "cauliflower",
    "apple", "banana", "watermelon", "blueberr", "strawberr", "kiwi",
    "orange", "mango", "grape", "peach", "melon", "fruit", "berries",
)
PRODUCE_MEALS_REQUIRED = 2          # veg/fruit at both real meals

# Tripwire windows (trend rules)
PROTEIN_STREAK_DAYS = 3             # 3 consecutive days under floor
NO_LOG_STREAK_DAYS = 2              # 2 consecutive days with zero meals
HR_CREEP_WINDOW_D = 14              # easy-run HR: mean of last 14d vs prior 14d
HR_CREEP_MIN_RUNS = 2               # per window, else NO_DATA
HR_CREEP_DELTA_BPM = 4              # rise >= this fires the tripwire
SLEEP_DECLINE_WINDOW_D = 7          # mean sleep score, last 7d vs prior 7d
SLEEP_DECLINE_POINTS = 8            # drop >= this fires the tripwire

RULES = {
    "protein_floor":  {"name": "Protein floor 150g",        "period": "day"},
    "fiber_floor":    {"name": "Fiber floor 30g",           "period": "day"},
    "produce":        {"name": "Veg/fruit at real meals",   "period": "day"},
    "kcal_template":  {"name": "Day-template kcal band",    "period": "day"},
    "pre_training":   {"name": "Pre-lift snack / pre-run meal", "period": "day"},
    "easy_run_hr":    {"name": "Easy-run HR ceiling 150",   "period": "run"},
    "sleep_floor":    {"name": "Sleep floor 7h",            "period": "day"},
    "tuna_cap":       {"name": "Tuna cap 3/wk",             "period": "window"},
    "salmon_cap":     {"name": "Salmon dinners 2/wk",       "period": "window"},
    "tw_protein":     {"name": "Tripwire: protein streak",  "period": "window"},
    "tw_no_logs":     {"name": "Tripwire: logging gap",     "period": "window"},
    "tw_hr_creep":    {"name": "Tripwire: easy-run HR creep", "period": "window"},
    "tw_sleep":       {"name": "Tripwire: sleep-score decline", "period": "window"},
}
