#!/bin/bash
# Monitor packet loss and latency between AmneziaWG nodes.
# Usage: ./monitor_tunnel.sh <IP_ADDRESS> [INTERFACE]
# Logs to /var/log/awg_monitor.log with automatic rotation at 10MB (keeps 3 archives).
# Auto-restarts the real tunnel owner. If the interface belongs to awg-ru, restart that container.

set -u

if [ -z "${1:-}" ]; then
    echo "Usage: ./monitor_tunnel.sh <IP_ADDRESS> [INTERFACE]"
    exit 1
fi

TARGET="$1"
INTERFACE="${2:-awg1}"
LOGFILE="/var/log/awg_monitor.log"
MAX_SIZE=$((10 * 1024 * 1024))
KEEP_ARCHIVES=3
PING_COUNT=10

MAX_RETRIES=3
BACKOFF_DELAYS=(0 300 900)  # seconds: immediate, 5min, 15min

restart_attempts=0
last_restart_time=0
in_failure=0

rotate_log() {
    if [ -f "$LOGFILE" ] && [ "$(stat -c%s "$LOGFILE")" -ge "$MAX_SIZE" ]; then
        for i in $(seq $((KEEP_ARCHIVES - 1)) -1 1); do
            [ -f "${LOGFILE}.${i}.gz" ] && mv "${LOGFILE}.${i}.gz" "${LOGFILE}.$((i + 1)).gz"
        done
        gzip -c "$LOGFILE" > "${LOGFILE}.1.gz"
        > "$LOGFILE"
        echo "[$(date "+%Y-%m-%d %H:%M:%S")] Log rotated." >> "$LOGFILE"
    fi
}

log() {
    echo "[$(date "+%Y-%m-%d %H:%M:%S")] $1" >> "$LOGFILE"
}

run_logged() {
    local label="$1"
    shift
    "$@" 2>&1 | while IFS= read -r line; do log "  [$label] $line"; done
    return ${PIPESTATUS[0]}
}

restart_interface() {
    log "ACTION: Restarting $INTERFACE owner (attempt $((restart_attempts + 1))/$MAX_RETRIES)..."

    if docker ps --format "{{.Names}}" | grep -qx "awg-ru" && docker exec awg-ru test -f "/etc/amnezia/amneziawg/${INTERFACE}.conf"; then
        log "ACTION: $INTERFACE is managed inside docker container awg-ru; restarting container."
        run_logged "docker restart awg-ru" docker restart -t 20 awg-ru || log "ERROR: docker restart awg-ru failed."
    elif [ -f "/etc/amnezia/amneziawg/${INTERFACE}.conf" ]; then
        log "ACTION: $INTERFACE is managed on host via awg-quick."
        run_logged "awg-quick down" awg-quick down "$INTERFACE" || true
        sleep 3
        run_logged "awg-quick up" env WG_QUICK_USERSPACE_IMPLEMENTATION=amneziawg-go awg-quick up "$INTERFACE" || log "ERROR: awg-quick up $INTERFACE failed."
    else
        log "ERROR: Cannot find owner/config for $INTERFACE; no restart performed."
    fi

    last_restart_time=$(date +%s)
    restart_attempts=$((restart_attempts + 1))
    log "ACTION: Restart action complete. Waiting 30s for tunnel to establish..."
    sleep 30
}

log "Starting tunnel monitoring for $TARGET (interface: $INTERFACE)..."

while true; do
    TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")
    NOW=$(date +%s)

    PING_OUT=$(ping -c "$PING_COUNT" -q "$TARGET" 2>&1)
    LOSS=$(echo "$PING_OUT" | grep -oP "\d+(?=% packet loss)" || true)
    LATENCY=$(echo "$PING_OUT" | grep -oP "min/avg/max/(mdev|stddev) = \K[^/]+/[^/]+" | cut -d"/" -f2 || true)

    if [ -z "$LOSS" ]; then
        LOSS="100"
        LATENCY="timeout"
    fi
    [ -n "$LATENCY" ] || LATENCY="n/a"

    echo "[$TIMESTAMP] Target: $TARGET | Loss: ${LOSS}% | Avg RTT: ${LATENCY}ms" >> "$LOGFILE"

    if [ "$LOSS" -gt 20 ]; then
        if [ "$in_failure" -eq 0 ]; then
            in_failure=1
            restart_attempts=0
            log "WARNING: High packet loss detected (${LOSS}%). Starting recovery..."
        fi

        if [ "$restart_attempts" -lt "$MAX_RETRIES" ]; then
            delay=${BACKOFF_DELAYS[$restart_attempts]}
            time_since_last=$((NOW - last_restart_time))

            if [ "$time_since_last" -ge "$delay" ]; then
                restart_interface
            else
                remaining=$((delay - time_since_last))
                log "WARNING: Loss ${LOSS}%. Next restart in ${remaining}s (attempt $((restart_attempts + 1))/$MAX_RETRIES)."
            fi
        else
            log "CRITICAL: Loss ${LOSS}%. All $MAX_RETRIES restart attempts exhausted. Manual intervention required."
        fi
    else
        if [ "$in_failure" -eq 1 ]; then
            log "RECOVERY: Tunnel restored after $restart_attempts restart(s). Loss: ${LOSS}%, RTT: ${LATENCY}ms"
            in_failure=0
            restart_attempts=0
            last_restart_time=0
        fi
    fi

    rotate_log
    sleep 60
done
