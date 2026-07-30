from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .db import connect, init_db
from .paths import DEFAULT_DB, DEFAULT_TTL_MINUTES, UnsafeOutputPath, ensure_output_path_safe, write_private_file
from .keys import KeyGenerationError
from .peers import DEFAULT_EXPORT_PROFILE, PeerRuntimeSyncError, add_peer, get_peer, list_peers, remove_peer, rotate_peer, schedule_cleanup, set_enabled, sync_peer_runtime
from .webui import serve as serve_webui
from .repo_check import scan_repo

PEER_NOT_FOUND = 'peer not found'


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='vpnchain')
    p.add_argument('--db', default=str(DEFAULT_DB), help='SQLite DB path (default: %(default)s)')
    sub = p.add_subparsers(dest='cmd', required=True)
    web = sub.add_parser('webui')
    web.add_argument('--host', default='127.0.0.1')
    web.add_argument('--port', type=int, default=8080)
    web.add_argument('--interface', help='wg/awg interface for live activity, e.g. awg0')
    web.add_argument('--activity-tool', default='wg', choices=('wg', 'awg'))
    web.add_argument('--activity-command', action='append', default=[], help='Override activity command prefix, repeatable; example: --activity-command docker --activity-command exec --activity-command awg-ru --activity-command awg')
    web.add_argument('--remote', help='SSH target for active v2 server, e.g. vpnchain-ru')
    web.add_argument('--remote-db', default='/var/lib/vpnchain/vpnchain.sqlite', help='SQLite DB path on the remote server')
    web.add_argument('--remote-vpnchain', default='vpnchain', help='vpnchain command path on the remote server')
    web.add_argument('--ssh-option', action='append', default=[], help='Extra ssh option, repeatable; pass as "-o BatchMode=yes"')
    web.add_argument('--manager-command', help='Local active-server manager command backend')
    web.add_argument('--basic-auth', help='Require HTTP Basic auth as user:password')
    sub.add_parser('init-db')
    repo = sub.add_parser('repo-check')
    repo.add_argument('path', nargs='?', default='.')

    peer = sub.add_parser('peer')
    ps = peer.add_subparsers(dest='peer_cmd', required=True)
    add = ps.add_parser('add')
    add.add_argument('name')
    add.add_argument('--address')
    add.add_argument('--client-type', default='client')
    add.add_argument('--platform', default='generic')
    add.add_argument('--export-profile', default=DEFAULT_EXPORT_PROFILE, choices=(DEFAULT_EXPORT_PROFILE, 'amneziawg-ios'), help='Client config export profile. amneziawg-ios emits a strict .conf accepted by the native iOS AmneziaWG app.')
    add.add_argument('--notes')
    add.add_argument('--server-public-key', default=None, help='Server public key to embed; defaults to VPNCHAIN_RUNTIME_CONFIG or VPNCHAIN_SERVER_PUBLIC_KEY')
    add.add_argument('--server-endpoint', default=None, help='Server HOST:PORT to embed; defaults to VPNCHAIN_RUNTIME_CONFIG or VPNCHAIN_SERVER_ENDPOINT')
    add.add_argument('--output')
    add.add_argument('--print-once', action='store_true')
    add.add_argument('--ttl-minutes', type=int, default=DEFAULT_TTL_MINUTES)
    ps.add_parser('list')
    show = ps.add_parser('show'); show.add_argument('name')
    dis = ps.add_parser('disable'); dis.add_argument('name')
    ena = ps.add_parser('enable'); ena.add_argument('name')
    rm = ps.add_parser('remove'); rm.add_argument('name')
    rot = ps.add_parser('rotate'); rot.add_argument('name'); rot.add_argument('--output'); rot.add_argument('--server-public-key', default=None); rot.add_argument('--server-endpoint', default=None); rot.add_argument('--ttl-minutes', type=int, default=DEFAULT_TTL_MINUTES)
    exp = ps.add_parser('export'); exp.add_argument('name'); exp.add_argument('--format', default=None); exp.add_argument('--output', required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == 'init-db':
        init_db(args.db)
        print(f'initialized {args.db}')
        return 0
    if args.cmd == 'repo-check':
        return _handle_repo_check(args)
    if args.cmd == 'webui':
        return _handle_webui(args)
    if args.cmd == 'peer':
        return _handle_peer(args)
    return 2


def _handle_repo_check(args: argparse.Namespace) -> int:
    findings = scan_repo(args.path)
    for finding in findings:
        print(f'{finding.path}: {finding.reason}')
    return 1 if findings else 0


def _handle_webui(args: argparse.Namespace) -> int:
    from .webui import _split_ssh_options

    serve_webui(
        args.db,
        host=args.host,
        port=args.port,
        interface=args.interface,
        activity_tool=args.activity_tool,
        remote=args.remote,
        remote_db=args.remote_db,
        remote_vpnchain=args.remote_vpnchain,
        ssh_options=_split_ssh_options(args.ssh_option),
        manager_command=args.manager_command,
        activity_command=args.activity_command or None,
        basic_auth=args.basic_auth,
    )
    return 0


def _handle_peer(args: argparse.Namespace) -> int:
    handlers = {
        'add': _handle_peer_add,
        'list': _handle_peer_list,
        'show': _handle_peer_show,
        'disable': _handle_peer_enabled,
        'enable': _handle_peer_enabled,
        'remove': _handle_peer_remove,
        'rotate': _handle_peer_rotate,
        'export': _handle_peer_export,
    }
    return handlers[args.peer_cmd](args)


def _handle_peer_add(args: argparse.Namespace) -> int:
    output_path = _preflight_output(args.output)
    init_db(args.db)
    try:
        result = add_peer(
            args.db,
            args.name,
            address=args.address,
            client_type=args.client_type,
            platform=args.platform,
            export_profile=args.export_profile,
            notes=args.notes,
            server_public_key=args.server_public_key,
            server_endpoint=args.server_endpoint,
        )
    except KeyGenerationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        sync_peer_runtime(result.peer)
    except PeerRuntimeSyncError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _emit_or_write(
        args.db,
        result.client_config,
        output_path,
        args.print_once or not output_path,
        args.ttl_minutes,
    )
    print(json.dumps(_public_peer(result.peer), indent=2))
    return 0


def _handle_peer_list(args: argparse.Namespace) -> int:
    init_db(args.db)
    print(json.dumps([_public_peer(peer) for peer in list_peers(args.db)], indent=2))
    return 0


def _handle_peer_show(args: argparse.Namespace) -> int:
    init_db(args.db)
    peer = get_peer(args.db, args.name)
    if not peer:
        print(PEER_NOT_FOUND, file=sys.stderr)
        return 1
    print(json.dumps(_public_peer(peer), indent=2))
    return 0


def _handle_peer_enabled(args: argparse.Namespace) -> int:
    init_db(args.db)
    if not set_enabled(args.db, args.name, args.peer_cmd == 'enable'):
        print(PEER_NOT_FOUND, file=sys.stderr)
        return 1
    peer = get_peer(args.db, args.name)
    assert peer is not None
    try:
        sync_peer_runtime(peer, remove=args.peer_cmd != 'enable')
    except PeerRuntimeSyncError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f'{args.peer_cmd}d {args.name}')
    return 0


