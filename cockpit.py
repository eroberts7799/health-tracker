"""Cockpit — the nightly one-page flight log rendered from verdicts.

    uv run python cockpit.py            # render cockpit.html + cockpit.png
    uv run python cockpit.py --send     # ...and send the PNG to Telegram
    uv run python cockpit.py --html-only

PNG needs `wkhtmltoimage` (apt install wkhtmltopdf). If it's missing the
HTML file is sent as a document instead, with a flag in the caption —
degraded, never blocked. Aesthetic contract: typeset flight log, no boxes,
hairlines only, one rust accent (see docs/designs/cockpit-wireframe.html).
"""

import argparse
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import db
import doctrine_eval
import doctrine_rules as R

OUT_HTML = Path(__file__).parent / "cockpit.html"
OUT_PNG = Path(__file__).parent / "cockpit.png"
SPARK_DAYS = 60

CSS = """
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: ui-monospace, "SF Mono", Menlo, monospace; color: #1a1a1a;
         background: #fcfcfa; width: 680px; margin: 0 auto; padding: 56px 24px 72px;
         line-height: 1.5; font-size: 14px; }
  .hairline { border: 0; border-top: 1px solid #d8d8d2; margin: 36px 0; }
  .eyebrow { font-size: 11px; letter-spacing: 0.18em; color: #8a8a82; text-transform: uppercase; }
  h1 { font-size: 32px; font-weight: 700; letter-spacing: -0.01em; margin: 6px 0 2px; }
  .dayline { font-size: 15px; color: #55554e; margin-top: 4px; }
  .rule { display: flex; justify-content: space-between; align-items: baseline;
          padding: 12px 0; border-bottom: 1px solid #ecece6; }
  .rule:last-child { border-bottom: 0; }
  .fail { color: #b3401f; font-weight: 700; }
  .nd { color: #8a8a82; }
  .m { color: #8a8a82; font-size: 13px; margin-left: 12px; font-weight: 400; }
  .spark-row { display: flex; justify-content: space-between; align-items: baseline;
               padding: 7px 0; font-size: 13px; }
  .spark-row > span:first-child { white-space: nowrap; margin-right: 16px; }
  .spark { color: #b9b9b0; font-size: 11px; letter-spacing: 0; white-space: nowrap; }
  .spark b { color: #1a1a1a; font-weight: 400; font-size: 13px; margin-left: 8px; }
  footer { color: #8a8a82; font-size: 13px; }
"""


def spark(statuses):
    return "".join({"PASS": "▇", "FAIL": "▁"}.get(s, "·") for s in statuses)


def build_html() -> str:
    conn = db.connect()
    conn.executescript(doctrine_eval.VERDICTS_SCHEMA)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with conn:
        doctrine_eval.evaluate(conn, yesterday)

    verdicts = conn.execute(
        """SELECT * FROM verdicts WHERE date=?
           ORDER BY CASE status WHEN 'FAIL' THEN 0 WHEN 'PASS' THEN 1 ELSE 2 END,
                    period, rule_id""", (yesterday,)
    ).fetchall()
    graded = [v for v in verdicts if v["status"] != "NO_DATA"]
    fails = [v for v in graded if v["status"] == "FAIL"]
    if fails:
        worst = min(fails, key=lambda v: v["margin"] if v["margin"] is not None else 0)
        dayline = (f"{len(graded) - len(fails)} of {len(graded)} rules holding. "
                   f"{R.RULES[worst['rule_id']]['name']} is the leak — {worst['detail']}.")
    else:
        dayline = f"All {len(graded)} graded rules holding."

    rows = []
    for v in verdicts:
        name = R.RULES.get(v["rule_id"], {}).get("name", v["rule_id"])
        m = f"{v['margin']:+.0f}" if v["margin"] is not None and v["status"] != "NO_DATA" else ""
        cls = {"FAIL": "fail", "NO_DATA": "nd"}.get(v["status"], "")
        retro = "retrospective" in (v["detail"] or "")
        detail = (v["detail"] or "").replace("retrospective; ", "")
        rows.append(
            f'<div class="rule"><span class="{cls}">{name}</span>'
            f'<span><span class="{cls}">{v["status"]}</span>'
            f'<span class="m">{m} {"· " + detail if detail else ""}{" · retro" if retro else ""}</span></span></div>'
        )

    since = (date.today() - timedelta(days=SPARK_DAYS)).isoformat()
    sparks = []
    for rule_id, meta in R.RULES.items():
        if meta["period"] != "day":
            continue
        hist = {r["date"]: r["status"] for r in conn.execute(
            "SELECT date, status FROM verdicts WHERE rule_id=? AND date >= ?", (rule_id, since))}
        days = [(date.today() - timedelta(days=i)).isoformat() for i in range(SPARK_DAYS, 0, -1)]
        statuses = [hist.get(d2) for d2 in days]
        graded_n = sum(1 for s in statuses if s in ("PASS", "FAIL"))
        if graded_n == 0:
            continue
        pct = 100 * sum(1 for s in statuses if s == "PASS") / graded_n
        sparks.append(
            f'<div class="spark-row"><span>{meta["name"]}</span>'
            f'<span class="spark">{spark(statuses)}<b>{pct:.0f}%</b></span></div>'
        )

    tripwires = [v for v in verdicts if v["rule_id"].startswith("tw_") and v["status"] == "FAIL"]
    tw_line = ("TRIPWIRE FIRED: " + ", ".join(R.RULES[v["rule_id"]]["name"] for v in tripwires)
               ) if tripwires else "No tripwires fired."

    today_h = date.today().strftime("%A, %B %-d")
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
  <div class="eyebrow">Doctrine · graded {yesterday}</div>
  <h1>{today_h}</h1>
  <div class="dayline">{dayline}</div>
  <hr class="hairline">
  <div class="eyebrow">Verdicts</div>
  {"".join(rows)}
  <hr class="hairline">
  <div class="eyebrow">{SPARK_DAYS}-day adherence</div>
  {"".join(sparks)}
  <hr class="hairline">
  <footer>{tw_line} · Amendment proposals arrive in the 06:05 briefing.</footer>
</body></html>"""


def render_png() -> bool:
    tool = shutil.which("wkhtmltoimage")
    if not tool:
        return False
    r = subprocess.run(
        [tool, "--width", "728", "--quality", "80", str(OUT_HTML), str(OUT_PNG)],
        capture_output=True, text=True, timeout=120,
    )
    return r.returncode == 0 and OUT_PNG.exists()


def main():
    p = argparse.ArgumentParser(description="Render the doctrine cockpit")
    p.add_argument("--send", action="store_true", help="send to Telegram")
    p.add_argument("--html-only", action="store_true")
    args = p.parse_args()

    OUT_HTML.write_text(build_html())
    print(f"wrote {OUT_HTML}")
    if args.html_only:
        return

    have_png = render_png()
    if have_png:
        print(f"wrote {OUT_PNG}")
    else:
        print("wkhtmltoimage unavailable or failed — PNG skipped", file=sys.stderr)

    if args.send:
        import notify
        if have_png:
            notify.send_photo(str(OUT_PNG), caption="cockpit")
        else:
            notify.send_document(str(OUT_HTML), caption="cockpit (PNG render unavailable — install wkhtmltopdf)")
        print("sent")


if __name__ == "__main__":
    sys.exit(main())
