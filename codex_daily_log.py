#!/usr/bin/env python3
"""
Export daily OpenAI Codex CLI conversations to Obsidian.

Parses ~/.codex/sessions/ JSONL files and the SQLite metadata database,
extracts user messages and Codex's text responses, and writes them as
Obsidian notes — mirroring the format of claude_daily_log.py.

Output structure:
    <output_dir>/YYYY-MM-DD/
        YYYY-MM-DD.md              (daily index with links to sessions)
        01 - <first message>.md    (session conversation)
        02 - <first message>.md
        ...

Configuration: shares config.json with claude_daily_log.py, uses
    "codex_output_subdir" (default: "Codex Logs")

Usage:
    python3 codex_daily_log.py                # Export today's conversations
    python3 codex_daily_log.py 2026-03-20     # Export a specific date
    python3 codex_daily_log.py --backfill 7   # Export last 7 days
"""

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --- Configuration -----------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.json"

_defaults = {
    "obsidian_vault": "",
    "codex_output_subdir": "Codex Logs",
    "timezone_offset": 8,
}


def _load_config():
    cfg = dict(_defaults)
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            file_cfg = json.load(f)
            cfg.update({k: v for k, v in file_cfg.items() if v})
    if os.environ.get("OBSIDIAN_VAULT"):
        cfg["obsidian_vault"] = os.environ["OBSIDIAN_VAULT"]
    if os.environ.get("CODEX_OUTPUT_SUBDIR"):
        cfg["codex_output_subdir"] = os.environ["CODEX_OUTPUT_SUBDIR"]
    if os.environ.get("TIMEZONE_OFFSET"):
        cfg["timezone_offset"] = float(os.environ["TIMEZONE_OFFSET"])
    return cfg


_config = _load_config()

CODEX_DIR = Path.home() / ".codex"
SESSIONS_DIR = CODEX_DIR / "sessions"
STATE_DB = CODEX_DIR / "state_5.sqlite"

if not _config["obsidian_vault"]:
    _candidates = [
        Path.home() / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents",
        Path.home() / "Documents" / "Obsidian",
        Path.home() / "Obsidian",
    ]
    for c in _candidates:
        if c.exists():
            for sub in sorted(c.iterdir()):
                if (sub / ".obsidian").exists():
                    _config["obsidian_vault"] = str(sub)
                    break
            if _config["obsidian_vault"]:
                break

if not _config["obsidian_vault"]:
    print("Error: Could not find Obsidian vault. Set OBSIDIAN_VAULT env var or edit config.json.")
    sys.exit(1)

OUTPUT_DIR = Path(_config["obsidian_vault"]) / _config["codex_output_subdir"]
LOCAL_TZ = timezone(timedelta(hours=_config["timezone_offset"]))

# --- Helpers -----------------------------------------------------------------


def iso_to_local(iso_str):
    """Convert ISO timestamp string to local datetime."""
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return dt.astimezone(LOCAL_TZ)


def ts_to_local(ts_seconds):
    """Convert Unix timestamp (seconds) to local datetime."""
    return datetime.fromtimestamp(ts_seconds, tz=LOCAL_TZ)


# Trivial messages that don't constitute meaningful conversation
_TRIVIAL_PATTERNS = re.compile(
    r"^("
    r"h[ie]|hey|hello|yo|sup|嗨|你好|哈[喽啰罗]|"
    r"ok|okay|好的?|嗯|行|可以|知道了|收到|谢谢|thanks|thx|ty|"
    r"bye|再见|exit|quit|"
    r"你是什么模型|what model|which model|你是谁|who are you|"
    r"test|测试|ping"
    r")[？?！!。.\s]*$",
    re.IGNORECASE,
)

