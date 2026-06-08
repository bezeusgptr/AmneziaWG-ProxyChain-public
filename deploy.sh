#!/bin/bash
set -e

# Скрипт для автоматического поднятия всех сервисов с обменом ключами
echo "Starting AmneziaWG Proxy Chain..."

docker compose --profile ru --profile am up -d

echo "Waiting for containers to generate keys..."

# Polling с таймаутом вместо фиксированного sleep
wait_for_key() {
    local container="$1"
    local max_attempts=30
    for i in $(seq 1 $max_attempts); do
        if docker exec "$container" test -f /etc/amnezia/amneziawg/server_public_key 2>/dev/null; then
            return 0
        fi
        echo "  Waiting for $container to generate keys... ($i/$max_attempts)"
        sleep 1
    done
    echo "ERROR: Timeout waiting for $container to generate keys"
    return 1
}

# Проверка реального поднятия интерфейса awg0
wait_for_interface() {
    local container="$1"
    local interface="$2"
    local max_attempts=30
    for i in $(seq 1 $max_attempts); do
        if docker exec "$container" ip link show "$interface" >/dev/null 2>&1; then
            return 0
        fi
        echo "  Waiting for $container to bring up $interface... ($i/$max_attempts)"
        sleep 1
    done
    echo "ERROR: Timeout waiting for interface $interface on $container. Tunnel failed to start!"
    return 1
}

wait_for_key awg-ru
wait_for_key awg-am

# Извлекаем публичные ключи серверов
RU_KEY=$(docker exec awg-ru cat /etc/amnezia/amneziawg/server_public_key)
AM_KEY=$(docker exec awg-am cat /etc/amnezia/amneziawg/server_public_key)

# Проверяем, что ключи не пустые
if [ -z "$RU_KEY" ] || [ -z "$AM_KEY" ]; then
    echo "ERROR: One or both keys are empty!"
    echo "  RU_KEY='$RU_KEY'"
    echo "  AM_KEY='$AM_KEY'"
    exit 1
fi

echo "----------------------------------------"
echo "Public Key (Server RU): $RU_KEY"
echo "Public Key (Server AM): $AM_KEY"
echo "----------------------------------------"

# Автоматический обмен ключами: обновляем environment и перезапускаем контейнеры
echo "Injecting keys and restarting containers..."

# Устанавливаем AM_PUB_KEY для сервера РФ и RU_PUB_KEY для сервера Армении
AM_PUB_KEY="$AM_KEY" RU_PUB_KEY="$RU_KEY" docker compose --profile ru --profile am up -d

echo "Waiting for containers to restart with new configuration..."
wait_for_interface awg-ru awg0
wait_for_interface awg-am awg0

echo "========================================="
echo "Proxy chain is UP."
echo "Use these keys in your client AWG configuration:"
echo "  Server RU Public Key: $RU_KEY"
echo "========================================="
