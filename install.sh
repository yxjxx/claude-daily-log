#!/bin/bash
# Install claude-daily-log as a macOS LaunchAgent (runs daily at 23:50)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.claude-daily-log.plist"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME"
PYTHON="/usr/bin/python3"

# Check python3
if ! command -v $PYTHON &>/dev/null; then
    PYTHON="$(command -v python3)"
fi

echo "Script:  $SCRIPT_DIR/claude_daily_log.py"
echo "Python:  $PYTHON"
echo "Plist:   $PLIST_PATH"
echo ""

# Unload existing if present
if launchctl list | grep -q "com.claude-daily-log"; then
    echo "Unloading existing LaunchAgent..."
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
fi

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claude-daily-log</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$SCRIPT_DIR/claude_daily_log.py</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>23</integer>
        <key>Minute</key>
        <integer>50</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/claude-daily-log.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/claude-daily-log.log</string>
</dict>
</plist>
EOF

launchctl load "$PLIST_PATH"
echo "Installed and loaded. Will run daily at 23:50."
echo ""
echo "Test now with: $PYTHON $SCRIPT_DIR/claude_daily_log.py"
echo "Backfill with: $PYTHON $SCRIPT_DIR/claude_daily_log.py --backfill 30"
