# claude-daily-log

Export Claude Code, OpenAI Codex CLI, and OpenClaw conversations to Obsidian — one folder per day, one note per session.

## Output Structure

```
Claude Logs/
  2026-03-20/
    00 - 2026-03-20.md                 ← daily index with table linking to sessions
    01 - Configure rclone backup.md    ← session 1
    02 - Fix docker networking.md      ← session 2

Codex Logs/
  2026-03-20/
    00 - 2026-03-20.md                 ← daily index
    01 - Debug API endpoint.md         ← session 1

OpenClaw Logs/
  2026-03-20/
    00 - 2026-03-20.md                 ← daily index
    01 - daily-115-cloud-cleanup.md    ← cron task
    02 - 转存最新资源.md                ← user conversation
```

## Setup

```bash
# 1. Clone
git clone https://github.com/yxjxx/claude-daily-log.git
cd claude-daily-log

# 2. Configure
cp config.json.example config.json
# Edit config.json with your Obsidian vault path and timezone

# 3. Install daily cron (macOS LaunchAgent, runs at 23:50)
chmod +x install.sh
./install.sh
```

## Configuration

Edit `config.json` or set environment variables:

| config.json key | Env var | Default | Description |
|----------------|---------|---------|-------------|
| `obsidian_vault` | `OBSIDIAN_VAULT` | auto-detect | Path to Obsidian vault root |
| `output_subdir` | `OUTPUT_SUBDIR` | `Claude Logs` | Subfolder within vault for Claude |
| `codex_output_subdir` | `CODEX_OUTPUT_SUBDIR` | `Codex Logs` | Subfolder within vault for Codex |
| `openclaw_output_subdir` | `OPENCLAW_OUTPUT_SUBDIR` | `OpenClaw Logs` | Subfolder within vault for OpenClaw |
| `timezone_offset` | `TIMEZONE_OFFSET` | `8` | UTC offset in hours |

Auto-detection checks these locations:
- `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/*/` (macOS iCloud)
- `~/Documents/Obsidian/*/`
- `~/Obsidian/*/`

## Usage

```bash
# Export today
python3 claude_daily_log.py
python3 codex_daily_log.py
python3 openclaw_daily_log.py

# Export specific date
python3 claude_daily_log.py 2026-03-20
python3 codex_daily_log.py 2026-03-20
python3 openclaw_daily_log.py 2026-03-20

# Backfill last N days
python3 claude_daily_log.py --backfill 30
python3 codex_daily_log.py --backfill 30
python3 openclaw_daily_log.py --backfill 30
```

## How It Works

### Claude Code (`claude_daily_log.py`)

1. Reads `~/.claude/history.jsonl` to find sessions for the target date
2. For each session, parses the JSONL file under `~/.claude/projects/`
3. Extracts user messages (excludes tool results) and Claude's text responses (excludes thinking/tool_use)
4. Writes each session as a separate Obsidian note with YAML frontmatter
5. Creates a daily index note with a table linking to all sessions

### Codex CLI (`codex_daily_log.py`)

1. Scans `~/.codex/sessions/YYYY/MM/DD/` for session JSONL files
2. Reads thread metadata (title, project) from `~/.codex/state_5.sqlite`
3. Extracts user messages and Codex's text responses from session events
4. Writes notes in the same format, with `codex-log` tags

### OpenClaw (`openclaw_daily_log.py`)

1. Scans `~/.openclaw/agents/*/sessions/*.jsonl` across all agents
2. Strips Telegram/channel metadata wrappers from user messages
3. Identifies cron-triggered vs user-initiated conversations (tagged separately)
4. Filters out system messages (heartbeat, session startup)
5. Writes notes with `openclaw-log` tags (and `cron` tag for automated tasks)

## Requirements

- Python 3.6+ (standard library only, no external packages)
- Claude Code (`~/.claude/`) and/or Codex CLI (`~/.codex/`) and/or OpenClaw (`~/.openclaw/`)
- macOS for LaunchAgent (or set up your own cron on Linux)