# Patterns that make bad titles
_BAD_TITLE_PATTERNS = re.compile(
    r"^("
    r"<[a-z]|"
    r"[~$]\s|"
    r"ssh\s|scp\s|rsync\s|"
    r"docker\s|kubectl\s|"
    r"git\s|cd\s|ls\s|cat\s|rm\s|"
    r"sudo\s|chmod\s|chown\s|"
    r"curl\s|wget\s|pip\s|npm\s|"
    r"[a-zA-Z0-9_.@-]+:\s*[~/]"
    r")",
    re.IGNORECASE,
)


def sanitize_filename(name, max_len=50):
    for ch in r'\/:"*?<>|#^[]':
        name = name.replace(ch, "")
    name = name.strip(". ")
    if len(name) > max_len:
        name = name[:max_len].rstrip(". ")
    return name or "untitled"


def _looks_like_code(text):
    lines = text.split("\n")
    if len(lines) < 3:
        return False
    if text.lstrip().startswith("```"):
        return False
    code_indicators = [
        "#!/", "if [[ ", "if ((", "for ((", "function ", "local ",
        "echo ", "cat ", "mkdir ", "curl ", "printf ",
        "import ", "from ", "def ", "class ", "return ",
        "const ", "let ", "var ", "function(",
        "= {", "=> {", "= (", "${", "$(", "<<'EOF'", "<<EOF",
    ]
    indicator_count = sum(1 for line in lines for ind in code_indicators if ind in line)
    return indicator_count >= 3


def _fence_wrap(text, lang=""):
    max_ticks = 2
    for m in re.finditer(r"`+", text):
        max_ticks = max(max_ticks, len(m.group()))
    fence = "`" * (max_ticks + 1)
    return f"{fence}{lang}\n{text}\n{fence}"


def _format_user_content(text):
    if _looks_like_code(text):
        if "#!/bin/bash" in text or "#!/bin/sh" in text or "${" in text:
            lang = "bash"
        elif "import " in text and ("def " in text or "from " in text):
            lang = "python"
        elif "const " in text or "function(" in text or "=> {" in text:
            lang = "javascript"
        else:
            lang = ""
        return _fence_wrap(text, lang)
    return text


# --- Session Discovery -------------------------------------------------------


def get_thread_metadata():
    """Read thread metadata from the Codex SQLite database."""
    meta = {}
    if not STATE_DB.exists():
        return meta
    try:
        conn = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        for row in conn.execute(
            "SELECT id, title, cwd, created_at, first_user_message FROM threads"
        ):
            meta[row["id"]] = {
                "title": row["title"],
                "cwd": row["cwd"],
                "created_at": row["created_at"],
                "first_user_message": row["first_user_message"],
            }
        conn.close()
    except Exception:
        pass
    return meta


def get_sessions_for_date(target_date):
    """Find Codex session JSONL files for a given date.

    Codex stores sessions in ~/.codex/sessions/YYYY/MM/DD/.
    Returns list of (session_id, session_file, metadata) tuples.
    """
    year = target_date.strftime("%Y")
    month = target_date.strftime("%m")
    day = target_date.strftime("%d")
    day_dir = SESSIONS_DIR / year / month / day
    if not day_dir.exists():
        return []

    thread_meta = get_thread_metadata()
    sessions = []

    for jsonl_file in sorted(day_dir.glob("*.jsonl")):
        # Extract session id from filename: rollout-<timestamp>-<uuid>.jsonl
        name = jsonl_file.stem
        parts = name.split("-", 2)  # rollout-YYYY-MM-DDTHH-MM-SS-<uuid>
        # The session ID is the UUID part, extract from session_meta inside the file
        session_id = None
        try:
            with open(jsonl_file) as f:
                first_line = f.readline().strip()
                if first_line:
                    obj = json.loads(first_line)
                    if obj.get("type") == "session_meta":
                        session_id = obj["payload"]["id"]
        except Exception:
            pass

        if not session_id:
            # Fallback: extract UUID from filename
            # Format: rollout-2026-03-08T15-02-37-019ccc41-4b3b-7e92-849d-db1a2d8160fd.jsonl
            # UUID is the last 5 hyphen-separated groups
            all_parts = name.split("-")
            if len(all_parts) >= 9:
                session_id = "-".join(all_parts[-5:])

        meta = thread_meta.get(session_id, {})
        sessions.append((session_id or name, jsonl_file, meta))

    return sessions


