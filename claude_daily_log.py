#!/usr/bin/env python3
"""
Export daily Claude Code conversations to Obsidian.

Parses ~/.claude/ session JSONL files, extracts user messages and Claude's
text responses from the specified date, and writes them as Obsidian notes.

Output structure:
    <output_dir>/YYYY-MM-DD/
        YYYY-MM-DD.md              (daily index with links to sessions)
        01 - <first message>.md    (session conversation)
        02 - <first message>.md
        ...

Configuration (env vars or config.json):
    OBSIDIAN_VAULT   - Path to Obsidian vault
    OUTPUT_SUBDIR    - Subfolder within vault (default: "Claude Logs")
    TIMEZONE_OFFSET  - UTC offset in hours (default: 8 for UTC+8)

Usage:
    python3 claude_daily_log.py                # Export today's conversations
    python3 claude_daily_log.py 2026-03-20     # Export a specific date
    python3 claude_daily_log.py --backfill 7   # Export last 7 days
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --- Configuration -----------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.json"

# Defaults
_defaults = {
    "obsidian_vault": "",
    "output_subdir": "Claude Logs",
    "timezone_offset": 8,
}


def _load_config():
    """Load config from config.json, with env var overrides."""
    cfg = dict(_defaults)

    # Load from config.json if exists
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            file_cfg = json.load(f)
            cfg.update({k: v for k, v in file_cfg.items() if v})

    # Env vars take precedence
    if os.environ.get("OBSIDIAN_VAULT"):
        cfg["obsidian_vault"] = os.environ["OBSIDIAN_VAULT"]
    if os.environ.get("OUTPUT_SUBDIR"):
        cfg["output_subdir"] = os.environ["OUTPUT_SUBDIR"]
    if os.environ.get("TIMEZONE_OFFSET"):
        cfg["timezone_offset"] = float(os.environ["TIMEZONE_OFFSET"])

    return cfg


_config = _load_config()

CLAUDE_DIR = Path.home() / ".claude"
HISTORY_FILE = CLAUDE_DIR / "history.jsonl"
PROJECTS_DIR = CLAUDE_DIR / "projects"

if not _config["obsidian_vault"]:
    # Auto-detect common Obsidian vault locations
    _candidates = [
        Path.home() / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents",  # macOS iCloud
        Path.home() / "Documents" / "Obsidian",
        Path.home() / "Obsidian",
    ]
    for c in _candidates:
        if c.exists():
            # Use first vault found inside
            for sub in sorted(c.iterdir()):
                if (sub / ".obsidian").exists():
                    _config["obsidian_vault"] = str(sub)
                    break
            if _config["obsidian_vault"]:
                break

if not _config["obsidian_vault"]:
    print("Error: Could not find Obsidian vault. Set OBSIDIAN_VAULT env var or edit config.json.")
    sys.exit(1)

OUTPUT_DIR = Path(_config["obsidian_vault"]) / _config["output_subdir"]
LOCAL_TZ = timezone(timedelta(hours=_config["timezone_offset"]))

# --- Core Logic --------------------------------------------------------------


def ts_to_local(ts_ms):
    """Convert millisecond timestamp to local datetime."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=LOCAL_TZ)


def iso_to_local(iso_str):
    """Convert ISO timestamp string to local datetime."""
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return dt.astimezone(LOCAL_TZ)


