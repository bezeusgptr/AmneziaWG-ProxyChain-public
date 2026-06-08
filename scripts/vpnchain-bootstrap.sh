#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
vpnchain-bootstrap.sh — guided installer/checker для AmneziaWG ProxyChain v2

По умолчанию включён dry-run: скрипт проверяет prerequisites и печатает план действий.
Используйте --apply, чтобы создать runtime-директории, сохранить env-файл и запустить docker compose.

Новая v2-схема:
  1. AM/exit поднимается как обычный AWG server.
  2. На AM генерируется готовый client config для RU uplink.
  3. Этот config копируется на RU и используется как awg1.conf.
  4. RU поднимает awg0 для конечных клиентов и awg1 как клиент к AM.

Использование:
  scripts/vpnchain-bootstrap.sh --role am [--apply]
  scripts/vpnchain-bootstrap.sh --role am --generate-ru-uplink --am-endpoint HOST:PORT --output /secure/ru-awg1.conf [--apply]
  scripts/vpnchain-bootstrap.sh --role ru --ru-uplink-conf /secure/ru-awg1.conf --server-endpoint RU_HOST:51820 [--client-public-key KEY] [--apply]
  scripts/vpnchain-bootstrap.sh --env-file /etc/vpnchain/vpnchain.env [--apply]

Опции:
  --role ru|am                 Роль сервера: RU-входной узел или AM/exit-узел.
  --repo PATH                  Путь к репозиторию (по умолчанию current working directory).
  --env-file PATH              Читать ROLE/VPNCHAIN_ROLE и CLIENT*_PUB_KEY.
  --generate-ru-uplink         Для AM-роли: сгенерировать готовый awg1.conf для RU как для клиента.
  --output PATH                Куда записать сгенерированный RU uplink config.
  --ru-uplink-conf PATH        Для RU-роли: готовый awg1.conf, сгенерированный на AM.
  --am-endpoint HOST:PORT      Публичный endpoint AM/exit-узла для generated RU uplink config.
  --server-endpoint HOST:PORT  Для RU-роли: endpoint, который будет автоматически подставляться в client configs.
  --client-public-key KEY      Опциональный public key первого конечного клиента для RU-роли (env slot).
  --runtime-dir PATH           Runtime data dir (по умолчанию /var/lib/vpnchain).
  --config-dir PATH            Config dir (по умолчанию /etc/vpnchain).
  --compose-file PATH          Compose file (по умолчанию REPO/docker-compose.yml).
  --apply                      Выполнить изменения. Без этого — только dry-run.
  --dry-run                    Принудительно включить dry-run.
  -h, --help                   Показать справку.

Скрипт не хардкодит приватные ключи/endpoints. RU uplink private key генерируется
только на AM при --generate-ru-uplink и записывается в указанный output-файл;
передавайте его на RU только по защищённому каналу. Существующие env/config файлы
сохраняются в backup перед заменой; Docker named volumes сохраняют server keys между рестартами.
USAGE
}

log() { printf '%s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

APPLY=0
ROLE=""
REPO="$(pwd)"
ENV_FILE=""
AM_UPLINK_ENDPOINT="${AM_UPLINK_ENDPOINT:-}"
CLIENT_PUB_KEY="${CLIENT_PUB_KEY:-}"
SERVER_ENDPOINT="${VPNCHAIN_SERVER_ENDPOINT:-}"
RUNTIME_DIR="${VPNCHAIN_RUNTIME_DIR:-/var/lib/vpnchain}"
CONFIG_DIR="${VPNCHAIN_CONFIG_DIR:-/etc/vpnchain}"
COMPOSE_FILE=""
GENERATE_RU_UPLINK=0
OUTPUT=""
RU_UPLINK_CONF=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --role) ROLE="${2:-}"; shift 2 ;;
    --repo) REPO="${2:-}"; shift 2 ;;
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    --generate-ru-uplink) GENERATE_RU_UPLINK=1; shift ;;
    --output) OUTPUT="${2:-}"; shift 2 ;;
    --ru-uplink-conf) RU_UPLINK_CONF="${2:-}"; shift 2 ;;
    --am-endpoint) AM_UPLINK_ENDPOINT="${2:-}"; shift 2 ;;
    --server-endpoint) SERVER_ENDPOINT="${2:-}"; shift 2 ;;
    --client-public-key) CLIENT_PUB_KEY="${2:-}"; shift 2 ;;
    --runtime-dir) RUNTIME_DIR="${2:-}"; shift 2 ;;
    --config-dir) CONFIG_DIR="${2:-}"; shift 2 ;;
    --compose-file) COMPOSE_FILE="${2:-}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    --dry-run) APPLY=0; shift ;;
    -h|--help) usage; exit 0 ;;
    --init-keys-only|--ru-public-key|--am-public-key)
      fail "$1 is removed in v2 uplink flow. Use: AM --generate-ru-uplink, then RU --ru-uplink-conf" ;;
    *) fail "unknown argument: $1" ;;
  esac
