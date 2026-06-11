#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${VPNCHAIN_CONTAINER:-awg-ru}"
STATE_DIR="${VPNCHAIN_WATCHDOG_STATE_DIR:-/var/lib/vpnchain/watchdog}"
LOG_FILE="${VPNCHAIN_WATCHDOG_LOG_FILE:-/var/log/vpnchain-watchdog.log}"
MAX_RESTARTS="${VPNCHAIN_WATCHDOG_MAX_RESTARTS:-5}"
RESTART_COUNT_FILE="$STATE_DIR/${CONTAINER}.restart-count"

log() {
  local level="$1"; shift
  local msg="vpnchain-watchdog[$CONTAINER]: $*"
  printf '%s %s\n' "$(date -Is)" "$msg" >> "$LOG_FILE" 2>/dev/null || true
  logger -t vpnchain-watchdog -p "daemon.${level}" -- "$msg" 2>/dev/null || true
  printf '%s\n' "$msg"
}

read_restart_count() {
  if [ -r "$RESTART_COUNT_FILE" ]; then
    cat "$RESTART_COUNT_FILE"
  else
    printf '0\n'
  fi
}

write_restart_count() {
  mkdir -p "$STATE_DIR"
  printf '%s\n' "$1" > "$RESTART_COUNT_FILE"
}

reset_restart_count() {
  write_restart_count 0
}

container_running() {
  docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -qx true
}

container_healthy() {
  local status
  status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER" 2>/dev/null || true)"
  [ "$status" = healthy ] || [ "$status" = none ]
}

exec_in_container() {
  docker exec "$CONTAINER" sh -lc "$1"
}

awg_ok() {
  local iface="$1"
  exec_in_container "awg show '$iface' >/dev/null 2>&1"
}

link_exists() {
  local iface="$1"
  exec_in_container "ip link show '$iface' >/dev/null 2>&1"
}

try_up_iface() {
  local iface="$1"
  local conf="/etc/amnezia/amneziawg/${iface}.conf"
  if ! exec_in_container "test -s '$conf'"; then
    log err "cannot repair $iface: missing $conf"
    return 1
  fi
  log warning "problem detected: $iface is unavailable; trying awg-quick up $iface"
  exec_in_container "ip link delete '$iface' 2>/dev/null || true; env WG_QUICK_USERSPACE_IMPLEMENTATION=amneziawg-go awg-quick up '$iface'" \
    && awg_ok "$iface"
}

repair_dnsmasq() {
  if ! link_exists awg0; then
    log warning "skip dnsmasq repair: awg0 is not present"
    return 1
  fi
  if exec_in_container "pgrep -x dnsmasq >/dev/null 2>&1 && nslookup ya.ru 10.8.0.1 >/dev/null 2>&1"; then
    return 0
  fi
  log warning "problem detected: dnsmasq/10.8.0.1 is unavailable; restarting dnsmasq"
  exec_in_container 'pkill dnsmasq 2>/dev/null || true; DNSMASQ_CONF=/tmp/dnsmasq-ru-domains.conf; test -s "$DNSMASQ_CONF" && dnsmasq --conf-file="$DNSMASQ_CONF"'
}

restart_container_limited() {
  local reason="$1"
  local count
  count="$(read_restart_count)"
  if ! [[ "$count" =~ ^[0-9]+$ ]]; then
    count=0
  fi
  if [ "$count" -ge "$MAX_RESTARTS" ]; then
    log err "restart limit reached ($count/$MAX_RESTARTS); not restarting. reason=$reason"
    return 2
  fi
  count=$((count + 1))
  write_restart_count "$count"
  log err "problem persists; restarting container attempt $count/$MAX_RESTARTS. reason=$reason"
  docker restart "$CONTAINER" >/dev/null
}

main() {
  [ "$(id -u)" -eq 0 ] || { log err "must run as root"; exit 1; }
  mkdir -p "$STATE_DIR"
  touch "$LOG_FILE" 2>/dev/null || true

  if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    log err "container not found"
    exit 1
  fi

  if ! container_running; then
    restart_container_limited "container is not running"
    exit $?
  fi

  local repaired=0 failed=0

  if ! awg_ok awg0; then
    try_up_iface awg0 && repaired=1 || failed=1
  fi

  if exec_in_container 'test -s /etc/amnezia/amneziawg/awg1.conf' && ! awg_ok awg1; then
    try_up_iface awg1 && repaired=1 || failed=1
  fi

  repair_dnsmasq || failed=1

  if ! container_healthy; then
    log warning "problem detected: Docker healthcheck is not healthy after soft repair"
    failed=1
  fi

  if [ "$failed" -eq 0 ]; then
    if [ "$(read_restart_count)" != 0 ]; then
      log info "VPN chain is healthy again; resetting restart counter"
    elif [ "$repaired" -eq 1 ]; then
      log info "VPN chain soft repair completed"
    fi
    reset_restart_count
    exit 0
  fi

  restart_container_limited "soft repair failed"
}

main "$@"
