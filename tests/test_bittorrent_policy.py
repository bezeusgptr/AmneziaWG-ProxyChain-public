import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOADER = ROOT / 'scripts' / 'vpnchain-bittorrent-policy.sh'
COMPOSE = ROOT / 'docker-compose.yml'
RU_ENTRYPOINT = ROOT / 'server-ru' / 'entrypoint.sh'
AM_ENTRYPOINT = ROOT / 'server-am' / 'entrypoint.sh'
ALLOWLIST = ROOT / 'policy' / 'allowed-egress.policy'


def run_loader(*args, **env_overrides):
    env = os.environ.copy()
    env.update(env_overrides)
    return subprocess.run(
        ['/bin/bash', str(LOADER), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


def test_policy_is_default_disabled_and_requires_no_firewall_tools():
    result = run_loader('check', PATH='')

    assert result.returncode == 0
    assert 'disabled' in result.stdout.lower()


def test_enforce_check_rejects_empty_allowlist(tmp_path):
    allowlist = tmp_path / 'allowed-egress.conf'
    allowlist.write_text('# no destinations\n')

    result = run_loader(
        'check',
        VPNCHAIN_BT_POLICY_MODE='enforce',
        VPNCHAIN_BT_POLICY_ROLE='ru',
        VPNCHAIN_BT_ALLOWLIST=str(allowlist),
    )

    assert result.returncode != 0
    assert 'allowlist' in result.stderr.lower()


def test_enforce_check_accepts_strict_protocol_destination_port_entries(tmp_path):
    allowlist = tmp_path / 'allowed-egress.conf'
    allowlist.write_text('tcp 192.0.2.10/32 443\nudp 192.0.2.53/32 53\n')

    result = run_loader(
        'check',
        VPNCHAIN_BT_POLICY_MODE='enforce',
        VPNCHAIN_BT_POLICY_ROLE='am',
        VPNCHAIN_BT_ALLOWLIST=str(allowlist),
    )

    assert result.returncode == 0, result.stderr


def test_enforce_check_rejects_unbounded_or_malformed_entries(tmp_path):
    invalid_entries = (
        'tcp 0.0.0.0/0 443\n',
        'all 192.0.2.10/32 443\n',
        'udp 192.0.2.10/32 any\n',
        'tcp example.test 443\n',
        'tcp 192.0.2.10/32 70000\n',
    )

    for entry in invalid_entries:
        allowlist = tmp_path / 'allowed-egress.conf'
        allowlist.write_text(entry)
        result = run_loader(
            'check',
            VPNCHAIN_BT_POLICY_MODE='enforce',
            VPNCHAIN_BT_POLICY_ROLE='ru',
            VPNCHAIN_BT_ALLOWLIST=str(allowlist),
        )
        assert result.returncode != 0, entry


def test_repository_wiring_is_read_only_and_default_disabled():
    compose = COMPOSE.read_text()
    ru = RU_ENTRYPOINT.read_text()
    am = AM_ENTRYPOINT.read_text()

    assert './scripts/vpnchain-bittorrent-policy.sh:/usr/local/sbin/vpnchain-bittorrent-policy:ro' in compose
    assert './policy:/etc/vpnchain/bittorrent-policy:ro' in compose
    assert 'VPNCHAIN_BT_POLICY_MODE:-disabled' in ru
    assert 'VPNCHAIN_BT_POLICY_MODE:-disabled' in am
    assert 'vpnchain-bittorrent-policy apply' in ru
    assert 'vpnchain-bittorrent-policy apply' in am


def test_policy_allowlist_contains_no_active_destinations():
    active = [
        line
        for line in ALLOWLIST.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]

    assert active == []


def test_loader_has_atomic_backup_readback_rollback_and_ipv6_fail_closed():
    script = LOADER.read_text()

    assert 'iptables-save' in script
    assert 'iptables-restore' in script
    assert 'ip6tables-restore' in script
    assert 'read-back' in script
    assert 'rollback' in script
    assert 'OWNER_COMMENT="vpnchain-bittorrent-policy"' in script
    assert '-j DROP' in script
    assert 'RELATED,ESTABLISHED' in script


def test_active_apply_validation_failure_installs_ipv4_and_ipv6_guards(tmp_path):
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    log = tmp_path / 'firewall.log'
    log.touch()
    backend = fake_bin / 'firewall-backend'
    backend.write_text(
        '''#!/bin/sh
printf '%s %s\\n' "$(basename "$0")" "$*" >> "$FAKE_FIREWALL_LOG"
case "$1 $2" in
    '-C FORWARD') exit 1 ;;
esac
exit 0
'''
    )
    backend.chmod(0o755)
    for name in (
        'iptables',
        'ip6tables',
        'iptables-save',
        'ip6tables-save',
        'iptables-restore',
        'ip6tables-restore',
    ):
        (fake_bin / name).symlink_to(backend)

    result = run_loader(
        'apply',
        PATH=f'{fake_bin}:{os.environ["PATH"]}',
        FAKE_FIREWALL_LOG=str(log),
        VPNCHAIN_BT_POLICY_MODE='enforce',
        VPNCHAIN_BT_POLICY_ROLE='ru',
        VPNCHAIN_BT_ALLOWLIST=str(tmp_path / 'missing.policy'),
        VPNCHAIN_BT_STATE_DIR=str(tmp_path / 'state'),
    )

    assert result.returncode != 0
    calls = log.read_text().splitlines()
    assert any(line.startswith('iptables -A VPCBTRU ') and line.endswith('-j DROP') for line in calls)
    assert any(line.startswith('ip6tables -A VPCBTRU ') and line.endswith('-j DROP') for line in calls)


def test_repeated_apply_keeps_original_rollback_snapshot(tmp_path):
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    log = tmp_path / 'firewall.log'
    state = tmp_path / 'firewall.state'
    backend = fake_bin / 'firewall-backend'
    backend.write_text(
        '''#!/usr/bin/env python3
import os
import sys
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
log = Path(os.environ['FAKE_FIREWALL_LOG'])
state = Path(os.environ['FAKE_FIREWALL_STATE'])
with log.open('a') as stream:
    stream.write(name + ' ' + ' '.join(args) + '\\n')

if name.endswith('-save'):
    pass  # An empty ruleset is a valid baseline in a fresh network namespace.
elif name.endswith('-restore'):
    sys.stdin.read()
elif args[:2] == ['-C', 'FORWARD']:
    marker = name + '-' + args[args.index('--comment') + 1]
    present = state.exists() and marker in state.read_text().splitlines()
    raise SystemExit(0 if present else 1)
elif args[:2] == ['-I', 'FORWARD']:
    marker = name + '-' + args[args.index('--comment') + 1]
    with state.open('a') as stream:
        stream.write(marker + '\\n')
elif args[:2] == ['-D', 'FORWARD']:
    marker = name + '-' + args[args.index('--comment') + 1]
    remaining = [line for line in state.read_text().splitlines() if line != marker]
    state.write_text('\\n'.join(remaining) + ('\\n' if remaining else ''))
elif args[:1] == ['-S']:
    print('-N VPCBTRU\\n-A VPCBTRU -j DROP')
'''
    )
    backend.chmod(0o755)
    for name in (
        'iptables',
        'ip6tables',
        'iptables-save',
        'ip6tables-save',
        'iptables-restore',
        'ip6tables-restore',
    ):
        (fake_bin / name).symlink_to(backend)

    allowlist = tmp_path / 'allowed-egress.conf'
    allowlist.write_text('tcp 192.0.2.10/32 443\n')
    state_dir = tmp_path / 'state'
    env = {
        'PATH': f'{fake_bin}:{os.environ["PATH"]}',
        'FAKE_FIREWALL_LOG': str(log),
        'FAKE_FIREWALL_STATE': str(state),
        'VPNCHAIN_BT_POLICY_MODE': 'enforce',
        'VPNCHAIN_BT_POLICY_ROLE': 'ru',
        'VPNCHAIN_BT_ALLOWLIST': str(allowlist),
        'VPNCHAIN_BT_STATE_DIR': str(state_dir),
    }

    first = run_loader('apply', **env)
    second = run_loader('apply', **env)
    rollback = run_loader('rollback', **env)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert rollback.returncode == 0, rollback.stderr
    calls = log.read_text().splitlines()
    assert calls.count('iptables-save ') == 1
    assert calls.count('ip6tables-save ') == 1
    assert sum(line.startswith('iptables -I FORWARD') for line in calls) == 4
    assert sum(line.startswith('ip6tables -I FORWARD') for line in calls) == 4
    assert sum(line.startswith('iptables -D FORWARD') for line in calls) == 4
    assert sum(line.startswith('ip6tables -D FORWARD') for line in calls) == 4
    assert not (state_dir / 'rollback-snapshot').exists()


def test_snapshot_pair_is_not_published_when_ipv6_save_fails(tmp_path):
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    backend = fake_bin / 'firewall-backend'
    backend.write_text(
        '''#!/bin/sh
case "$(basename "$0")" in
    ip6tables-save) exit 1 ;;
    iptables-save) printf '*filter\\nCOMMIT\\n' ;;
esac
case "$1 $2" in
    '-C FORWARD') exit 1 ;;
esac
exit 0
'''
    )
    backend.chmod(0o755)
    for name in (
        'iptables',
        'ip6tables',
        'iptables-save',
        'ip6tables-save',
        'iptables-restore',
        'ip6tables-restore',
    ):
        (fake_bin / name).symlink_to(backend)

    allowlist = tmp_path / 'allowed-egress.conf'
    allowlist.write_text('tcp 192.0.2.10/32 443\n')
    state_dir = tmp_path / 'state'
    result = run_loader(
        'apply',
        PATH=f'{fake_bin}:{os.environ["PATH"]}',
        VPNCHAIN_BT_POLICY_MODE='enforce',
        VPNCHAIN_BT_POLICY_ROLE='ru',
        VPNCHAIN_BT_ALLOWLIST=str(allowlist),
        VPNCHAIN_BT_STATE_DIR=str(state_dir),
    )

    assert result.returncode != 0
    assert not (state_dir / 'rollback-snapshot').exists()
    assert list(state_dir.glob('.rollback-snapshot.*')) == []


def test_legacy_snapshot_pair_is_migrated_without_recapturing_baseline(tmp_path):
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    backend = fake_bin / 'firewall-backend'
    backend.write_text(
        '''#!/bin/sh
case "$(basename "$0")" in
    *-save) exit 99 ;;
    *-restore) cat >/dev/null ;;
esac
case "$1 $2" in
    '-C FORWARD') exit 1 ;;
    '-S VPCBTRU') printf '%s\\n' '-N VPCBTRU' '-A VPCBTRU -j DROP' ;;
esac
exit 0
'''
    )
    backend.chmod(0o755)
    for name in (
        'iptables', 'ip6tables', 'iptables-save', 'ip6tables-save',
        'iptables-restore', 'ip6tables-restore',
    ):
        (fake_bin / name).symlink_to(backend)
    state_dir = tmp_path / 'state'
    state_dir.mkdir()
    (state_dir / 'rollback-v4.rules').write_text('legacy-v4\n')
    (state_dir / 'rollback-v6.rules').write_text('legacy-v6\n')
    allowlist = tmp_path / 'allowed-egress.conf'
    allowlist.write_text('tcp 192.0.2.10/32 443\n')

    result = run_loader(
        'apply', PATH=f'{fake_bin}:{os.environ["PATH"]}',
        VPNCHAIN_BT_POLICY_MODE='enforce', VPNCHAIN_BT_POLICY_ROLE='ru',
        VPNCHAIN_BT_ALLOWLIST=str(allowlist), VPNCHAIN_BT_STATE_DIR=str(state_dir),
    )

    # The minimal fake intentionally fails final read-back; migration occurs
    # before candidate restore and must still preserve the original baseline.
    assert result.returncode != 0
    assert (state_dir / 'rollback-snapshot' / 'ipv4.rules').read_text() == 'legacy-v4\n'
    assert (state_dir / 'rollback-snapshot' / 'ipv6.rules').read_text() == 'legacy-v6\n'
    assert not (state_dir / 'rollback-v4.rules').exists()
    assert not (state_dir / 'rollback-v6.rules').exists()
