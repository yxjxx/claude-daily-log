#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-/usr/bin/python3}"
$PYTHON "$SCRIPT_DIR/claude_daily_log.py"
$PYTHON "$SCRIPT_DIR/codex_daily_log.py"
$PYTHON "$SCRIPT_DIR/openclaw_daily_log.py"
