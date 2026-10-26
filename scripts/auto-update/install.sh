#!/bin/zsh
# Installs the background job that publishes new sessions. It runs when Gambit drops a report into
# the private inbox, and once an hour in case one was missed. scripts/auto-update/uninstall.sh
# removes it. The repository must already be pushed to GitHub (the job pulls and pushes main).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
LABEL="com.myrhex.grok-trading-research"
INBOX="$HOME/Library/Application Support/Grok-Trading-Research/inbox"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/Grok-Trading-Research.log"

mkdir -p "$INBOX" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>$REPO/scripts/research.py</string>
        <string>publish</string>
    </array>
    <key>WatchPaths</key>
    <array>
        <string>$INBOX</string>
    </array>
    <key>StartInterval</key>
    <integer>3600</integer>
    <key>ThrottleInterval</key>
    <integer>60</integer>
    <key>StandardOutPath</key>
    <string>$LOG</string>
    <key>StandardErrorPath</key>
    <string>$LOG</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $LABEL. Log: $LOG"
