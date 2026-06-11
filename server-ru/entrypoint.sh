#!/bin/bash
set -e

# Убедимся, что директория существует
mkdir -p /etc/amnezia/amneziawg

# Если приватный ключ сервера не существует - генерируем его
if [ ! -f /etc/amnezia/amneziawg/server_private_key ]; then
    echo "Generating new server keys..."
    umask 077
    awg genkey | tee /etc/amnezia/amneziawg/server_private_key | awg pubkey > /etc/amnezia/amneziawg/server_public_key
    chmod 600 /etc/amnezia/amneziawg/server_private_key
fi

SERVER_PRIV_KEY=$(cat /etc/amnezia/amneziawg/server_private_key)
export SERVER_PRIV_KEY

# Подстановка переменных окружения в шаблон конфига awg0.
# v2 manager can persist generated peers directly in /etc/amnezia/amneziawg/awg0.conf.
# Preserve an existing runtime config on container restart so peer sections are not
# wiped when CLIENT*_PUB_KEY variables are absent from docker compose env.
if [ ! -s /etc/amnezia/amneziawg/awg0.conf ] || ! grep -q '^PrivateKey = ' /etc/amnezia/amneziawg/awg0.conf; then
    envsubst < /config/awg0.conf.template > /etc/amnezia/amneziawg/awg0.conf

    # Удаляем секции [Peer], если PublicKey остался пустым
    sed -i '/^\[Peer\]/{N;/\nPublicKey = *$/{N;d;}}' /etc/amnezia/amneziawg/awg0.conf
    sed -i '/^\[Peer\]/{N;/\nPublicKey = *$/d;}' /etc/amnezia/amneziawg/awg0.conf
    sed -i '/^PublicKey = *$/d' /etc/amnezia/amneziawg/awg0.conf
else
    echo "Preserving existing /etc/amnezia/amneziawg/awg0.conf"
fi

# awg1 больше не генерируется из LEGACY_AM_PUB_KEY/LEGACY_AM_ENDPOINT.
# В v2 uplink-flow AM/exit создаёт готовый client config для RU, а bootstrap
# копирует его сюда как /etc/amnezia/amneziawg/awg1.conf.
if [ -n "${LEGACY_AM_PUB_KEY:-}" ] || [ -n "${LEGACY_AM_ENDPOINT:-}" ]; then
    echo "WARNING: LEGACY_AM_PUB_KEY/LEGACY_AM_ENDPOINT are ignored. Provide ready awg1.conf generated on AM."
fi

echo "Downloading RU subnets..."
ipset create ru_subnets hash:net 2>/dev/null || ipset flush ru_subnets
# IPs learned from DNS answers for Russian domains hosted outside RU GeoIP.
ipset create ru_domains hash:ip timeout 86400 2>/dev/null || ipset flush ru_domains

RU_CIDR_URL="https://www.ipdeny.com/ipblocks/data/countries/ru.zone"
if curl --max-time 30 --connect-timeout 10 -sSL "$RU_CIDR_URL" -o /tmp/ru.cidr; then
    # Валидация: проверяем, что файл содержит CIDR-записи (используем POSIX ERE для alpine busybox, учитываем \r)
    if grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+[[:space:]]*$' /tmp/ru.cidr; then
        sed -e 's/^/add ru_subnets /' /tmp/ru.cidr | ipset restore -! || echo "WARNING: Failed to load some ru subnets"
    else
        echo "ERROR: Downloaded file does not contain valid CIDR data"
    fi
    rm -f /tmp/ru.cidr
else
    echo "ERROR: Failed to download RU subnets (curl timeout or network error)"
fi

echo "Configuring iptables for Selective Routing..."

# Очистка только наших правил при рестарте. Не flush всего POSTROUTING:
# это ломает Docker bridge NAT и из-за этого docker build/default containers
# теряют доступ к Alpine mirrors.
while iptables -t mangle -D PREROUTING -i awg0 -m set ! --match-set ru_subnets dst -m set ! --match-set ru_domains dst -m set ! --match-set private_subnets dst -j MARK --set-mark 1 2>/dev/null; do :; done
while iptables -t mangle -D PREROUTING -i awg0 -m set ! --match-set ru_subnets dst -m set ! --match-set private_subnets dst -j MARK --set-mark 1 2>/dev/null; do :; done
while iptables -t nat -D POSTROUTING -o eth0 -m set --match-set ru_subnets dst -j MASQUERADE 2>/dev/null; do :; done
while iptables -t nat -D POSTROUTING -o eth0 -m set --match-set ru_domains dst -j MASQUERADE 2>/dev/null; do :; done
while iptables -t nat -D POSTROUTING -o awg1 -j MASQUERADE 2>/dev/null; do :; done

# Создаем список приватных сетей
ipset create private_subnets hash:net 2>/dev/null || ipset flush private_subnets
ipset add private_subnets 10.0.0.0/8 2>/dev/null || true
ipset add private_subnets 172.16.0.0/12 2>/dev/null || true
ipset add private_subnets 192.168.0.0/16 2>/dev/null || true

