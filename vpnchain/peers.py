from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
import os

from .db import connect
from .keys import KeyGenerator, KeyPair

# Must match server-ru/awg0.conf.template Address = 10.8.0.1/24.
DEFAULT_NETWORK = ipaddress.ip_network('10.8.0.0/24')

AMNEZIAWG_OBFUSCATION = {
    'Jc': 4,
    'Jmin': 50,
    'Jmax': 1000,
    'S1': 80,
    'S2': 120,
    'H1': 1,
    'H2': 2,
    'H3': 3,
    'H4': 4,
}

IOS_AMNEZIAWG_EXPORT_PROFILE = 'amneziawg-ios'
DEFAULT_EXPORT_PROFILE = 'amneziawg'
DEFAULT_CLIENT_DNS = '10.8.0.1'
# Conservative default for nested VPN/router clients. Higher MTU values (for
# example WireGuard's common 1420) can make TCP connect but stall during TLS on
# some paths/CDNs when the client is behind another router or VPN hop.
DEFAULT_CLIENT_MTU = 1280
DEFAULT_CLIENT_ALLOWED_IPS = '0.0.0.0/0'
PEER_BY_NAME_QUERY = 'SELECT * FROM peers WHERE name = ?'


@dataclass(frozen=True)
class PeerAddResult:
    peer: dict[str, Any]
    private_key: str
    client_config: str


def _rowdict(row) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def next_address(conn, network=DEFAULT_NETWORK) -> str:
    used = {r['address'].split('/')[0] for r in conn.execute('SELECT address FROM peers')}
    for host in list(network.hosts())[2:]:
        if str(host) not in used:
            return f'{host}/32'
    raise RuntimeError('no free peer addresses')


def add_peer(db_path, name: str, *, address: str | None = None, client_type='client', platform='generic', export_profile=DEFAULT_EXPORT_PROFILE, notes: str | None = None, keygen: KeyGenerator | None = None, server_public_key: str | None = None, server_endpoint: str | None = None) -> PeerAddResult:
    keygen = keygen or KeyGenerator()
    pair = keygen.generate()
    with connect(db_path) as conn:
        addr = address or next_address(conn)
        conn.execute('''INSERT INTO peers(name, public_key, address, enabled, client_type, platform, export_profile, notes)
                        VALUES (?, ?, ?, 1, ?, ?, ?, ?)''',
                     (name, pair.public_key, addr, client_type, platform, export_profile, notes))
        peer = _rowdict(conn.execute(PEER_BY_NAME_QUERY, (name,)).fetchone())
    return PeerAddResult(peer=peer, private_key=pair.private_key, client_config=render_client_config(peer, pair.private_key, server_public_key=server_public_key, server_endpoint=server_endpoint))


def list_peers(db_path) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute('SELECT id, name, public_key, address, enabled, client_type, platform, export_profile, notes, created_at, disabled_at FROM peers ORDER BY id')]


def get_peer(db_path, name: str) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        return _rowdict(conn.execute(PEER_BY_NAME_QUERY, (name,)).fetchone())


def set_enabled(db_path, name: str, enabled: bool) -> bool:
    with connect(db_path) as conn:
        cur = conn.execute('UPDATE peers SET enabled = ?, disabled_at = ? WHERE name = ?', (1 if enabled else 0, None if enabled else _now(), name))
        return cur.rowcount > 0


def remove_peer(db_path, name: str) -> bool:
    with connect(db_path) as conn:
        cur = conn.execute('DELETE FROM peers WHERE name = ?', (name,))
        return cur.rowcount > 0


def rotate_peer(db_path, name: str, *, keygen: KeyGenerator | None = None, server_public_key: str | None = None, server_endpoint: str | None = None) -> PeerAddResult:
    keygen = keygen or KeyGenerator()
    pair = keygen.generate()
    with connect(db_path) as conn:
        cur = conn.execute('UPDATE peers SET public_key = ?, enabled = 1, disabled_at = NULL WHERE name = ?', (pair.public_key, name))
        if cur.rowcount == 0:
            raise KeyError(name)
        peer = _rowdict(conn.execute(PEER_BY_NAME_QUERY, (name,)).fetchone())
    return PeerAddResult(peer=peer, private_key=pair.private_key, client_config=render_client_config(peer, pair.private_key, server_public_key=server_public_key, server_endpoint=server_endpoint))