def _handle_peer_remove(args: argparse.Namespace) -> int:
    init_db(args.db)
    peer = get_peer(args.db, args.name)
    if not peer:
        print(PEER_NOT_FOUND, file=sys.stderr)
        return 1
    if not remove_peer(args.db, args.name):
        print(PEER_NOT_FOUND, file=sys.stderr)
        return 1
    try:
        sync_peer_runtime(peer, remove=True)
    except PeerRuntimeSyncError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f'removed {args.name}')
    return 0


def _handle_peer_rotate(args: argparse.Namespace) -> int:
    output_path = _preflight_output(args.output)
    init_db(args.db)
    try:
        result = rotate_peer(
            args.db,
            args.name,
            server_public_key=args.server_public_key,
            server_endpoint=args.server_endpoint,
        )
    except KeyError:
        print(PEER_NOT_FOUND, file=sys.stderr)
        return 1
    except KeyGenerationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        sync_peer_runtime(result.peer)
    except PeerRuntimeSyncError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _emit_or_write(args.db, result.client_config, output_path, not output_path, args.ttl_minutes)
    print(json.dumps(_public_peer(result.peer), indent=2))
    return 0


def _handle_peer_export(args: argparse.Namespace) -> int:
    print('export requires a private key and is only available at add/rotate time', file=sys.stderr)
    return 2


def _preflight_output(output: str | None) -> str | None:
    if not output:
        return None
    try:
        return str(ensure_output_path_safe(output))
    except UnsafeOutputPath as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)


def _emit_or_write(db_path: str, content: str, output: str | None, print_once: bool, ttl_minutes: int) -> None:
    if output:
        path = Path(output)
        write_private_file(path, content)
        with connect(db_path) as conn:
            schedule_cleanup(conn, str(path), ttl_minutes)
        print(f'wrote one-time client config to {path} (mode 0600; cleanup TTL {ttl_minutes} minutes)')
    if print_once:
        print(content, end='')


def _public_peer(peer: dict) -> dict:
    return {k: v for k, v in peer.items() if k != 'private_key'}

if __name__ == '__main__':
    raise SystemExit(main())