# --- Conversation Extraction -------------------------------------------------


def extract_conversation(session_file, target_date):
    """Extract user messages and assistant text responses from a Codex session JSONL.

    Codex event types:
    - event_msg (payload.type == "input"): user message in payload.message
    - response_item (payload.type == "message", role == "assistant"): text in
      payload.content[].text where content[].type == "output_text"
    - response_item (payload.type == "function_call"): tool call
    - response_item (payload.type == "function_call_output"): tool result
    """
    conversation = []
    current_assistant_texts = []
    current_assistant_time = None

    def flush_assistant():
        nonlocal current_assistant_texts, current_assistant_time
        if current_assistant_texts:
            conversation.append({
                "role": "assistant",
                "time": current_assistant_time,
                "content": "\n\n".join(current_assistant_texts),
            })
            current_assistant_texts = []
            current_assistant_time = None

    with open(session_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = obj.get("type", "")
            payload = obj.get("payload", {})
            timestamp_str = obj.get("timestamp", "")

            # Parse timestamp
            dt = None
            if isinstance(timestamp_str, str) and timestamp_str:
                try:
                    dt = iso_to_local(timestamp_str)
                except (ValueError, TypeError):
                    pass

            if dt and dt.date() != target_date:
                continue

            # User input: event_msg with payload.type == "user_message"
            if msg_type == "event_msg" and payload.get("type") == "user_message":
                flush_assistant()
                message = payload.get("message", "")
                if isinstance(message, str) and message.strip():
                    conversation.append({
                        "role": "user",
                        "time": dt,
                        "content": message.strip(),
                    })

            # Assistant response: response_item with type "message" and role "assistant"
            # phase can be "commentary" (thinking) or "final_answer" (response)
            # We include both to capture the full conversation.
            elif msg_type == "response_item" and payload.get("type") == "message":
                if payload.get("role") == "assistant":
                    content_blocks = payload.get("content", [])
                    for block in content_blocks:
                        if isinstance(block, dict) and block.get("type") == "output_text":
                            text = block.get("text", "").strip()
                            if text:
                                if current_assistant_time is None:
                                    current_assistant_time = dt
                                current_assistant_texts.append(text)

    flush_assistant()
    return conversation


def _is_trivial_session(conv):
    user_messages = [m for m in conv if m["role"] == "user"]
    if not user_messages:
        return True
    return all(_TRIVIAL_PATTERNS.match(m["content"]) for m in user_messages)


# --- Note Generation ---------------------------------------------------------


def _pick_title(conv):
    """Pick the best title from conversation messages."""
    for msg in conv:
        if msg["role"] == "user":
            text = msg["content"]
            if _TRIVIAL_PATTERNS.match(text):
                continue
            if _looks_like_code(text):
                continue
            if _BAD_TITLE_PATTERNS.match(text):
                continue
            return text.split("\n")[0][:60]
    return None


def generate_session_note(target_date, session_id, project, conv, session_index, meta_title=None):
    date_str = target_date.strftime("%Y-%m-%d")
    first_time = conv[0]["time"].strftime("%H:%M") if conv[0]["time"] else "?"
    project_name = project

    title = _pick_title(conv) or meta_title or f"Session {session_index}"

    lines = [
        f"---",
        f"date: {date_str}",
        f"time: \"{first_time}\"",
        f"project: \"{project_name}\"",
        f"session_id: \"{session_id}\"",
        f"tags: [codex-log]",
        f"---",
        f"",
        f"# {title}",
        f"",
        f"**Project**: `{project_name}` | **Started**: {first_time} | **Messages**: {len(conv)}",
        f"",
        f"---",
        f"",
    ]

    for msg in conv:
        time_str = msg["time"].strftime("%H:%M") if msg["time"] else ""
        role = msg["role"]
        if role == "user":
            lines.append(f"## 🧑 User `{time_str}`")
            lines.append("")
            lines.append(_format_user_content(msg["content"]))
            lines.append("")
        else:  # assistant
            lines.append(f"## 🤖 Codex `{time_str}`")
            lines.append("")
            lines.append(msg["content"])
            lines.append("")

    return "\n".join(lines)


def generate_daily_index(target_date, session_notes):
    date_str = target_date.strftime("%Y-%m-%d")
    weekday = target_date.strftime("%A")

    total_sessions = len(session_notes)
    projects = set(n["project"] for n in session_notes)
    total_messages = sum(n["message_count"] for n in session_notes)

    lines = [
        f"---",
        f"date: {date_str}",
        f"tags: [codex-log, daily-index]",
        f"---",
        f"",
        f"# Codex Log - {date_str} ({weekday})",
        f"",
        f"**{total_sessions} sessions** across **{len(projects)} projects**, **{total_messages} messages** total.",
        f"",
        f"## Sessions",
        f"",
        f"| Time | Project | Topic | Messages |",
        f"|------|---------|-------|----------|",
    ]

    for n in sorted(session_notes, key=lambda x: x["time"]):
        lines.append(
            f"| {n['time']} | `{n['project']}` | [[{n['filename']}\\|{n['title']}]] | {n['message_count']} |"
        )

    lines.append("")
    return "\n".join(lines)


# --- Export ------------------------------------------------------------------


def project_display_name(cwd):
    if not cwd:
        return "unknown"
    name = os.path.basename(cwd)
    return name if name else cwd


def export_date(target_date):
    sessions = get_sessions_for_date(target_date)
    if not sessions:
        print(f"[{target_date}] No Codex sessions found.")
        return False

    date_str = target_date.strftime("%Y-%m-%d")
    day_dir = OUTPUT_DIR / date_str
    day_dir.mkdir(parents=True, exist_ok=True)

    session_notes = []
    session_index = 0

    for session_id, session_file, meta in sessions:
        conv = extract_conversation(session_file, target_date)
        if not conv:
            continue
        if _is_trivial_session(conv):
            continue

        session_index += 1
        project = project_display_name(meta.get("cwd", ""))

        first_time = conv[0]["time"].strftime("%H:%M") if conv[0]["time"] else "?"

        meta_title = meta.get("title", "")
        title = _pick_title(conv) or meta_title or f"Session {session_index}"
        safe_title = sanitize_filename(title)
        filename = f"{session_index:02d} - {safe_title}"

        note_content = generate_session_note(
            target_date, session_id, project, conv, session_index, meta_title
        )
        output_file = day_dir / f"{filename}.md"
        output_file.write_text(note_content, encoding="utf-8")

        session_notes.append({
            "time": first_time,
            "project": project,
            "title": title,
            "filename": f"{date_str}/{filename}",
            "message_count": len(conv),
        })

    if not session_notes:
        print(f"[{target_date}] No Codex conversations with content found.")
        return False

    index_content = generate_daily_index(target_date, session_notes)
    index_file = day_dir / f"00 - {date_str}.md"
    index_file.write_text(index_content, encoding="utf-8")

    print(f"[{target_date}] Exported {len(session_notes)} Codex sessions to {day_dir}/")
    return True


def main():
    if not CODEX_DIR.exists():
        print("Error: ~/.codex not found. Is Codex CLI installed?")
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "--backfill":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        today = datetime.now(LOCAL_TZ).date()
        for i in range(days, -1, -1):
            export_date(today - timedelta(days=i))
    elif len(sys.argv) > 1:
        target_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        export_date(target_date)
    else:
        today = datetime.now(LOCAL_TZ).date()
        export_date(today)


if __name__ == "__main__":
    main()
