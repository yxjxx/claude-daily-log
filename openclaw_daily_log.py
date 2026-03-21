#!/usr/bin/env python3
"""
Export daily OpenClaw conversations to Obsidian.

Parses ~/.openclaw/agents/*/sessions/*.jsonl, extracts user messages and
assistant text responses, and writes them as Obsidian notes — mirroring
the format of claude_daily_log.py.

Output structure:
    <output_dir>/YYYY-MM-DD/
        00 - YYYY-MM-DD.md              (daily index with links to sessions)
        01 - <first message>.md         (session conversation)
        02 - <first message>.md
        ...

Configuration: shares config.json with claude_daily_log.py, uses
    "openclaw_output_subdir" (default: "OpenClaw Logs")

Usage:
    python3 openclaw_daily_log.py                # Export today's conversations
    python3 openclaw_daily_log.py 2026-03-20     # Export a specific date
    python3 openclaw_daily_log.py --backfill 7   # Export last 7 days
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --- Configuration -----------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.json"

_defaults = {
    "obsidian_vault": "",
    "openclaw_output_subdir": "OpenClaw Logs",
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
    if os.environ.get("OPENCLAW_OUTPUT_SUBDIR"):
        cfg["openclaw_output_subdir"] = os.environ["OPENCLAW_OUTPUT_SUBDIR"]
    if os.environ.get("TIMEZONE_OFFSET"):
        cfg["timezone_offset"] = float(os.environ["TIMEZONE_OFFSET"])
    return cfg


_config = _load_config()

OPENCLAW_DIR = Path.home() / ".openclaw"
AGENTS_DIR = OPENCLAW_DIR / "agents"

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

OUTPUT_DIR = Path(_config["obsidian_vault"]) / _config["openclaw_output_subdir"]
LOCAL_TZ = timezone(timedelta(hours=_config["timezone_offset"]))

# --- Helpers -----------------------------------------------------------------


def iso_to_local(iso_str):
    """Convert ISO timestamp string to local datetime."""
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return dt.astimezone(LOCAL_TZ)


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

# Pattern to extract actual user text from OpenClaw's metadata-wrapped messages
# OpenClaw wraps Telegram messages with "Conversation info" and "Sender" JSON blocks
_METADATA_BLOCK_RE = re.compile(
    r"(?:Conversation info \(untrusted metadata\):\s*```json\s*\{[^}]*\}\s*```\s*)"
    r"|(?:Sender \(untrusted metadata\):\s*```json\s*\{[^}]*\}\s*```\s*)",
    re.DOTALL,
)

# System session messages to skip entirely
_SESSION_MSG_PATTERNS = [
    re.compile(r"^A new session was started via /new or /reset"),
    re.compile(r"^✅ New session started"),
    # HEARTBEAT polling — system-level, not user conversation
    re.compile(r"^Read HEARTBEAT\.md if it exists"),
    re.compile(r"^HEARTBEAT_OK"),
]

# Cron trigger pattern: [cron:<uuid> <task-name>] <instructions...>
_CRON_TRIGGER_RE = re.compile(
    r"^\[cron:[0-9a-f-]+ ([^\]]+)\]\s*",
    re.IGNORECASE,
)

# Trailing "Current time:" line appended by OpenClaw to cron/system messages
_CURRENT_TIME_RE = re.compile(
    r"\nCurrent time:.*$",
    re.MULTILINE,
)

# Trailing delivery instruction appended by OpenClaw
_DELIVERY_INSTRUCTION_RE = re.compile(
    r"\nReturn your summary as plain text;.*$",
    re.MULTILINE | re.DOTALL,
)


def _extract_user_text(text):
    """Strip OpenClaw metadata wrappers and return the actual user message."""
    cleaned = _METADATA_BLOCK_RE.sub("", text).strip()
    # Strip trailing "Current time:" lines
    cleaned = _CURRENT_TIME_RE.sub("", cleaned).strip()
    # Strip delivery instructions
    cleaned = _DELIVERY_INSTRUCTION_RE.sub("", cleaned).strip()
    return cleaned


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


def get_all_sessions():
    """Find all active session JSONL files across all agents."""
    sessions = []
    if not AGENTS_DIR.exists():
        return sessions

    for agent_dir in sorted(AGENTS_DIR.iterdir()):
        if not agent_dir.is_dir():
            continue
        sessions_dir = agent_dir / "sessions"
        if not sessions_dir.exists():
            continue
        agent_name = agent_dir.name
        for jsonl_file in sorted(sessions_dir.glob("*.jsonl")):
            # Skip deleted and reset sessions
            if ".deleted." in jsonl_file.name or ".reset." in jsonl_file.name:
                continue
            sessions.append((agent_name, jsonl_file))

    return sessions


def _get_session_metadata(session_file):
    """Read session metadata from the first line."""
    try:
        with open(session_file) as f:
            first_line = f.readline().strip()
            if first_line:
                obj = json.loads(first_line)
                if obj.get("type") == "session":
                    return {
                        "id": obj.get("id", ""),
                        "cwd": obj.get("cwd", ""),
                        "timestamp": obj.get("timestamp", ""),
                    }
    except Exception:
        pass
    return {}


# --- Conversation Extraction -------------------------------------------------


def extract_conversation(session_file, agent_name, target_date):
    """Extract user messages and assistant text responses from an OpenClaw session.

    OpenClaw JSONL format:
    - type: "session" (metadata), "message", "thinking_level_change", "custom"
    - For "message" type:
      - message.role: "user", "assistant", "toolResult"
      - message.content[]: {type: "text", text: "..."} or {type: "toolCall", ...}
    """
    conversation = []
    current_assistant_texts = []
    current_assistant_time = None
    has_messages_on_date = False

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

            if obj.get("type") != "message":
                continue

            timestamp_str = obj.get("timestamp", "")
            dt = None
            if isinstance(timestamp_str, str) and timestamp_str:
                try:
                    dt = iso_to_local(timestamp_str)
                except (ValueError, TypeError):
                    continue

            if not dt or dt.date() != target_date:
                continue

            has_messages_on_date = True
            message = obj.get("message", {})
            role = message.get("role", "")
            content_blocks = message.get("content", [])

            if role == "user":
                flush_assistant()
                # Extract text content
                texts = []
                for block in content_blocks:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text", ""))

                full_text = "\n".join(texts).strip()
                if not full_text:
                    continue

                # Strip OpenClaw metadata wrappers
                user_text = _extract_user_text(full_text)
                if not user_text:
                    continue

                # Skip system session messages
                if any(p.match(user_text) for p in _SESSION_MSG_PATTERNS):
                    continue

                # Handle cron-triggered messages: extract task name, simplify content
                cron_match = _CRON_TRIGGER_RE.match(user_text)
                if cron_match:
                    cron_task = cron_match.group(1).strip()
                    cron_body = user_text[cron_match.end():].strip()
                    # Use first meaningful line of cron body as summary
                    summary_lines = []
                    for cron_line in cron_body.split("\n"):
                        cron_line = cron_line.strip()
                        if not cron_line or cron_line.startswith("```"):
                            break
                        summary_lines.append(cron_line)
                        if len(summary_lines) >= 2:
                            break
                    summary = " ".join(summary_lines) if summary_lines else ""
                    display = f"**Cron**: `{cron_task}`"
                    if summary:
                        display += f"\n\n{summary}"
                    conversation.append({
                        "role": "cron",
                        "time": dt,
                        "content": display,
                        "_cron_task": cron_task,
                    })
                else:
                    conversation.append({
                        "role": "user",
                        "time": dt,
                        "content": user_text,
                    })

            elif role == "assistant":
                for block in content_blocks:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "").strip()
                        if text:
                            # Skip session startup messages
                            if any(p.match(text) for p in _SESSION_MSG_PATTERNS):
                                continue
                            if current_assistant_time is None:
                                current_assistant_time = dt
                            current_assistant_texts.append(text)

    flush_assistant()

    if not has_messages_on_date:
        return None  # Distinguish "no messages on date" from "empty after filtering"

    return conversation


def _is_trivial_session(conv):
    user_messages = [m for m in conv if m["role"] in ("user", "cron")]
    if not user_messages:
        return True
    # Cron-triggered sessions are never trivial
    if any(m["role"] == "cron" for m in user_messages):
        return False
    return all(_TRIVIAL_PATTERNS.match(m["content"]) for m in user_messages)


# --- Note Generation ---------------------------------------------------------


def _pick_title(conv):
    # If first message is a cron trigger, use the task name
    for msg in conv:
        if msg["role"] == "cron":
            return msg.get("_cron_task", "Cron Task")
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


def generate_session_note(target_date, session_id, agent_name, conv, session_index):
    date_str = target_date.strftime("%Y-%m-%d")
    first_time = conv[0]["time"].strftime("%H:%M") if conv[0]["time"] else "?"

    title = _pick_title(conv) or f"Session {session_index}"
    is_cron = any(m["role"] == "cron" for m in conv)
    tags = "openclaw-log, cron" if is_cron else "openclaw-log"

    lines = [
        f"---",
        f"date: {date_str}",
        f"time: \"{first_time}\"",
        f"agent: \"{agent_name}\"",
        f"session_id: \"{session_id}\"",
        f"tags: [{tags}]",
        f"---",
        f"",
        f"# {title}",
        f"",
        f"**Agent**: `{agent_name}` | **Started**: {first_time} | **Messages**: {len(conv)}",
        f"",
        f"---",
        f"",
    ]

    for msg in conv:
        time_str = msg["time"].strftime("%H:%M") if msg["time"] else ""
        role = msg["role"]
        if role == "cron":
            lines.append(f"## ⏰ Cron `{time_str}`")
            lines.append("")
            lines.append(msg["content"])
            lines.append("")
        elif role == "user":
            lines.append(f"## 🧑 User `{time_str}`")
            lines.append("")
            lines.append(_format_user_content(msg["content"]))
            lines.append("")
        else:  # assistant
            lines.append(f"## 🤖 OpenClaw `{time_str}`")
            lines.append("")
            lines.append(msg["content"])
            lines.append("")

    return "\n".join(lines)


def generate_daily_index(target_date, session_notes):
    date_str = target_date.strftime("%Y-%m-%d")
    weekday = target_date.strftime("%A")

    total_sessions = len(session_notes)
    agents = set(n["agent"] for n in session_notes)
    total_messages = sum(n["message_count"] for n in session_notes)

    lines = [
        f"---",
        f"date: {date_str}",
        f"tags: [openclaw-log, daily-index]",
        f"---",
        f"",
        f"# OpenClaw Log - {date_str} ({weekday})",
        f"",
        f"**{total_sessions} sessions** across **{len(agents)} agents**, **{total_messages} messages** total.",
        f"",
        f"## Sessions",
        f"",
        f"| Time | Agent | Topic | Messages |",
        f"|------|-------|-------|----------|",
    ]

    for n in sorted(session_notes, key=lambda x: x["time"]):
        lines.append(
            f"| {n['time']} | `{n['agent']}` | [[{n['filename']}\\|{n['title']}]] | {n['message_count']} |"
        )

    lines.append("")
    return "\n".join(lines)


# --- Export ------------------------------------------------------------------


def export_date(target_date):
    all_sessions = get_all_sessions()
    if not all_sessions:
        print(f"[{target_date}] No OpenClaw sessions found.")
        return False

    date_str = target_date.strftime("%Y-%m-%d")
    day_dir = OUTPUT_DIR / date_str

    # Clean old exports for this date to avoid stale files
    if day_dir.exists():
        for old_file in day_dir.glob("*.md"):
            old_file.unlink()

    session_notes = []
    session_index = 0

    for agent_name, session_file in all_sessions:
        meta = _get_session_metadata(session_file)
        session_id = meta.get("id", session_file.stem)

        conv = extract_conversation(session_file, agent_name, target_date)
        if conv is None:  # No messages on this date
            continue
        if not conv:  # Messages existed but all filtered out
            continue
        if _is_trivial_session(conv):
            continue

        session_index += 1
        first_time = conv[0]["time"].strftime("%H:%M") if conv[0]["time"] else "?"

        title = _pick_title(conv) or f"Session {session_index}"
        safe_title = sanitize_filename(title)
        filename = f"{session_index:02d} - {safe_title}"

        note_content = generate_session_note(
            target_date, session_id, agent_name, conv, session_index
        )

        day_dir.mkdir(parents=True, exist_ok=True)
        output_file = day_dir / f"{filename}.md"
        output_file.write_text(note_content, encoding="utf-8")

        session_notes.append({
            "time": first_time,
            "agent": agent_name,
            "title": title,
            "filename": f"{date_str}/{filename}",
            "message_count": len(conv),
        })

    if not session_notes:
        print(f"[{target_date}] No OpenClaw conversations with content found.")
        return False

    index_content = generate_daily_index(target_date, session_notes)
    index_file = day_dir / f"00 - {date_str}.md"
    index_file.write_text(index_content, encoding="utf-8")

    print(f"[{target_date}] Exported {len(session_notes)} OpenClaw sessions to {day_dir}/")
    return True


def main():
    if not OPENCLAW_DIR.exists():
        print("Error: ~/.openclaw not found. Is OpenClaw installed?")
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
