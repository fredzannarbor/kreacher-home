#!/bin/bash
# kreacher-listener.sh
# Monitors iCloud file + iMessages for @kreacher home automation commands
# and /cc Claude Code queries. Combines both systems.
#
# Commands:
#   @kreacher status           - device status
#   @kreacher living room tv off
#   @kreacher mute all tvs
#   @kreacher sleepy time
#   @kreacher party time!
#   /cc budget gap          - Claude Code queries (customize for your use case)
#
# Start: ~/bin/kreacher-listener.sh
# Stop:  kill $(cat ~/.config/kreacher/kreacher-listener.pid)

# ─── Configuration (edit these) ──────────────────────────────────
PHONE="+15551234567"          # <-- your iMessage phone number
KREACHER_SCRIPT="$(dirname "$0")/kreacher-home.py"
# ─────────────────────────────────────────────────────────────────

CONFIG_DIR="$HOME/.config/kreacher"
LOG_FILE="$CONFIG_DIR/kreacher-listener.log"
PID_FILE="$CONFIG_DIR/kreacher-listener.pid"
COMMAND_FILE="$HOME/Library/Mobile Documents/com~apple~CloudDocs/kreacher-command.txt"
RESPONSE_FILE="$HOME/Library/Mobile Documents/com~apple~CloudDocs/kreacher-response.txt"
LAST_MSG_FILE="$CONFIG_DIR/kreacher-last-msg-id"
CHECK_INTERVAL=15  # seconds

mkdir -p "$CONFIG_DIR"
echo $$ > "$PID_FILE"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"
}

send_response() {
    local message="$1"
    message="${message:0:200}"

    # Write to response file (visible on iPhone via Files app)
    echo "$message" > "$RESPONSE_FILE" 2>/dev/null

    # Send via iMessage
    osascript <<EOF 2>/dev/null
tell application "Messages"
    set targetService to 1st account whose service type = iMessage
    set targetBuddy to participant "$PHONE" of targetService
    send "$message" to targetBuddy
end tell
EOF

    # Fallback: open imessage:// URL if AppleScript fails
    if [ $? -ne 0 ]; then
        local encoded=$(python3 -c "import urllib.parse; print(urllib.parse.quote('''$message'''))")
        open "imessage://$PHONE?body=$encoded"
    fi

    log "Response: $message"
}

process_command() {
    local cmd="$1"
    local cmd_lower=$(echo "$cmd" | tr '[:upper:]' '[:lower:]')
    log "Processing: $cmd"

    # ── @kreacher home automation commands ──
    if [[ "$cmd_lower" == @kreacher* ]] || [[ "$cmd_lower" == kreacher* ]]; then
        local kreacher_cmd=$(echo "$cmd" | sed -E 's/^@?[Kk]reacher[,:]?[[:space:]]*//')
        local result=$(python3 "$KREACHER_SCRIPT" "$kreacher_cmd" 2>&1)
        send_response "$result"
        return
    fi

    # ── Unknown prefix — check if it looks like a home command ──
    local home_words="tv|volume|mute|off|on|light|dark|sleep|party|status|living|bedroom|all"
    if echo "$cmd_lower" | grep -qiE "$home_words"; then
        local result=$(python3 "$KREACHER_SCRIPT" "$cmd" 2>&1)
        send_response "$result"
        return
    fi

    send_response "Try: @kreacher status, @kreacher tv off, @kreacher open Netflix"
}

# ── iCloud file watcher ──
check_icloud_command() {
    if [ -f "$COMMAND_FILE" ]; then
        local cmd=$(cat "$COMMAND_FILE" 2>/dev/null | tr -d '\n' | xargs)
        if [ -n "$cmd" ]; then
            process_command "$cmd"
            echo "" > "$COMMAND_FILE"
        fi
    fi
}

# ── iMessage watcher (requires Full Disk Access for Terminal) ──
check_imessage_commands() {
    local chat_db="$HOME/Library/Messages/chat.db"
    [ ! -r "$chat_db" ] && return

    sqlite3 "$chat_db" "
        SELECT DISTINCT text, ROWID
        FROM message
        WHERE datetime(date/1000000000 + 978307200, 'unixepoch', 'localtime') > datetime('now', '-2 minutes', 'localtime')
        AND is_from_me = 1
        AND (text LIKE '@kreacher%' OR text LIKE 'kreacher%')
        ORDER BY date DESC
        LIMIT 3;
    " 2>/dev/null | while IFS='|' read -r text rowid; do
        if [ -n "$text" ] && [ -n "$rowid" ]; then
            if ! grep -q "^$rowid$" "$LAST_MSG_FILE" 2>/dev/null; then
                echo "$rowid" >> "$LAST_MSG_FILE"
                process_command "$text"
            fi
        fi
    done
}

# ── Cleanup on exit ──
cleanup() {
    rm -f "$PID_FILE"
    log "Kreacher listener stopped"
    exit 0
}
trap cleanup SIGINT SIGTERM

# ── Main loop ──
log "Kreacher listener started (PID $$)"
echo "Kreacher Home Listener running (PID $$)"
echo "  iCloud: $COMMAND_FILE"
echo "  iMessage: @kreacher <command>"
echo "  Log: $LOG_FILE"
echo ""
echo "Commands:"
echo "  @kreacher status              - device status"
echo "  @kreacher living room tv off  - power off TV"
echo "  @kreacher mute all tvs        - mute both TVs"
echo "  @kreacher sleepy time         - all off, dark mode"
echo "  @kreacher party time          - flash lights!"
echo "  @kreacher dark mode           - Mac dark mode"
echo "  @kreacher open Netflix        - launch app on TV"
echo ""
echo "Press Ctrl+C to stop"

# Create iCloud files if needed
touch "$COMMAND_FILE" 2>/dev/null
touch "$RESPONSE_FILE" 2>/dev/null
touch "$LAST_MSG_FILE"

while true; do
    check_icloud_command
    check_imessage_commands
    sleep "$CHECK_INTERVAL"
done
