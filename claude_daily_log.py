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
import re
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


# Patterns for parsing system/command messages
_SYSTEM_BLOCKS_RE = re.compile(
    r"\s*<(?:system-reminder|task-notification)>[\s\S]*?</(?:system-reminder|task-notification)>\s*",
    re.MULTILINE,
)
_COMMAND_NAME_RE = re.compile(r"<command-name>/?(.+?)</command-name>")
_LOCAL_STDOUT_RE = re.compile(
    r"<local-command-stdout>([\s\S]*?)</local-command-stdout>"
)
_BASH_INPUT_RE = re.compile(r"<bash-input>([\s\S]*?)</bash-input>")
_BASH_STDOUT_RE = re.compile(r"<bash-stdout>([\s\S]*?)</bash-stdout>")
_BASH_STDERR_RE = re.compile(r"<bash-stderr>([\s\S]*?)</bash-stderr>")

# Trivial messages that don't constitute meaningful conversation
_TRIVIAL_PATTERNS = re.compile(
    r"^("
    r"h[ie]|hey|hello|yo|sup|嗨|你好|哈[喽啰罗]|"
    r"ok|okay|好的?|嗯|行|可以|知道了|收到|谢谢|thanks|thx|ty|"
    r"bye|再见|exit|quit|"
    r"你是什么模型|what model|which model|你是谁|who are you|"
    r"test|测试|ping"
    r")[？?！!。.\s]*$",
    re.IGNORECASE
)

# Patterns that make bad titles (HTML, commands, terminal prompts)
_BAD_TITLE_PATTERNS = re.compile(
    r"^("
    r"<[a-z]|"                        # HTML tags
    r"<bash-|"                        # bash XML tags
    r"[~$]\s|"                        # shell prompt (~/ or $)
    r"ssh\s|scp\s|rsync\s|"          # remote commands
    r"docker\s|kubectl\s|"           # infra commands
    r"git\s|cd\s|ls\s|cat\s|rm\s|"  # common shell commands
    r"sudo\s|chmod\s|chown\s|"      # admin commands
    r"curl\s|wget\s|pip\s|npm\s|"   # package/download commands
    r"[a-zA-Z0-9_.@-]+:\s*[~/]|"    # terminal prompts like user@host: ~
    r"Accessing\s|Loading\s"         # Claude Code UI text
    r")",
    re.IGNORECASE
)

# Messages to skip entirely
_SKIP_PATTERNS = [
    re.compile(r"<local-command-caveat>"),
    re.compile(r"<user-prompt-submit-hook>"),
]


def _transform_user_message(text):
    """Transform system/command messages into human-friendly format.

    Returns:
        (str, str) - (role, content) where role is 'user', 'command', or None to skip.
    """
    # Skip caveats and hook messages entirely
    for pat in _SKIP_PATTERNS:
        if pat.search(text):
            return None, None

    # Slash commands: /clear, /plugin, etc.
    m = _COMMAND_NAME_RE.search(text)
    if m:
        cmd = m.group(1)
        return "command", f"`/{cmd}`"

    # Command stdout
    m = _LOCAL_STDOUT_RE.search(text)
    if m:
        stdout = m.group(1).strip()
        if not stdout or stdout == "(no content)":
            return None, None
        return "command_output", f"```\n{stdout}\n```"

    # Bash input/output tags (from terminal interactions)
    m = _BASH_INPUT_RE.search(text)
    if m:
        cmd = m.group(1).strip()
        stdout = ""
        m2 = _BASH_STDOUT_RE.search(text)
        if m2:
            stdout = m2.group(1).strip()
        if not cmd:
            return None, None
        if stdout:
            return "command_output", f"`$ {cmd}`\n\n```\n{stdout}\n```"
        return "command", f"`$ {cmd}`"

    # Standalone bash stdout (no input tag)
    m = _BASH_STDOUT_RE.search(text)
    if m:
        stdout = m.group(1).strip()
        if not stdout:
            return None, None
        return "command_output", f"```\n{stdout}\n```"

    # Standalone bash stderr
    if _BASH_STDERR_RE.search(text):
        m = _BASH_STDERR_RE.search(text)
        stderr = m.group(1).strip()
        if not stderr:
            return None, None
        return "command_output", f"```\n{stderr}\n```"

    # System reminder / task notification in user message
    if "<system-reminder>" in text or "<task-notification>" in text:
        return None, None

    return "user", text


