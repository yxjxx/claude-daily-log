# claude-daily-log

Export Claude Code conversations to Obsidian — one folder per day, one note per session.

## Output Structure

```
Claude Logs/
  2026-03-20/
    2026-03-20.md                      ← daily index with table linking to sessions
    01 - Configure rclone backup.md    ← session 1
    02 - Fix docker networking.md      ← session 2
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
| `output_subdir` | `OUTPUT_SUBDIR` | `Claude Logs` | Subfolder within vault |
| `timezone_offset` | `TIMEZONE_OFFSET` | `8` | UTC offset in hours |

Auto-detection checks these locations:
- `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/*/` (macOS iCloud)
- `~/Documents/Obsidian/*/`
- `~/Obsidian/*/`

## Usage

```bash
# Export today
python3 claude_daily_log.py

# Export specific date
python3 claude_daily_log.py 2026-03-20

# Backfill last N days
python3 claude_daily_log.py --backfill 30
```

## How It Works

1. Reads `~/.claude/history.jsonl` to find sessions for the target date
2. For each session, parses the JSONL file under `~/.claude/projects/`
3. Extracts user messages (excludes tool results) and Claude's text responses (excludes thinking/tool_use)
4. Writes each session as a separate Obsidian note with YAML frontmatter
5. Creates a daily index note with a table linking to all sessions

## Requirements

- Python 3.6+
- Claude Code (conversations stored in `~/.claude/`)
- macOS for LaunchAgent (or set up your own cron on Linux)