def get_sessions_for_date(target_date):
    """Read history.jsonl and return {sessionId: {project, entries}} for the target date."""
    sessions = {}
    if not HISTORY_FILE.exists():
        return sessions

    with open(HISTORY_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            ts = entry.get("timestamp", 0)
            dt = ts_to_local(ts)
            if dt.date() != target_date:
                continue
            sid = entry.get("sessionId", "")
            if not sid:
                continue
            if sid not in sessions:
                project = entry.get("project", "unknown")
                sessions[sid] = {
                    "project": project,
                    "display_entries": [],
                }
            sessions[sid]["display_entries"].append({
                "time": dt,
                "display": entry.get("display", ""),
            })
    return sessions


def find_session_file(session_id):
    """Find the JSONL file for a given session across all project dirs."""
    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        jsonl = project_dir / f"{session_id}.jsonl"
        if jsonl.exists():
            return jsonl
    return None


def extract_conversation(session_file, target_date):
    """Extract user messages and assistant text responses from a session JSONL file.

    Only includes messages from the target date. Groups consecutive assistant
    text blocks into a single response.
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
            timestamp_str = obj.get("timestamp", "")

            # Parse timestamp - could be ISO string or ms integer
            dt = None
            if isinstance(timestamp_str, str) and timestamp_str:
                try:
                    dt = iso_to_local(timestamp_str)
                except (ValueError, TypeError):
                    pass
            elif isinstance(timestamp_str, (int, float)) and timestamp_str > 0:
                dt = ts_to_local(timestamp_str)

            if dt and dt.date() != target_date:
                continue

            if msg_type == "user":
                flush_assistant()
                message = obj.get("message", {})
                content = message.get("content", "")
                # Only include actual user text input, not tool results
                if isinstance(content, str) and content.strip():
                    conversation.append({
                        "role": "user",
                        "time": dt,
                        "content": content.strip(),
                    })

            elif msg_type == "assistant":
                message = obj.get("message", {})
                content = message.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "").strip()
                            if text:
                                if current_assistant_time is None:
                                    current_assistant_time = dt
                                current_assistant_texts.append(text)

    flush_assistant()
    return conversation


def project_display_name(project_path):
    """Convert project path to a readable name."""
    if not project_path:
        return "unknown"
    name = os.path.basename(project_path)
    return name if name else project_path


def sanitize_filename(name, max_len=50):
    """Remove characters not safe for filenames."""
    for ch in r'\/:"*?<>|#^[]':
        name = name.replace(ch, "")
    name = name.strip(". ")
    if len(name) > max_len:
        name = name[:max_len].rstrip(". ")
    return name or "untitled"


# --- Note Generation ---------------------------------------------------------


def generate_session_note(target_date, session_id, project, conv, session_index):
    """Generate Obsidian markdown for a single session."""
    date_str = target_date.strftime("%Y-%m-%d")
    first_time = conv[0]["time"].strftime("%H:%M") if conv[0]["time"] else "?"
    project_name = project_display_name(project)

    first_user_msg = ""
    for msg in conv:
        if msg["role"] == "user":
            first_user_msg = msg["content"].split("\n")[0][:60]
            break
    title = first_user_msg or f"Session {session_index}"

    lines = [
        f"---",
        f"date: {date_str}",
        f"time: \"{first_time}\"",
        f"project: \"{project_name}\"",
        f"session_id: \"{session_id}\"",
        f"tags: [claude-log]",
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
        if msg["role"] == "user":
            lines.append(f"## 🧑 User `{time_str}`")
            lines.append("")
            lines.append(msg["content"])
            lines.append("")
        else:
            lines.append(f"## 🤖 Claude `{time_str}`")
            lines.append("")
            lines.append(msg["content"])
            lines.append("")

    return "\n".join(lines)


def generate_daily_index(target_date, session_notes):
    """Generate the daily index note that links to all session notes."""
    date_str = target_date.strftime("%Y-%m-%d")
    weekday = target_date.strftime("%A")

    total_sessions = len(session_notes)
    projects = set(n["project"] for n in session_notes)
    total_messages = sum(n["message_count"] for n in session_notes)

    lines = [
        f"---",
        f"date: {date_str}",
        f"tags: [claude-log, daily-index]",
        f"---",
        f"",
        f"# Claude Code Log - {date_str} ({weekday})",
        f"",
        f"**{total_sessions} sessions** across **{len(projects)} projects**, **{total_messages} messages** total.",
        f"",
        f"## Sessions",
        f"",
        f"| Time | Project | Topic | Messages |",
        f"|------|---------|-------|----------|",
    ]

    for n in sorted(session_notes, key=lambda x: x["time"]):
        lines.append(f"| {n['time']} | `{n['project']}` | [[{n['filename']}\\|{n['title']}]] | {n['message_count']} |")

    lines.append("")
    return "\n".join(lines)


# --- Export ------------------------------------------------------------------


def export_date(target_date):
    """Export conversations for a single date to Obsidian."""
    sessions = get_sessions_for_date(target_date)
    if not sessions:
        print(f"[{target_date}] No sessions found.")
        return False

    date_str = target_date.strftime("%Y-%m-%d")
    day_dir = OUTPUT_DIR / date_str
    day_dir.mkdir(parents=True, exist_ok=True)

    session_notes = []
    session_index = 0

    for sid, info in sessions.items():
        session_file = find_session_file(sid)
        if not session_file:
            continue
        conv = extract_conversation(session_file, target_date)
        if not conv:
            continue

        session_index += 1
        project = project_display_name(info["project"])
        first_time = conv[0]["time"].strftime("%H:%M") if conv[0]["time"] else "?"

        first_user_msg = ""
        for msg in conv:
            if msg["role"] == "user":
                first_user_msg = msg["content"].split("\n")[0][:60]
                break
        title = first_user_msg or f"Session {session_index}"

        safe_title = sanitize_filename(title)
        filename = f"{session_index:02d} - {safe_title}"

        note_content = generate_session_note(
            target_date, sid, info["project"], conv, session_index
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
        print(f"[{target_date}] No conversations with content found.")
        return False

    index_content = generate_daily_index(target_date, session_notes)
    index_file = day_dir / f"{date_str}.md"
    index_file.write_text(index_content, encoding="utf-8")

    print(f"[{target_date}] Exported {len(session_notes)} sessions to {day_dir}/")
    return True


def main():
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
