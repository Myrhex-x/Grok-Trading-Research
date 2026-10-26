#!/bin/zsh
# Removes the background job. The inbox, the published data and the repository stay as they are.
LABEL="com.myrhex.grok-trading-research"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Removed $LABEL."
