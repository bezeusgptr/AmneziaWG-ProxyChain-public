#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE="vpnchain-bittorrent-policy-test:local"
RUNTIME="${CONTAINER_RUNTIME:-docker}"
SUFFIX="$$"
CLIENT_NET="vpc-bt-client-$SUFFIX"
SERVER_NET="vpc-bt-server-$SUFFIX"
ROUTER="vpc-bt-router-$SUFFIX"
CLIENT="vpc-bt-client-$SUFFIX"
SERVER="vpc-bt-server-$SUFFIX"

cleanup() {
    "$RUNTIME" rm -f "$CLIENT" "$SERVER" "$ROUTER" >/dev/null 2>&1 || true
    "$RUNTIME" network rm "$CLIENT_NET" "$SERVER_NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

"$RUNTIME" build -f "$ROOT/tests/container/Dockerfile.bittorrent-policy" -t "$IMAGE" "$ROOT"
"$RUNTIME" network create --ipv6 --subnet 10.210.1.0/24 --subnet fd00:210:1::/64 "$CLIENT_NET" >/dev/null
"$RUNTIME" network create --ipv6 --subnet 10.210.2.0/24 --subnet fd00:210:2::/64 "$SERVER_NET" >/dev/null

"$RUNTIME" run -d --name "$ROUTER" --cap-add NET_ADMIN \
    --sysctl net.ipv4.ip_forward=1 --sysctl net.ipv6.conf.all.forwarding=1 \
    --network "$CLIENT_NET" --ip 10.210.1.2 --ip6 fd00:210:1::2 \
    --entrypoint /bin/sleep "$IMAGE" infinity >/dev/null
"$RUNTIME" network connect --ip 10.210.2.2 --ip6 fd00:210:2::2 "$SERVER_NET" "$ROUTER"
"$RUNTIME" run -d --name "$CLIENT" --cap-add NET_ADMIN \
    --network "$CLIENT_NET" --ip 10.210.1.3 --ip6 fd00:210:1::3 \
    --entrypoint /bin/sleep "$IMAGE" infinity >/dev/null
"$RUNTIME" run -d --name "$SERVER" --cap-add NET_ADMIN \
    --network "$SERVER_NET" --ip 10.210.2.3 --ip6 fd00:210:2::3 \
    --entrypoint /bin/sleep "$IMAGE" infinity >/dev/null

"$RUNTIME" exec "$CLIENT" ip route add 10.210.2.0/24 via 10.210.1.2
"$RUNTIME" exec "$CLIENT" ip -6 route add fd00:210:2::/64 via fd00:210:1::2
"$RUNTIME" exec "$SERVER" ip route add 10.210.1.0/24 via 10.210.2.2
"$RUNTIME" exec "$SERVER" ip -6 route add fd00:210:1::/64 via fd00:210:2::2

"$RUNTIME" exec -d "$SERVER" python3 -c '
import select, socket
listeners = []
connections = []
for family, address in ((socket.AF_INET, "10.210.2.3"), (socket.AF_INET6, "fd00:210:2::3")):
    for port in (443, 6881, 6999, 8443):
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((address, port)); sock.listen(); listeners.append(sock)
    for port in (53, 6881):
        sock = socket.socket(family, socket.SOCK_DGRAM)
        sock.bind((address, port)); connections.append(sock)
while True:
    for sock in select.select(listeners + connections, [], [])[0]:
        if sock in listeners:
            conn, _ = sock.accept()
            if sock.getsockname()[1] == 6999:
                connections.append(conn)
            else:
                conn.sendall(b"ok"); conn.close()
        elif sock.type == socket.SOCK_STREAM:
            data = sock.recv(32)
            if data:
                sock.sendall(data)
            else:
                connections.remove(sock); sock.close()
        else:
            data, peer = sock.recvfrom(32); sock.sendto(data, peer)
'
sleep 1

CLIENT_IFACE="$("$RUNTIME" exec "$ROUTER" ip -o route get 10.210.1.3 | sed -n 's/.* dev \([^ ]*\).*/\1/p')"
SERVER_IFACE="$("$RUNTIME" exec "$ROUTER" ip -o route get 10.210.2.3 | sed -n 's/.* dev \([^ ]*\).*/\1/p')"
[[ -n "$CLIENT_IFACE" && -n "$SERVER_IFACE" ]]
# Rootless Podman rejects a transit source from another managed network. This
# test-only edge-router SNAT models the production gateway's egress NAT.
"$RUNTIME" exec "$ROUTER" iptables -t nat -A POSTROUTING -o "$SERVER_IFACE" -j MASQUERADE

# Establish a blocked TCP flow before enforcement. A generic
# RELATED,ESTABLISHED accept would let this connection bypass the allowlist.
"$RUNTIME" exec -d "$CLIENT" python3 -c '
import pathlib, socket, time
sock = socket.create_connection(("10.210.2.3", 6999), timeout=2)
sock.settimeout(1)
pathlib.Path("/tmp/preexisting-ready").touch()
while not pathlib.Path("/tmp/policy-applied").exists(): time.sleep(0.05)
try:
    sock.sendall(b"blocked")
    allowed = sock.recv(7) == b"blocked"
except OSError:
    allowed = False
pathlib.Path("/tmp/preexisting-result").write_text("allowed" if allowed else "blocked")
'
for _ in $(seq 1 50); do
    "$RUNTIME" exec "$CLIENT" test -f /tmp/preexisting-ready && break
    sleep 0.1
done
"$RUNTIME" exec "$CLIENT" test -f /tmp/preexisting-ready

"$RUNTIME" exec "$ROUTER" /bin/bash -ceu '
cat >/tmp/allowed-egress.conf <<EOF
tcp 10.210.2.3/32 443
udp 10.210.2.3/32 53
EOF
export VPNCHAIN_BT_POLICY_MODE=enforce
export VPNCHAIN_BT_POLICY_ROLE=ru
export VPNCHAIN_BT_POLICY_INTERFACE="$1"
export VPNCHAIN_BT_ALLOWLIST=/tmp/allowed-egress.conf
iptables -N VPCBASE; iptables -X VPCBASE
ip6tables -N VPCBASE; ip6tables -X VPCBASE
iptables-save | grep -v "^#" >/tmp/baseline-v4.rules
ip6tables-save | grep -v "^#" >/tmp/baseline-v6.rules
vpnchain-bittorrent-policy check
vpnchain-bittorrent-policy apply
vpnchain-bittorrent-policy apply
[ "$(iptables -S FORWARD | grep -c -- "-j VPCBTRU")" -eq 1 ]
[ "$(ip6tables -S FORWARD | grep -c -- "-j VPCBTRU")" -eq 1 ]
iptables -C VPCBTRU -p tcp -d 10.210.2.3/32 --dport 443 -m conntrack --ctstate NEW,ESTABLISHED -m comment --comment vpnchain-bittorrent-policy -j ACCEPT
iptables -C VPCBTRU -p udp -d 10.210.2.3/32 --dport 53 -m conntrack --ctstate NEW,ESTABLISHED -m comment --comment vpnchain-bittorrent-policy -j ACCEPT
iptables -C VPCBTRU -m comment --comment vpnchain-bittorrent-policy-denied -j DROP
ip6tables -C VPCBTRU -m comment --comment vpnchain-bittorrent-policy-denied -j DROP
' -- "$CLIENT_IFACE"

"$RUNTIME" exec "$CLIENT" touch /tmp/policy-applied
for _ in $(seq 1 50); do
    "$RUNTIME" exec "$CLIENT" test -f /tmp/preexisting-result && break
    sleep 0.1
done
test "$("$RUNTIME" exec "$CLIENT" cat /tmp/preexisting-result)" = blocked

"$RUNTIME" exec "$CLIENT" python3 -c '
import socket

def tcp(address, port, family=socket.AF_INET, allowed=False):
    sock = socket.socket(family, socket.SOCK_STREAM); sock.settimeout(1)
    try:
        sock.connect((address, port)); result = sock.recv(2) == b"ok"
    except OSError:
        result = False
    finally:
        sock.close()
    assert result is allowed, (address, port, result, allowed)

def udp(address, port, allowed=False):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sock.settimeout(1)
    try:
        sock.sendto(b"ok", (address, port)); result = sock.recvfrom(2)[0] == b"ok"
    except OSError:
        result = False
    finally:
        sock.close()
    assert result is allowed, (address, port, result, allowed)

tcp("10.210.2.3", 443, allowed=True)
udp("10.210.2.3", 53, allowed=True)
tcp("10.210.2.3", 6881)
udp("10.210.2.3", 6881)
tcp("10.210.2.3", 8443)
tcp("fd00:210:2::3", 443, socket.AF_INET6)
'

"$RUNTIME" exec "$ROUTER" /bin/bash -ceu '
export VPNCHAIN_BT_POLICY_MODE=enforce
export VPNCHAIN_BT_POLICY_ROLE=ru
export VPNCHAIN_BT_POLICY_INTERFACE="$1"
export VPNCHAIN_BT_ALLOWLIST=/tmp/allowed-egress.conf
vpnchain-bittorrent-policy read-back >/tmp/read-back.txt
vpnchain-bittorrent-policy rollback
iptables -N VPCBTRU
ip6tables -N VPCBTRU
iptables -I FORWARD 1 -i "$1" -m comment --comment vpnchain-bittorrent-policy-reorder-guard -j VPCBTRU
ip6tables -I FORWARD 1 -i "$1" -m comment --comment vpnchain-bittorrent-policy-reorder-guard -j VPCBTRU
export VPNCHAIN_BT_POLICY_MODE=disabled
vpnchain-bittorrent-policy apply
! iptables -S FORWARD | grep -q vpnchain-bittorrent-policy
! ip6tables -S FORWARD | grep -q vpnchain-bittorrent-policy
iptables-save | grep -v "^#" >/tmp/after-v4.rules
ip6tables-save | grep -v "^#" >/tmp/after-v6.rules
diff -u /tmp/baseline-v4.rules /tmp/after-v4.rules
diff -u /tmp/baseline-v6.rules /tmp/after-v6.rules
' -- "$CLIENT_IFACE"
printf 'container policy traffic integration: PASS\n'