done

if [ -n "$ENV_FILE" ]; then
  [ -r "$ENV_FILE" ] || fail "env file is not readable: $ENV_FILE"
  # shellcheck disable=SC1090
  set -a; . "$ENV_FILE"; set +a
  ROLE="${ROLE:-${VPNCHAIN_ROLE:-}}"
  AM_UPLINK_ENDPOINT="${AM_UPLINK_ENDPOINT:-}"
  CLIENT_PUB_KEY="${CLIENT_PUB_KEY:-}"
  SERVER_ENDPOINT="${SERVER_ENDPOINT:-${VPNCHAIN_SERVER_ENDPOINT:-}}"
fi

REPO="$(cd "$REPO" 2>/dev/null && pwd)" || fail "repo path does not exist: $REPO"
COMPOSE_FILE="${COMPOSE_FILE:-$REPO/docker-compose.yml}"
[ -f "$COMPOSE_FILE" ] || fail "compose file not found: $COMPOSE_FILE"
[ -f "$REPO/server-ru/entrypoint.sh" ] || fail "repo path does not look like AmneziaWG-ProxyChain: $REPO"
[ -f "$REPO/server-am/entrypoint.sh" ] || fail "repo path does not look like AmneziaWG-ProxyChain: $REPO"

case "$ROLE" in
  ru|am) ;;
  "") fail "--role ru|am is required unless ROLE/VPNCHAIN_ROLE is set in --env-file" ;;
  *) fail "invalid role: $ROLE (expected ru or am)" ;;
esac

if [ "$ROLE" = "ru" ]; then
  [ "$GENERATE_RU_UPLINK" -eq 0 ] || fail "--generate-ru-uplink is only valid with --role am"
  [ -n "$RU_UPLINK_CONF" ] || fail "RU role requires --ru-uplink-conf /path/to/awg1.conf generated on AM"
  [ -r "$RU_UPLINK_CONF" ] || fail "RU uplink config is not readable: $RU_UPLINK_CONF"
  [ -n "$SERVER_ENDPOINT" ] || fail "RU role requires --server-endpoint RU_HOST:51820 so generated client configs do not contain placeholders"
else
  if [ "$GENERATE_RU_UPLINK" -eq 1 ]; then
    [ -n "$AM_UPLINK_ENDPOINT" ] || fail "--generate-ru-uplink requires --am-endpoint HOST:PORT"
    [ -n "$OUTPUT" ] || fail "--generate-ru-uplink requires --output PATH"
  fi
fi

log "Режим: $([ "$APPLY" -eq 1 ] && echo apply || echo dry-run)"
log "Роль: $ROLE"
log "Репозиторий: $REPO"
log "Runtime dir: $RUNTIME_DIR"
[ "$GENERATE_RU_UPLINK" -eq 0 ] || log "Режим AM generate-ru-uplink: будет создан готовый awg1.conf для RU"
[ -z "$RU_UPLINK_CONF" ] || log "RU будет использовать готовый uplink config: $RU_UPLINK_CONF"
log "Config dir: $CONFIG_DIR"

if [ "$(id -u)" -ne 0 ]; then
  warn "not running as root; --apply normally needs root for /etc, /var/lib, Docker, and kernel checks"
fi

missing=0
check_cmd() { command -v "$1" >/dev/null 2>&1 || { warn "missing command: $1"; missing=1; }; }
check_cmd docker
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  warn "missing Docker Compose plugin/binary"
  missing=1
  COMPOSE=(docker compose)
fi
check_cmd awk
check_cmd sed

if command -v awg >/dev/null 2>&1; then
  log "Found awg userspace tool."
else
  warn "awg tool not found on host; containers include tools, but host diagnostics/client generation may be limited"
fi

if lsmod 2>/dev/null | grep -q '^amneziawg\b'; then
  log "Kernel module amneziawg is loaded."
elif modinfo amneziawg >/dev/null 2>&1; then
  warn "Kernel module amneziawg is installed but not loaded; run: modprobe amneziawg"
else
  warn "Kernel module amneziawg not found. Install it first: bash install_kernel_module.sh"
fi

