#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE="vpnchain-bittorrent-policy-test:local"

docker build -f "$ROOT/tests/container/Dockerfile.bittorrent-policy" -t "$IMAGE" "$ROOT"

docker run --rm --network none --cap-add NET_ADMIN \
    -v "$ROOT/policy/allowed-egress.policy:/etc/vpnchain/bittorrent-policy/allowed-egress.policy:ro" \
    --entrypoint /bin/bash "$IMAGE" -ceu '
cat >/tmp/allowed-egress.conf <<EOF
tcp 192.0.2.10/32 443
udp 192.0.2.53/32 53
EOF
export VPNCHAIN_BT_POLICY_MODE=enforce
export VPNCHAIN_BT_POLICY_ROLE=ru
export VPNCHAIN_BT_POLICY_INTERFACE=lo
export VPNCHAIN_BT_ALLOWLIST=/tmp/allowed-egress.conf
# Materialize the otherwise-lazy empty filter tables so byte-for-byte
# baseline comparison measures policy state rather than backend initialization.
iptables -N VPCBASE
iptables -X VPCBASE
ip6tables -N VPCBASE
ip6tables -X VPCBASE
iptables-save | grep -v '^#' >/tmp/baseline-v4.rules
ip6tables-save | grep -v '^#' >/tmp/baseline-v6.rules
vpnchain-bittorrent-policy check
vpnchain-bittorrent-policy apply
vpnchain-bittorrent-policy apply
[ "$(iptables -S FORWARD | grep -c -- "-j VPCBTRU")" -eq 1 ]
[ "$(ip6tables -S FORWARD | grep -c -- "-j VPCBTRU")" -eq 1 ]
iptables -C VPCBTRU -p tcp -d 192.0.2.10/32 --dport 443 -m conntrack --ctstate NEW -m comment --comment vpnchain-bittorrent-policy -j ACCEPT
iptables -C VPCBTRU -p udp -d 192.0.2.53/32 --dport 53 -m conntrack --ctstate NEW -m comment --comment vpnchain-bittorrent-policy -j ACCEPT
iptables -C VPCBTRU -m comment --comment vpnchain-bittorrent-policy-denied -j DROP
ip6tables -C VPCBTRU -m comment --comment vpnchain-bittorrent-policy-denied -j DROP
vpnchain-bittorrent-policy read-back >/tmp/read-back.txt
vpnchain-bittorrent-policy rollback
iptables-save | grep -v '^#' >/tmp/after-v4.rules
ip6tables-save | grep -v '^#' >/tmp/after-v6.rules
diff -u /tmp/baseline-v4.rules /tmp/after-v4.rules
diff -u /tmp/baseline-v6.rules /tmp/after-v6.rules
printf "container policy integration: PASS\n"
'
