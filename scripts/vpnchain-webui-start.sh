#!/usr/bin/env bash
# Starts vpnchain WebUI as a foreground process for systemd.
# Reads /etc/vpnchain/vpnchain.env (or $VPNCHAIN_ENV_FILE).
# Exits 0 without starting if VPNCHAIN_WEBUI_AUTH is not set or empty.
set -euo pipefail

ENV_FILE="${VPNCHAIN_ENV_FILE:-/etc/vpnchain/vpnchain.env}"

if [ -r "$ENV_FILE" ]; then
    set -a; . "$ENV_FILE"; set +a
fi

if [ -z "${VPNCHAIN_WEBUI_AUTH:-}" ]; then
    echo "vpnchain-webui: VPNCHAIN_WEBUI_AUTH is not configured; WebUI will not start" >&2
    exit 0
fi

REPO="${VPNCHAIN_REPO:-/opt/AmneziaWG-ProxyChain}"
DB="${VPNCHAIN_DB:-/var/lib/vpnchain/vpnchain.sqlite}"
PORT="${VPNCHAIN_WEBUI_PORT:-8080}"
MANAGER="${REPO}/bin/vpnchain-v2-active"
VPNCHAIN_PUBLIC_ENDPOINT="${VPNCHAIN_PUBLIC_ENDPOINT:-${VPNCHAIN_SERVER_ENDPOINT:-}}"

exec env \
    PYTHONPATH="$REPO" \
    VPNCHAIN_SERVER_PUBLIC_KEY="${VPNCHAIN_SERVER_PUBLIC_KEY:-}" \
    VPNCHAIN_SERVER_ENDPOINT="${VPNCHAIN_SERVER_ENDPOINT:-}" \
    VPNCHAIN_PUBLIC_ENDPOINT="$VPNCHAIN_PUBLIC_ENDPOINT" \
    VPNCHAIN_RUNTIME_SYNC="${VPNCHAIN_RUNTIME_SYNC:-1}" \
    VPNCHAIN_RUNTIME_AWG_CONTAINER="${VPNCHAIN_RUNTIME_AWG_CONTAINER:-awg-ru}" \
    VPNCHAIN_RUNTIME_AWG_INTERFACE="${VPNCHAIN_RUNTIME_AWG_INTERFACE:-awg0}" \
    VPNCHAIN_RUNTIME_AWG_CONFIG="${VPNCHAIN_RUNTIME_AWG_CONFIG:-}" \
    python3 -m vpnchain \
    --db "$DB" \
    webui \
    --host 127.0.0.1 \
    --port "$PORT" \
    --interface awg0 \
    --activity-tool awg \
    --activity-command docker \
    --activity-command exec \
    --activity-command awg-ru \
    --activity-command awg \
    --manager-command "$MANAGER" \
    --basic-auth "$VPNCHAIN_WEBUI_AUTH"