# Перехватываем DNS клиентов на локальный dnsmasq, чтобы domain-based правила
# работали даже для уже выданных клиентских конфигов без DNS = 10.8.0.1.
# Удаляем только наши старые DNS redirect правила, не трогая Docker PREROUTING.
while iptables -t nat -D PREROUTING -i awg0 -p udp -m udp --dport 53 -j REDIRECT --to-ports 53 2>/dev/null; do :; done
while iptables -t nat -D PREROUTING -i awg0 -p tcp -m tcp --dport 53 -j REDIRECT --to-ports 53 2>/dev/null; do :; done
# Clean up one earlier malformed deployment where --dport was lost.
while iptables -t nat -D PREROUTING -i awg0 -p udp -j REDIRECT --to-ports 53 2>/dev/null; do :; done
while iptables -t nat -D PREROUTING -i awg0 -p tcp -j REDIRECT --to-ports 53 2>/dev/null; do :; done
iptables -t nat -A PREROUTING -i awg0 -p udp -m udp --dport 53 -j REDIRECT --to-ports 53
iptables -t nat -A PREROUTING -i awg0 -p tcp -m tcp --dport 53 -j REDIRECT --to-ports 53

# Маркируем пакеты от клиента (awg0), которые идут НЕ в российские GeoIP-подсети,
# НЕ к IP российских доменов из dnsmasq/ipset и НЕ к приватным адресам.
iptables -t mangle -A PREROUTING -i awg0 -m set ! --match-set ru_subnets dst -m set ! --match-set ru_domains dst -m set ! --match-set private_subnets dst -j MARK --set-mark 1

# Настраиваем NAT для трафика, уходящего в интернет напрямую с Сервера РФ
# (российские GeoIP-подсети + российские домены с non-RU IP).
iptables -t nat -A POSTROUTING -o eth0 -m set --match-set ru_subnets dst -j MASQUERADE
iptables -t nat -A POSTROUTING -o eth0 -m set --match-set ru_domains dst -j MASQUERADE
# Preserve/restore Docker bridge NAT for docker build and default-network containers.
iptables -t nat -C POSTROUTING -s 172.17.0.0/16 ! -o docker0 -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s 172.17.0.0/16 ! -o docker0 -j MASQUERADE
iptables -C FORWARD -i docker0 ! -o docker0 -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -i docker0 ! -o docker0 -j ACCEPT
iptables -C FORWARD -o docker0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -o docker0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT

# Разрешаем FORWARD-трафик для интерфейсов после перевода в network_mode: host
iptables -I FORWARD 1 -i awg0 -j ACCEPT 2>/dev/null || true
iptables -I FORWARD 1 -o awg0 -j ACCEPT 2>/dev/null || true
iptables -I FORWARD 1 -i awg1 -j ACCEPT 2>/dev/null || true
iptables -I FORWARD 1 -o awg1 -j ACCEPT 2>/dev/null || true

echo "Starting awg-quick on awg0..."
ip link delete awg0 2>/dev/null || true
ip link delete awg1 2>/dev/null || true
env WG_QUICK_USERSPACE_IMPLEMENTATION=amneziawg-go awg-quick up awg0

echo "Configuring DNS-assisted selective routing..."

# dnsmasq fills ru_domains ipset for configured Russian domains, including
# domains that use non-RU hosting/CDN IPs. Client DNS is transparently
# redirected below, so existing client configs do not need DNS changes.
DNSMASQ_CONF=/tmp/dnsmasq-ru-domains.conf
{
    echo "no-resolv"
    echo "server=1.1.1.1"
    echo "server=8.8.8.8"
    echo "bind-interfaces"
    echo "listen-address=10.8.0.1,127.0.0.1"
    echo "cache-size=10000"
    if [ -f /config/ru-domains.txt ]; then
        sed 's/#.*//; s/^[[:space:]]*//; s/[[:space:]]*$//' /config/ru-domains.txt | while read -r domain; do
            [ -n "$domain" ] && echo "ipset=/$domain/ru_domains"
        done
    fi
} > "$DNSMASQ_CONF"
pkill dnsmasq 2>/dev/null || true
dnsmasq --conf-file="$DNSMASQ_CONF"


if [ -f /etc/amnezia/amneziawg/awg1.conf ]; then
    echo "Starting awg-quick on awg1 (Link to AM)..."
    env WG_QUICK_USERSPACE_IMPLEMENTATION=amneziawg-go awg-quick up awg1

    # Настраиваем Policy Routing ПОСЛЕ поднятия awg1, чтобы таблица 100 была уже заполнена
    ip rule add fwmark 1 table 100 2>/dev/null || true

    # NAT для трафика, уходящего в туннель до Армении
    iptables -t nat -A POSTROUTING -o awg1 -j MASQUERADE
fi

echo "AmneziaWG is running. Tailing logs..."
# Оставляем контейнер работать
tail -f /dev/null
