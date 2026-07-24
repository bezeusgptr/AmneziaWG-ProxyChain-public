#!/bin/bash
set -e

mkdir -p /etc/amnezia/amneziawg

if [ ! -f /etc/amnezia/amneziawg/server_private_key ]; then
    echo "Generating new server keys for AM node..."
    umask 077
    awg genkey | tee /etc/amnezia/amneziawg/server_private_key | awg pubkey > /etc/amnezia/amneziawg/server_public_key
    chmod 600 /etc/amnezia/amneziawg/server_private_key
fi

SERVER_PRIV_KEY=$(cat /etc/amnezia/amneziawg/server_private_key)
export SERVER_PRIV_KEY

envsubst < /config/awg0.conf.template > /etc/amnezia/amneziawg/awg0.conf

# Удаляем пустую peer-секцию, если RU_PUB_KEY ещё не задан.
sed -i '/^\[Peer\]/{N;/\nPublicKey = *$/{N;d;}}' /etc/amnezia/amneziawg/awg0.conf
sed -i '/^\[Peer\]/{N;/\nPublicKey = *$/d;}' /etc/amnezia/amneziawg/awg0.conf
sed -i '/^PublicKey = *$/d' /etc/amnezia/amneziawg/awg0.conf

echo "Starting awg-quick on awg0 for AM node..."
ip link delete awg0 2>/dev/null || true
env WG_QUICK_USERSPACE_IMPLEMENTATION=amneziawg-go awg-quick up awg0

# Независимый AM egress-gate. disabled сохраняет текущее поведение, а active
# режим всегда ставит проектный jump перед broad ACCEPT из AWG PostUp.
VPNCHAIN_BT_POLICY_MODE="${VPNCHAIN_BT_POLICY_MODE:-disabled}" \
VPNCHAIN_BT_POLICY_ROLE=am \
    vpnchain-bittorrent-policy apply

echo "Tailing logs..."
tail -f /dev/null
