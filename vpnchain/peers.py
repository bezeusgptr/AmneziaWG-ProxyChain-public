from __future__ import annotations

import base64
import binascii
import ipaddress
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .db import connect
from .keys import KeyGenerationError, KeyGenerator

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
DEFAULT_SERVER_RUNTIME_CONFIG = Path('/etc/vpnchain/vpnchain.env')
SERVER_PUBLIC_KEY_ENV = 'VPNCHAIN_SERVER_PUBLIC_KEY'
SERVER_ENDPOINT_ENV = 'VPNCHAIN_SERVER_ENDPOINT'
_PUBLIC_KEY_RE = re.compile(r'^[A-Za-z0-9+/]{43}=$')
_DNS_NAME_RE = re.compile(r'^(?=.{1,253}\.?$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$')
_BRACKETED_ENDPOINT_RE = re.compile(r'^\[([^]]+)]:(\d+)$')


class ServerRuntimeConfigError(KeyGenerationError, ValueError):
    pass


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
    server_runtime = resolve_server_runtime(server_public_key, server_endpoint)
    keygen = keygen or KeyGenerator()
    pair = keygen.generate()
    with connect(db_path) as conn:
        addr = address or next_address(conn)
        conn.execute('''INSERT INTO peers(name, public_key, address, enabled, client_type, platform, export_profile, notes)
                        VALUES (?, ?, ?, 1, ?, ?, ?, ?)''',
                     (name, pair.public_key, addr, client_type, platform, export_profile, notes))
        peer = _rowdict(conn.execute(PEER_BY_NAME_QUERY, (name,)).fetchone())
        assert peer is not None
        client_config = render_client_config(peer, pair.private_key, _resolved_runtime=server_runtime)
    return PeerAddResult(peer=peer, private_key=pair.private_key, client_config=client_config)


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
    server_runtime = resolve_server_runtime(server_public_key, server_endpoint)
    keygen = keygen or KeyGenerator()
    pair = keygen.generate()
    with connect(db_path) as conn:
        cur = conn.execute('UPDATE peers SET public_key = ?, enabled = 1, disabled_at = NULL WHERE name = ?', (pair.public_key, name))
        if cur.rowcount == 0:
            raise KeyError(name)
        peer = _rowdict(conn.execute(PEER_BY_NAME_QUERY, (name,)).fetchone())
        assert peer is not None
        client_config = render_client_config(peer, pair.private_key, _resolved_runtime=server_runtime)
    return PeerAddResult(peer=peer, private_key=pair.private_key, client_config=client_config)


def schedule_cleanup(conn, path: str, ttl_minutes: int = 15) -> None:
    delete_after = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).replace(microsecond=0).isoformat()
    conn.execute('INSERT INTO cleanup_jobs(path, delete_after, status) VALUES (?, ?, ?)', (path, delete_after, 'pending'))


def render_client_config(peer: dict[str, Any], private_key: str, *, server_public_key: str | None = None, server_endpoint: str | None = None, _resolved_runtime: tuple[str, str] | None = None) -> str:
    server_public_key, server_endpoint = _resolved_runtime or resolve_server_runtime(server_public_key, server_endpoint)
    export_profile = (peer.get('export_profile') or DEFAULT_EXPORT_PROFILE).lower()
    if export_profile == IOS_AMNEZIAWG_EXPORT_PROFILE:
        return _render_amneziawg_ios_config(peer, private_key, server_public_key, server_endpoint)
    return _render_amneziawg_config(peer, private_key, server_public_key, server_endpoint)


def render_amneziawg_config(peer: dict[str, Any], private_key: str, *, server_public_key: str | None = None, server_endpoint: str | None = None) -> str:
    server_public_key, server_endpoint = resolve_server_runtime(server_public_key, server_endpoint)
    return _render_amneziawg_config(peer, private_key, server_public_key, server_endpoint)


def _render_amneziawg_config(peer: dict[str, Any], private_key: str, server_public_key: str, server_endpoint: str) -> str:
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
        '# Server peer generated from RU runtime configuration.',
        f'PublicKey = {server_public_key}',
        f'AllowedIPs = {DEFAULT_CLIENT_ALLOWED_IPS}',
        f'Endpoint = {server_endpoint}',
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
    server_public_key, server_endpoint = resolve_server_runtime(server_public_key, server_endpoint)
    return _render_amneziawg_ios_config(peer, private_key, server_public_key, server_endpoint)