if [ "$missing" -ne 0 ]; then
  warn "missing prerequisites were detected; install them before --apply"
fi

ENV_OUT="$CONFIG_DIR/vpnchain.env"
backup_if_exists() {
  path="$1"
  if [ -e "$path" ]; then
    backup="$path.bak.$(date -u +%Y%m%dT%H%M%SZ)"
    log "Backing up existing $path -> $backup"
    cp -a "$path" "$backup"
  fi
}

write_env() {
  tmp="$(mktemp)"
  chmod 600 "$tmp"
  existing_ru_pub_key="$(sed -n 's/^RU_PUB_KEY=//p' "$ENV_OUT" 2>/dev/null | tail -n 1)"
  {
    printf 'VPNCHAIN_ROLE=%s\n' "$ROLE"
    if [ "$ROLE" = "ru" ]; then
      [ -z "$CLIENT_PUB_KEY" ] || printf 'CLIENT_PUB_KEY=%s\n' "$CLIENT_PUB_KEY"
    else
      # Internal persistence for the generated RU-uplink client peer on AM.
      # It is not an operator-facing key-exchange step.
      [ -z "$existing_ru_pub_key" ] || printf 'RU_PUB_KEY=%s\n' "$existing_ru_pub_key"
    fi
  } > "$tmp"
  backup_if_exists "$ENV_OUT"
  mv "$tmp" "$ENV_OUT"
  chmod 600 "$ENV_OUT"
}

append_or_replace_env() {
  key="$1"
  value="$2"
  if grep -q "^${key}=" "$ENV_OUT" 2>/dev/null; then
    sed -i "s#^${key}=.*#${key}=${value}#" "$ENV_OUT"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_OUT"
  fi
  chmod 600 "$ENV_OUT"
}

generate_ru_uplink_config() {
  [ "$ROLE" = "am" ] || fail "--generate-ru-uplink is only valid with --role am"
  [ "$APPLY" -eq 1 ] || return 0
  [ -d "$(dirname "$OUTPUT")" ] || mkdir -p "$(dirname "$OUTPUT")"
  chmod 700 "$(dirname "$OUTPUT")" 2>/dev/null || true

  am_public_key="$(docker exec awg-am cat /etc/amnezia/amneziawg/server_public_key)"
  keypair="$(docker exec awg-am sh -lc 'priv=$(awg genkey); pub=$(printf "%s\n" "$priv" | awg pubkey); printf "%s\n%s\n" "$priv" "$pub"')"
  ru_private_key="$(printf '%s\n' "$keypair" | sed -n '1p')"
  ru_public_key="$(printf '%s\n' "$keypair" | sed -n '2p')"

  tmp="$(mktemp)"
  chmod 600 "$tmp"
  cat > "$tmp" <<EOF
# Generated by vpnchain-bootstrap on AM/exit. Copy this file to the RU server.
# RU uses this file as /etc/amnezia/amneziawg/awg1.conf.
# Keep it secret: it contains PrivateKey for the RU -> AM uplink.
[Interface]
PrivateKey = $ru_private_key
Address = 10.9.0.2/24
MTU = 1280
Table = 100

Jc = 4
Jmin = 50
Jmax = 1000
S1 = 80
S2 = 120
H1 = 1
H2 = 2
H3 = 3
H4 = 4

[Peer]
PublicKey = $am_public_key
Endpoint = $AM_UPLINK_ENDPOINT
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
EOF
  mv "$tmp" "$OUTPUT"
  chmod 600 "$OUTPUT"

  docker exec awg-am awg set awg0 peer "$ru_public_key" allowed-ips 10.9.0.2/32
  docker exec awg-am sh -lc "grep -q 'PublicKey = $ru_public_key' /etc/amnezia/amneziawg/awg0.conf || cat >> /etc/amnezia/amneziawg/awg0.conf <<EOF

[Peer]
PublicKey = $ru_public_key
AllowedIPs = 10.9.0.2/32
EOF"

  # Internal persistence: keeps the generated RU client peer on AM after
  # future compose recreate.
  append_or_replace_env RU_PUB_KEY "$ru_public_key"

  log "Создан RU uplink config: $OUTPUT"
  log "AM runtime peer добавлен для RU address 10.9.0.2/32"
  log "RU_PUB_KEY сохранён в $ENV_OUT для последующих restart/recreate AM-контейнера"
}

