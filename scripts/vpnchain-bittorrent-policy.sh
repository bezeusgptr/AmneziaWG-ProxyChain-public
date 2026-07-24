#!/usr/bin/env bash
set -euo pipefail

MODE="${VPNCHAIN_BT_POLICY_MODE:-disabled}"
ROLE="${VPNCHAIN_BT_POLICY_ROLE:-}"
INTERFACE="${VPNCHAIN_BT_POLICY_INTERFACE:-awg0}"
ALLOWLIST="${VPNCHAIN_BT_ALLOWLIST:-/etc/vpnchain/bittorrent-policy/allowed-egress.policy}"
STATE_DIR="${VPNCHAIN_BT_STATE_DIR:-/run/vpnchain-bittorrent-policy}"
OWNER_COMMENT="vpnchain-bittorrent-policy"

usage() {
    cat <<'EOF'
Usage: vpnchain-bittorrent-policy check|apply|read-back|rollback

Modes are selected with VPNCHAIN_BT_POLICY_MODE:
  disabled  remove only this project's policy chains (default)
  audit     count traffic that enforce mode would deny, then return
  enforce   allow only protocol/CIDR/port entries and drop everything else

Active modes require VPNCHAIN_BT_POLICY_ROLE=ru|am. The allowlist format is:
  tcp 192.0.2.10/32 443
  udp 192.0.2.53/32 53
EOF
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    return 1
}

chain_name() {
    case "$ROLE" in
        ru) printf 'VPCBTRU' ;;
        am) printf 'VPCBTAM' ;;
        *) fail 'VPNCHAIN_BT_POLICY_ROLE must be ru or am' ;;
    esac
}