def _render_amneziawg_ios_config(peer: dict[str, Any], private_key: str, server_public_key: str, server_endpoint: str) -> str:
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
        '# Server peer generated from RU runtime configuration.',
        f'PublicKey = {server_public_key}',
        f'AllowedIPs = {DEFAULT_CLIENT_ALLOWED_IPS}',
        f'Endpoint = {server_endpoint}',
        'PersistentKeepalive = 25',
        '',
    ])


def resolve_server_runtime(server_public_key: str | None = None, server_endpoint: str | None = None) -> tuple[str, str]:
    if server_public_key is not None or server_endpoint is not None:
        return _validate_server_runtime_pair(server_public_key, server_endpoint, 'explicit arguments')

    path = _server_runtime_config_path()
    if path.exists():
        runtime_config = _read_server_runtime_config()
        return _validate_server_runtime_pair(
            runtime_config.get(SERVER_PUBLIC_KEY_ENV),
            runtime_config.get(SERVER_ENDPOINT_ENV),
            f'runtime config {path}',
        )

    return _validate_server_runtime_pair(
        os.environ.get(SERVER_PUBLIC_KEY_ENV),
        os.environ.get(SERVER_ENDPOINT_ENV),
        'environment',
    )


def _validate_server_runtime_pair(public_key: str | None, endpoint: str | None, source: str) -> tuple[str, str]:
    public_key = public_key.strip() if public_key is not None else None
    endpoint = endpoint.strip() if endpoint is not None else None
    missing = [name for name, value in (('PublicKey', public_key), ('Endpoint', endpoint)) if not value]
    if missing:
        raise ServerRuntimeConfigError(
            f'{source} must provide both server PublicKey and Endpoint; missing {", ".join(missing)}'
        )
    assert public_key is not None and endpoint is not None
    _validate_server_public_key(public_key)
    _validate_server_endpoint(endpoint)
    return public_key, endpoint


def _read_server_runtime_config() -> dict[str, str]:
    path = _server_runtime_config_path()
    if not path.exists():
        return {}
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError as exc:
        raise ServerRuntimeConfigError(f'server runtime config is not readable: {path}') from exc
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in stripped:
            continue
        key, value = stripped.split('=', 1)
        if key not in (SERVER_PUBLIC_KEY_ENV, SERVER_ENDPOINT_ENV):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        values[key] = value
    return values


def _server_runtime_config_path() -> Path:
    configured_path = os.environ.get('VPNCHAIN_RUNTIME_CONFIG')
    return Path(configured_path) if configured_path else DEFAULT_SERVER_RUNTIME_CONFIG


def _validate_server_public_key(public_key: str) -> None:
    try:
        decoded = base64.b64decode(public_key, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ServerRuntimeConfigError('server PublicKey must be a valid 44-character WireGuard base64 public key') from exc
    if not _PUBLIC_KEY_RE.fullmatch(public_key) or len(decoded) != 32:
        raise ServerRuntimeConfigError('server PublicKey must be a valid 44-character WireGuard base64 public key')


def _validate_server_endpoint(endpoint: str) -> None:
    if any(character.isspace() for character in endpoint) or '<' in endpoint or '>' in endpoint:
        raise ServerRuntimeConfigError('server Endpoint must be a valid HOST:PORT UDP endpoint')
    bracketed = _BRACKETED_ENDPOINT_RE.fullmatch(endpoint)
    if bracketed:
        host, port_text = bracketed.groups()
        try:
            if ipaddress.ip_address(host).version != 6:
                raise ValueError
        except ValueError as exc:
            raise ServerRuntimeConfigError('server Endpoint must be a valid HOST:PORT UDP endpoint') from exc
    else:
        host, separator, port_text = endpoint.rpartition(':')
        if not separator or not host or ':' in host:
            raise ServerRuntimeConfigError('server Endpoint must be a valid HOST:PORT UDP endpoint')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if re.fullmatch(r'[0-9.]+', host):
                raise ServerRuntimeConfigError('server Endpoint must be a valid HOST:PORT UDP endpoint')
            if not _DNS_NAME_RE.fullmatch(host):
                raise ServerRuntimeConfigError('server Endpoint must be a valid HOST:PORT UDP endpoint')
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ServerRuntimeConfigError('server Endpoint must be a valid HOST:PORT UDP endpoint') from exc
    if not 1 <= port <= 65535 or str(port) != port_text:
        raise ServerRuntimeConfigError('server Endpoint must be a valid HOST:PORT UDP endpoint')


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