store_ru_server_public_key() {
  [ "$ROLE" = "ru" ] || return 0
  [ "$APPLY" -eq 1 ] || return 0
  ru_server_public_key="$(docker exec awg-ru cat /etc/amnezia/amneziawg/server_public_key)"
  append_or_replace_env VPNCHAIN_SERVER_PUBLIC_KEY "$ru_server_public_key"
  [ -z "$SERVER_ENDPOINT" ] || append_or_replace_env VPNCHAIN_SERVER_ENDPOINT "$SERVER_ENDPOINT"
  log "RU server public key и endpoint сохранены в $ENV_OUT для автоматической генерации client configs"
}

install_ru_uplink_config() {
  [ "$ROLE" = "ru" ] || fail "--ru-uplink-conf is only valid with --role ru"
  [ -n "$RU_UPLINK_CONF" ] || return 0
  [ "$APPLY" -eq 1 ] || return 0
  docker cp "$RU_UPLINK_CONF" awg-ru:/etc/amnezia/amneziawg/awg1.conf
  docker restart awg-ru >/dev/null
  log "RU uplink config установлен в awg-ru:/etc/amnezia/amneziawg/awg1.conf; контейнер awg-ru перезапущен"
}

if [ "$APPLY" -eq 0 ]; then
  log "Будет создано: $CONFIG_DIR, $RUNTIME_DIR/{backups,generated,tmp}"
  log "Будет записан env-файл: $ENV_OUT (с backup существующего файла, если он есть)"
  log "Будет выполнено: ${COMPOSE[*]} --env-file $ENV_OUT -f $COMPOSE_FILE --profile $ROLE up -d --build server-$ROLE"
  [ "$GENERATE_RU_UPLINK" -eq 0 ] || log "Будет создан RU uplink config: $OUTPUT, а AM добавит peer 10.9.0.2/32"
  [ -z "$RU_UPLINK_CONF" ] || log "Будет установлен готовый RU uplink config в awg-ru:/etc/amnezia/amneziawg/awg1.conf"
else
  [ "$missing" -eq 0 ] || fail "refusing --apply with missing required prerequisites"
  mkdir -p "$CONFIG_DIR" "$RUNTIME_DIR/backups" "$RUNTIME_DIR/generated" "$RUNTIME_DIR/tmp"
  chmod 700 "$CONFIG_DIR" "$RUNTIME_DIR" "$RUNTIME_DIR/backups" "$RUNTIME_DIR/generated" "$RUNTIME_DIR/tmp" 2>/dev/null || true
  write_env
  "${COMPOSE[@]}" --env-file "$ENV_OUT" -f "$COMPOSE_FILE" --profile "$ROLE" up -d --build "server-$ROLE"
  generate_ru_uplink_config
  install_ru_uplink_config
  store_ru_server_public_key
fi

cat <<NEXT

Следующие шаги:
  1. На AM/exit-узле поднимите AM как обычный AWG server:
       scripts/vpnchain-bootstrap.sh --role am --apply
  2. На AM/exit-узле сгенерируйте RU uplink client config:
       scripts/vpnchain-bootstrap.sh --role am --generate-ru-uplink --am-endpoint <AM-host>:51821 --output /etc/vpnchain/ru-awg1.conf --apply
  3. Скопируйте /etc/vpnchain/ru-awg1.conf с AM/exit-сервера на RU-сервер в /etc/vpnchain/ru-awg1.conf.
  4. На RU-сервере примените этот файл и запустите RU-контейнер:
       scripts/vpnchain-bootstrap.sh --role ru --ru-uplink-conf /etc/vpnchain/ru-awg1.conf --server-endpoint <RU-host>:51820 --apply
  5. На RU-сервере инициализируйте v2 manager DB и создайте пользовательский профиль:
       python3 -m vpnchain.cli --db $RUNTIME_DIR/vpnchain.sqlite init-db
       VPNCHAIN_SERVER_PUBLIC_KEY="\$(grep ^VPNCHAIN_SERVER_PUBLIC_KEY= $ENV_OUT | cut -d= -f2-)" \
       VPNCHAIN_SERVER_ENDPOINT="\$(grep ^VPNCHAIN_SERVER_ENDPOINT= $ENV_OUT | cut -d= -f2-)" \
       python3 -m vpnchain.cli --db $RUNTIME_DIR/vpnchain.sqlite peer add phone --output $RUNTIME_DIR/generated/phone.conf
     PublicKey/Endpoint в клиентском конфиге будут подставлены автоматически. Для live-применения public key клиента добавляйте в CLIENT*_PUB_KEY env slots.
  6. Проверьте маршрутизацию с подключённого клиента:
       curl https://ifconfig.me
       curl --resolve ya.ru:443:<known-ru-ip> https://ya.ru/  # опциональная точечная проверка RU-домена
NEXT