def schedule_cleanup(conn, path: str, ttl_minutes: int = 15) -> None:
    delete_after = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).replace(microsecond=0).isoformat()
    conn.execute('INSERT INTO cleanup_jobs(path, delete_after, status) VALUES (?, ?, ?)', (path, delete_after, 'pending'))


def render_client_config(peer: dict[str, Any], private_key: str, *, server_public_key: str | None = None, server_endpoint: str | None = None) -> str:
    server_public_key = server_public_key or os.environ.get('VPNCHAIN_SERVER_PUBLIC_KEY')
    server_endpoint = server_endpoint or os.environ.get('VPNCHAIN_SERVER_ENDPOINT')
    export_profile = (peer.get('export_profile') or DEFAULT_EXPORT_PROFILE).lower()
    if export_profile == IOS_AMNEZIAWG_EXPORT_PROFILE:
        return render_amneziawg_ios_config(peer, private_key, server_public_key=server_public_key, server_endpoint=server_endpoint)
    return render_amneziawg_config(peer, private_key, server_public_key=server_public_key, server_endpoint=server_endpoint)


def render_amneziawg_config(peer: dict[str, Any], private_key: str, *, server_public_key: str | None = None, server_endpoint: str | None = None) -> str:
    interface_lines = [
        '# Generated once by vpnchain. PrivateKey is not stored in SQLite.',
        '[Interface]',
        f'PrivateKey = {private_key}',
        f'Address = {peer["address"]}',
        f'DNS = {DEFAULT_CLIENT_DNS}',
        f'MTU = {DEFAULT_CLIENT_MTU}',
    ]
    interface_lines.extend(f'{key} = {value}' for key, value in AMNEZIAWG_OBFUSCATION.items())
    return '\n'.join([
        *interface_lines,
        '',
        '[Peer]',
        '# Server peer generated from RU runtime configuration.' if server_public_key and server_endpoint else '# Fill server PublicKey/Endpoint from runtime server configuration.',
        f'PublicKey = {server_public_key or "<server-public-key>"}',
        f'AllowedIPs = {DEFAULT_CLIENT_ALLOWED_IPS}',
        f'Endpoint = {server_endpoint or "<server-endpoint>"}',
        'PersistentKeepalive = 25',
        '',
    ])


def render_amneziawg_ios_config(peer: dict[str, Any], private_key: str, *, server_public_key: str | None = None, server_endpoint: str | None = None) -> str:
    """Render an AmneziaWG iOS compatible wg-quick config.

    The native iOS AmneziaWG app imports plain .conf files with a strict
    WireGuard parser extended only with Jc/Jmin/Jmax/S1/S2/H1-H4 in the
    [Interface] section. Do not emit AmneziaVPN vpn:///JSON export data,
    AmneziaWG 2.0 CPS fields (I1-I5), S3/S4, or range values here.
    """
    interface_lines = [
        '# Generated once by vpnchain for AmneziaWG iOS. PrivateKey is not stored in SQLite.',
        '[Interface]',
        f'PrivateKey = {private_key}',
        f'Address = {peer["address"]}',
        f'DNS = {DEFAULT_CLIENT_DNS}',
        f'MTU = {DEFAULT_CLIENT_MTU}',
    ]
    interface_lines.extend(f'{key} = {value}' for key, value in AMNEZIAWG_OBFUSCATION.items())
    return '\n'.join([
        *interface_lines,
        '',
        '[Peer]',
        '# Server peer generated from RU runtime configuration.' if server_public_key and server_endpoint else '# Fill server PublicKey/Endpoint from runtime server configuration.',
        f'PublicKey = {server_public_key or "<server-public-key>"}',
        f'AllowedIPs = {DEFAULT_CLIENT_ALLOWED_IPS}',
        f'Endpoint = {server_endpoint or "<server-endpoint>"}',
        'PersistentKeepalive = 25',
        '',
    ])


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