def _is_trivial_session(conv):
    """Return True if the session has no substantive user messages."""
    user_messages = [m for m in conv if m["role"] == "user"]
    if not user_messages:
        return True
    # If every user message is trivial, skip the session
    return all(_TRIVIAL_PATTERNS.match(m["content"]) for m in user_messages)


def _looks_like_code(text):
    """Heuristic: return True if the text looks like pasted code/script."""
    lines = text.split("\n")
    if len(lines) < 3:
        return False
    # Already has a code fence
    if text.lstrip().startswith("```"):
        return False
    code_indicators = [
        "#!/",  "if [[ ", "if ((", "for ((", "function ", "local ",
        "echo ", "cat ", "mkdir ", "curl ", "printf ",
        "import ", "from ", "def ", "class ", "return ",
        "const ", "let ", "var ", "function(",
        "= {", "=> {", "= (", "${", "$(", "<<'EOF'", "<<EOF",
    ]
    indicator_count = sum(1 for line in lines for ind in code_indicators if ind in line)
    return indicator_count >= 3


def _fence_wrap(text, lang=""):
    """Wrap text in a code fence, using enough backticks to avoid conflicts."""
    # Find the longest run of backticks in the text
    max_ticks = 2
    for m in re.finditer(r"`+", text):
        max_ticks = max(max_ticks, len(m.group()))
    fence = "`" * (max_ticks + 1)
    return f"{fence}{lang}\n{text}\n{fence}"


def _format_user_content(text):
    """Wrap code-like user messages in a code fence."""
    if _looks_like_code(text):
        # Try to guess language
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


def _clean_text(text):
    """Remove system-reminder and task-notification blocks from assistant responses."""
    return _SYSTEM_BLOCKS_RE.sub("", text).strip()


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
                    role, transformed = _transform_user_message(content.strip())
                    if role is None:
                        continue
                    conversation.append({
                        "role": role,
                        "time": dt,
                        "content": transformed,
                    })

            elif msg_type == "assistant":
                message = obj.get("message", {})
                content = message.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = _clean_text(block.get("text", ""))
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
        if msg["role"] == "user" and not _TRIVIAL_PATTERNS.match(msg["content"]) and not _looks_like_code(msg["content"]) and not _BAD_TITLE_PATTERNS.match(msg["content"]):
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
        role = msg["role"]
        if role == "user":
            lines.append(f"## 🧑 User `{time_str}`")
            lines.append("")
            lines.append(_format_user_content(msg["content"]))
            lines.append("")
        elif role == "command":
            lines.append(f"> 🔧 {msg['content']}  `{time_str}`")
            lines.append("")
        elif role == "command_output":
            lines.append(f"> 📋 Command output:")
            lines.append("")
            lines.append(msg["content"])
            lines.append("")
        else:  # assistant
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
        if _is_trivial_session(conv):
            continue

        session_index += 1
        project = project_display_name(info["project"])
        first_time = conv[0]["time"].strftime("%H:%M") if conv[0]["time"] else "?"

        first_user_msg = ""
        for msg in conv:
            if msg["role"] == "user" and not _TRIVIAL_PATTERNS.match(msg["content"]) and not _looks_like_code(msg["content"]) and not _BAD_TITLE_PATTERNS.match(msg["content"]):
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
    index_file = day_dir / f"00 - {date_str}.md"
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
