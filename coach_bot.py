"""Claude Code over Telegram — Ethan's personal coach chat.

Every text (or photo) Ethan sends is forwarded to a persistent headless
Claude Code session (`claude -p --resume <id>`) running in this repo with
tools enabled, so it can pull Garmin, query health.db, log meals, commit,
and read photos exactly like the terminal session on his Mac. The reply
is sent back verbatim. Only TELEGRAM_CHAT_ID is accepted; everyone else is
ignored.

Routing is plain code — no model decides anything here.

Commands:  /new  (start a fresh session)   /status   /help
Local test without Telegram:  uv run python coach_bot.py --ask "ping"

Env (.env): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, optional CLAUDE_BIN,
CLAUDE_MODEL. ANTHROPIC_API_KEY is stripped from the claude subprocess env
so it can never outbill the subscription.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))
API = f"https://api.telegram.org/bot{TOKEN}"
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL")  # optional --model override
TZ = ZoneInfo("Asia/Jerusalem")

SESSION_FILE = ROOT / ".coach_session"   # gitignored; holds the live session id
INBOX = ROOT / "inbox"                   # gitignored; downloaded photos land here
TURN_TIMEOUT_S = 900
API_RETRIES = 1                  # extra attempt on Anthropic-side api_error (529 etc.)
API_RETRY_WAIT_S = 20
ALLOWED_TOOLS = "Bash,Read,Edit,Write,Glob,Grep,WebSearch,WebFetch"
STARTED_AT = time.time()

_work: "queue.Queue[dict]" = queue.Queue()
_busy = threading.Event()


# ---------------------------------------------------------------- telegram
def send(text: str, chat_id: int = CHAT_ID) -> None:
    text = text or "(empty reply)"
    for i in range(0, len(text), 4000):  # Telegram caps messages at 4096 chars
        requests.post(API + "/sendMessage", json={"chat_id": chat_id, "text": text[i:i + 4000]}, timeout=30)


def typing_forever(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            requests.post(API + "/sendChatAction", json={"chat_id": CHAT_ID, "action": "typing"}, timeout=10)
        except Exception:
            pass
        stop.wait(4)


def download(file_id: str, suffix: str) -> Path:
    INBOX.mkdir(exist_ok=True)
    info = requests.get(API + "/getFile", params={"file_id": file_id}, timeout=30).json()["result"]
    data = requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{info['file_path']}", timeout=60).content
    path = INBOX / f"{datetime.now(TZ):%Y%m%d_%H%M%S}_{file_id[-8:]}{suffix}"
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------- claude
def r_elapsed(data: dict) -> float:
    return (data.get("duration_ms") or 0) / 1000


def run_claude(message: str, on_retry=None) -> str:
    """One turn against the persistent session; returns reply text."""
    stamp = datetime.now(TZ).strftime("%A %Y-%m-%d %H:%M")
    prompt = f"[Tel Aviv time now: {stamp}]\n{message}"

    cmd = [CLAUDE_BIN, "-p", "--output-format", "json",
           "--permission-mode", "acceptEdits", "--allowedTools", ALLOWED_TOOLS]
    if CLAUDE_MODEL:
        cmd += ["--model", CLAUDE_MODEL]
    sid = SESSION_FILE.read_text().strip() if SESSION_FILE.exists() else ""
    if sid:
        cmd += ["--resume", sid]

    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env["TZ"] = "Asia/Jerusalem"
    for attempt in range(API_RETRIES + 1):
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           timeout=TURN_TIMEOUT_S, cwd=ROOT, env=env)
        out = r.stdout.strip()
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            data = None
        # 529 Overloaded / 5xx from Anthropic: nothing local is wrong — wait and retry
        if data and data.get("terminal_reason") == "api_error" and attempt < API_RETRIES:
            status = data.get("api_error_status")
            print(f"api_error (status {status}), retry {attempt + 1}", flush=True)
            if on_retry:
                on_retry(f"Anthropic returned {status} (overloaded) after {int(r_elapsed(data))}s — retrying once.")
            time.sleep(API_RETRY_WAIT_S)
            continue
        break

    if data is None:  # no JSON at all: the CLI itself died
        err = (r.stderr.strip() or out or "no output")[:600]
        if sid and ("session" in err.lower() and "not found" in err.lower() or "No conversation found" in err):
            SESSION_FILE.unlink(missing_ok=True)   # stale id (server cleanup) — next turn starts fresh
            return f"⚠️ Lost the previous session ({err[:120]}). Send that again — starting fresh."
        return f"⚠️ claude -p failed (exit {r.returncode}): {err}"

    if data.get("session_id"):
        SESSION_FILE.write_text(data["session_id"])
    reply = (data.get("result") or "").strip()
    if data.get("is_error") or r.returncode != 0:
        # JSON came back but flagged an error: surface the human text, not the blob
        detail = reply or str(data.get("error") or data.get("errors") or "")
        if not detail:
            detail = ", ".join(f"{k}={data[k]}" for k in ("stop_reason", "terminal_reason", "num_turns") if k in data)
        stderr = r.stderr.strip()[:300]
        reply = f"⚠️ Claude turn errored (exit {r.returncode}): {detail[:800]}" + (f"\nstderr: {stderr}" if stderr else "")
    denials = data.get("permission_denials") or []
    if denials:
        names = ", ".join(sorted({d.get("tool_name", "?") for d in denials}))
        reply += f"\n\n(⚠️ {len(denials)} tool call(s) denied: {names} — answer may be incomplete)"
    return reply


# ---------------------------------------------------------------- routing
def handle_command(text: str) -> str:
    cmd = text.split()[0].lower()
    if cmd == "/new":
        SESSION_FILE.unlink(missing_ok=True)
        return "Fresh session. Context from before is gone (git + health.db + NOTES.md persist)."
    if cmd == "/status":
        sid = SESSION_FILE.read_text().strip()[:8] if SESSION_FILE.exists() else "none yet"
        up = int((time.time() - STARTED_AT) / 60)
        return f"session {sid} · up {up} min · busy={_busy.is_set()} · queued={_work.qsize()} · model={CLAUDE_MODEL or 'cli default'}"
    return "/new — fresh session\n/status — session + queue\nAnything else goes straight to Claude Code."


def message_to_prompt(msg: dict) -> str | None:
    text = (msg.get("text") or msg.get("caption") or "").strip()
    attachments = []
    if msg.get("photo"):
        best = max(msg["photo"], key=lambda p: p.get("file_size", 0))
        attachments.append(download(best["file_id"], ".jpg"))
    doc = msg.get("document") or {}
    if doc.get("mime_type", "").startswith("image/"):
        ext = Path(doc.get("file_name", "")).suffix or ".img"
        attachments.append(download(doc["file_id"], ext))
    if not text and not attachments:
        return None
    for p in attachments:
        text += f"\n[Photo attached — view it with the Read tool: {p}]"
    return text.strip()


def worker() -> None:
    while True:
        msg = _work.get()
        _busy.set()
        stop = threading.Event()
        threading.Thread(target=typing_forever, args=(stop,), daemon=True).start()
        try:
            prompt = message_to_prompt(msg)
            if prompt:
                send(run_claude(prompt, on_retry=send))
        except subprocess.TimeoutExpired:
            send(f"⚠️ Claude took longer than {TURN_TIMEOUT_S // 60} min and was cut off. Try a narrower ask.")
        except Exception as e:
            traceback.print_exc()
            send(f"⚠️ bot error: {e}")
        finally:
            stop.set()
            _busy.clear()
            _work.task_done()


def handle_update(u: dict) -> None:
    msg = u.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if chat_id != CHAT_ID:
        return  # personal bot: silently ignore everyone else
    text = (msg.get("text") or "").strip()
    if text.startswith("/"):
        send(handle_command(text))
        return
    if _busy.is_set():
        send("⏳ still on the last one — queued.")
    _work.put(msg)


def main() -> None:
    if not TOKEN or not CHAT_ID:
        sys.exit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env")
    threading.Thread(target=worker, daemon=True).start()
    offset = 0
    print("coach bot polling...", flush=True)
    while True:
        try:
            r = requests.get(API + "/getUpdates", params={"offset": offset, "timeout": 50}, timeout=60).json()
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                handle_update(u)
        except KeyboardInterrupt:
            sys.exit(0)
        except Exception:
            traceback.print_exc()
            time.sleep(5)


if __name__ == "__main__":
    if "--ask" in sys.argv:  # local smoke test: same code path, no Telegram
        print(run_claude(sys.argv[sys.argv.index("--ask") + 1]))
    else:
        main()