validate_ipv4_cidr() {
    local cidr="$1" address prefix octet
    [[ "$cidr" == */* ]] || return 1
    address="${cidr%/*}"
    prefix="${cidr#*/}"
    [[ "$prefix" =~ ^[0-9]+$ ]] && (( prefix >= 1 && prefix <= 32 )) || return 1
    [[ "$address" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
    IFS='.' read -r -a octets <<< "$address"
    (( ${#octets[@]} == 4 )) || return 1
    for octet in "${octets[@]}"; do
        [[ "$octet" =~ ^[0-9]+$ ]] && (( 10#$octet <= 255 )) || return 1
    done
}

normalize_allowlist() {
    local output="$1" raw line protocol cidr port extra count=0
    : > "$output"
    if [[ ! -r "$ALLOWLIST" ]]; then
        [[ "$MODE" == 'audit' ]] && return 0
        fail "allowlist is not readable: $ALLOWLIST"
        return 1
    fi
    while IFS= read -r raw || [[ -n "$raw" ]]; do
        line="${raw%%#*}"
        read -r protocol cidr port extra <<< "$line"
        [[ -n "${protocol:-}" ]] || continue
        [[ -z "${extra:-}" ]] || { fail "invalid allowlist entry: $raw"; return 1; }
        [[ "$protocol" == 'tcp' || "$protocol" == 'udp' ]] || { fail "allowlist protocol must be tcp or udp: $raw"; return 1; }
        validate_ipv4_cidr "$cidr" || { fail "invalid or unbounded IPv4 CIDR in allowlist: $raw"; return 1; }
        [[ "$port" =~ ^[0-9]+$ ]] && (( port >= 1 && port <= 65535 )) || { fail "invalid allowlist port: $raw"; return 1; }
        printf '%s %s %s\n' "$protocol" "$cidr" "$port" >> "$output"
        count=$((count + 1))
    done < "$ALLOWLIST"
    if [[ "$MODE" == 'enforce' && "$count" -eq 0 ]]; then
        fail 'enforce mode requires at least one strict allowlist entry'
        return 1
    fi
}

validate_config() {
    case "$MODE" in
        disabled)
            printf 'BitTorrent policy mode: disabled\n'
            return 0
            ;;
        audit|enforce) ;;
        *) fail 'VPNCHAIN_BT_POLICY_MODE must be disabled, audit, or enforce'; return 1 ;;
    esac
    chain_name >/dev/null
    [[ "$INTERFACE" =~ ^[A-Za-z0-9_.:-]{1,15}$ ]] || { fail 'invalid policy interface'; return 1; }
    local normalized
    normalized="$(mktemp)"
    if ! normalize_allowlist "$normalized"; then
        rm -f "$normalized"
        return 1
    fi
    rm -f "$normalized"
    printf 'BitTorrent policy configuration valid: mode=%s role=%s interface=%s\n' "$MODE" "$ROLE" "$INTERFACE"
}

require_firewall_tools() {
    local tool
    for tool in iptables ip6tables iptables-save ip6tables-save iptables-restore ip6tables-restore; do
        command -v "$tool" >/dev/null 2>&1 || { fail "required firewall tool not found: $tool"; return 1; }
    done
}

render_rules() {
    local family="$1" output="$2" normalized="$3" chain protocol cidr port
    chain="$(chain_name)"
    {
        printf '*filter\n:%s - [0:0]\n-F %s\n' "$chain" "$chain"
        printf -- '-A %s -m conntrack --ctstate RELATED,ESTABLISHED -m comment --comment %s -j ACCEPT\n' "$chain" "$OWNER_COMMENT"
        if [[ "$family" == '4' ]]; then
            while read -r protocol cidr port; do
                [[ -n "${protocol:-}" ]] || continue
                printf -- '-A %s -p %s -d %s --dport %s -m conntrack --ctstate NEW -m comment --comment %s -j ACCEPT\n' \
                    "$chain" "$protocol" "$cidr" "$port" "$OWNER_COMMENT"
            done < "$normalized"
        fi
        if [[ "$MODE" == 'audit' ]]; then
            printf -- '-A %s -m comment --comment %s-audit-denied -j RETURN\n' "$chain" "$OWNER_COMMENT"
        else
            printf -- '-A %s -m comment --comment %s-denied -j DROP\n' "$chain" "$OWNER_COMMENT"
        fi
        printf 'COMMIT\n'
    } > "$output"
}

remove_policy() {
    local chain
    chain="$(chain_name)"
    while iptables -C FORWARD -i "$INTERFACE" -m comment --comment "$OWNER_COMMENT" -j "$chain" 2>/dev/null; do
        iptables -D FORWARD -i "$INTERFACE" -m comment --comment "$OWNER_COMMENT" -j "$chain"
    done
    while ip6tables -C FORWARD -i "$INTERFACE" -m comment --comment "$OWNER_COMMENT" -j "$chain" 2>/dev/null; do
        ip6tables -D FORWARD -i "$INTERFACE" -m comment --comment "$OWNER_COMMENT" -j "$chain"
    done
    iptables -F "$chain" 2>/dev/null || true
    iptables -X "$chain" 2>/dev/null || true
    ip6tables -F "$chain" 2>/dev/null || true
    ip6tables -X "$chain" 2>/dev/null || true
}

install_fail_closed() {
    local chain
    chain="$(chain_name)"
    printf 'ERROR: policy apply failed; installing owned fail-closed guards\n' >&2
    iptables -N "$chain" 2>/dev/null || true
    iptables -F "$chain" 2>/dev/null || true
    iptables -A "$chain" -m comment --comment "$OWNER_COMMENT-denied" -j DROP 2>/dev/null || true
    iptables -C FORWARD -i "$INTERFACE" -m comment --comment "$OWNER_COMMENT" -j "$chain" 2>/dev/null \
        || iptables -I FORWARD 1 -i "$INTERFACE" -m comment --comment "$OWNER_COMMENT" -j "$chain" 2>/dev/null || true
    ip6tables -N "$chain" 2>/dev/null || true
    ip6tables -F "$chain" 2>/dev/null || true
    ip6tables -A "$chain" -m comment --comment "$OWNER_COMMENT-denied" -j DROP 2>/dev/null || true
    ip6tables -C FORWARD -i "$INTERFACE" -m comment --comment "$OWNER_COMMENT" -j "$chain" 2>/dev/null \
        || ip6tables -I FORWARD 1 -i "$INTERFACE" -m comment --comment "$OWNER_COMMENT" -j "$chain" 2>/dev/null || true
}

read_back() {
    local chain
    chain="$(chain_name)"
    iptables -C FORWARD -i "$INTERFACE" -m comment --comment "$OWNER_COMMENT" -j "$chain"
    ip6tables -C FORWARD -i "$INTERFACE" -m comment --comment "$OWNER_COMMENT" -j "$chain"
    printf '%s\n' 'IPv4 read-back:'
    iptables -S "$chain"
    printf '%s\n' 'IPv6 read-back:'
    ip6tables -S "$chain"
}

apply_policy() {
    require_firewall_tools
    if [[ "$MODE" == 'disabled' ]]; then
        [[ "$ROLE" == 'ru' || "$ROLE" == 'am' ]] || { fail 'disabled apply still requires role to remove the correct owned chain'; return 1; }
        remove_policy
        rm -f "$STATE_DIR/rollback-v4.rules" "$STATE_DIR/rollback-v6.rules" \
            "$STATE_DIR/candidate-v4.rules" "$STATE_DIR/candidate-v6.rules" \
            "$STATE_DIR/allowlist.normalized"
        printf 'BitTorrent policy is disabled; owned chains are absent\n'
        return 0
    fi

    # Once firewall tools are available, every active-mode failure must leave
    # the client interface guarded, including validation and backup failures.
    trap 'install_fail_closed' ERR
    validate_config >/dev/null
    install -d -m 0700 "$STATE_DIR"
    local normalized rules4 rules6
    normalized="$STATE_DIR/allowlist.normalized"
    rules4="$STATE_DIR/candidate-v4.rules"
    rules6="$STATE_DIR/candidate-v6.rules"
    normalize_allowlist "$normalized"
    chmod 0600 "$normalized"
    render_rules 4 "$rules4" "$normalized"
    render_rules 6 "$rules6" "$normalized"
    chmod 0600 "$rules4" "$rules6"

    umask 077
    if [[ ! -f "$STATE_DIR/rollback-v4.rules" || ! -f "$STATE_DIR/rollback-v6.rules" ]]; then
        iptables-save > "$STATE_DIR/rollback-v4.rules"
        ip6tables-save > "$STATE_DIR/rollback-v6.rules"
    fi

    iptables-restore --test --noflush < "$rules4"
    ip6tables-restore --test --noflush < "$rules6"
    iptables-restore --noflush < "$rules4"
    ip6tables-restore --noflush < "$rules6"
    local chain
    chain="$(chain_name)"
    iptables -C FORWARD -i "$INTERFACE" -m comment --comment "$OWNER_COMMENT" -j "$chain" 2>/dev/null \
        || iptables -I FORWARD 1 -i "$INTERFACE" -m comment --comment "$OWNER_COMMENT" -j "$chain"
    ip6tables -C FORWARD -i "$INTERFACE" -m comment --comment "$OWNER_COMMENT" -j "$chain" 2>/dev/null \
        || ip6tables -I FORWARD 1 -i "$INTERFACE" -m comment --comment "$OWNER_COMMENT" -j "$chain"
    read_back >/dev/null
    trap - ERR
    printf 'BitTorrent policy applied: mode=%s role=%s\n' "$MODE" "$ROLE"
}

rollback_policy() {
    require_firewall_tools
    [[ -f "$STATE_DIR/rollback-v4.rules" && -f "$STATE_DIR/rollback-v6.rules" ]] \
        || { fail "rollback snapshots not found in $STATE_DIR"; return 1; }
    remove_policy
    iptables-restore < "$STATE_DIR/rollback-v4.rules"
    ip6tables-restore < "$STATE_DIR/rollback-v6.rules"
    rm -f "$STATE_DIR/rollback-v4.rules" "$STATE_DIR/rollback-v6.rules" \
        "$STATE_DIR/candidate-v4.rules" "$STATE_DIR/candidate-v6.rules" \
        "$STATE_DIR/allowlist.normalized"
    printf 'Firewall rollback restored for IPv4 and IPv6\n'
}

main() {
    local action="${1:-}"
    case "$action" in
        check) validate_config ;;
        apply) apply_policy ;;
        read-back)
            [[ "$MODE" != 'disabled' ]] || { printf 'BitTorrent policy mode: disabled\n'; return 0; }
            validate_config >/dev/null
            require_firewall_tools
            read_back
            ;;
        rollback) rollback_policy ;;
        -h|--help|help) usage ;;
        *) usage >&2; return 2 ;;
    esac
}

main "$@"
