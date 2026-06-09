from __future__ import annotations

import argparse
import base64
import html
import json
import shlex
import subprocess
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlparse

from .activity import load_activity_from_command, merge_peer_activity, parse_wg_dump
from .db import init_db
from .paths import DEFAULT_DB
from .peers import DEFAULT_EXPORT_PROFILE, PeerAddResult, add_peer, list_peers, remove_peer, set_enabled


NAME_REQUIRED_ERROR = 'name is required'
PEER_NOT_FOUND_ERROR = 'peer not found'
DEFAULT_REMOTE_DB = '/var/lib/vpnchain/vpnchain.sqlite'


class PeerBackend(Protocol):
    label: str

    def peers_with_activity(self) -> list[dict]: ...
    def create_peer(self, form: dict[str, list[str]]) -> PeerAddResult: ...
    def set_peer_enabled(self, name: str, enabled: bool) -> bool: ...
    def delete_peer(self, name: str) -> bool: ...


class LocalPeerBackend:
    label = 'local'

    def __init__(self, db_path: str | Path, *, interface: str | None = None, activity_tool: str = 'wg', activity_command: list[str] | None = None):
        self.db_path = str(db_path)
        self.interface = interface
        self.activity_tool = activity_tool
        self.activity_command = activity_command
        init_db(self.db_path)

    def peers_with_activity(self) -> list[dict]:
        activities = load_activity_from_command(self.interface, tool=self.activity_tool, command_prefix=self.activity_command) if self.interface else {}
        return merge_peer_activity(list_peers(self.db_path), activities)

    def create_peer(self, form: dict[str, list[str]]) -> PeerAddResult:
        name = _first(form, 'name').strip()
        if not name:
            raise ValueError(NAME_REQUIRED_ERROR)
        return add_peer(
            self.db_path,
            name,
            address=_optional(form, 'address'),
            client_type=_optional(form, 'client_type') or 'client',
            platform=_optional(form, 'platform') or 'generic',
            export_profile=_optional(form, 'export_profile') or DEFAULT_EXPORT_PROFILE,
            notes=_optional(form, 'notes'),
            server_public_key=_optional(form, 'server_public_key'),
            server_endpoint=_optional(form, 'server_endpoint'),
        )

    def set_peer_enabled(self, name: str, enabled: bool) -> bool:
        return set_enabled(self.db_path, name, enabled)

    def delete_peer(self, name: str) -> bool:
        return remove_peer(self.db_path, name)


def _build_peer_add_args(name: str, form: dict[str, list[str]]) -> list[str]:
    if not name:
        raise ValueError(NAME_REQUIRED_ERROR)
    args = ['peer', 'add', name, '--print-once']
    for key, flag in (
        ('address', '--address'),
        ('client_type', '--client-type'),
        ('platform', '--platform'),
        ('export_profile', '--export-profile'),
        ('notes', '--notes'),
        ('server_public_key', '--server-public-key'),
        ('server_endpoint', '--server-endpoint'),
    ):
        value = _optional(form, key)
        if value:
            args.extend([flag, value])
    return args


def _as_peer_add_result(output: str) -> PeerAddResult:
    client_config, peer = _split_client_config_and_peer_json(output)
    return PeerAddResult(peer=peer, private_key='', client_config=client_config)


def _is_missing_peer(exc: RemoteCommandError) -> bool:
    return PEER_NOT_FOUND_ERROR in exc.stderr.lower()


class CommandPeerBackend:
    """Backend for a WebUI that controls an active server through a local manager command."""

    def __init__(self, command: str, *, db_path: str, interface: str | None = None, activity_tool: str = 'awg', activity_command: list[str] | None = None, timeout: float = 10.0):
        self.command = command
        self.db_path = db_path
        self.interface = interface
        self.activity_tool = activity_tool
        self.activity_command = activity_command
        self.timeout = timeout
        self.label = f'command:{Path(command).name}'

    def peers_with_activity(self) -> list[dict]:
        peers = json.loads(self._manager('peer', 'list'))
        activities = load_activity_from_command(self.interface, tool=self.activity_tool, command_prefix=self.activity_command) if self.interface else {}
        return merge_peer_activity(peers, activities)

    def create_peer(self, form: dict[str, list[str]]) -> PeerAddResult:
        output = self._manager(*_build_peer_add_args(_first(form, 'name').strip(), form))
        return _as_peer_add_result(output)

    def set_peer_enabled(self, name: str, enabled: bool) -> bool:
        try:
            self._manager('peer', 'enable' if enabled else 'disable', name)
        except RemoteCommandError as exc:
            if _is_missing_peer(exc):
                return False
            raise
        return True

    def delete_peer(self, name: str) -> bool:
        try:
            self._manager('peer', 'remove', name)
        except RemoteCommandError as exc:
            if _is_missing_peer(exc):
                return False
            raise
        return True

    def _manager(self, *args: str) -> str:
        cmd = [self.command, '--db', self.db_path, *args]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout, check=False)
        if result.returncode != 0:
            raise RemoteCommandError(cmd, result.returncode, result.stdout, result.stderr)
        return result.stdout


class SshPeerBackend:
    """Backend for a local WebUI controlling an active remote v2 server over SSH."""

    def __init__(
        self,
        target: str,
        *,
        remote_db: str = DEFAULT_REMOTE_DB,
        remote_vpnchain: str = 'vpnchain',
        interface: str | None = None,
        activity_tool: str = 'awg',
        ssh_options: list[str] | None = None,
        timeout: float = 10.0,
    ):
        self.target = target
        self.remote_db = remote_db
        self.remote_vpnchain = remote_vpnchain
        self.interface = interface
        self.activity_tool = activity_tool
        self.ssh_options = ssh_options or []
        self.timeout = timeout
        self.label = f'ssh:{target}'

    def peers_with_activity(self) -> list[dict]:
        peers = self._json(self._vpnchain('peer', 'list'))
        activities = {}
        if self.interface:
            dump = self._ssh_text([self.activity_tool, 'show', self.interface, 'dump'], allow_failure=True)
            activities = parse_wg_dump(dump) if dump else {}
        return merge_peer_activity(peers, activities)

    def create_peer(self, form: dict[str, list[str]]) -> PeerAddResult:
        output = self._vpnchain(*_build_peer_add_args(_first(form, 'name').strip(), form))
        return _as_peer_add_result(output)

    def set_peer_enabled(self, name: str, enabled: bool) -> bool:
        try:
            self._vpnchain('peer', 'enable' if enabled else 'disable', name)
        except RemoteCommandError as exc:
            if _is_missing_peer(exc):
                return False
            raise
        return True

    def delete_peer(self, name: str) -> bool:
        try:
            self._vpnchain('peer', 'remove', name)
        except RemoteCommandError as exc:
            if _is_missing_peer(exc):
                return False
            raise
        return True

    def _vpnchain(self, *args: str) -> str:
        return self._ssh_text([self.remote_vpnchain, '--db', self.remote_db, *args])

    def _json(self, text: str):
        return json.loads(text)

    def _ssh_text(self, remote_args: list[str], *, allow_failure: bool = False) -> str:
        remote = ' '.join(shlex.quote(str(arg)) for arg in remote_args)
        cmd = ['ssh', *self.ssh_options, self.target, remote]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout, check=False)
        if result.returncode != 0:
            if allow_failure:
                return ''
            raise RemoteCommandError(cmd, result.returncode, result.stdout, result.stderr)
        return result.stdout


class VpnchainWebUI:
    def __init__(self, db_path: str | Path, *, interface: str | None = None, activity_tool: str = 'wg', activity_command: list[str] | None = None, backend: PeerBackend | None = None, basic_auth: str | None = None):
        self.backend = backend or LocalPeerBackend(db_path, interface=interface, activity_tool=activity_tool, activity_command=activity_command)
        self.basic_auth = basic_auth

    def peers_with_activity(self) -> list[dict]:
        return self.backend.peers_with_activity()

    def create_peer(self, form: dict[str, list[str]]) -> PeerAddResult:
        return self.backend.create_peer(form)

    def set_peer_enabled(self, name: str, enabled: bool) -> bool:
        return self.backend.set_peer_enabled(name, enabled)

    def delete_peer(self, name: str) -> bool:
        return self.backend.delete_peer(name)


@dataclass
class RemoteCommandError(RuntimeError):
    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str

    def __str__(self) -> str:
        detail = (self.stderr or self.stdout or '').strip()
        return f'remote command failed with exit code {self.returncode}: {detail}'


class VpnchainRequestHandler(BaseHTTPRequestHandler):
    app: VpnchainWebUI

    def do_GET(self):
        if not self._authorized():
            return self._auth_required()
        path = urlparse(self.path).path
        if path in ('/', '/peers'):
            return self._html(render_index(self.app.peers_with_activity(), backend_label=self.app.backend.label))
        if path == '/api/peers':
            return self._json({'backend': self.app.backend.label, 'peers': self.app.peers_with_activity()})
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if not self._authorized():
            return self._auth_required()
        try:
            return self._route_post(urlparse(self.path).path, self._form())
        except ValueError as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # keep local UI useful without exposing tracebacks
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))

    def _route_post(self, path: str, form: dict[str, list[str]]):
        if path == '/peers':
            return self._create_peer_html(form)
        if path == '/api/peers':
            return self._create_peer_api(form)
        if path.startswith('/peers/') or path.startswith('/api/peers/'):
            return self._peer_action(path)
        self.send_error(HTTPStatus.NOT_FOUND)

    def _create_peer_html(self, form: dict[str, list[str]]):
        result = self.app.create_peer(form)
        return self._html(render_created(result.peer, result.client_config))

    def _create_peer_api(self, form: dict[str, list[str]]):
        result = self.app.create_peer(form)
        return self._json({'peer': result.peer, 'client_config': result.client_config}, status=HTTPStatus.CREATED)

    def _peer_action(self, path: str):
        parsed = _parse_peer_action_path(path)
        if not parsed:
            return self.send_error(HTTPStatus.NOT_FOUND)
        is_api, name, action = parsed
        if action in ('enable', 'disable'):
            found = self.app.set_peer_enabled(name, action == 'enable')
        elif action == 'delete':
            found = self.app.delete_peer(name)
        else:
            return self.send_error(HTTPStatus.NOT_FOUND)
        return self._peer_action_response(found, is_api)

    def _peer_action_response(self, found: bool, is_api: bool):
        if not found:
            return self.send_error(HTTPStatus.NOT_FOUND, PEER_NOT_FOUND_ERROR)
        if is_api:
            return self._json({'ok': True})
        return self._redirect('/')

    def _form(self) -> dict[str, list[str]]:
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length).decode('utf-8') if length else ''
        if (self.headers.get('Content-Type') or '').startswith('application/json'):
            data = json.loads(raw or '{}')
            return {str(k): [str(v)] for k, v in data.items() if v is not None}
        return parse_qs(raw, keep_blank_values=True)

    def _authorized(self) -> bool:
        if not self.app.basic_auth:
            return True
        expected = 'Basic ' + base64.b64encode(self.app.basic_auth.encode('utf-8')).decode('ascii')
        return self.headers.get('Authorization') == expected

    def _auth_required(self):
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header('WWW-Authenticate', 'Basic realm="vpnchain"')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _json(self, data, *, status=HTTPStatus.OK):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body: str, *, status=HTTPStatus.OK):
        data = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header('Location', location)
        self.end_headers()

    def log_message(self, format, *args):
        return


def _parse_peer_action_path(path: str) -> tuple[bool, str, str] | None:
    is_api = path.startswith('/api/')
    parts = path.strip('/').split('/')
    offset = 1 if is_api else 0
    if len(parts) != 3 + offset:
        return None
    return is_api, parts[1 + offset], parts[2 + offset]


def make_handler(app: VpnchainWebUI):
    class Handler(VpnchainRequestHandler):
        pass

    Handler.app = app
    return Handler


def serve(
    db_path: str | Path = DEFAULT_DB,
    *,
    host: str = '127.0.0.1',
    port: int = 8080,
    interface: str | None = None,
    activity_tool: str = 'wg',
    remote: str | None = None,
    remote_db: str = DEFAULT_REMOTE_DB,
    remote_vpnchain: str = 'vpnchain',
    ssh_options: list[str] | None = None,
    manager_command: str | None = None,
    activity_command: list[str] | None = None,
    basic_auth: str | None = None,
) -> None:
    if manager_command:
        backend = CommandPeerBackend(manager_command, db_path=str(db_path), interface=interface, activity_tool=activity_tool, activity_command=activity_command)
        app = VpnchainWebUI(db_path, backend=backend, basic_auth=basic_auth)
        source = f'command={manager_command} db={db_path}'
    elif remote:
        backend = SshPeerBackend(remote, remote_db=remote_db, remote_vpnchain=remote_vpnchain, interface=interface, activity_tool=activity_tool, ssh_options=ssh_options)
        app = VpnchainWebUI(db_path, backend=backend, basic_auth=basic_auth)
        source = f'remote={remote} remote_db={remote_db}'
    else:
        app = VpnchainWebUI(db_path, interface=interface, activity_tool=activity_tool, activity_command=activity_command, basic_auth=basic_auth)
        source = f'db={db_path}'
    server = ThreadingHTTPServer((host, port), make_handler(app))
    print(f'vpnchain WebUI listening on http://{host}:{port} ({source})')
    server.serve_forever()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='vpnchain-webui')
    parser.add_argument('--db', default=str(DEFAULT_DB), help='Local SQLite DB path for local mode')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--interface', help='wg/awg interface for live activity, e.g. awg0')
    parser.add_argument('--activity-tool', default='wg', choices=('wg', 'awg'))
    parser.add_argument('--activity-command', action='append', default=[], help='Override activity command prefix, repeatable; example: --activity-command docker --activity-command exec --activity-command awg-ru --activity-command awg')
    parser.add_argument('--remote', help='SSH target for active v2 server, e.g. vpnchain-ru')
    parser.add_argument('--remote-db', default=DEFAULT_REMOTE_DB, help='SQLite DB path on the remote server')
    parser.add_argument('--remote-vpnchain', default='vpnchain', help='vpnchain command path on the remote server')
    parser.add_argument('--ssh-option', action='append', default=[], help='Extra ssh option, repeatable; pass as "-o BatchMode=yes"')
    parser.add_argument('--manager-command', help='Local active-server manager command backend')
    parser.add_argument('--basic-auth', help='Require HTTP Basic auth as user:password')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    serve(
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


def render_index(peers, *, backend_label: str = 'local') -> str:
    rows = '\n'.join(render_peer_row(peer) for peer in peers) or '<tr><td colspan="11">No profiles yet.</td></tr>'
    backend = html.escape(backend_label)
    return f'''<!doctype html>
<html><head><meta charset="utf-8"><title>vpnchain manager</title>{STYLE}</head>
<body><main>
<h1>vpnchain manager</h1>
<p class="muted">Local administration UI connected to <code>{backend}</code>. Client private keys are generated once and are not stored.</p>
<section class="card"><h2>Create profile</h2>
<form method="post" action="/peers" class="grid">
<label>Name <input name="name" required></label>
<label>Address (optional) <input name="address" placeholder="10.8.0.42/32"></label>
<label>Platform <input name="platform" value="generic"></label>
<label>Export profile <select name="export_profile"><option value="amneziawg">amneziawg</option><option value="amneziawg-ios">amneziawg-ios</option></select></label>
<label class="wide">Notes <input name="notes"></label>
<label>Server public key <input name="server_public_key" placeholder="auto from RU env when omitted"></label>
<label>Server endpoint <input name="server_endpoint" placeholder="ru.example:51820"></label>
<button>Create and show one-time config</button>
</form></section>
<section class="card"><h2>Profiles</h2>
<table><thead><tr><th>Name</th><th>Address</th><th>Platform</th><th>Profile type</th><th>Notes</th><th>Status</th><th>Handshake</th><th>Rx</th><th>Tx</th><th>Endpoint</th><th>Actions</th></tr></thead><tbody>{rows}</tbody></table>
</section></main></body></html>'''


def render_created(peer, client_config: str) -> str:
    safe_name = html.escape(peer['name'])
    config = html.escape(client_config)
    filename = html.escape(f"{peer['name']}.conf")
    return f'''<!doctype html>
<html><head><meta charset="utf-8"><title>Created {safe_name}</title>{STYLE}</head>
<body><main><section class="card">
<h1>Created {safe_name}</h1>
<p class="warn">Copy or download this now. The client private key is not stored and cannot be shown again.</p>
<p><a href="/">Back to profiles</a></p>
<textarea id="config" rows="18" spellcheck="false">{config}</textarea>
<p class="muted">Select the text above and save it as <code>{filename}</code>.</p>
</section></main></body></html>'''


def render_peer_row(peer) -> str:
    name = html.escape(str(peer['name']))
    activity = peer.get('activity') or {}
    status = 'enabled' if peer.get('enabled') else 'disabled'
    online = activity.get('online')
    if online is True:
        status += ' / online'
    elif online is False:
        status += ' / offline'
    else:
        status += ' / activity unknown'
    toggle = 'disable' if peer.get('enabled') else 'enable'
    notes = html.escape(str(peer.get('notes') or ''))
    return f'''<tr>
<td>{name}</td><td>{html.escape(str(peer.get('address') or ''))}</td><td>{html.escape(str(peer.get('platform') or 'generic'))}</td><td>{html.escape(str(peer.get('export_profile') or 'amneziawg'))}</td><td>{notes}</td><td>{html.escape(status)}</td>
<td>{_time(activity.get('latest_handshake'))}</td><td>{_bytes(activity.get('rx'))}</td><td>{_bytes(activity.get('tx'))}</td><td>{html.escape(str(activity.get('endpoint') or '—'))}</td>
<td class="actions"><form method="post" action="/peers/{name}/{toggle}"><button>{toggle}</button></form>
<form method="post" action="/peers/{name}/delete" onsubmit="return confirm('Delete {name}?')"><button class="danger">delete</button></form></td></tr>'''


def _split_client_config_and_peer_json(output: str) -> tuple[str, dict]:
    marker = '\n{'
    pos = output.rfind(marker)
    if pos < 0:
        raise ValueError('remote add output did not include peer JSON')
    config = output[:pos + 1]
    peer = json.loads(output[pos + 1:])
    return config, peer


def _split_ssh_options(options: list[str]) -> list[str]:
    split: list[str] = []
    for option in options:
        split.extend(shlex.split(option))
    return split


def _first(form: dict[str, list[str]], key: str) -> str:
    return form.get(key, [''])[0]


def _optional(form: dict[str, list[str]], key: str) -> str | None:
    value = _first(form, key).strip()
    return value or None


def _time(value) -> str:
    if not value:
        return '—'
    return html.escape(str(value))


def _bytes(value) -> str:
    if value is None:
        return '—'
    return f'{int(value):,}'


STYLE = '''<style>
body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;margin:0;background:#f6f7fb;color:#1b1f24}main{max-width:1100px;margin:2rem auto;padding:0 1rem}.card{background:white;border:1px solid #dde1e7;border-radius:12px;padding:1rem;margin:1rem 0;box-shadow:0 1px 3px #0001}.muted{color:#667085}.warn{color:#9a5b00;font-weight:600}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.75rem}.wide{grid-column:1/-1}label{display:flex;flex-direction:column;gap:.25rem;font-weight:600}input,select,textarea{font:inherit;padding:.55rem;border:1px solid #cbd5e1;border-radius:8px}textarea{width:100%;box-sizing:border-box;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}button{font:inherit;padding:.45rem .75rem;border:1px solid #94a3b8;border-radius:8px;background:#fff;cursor:pointer}.danger{border-color:#dc2626;color:#b91c1c}table{width:100%;border-collapse:collapse}th,td{text-align:left;border-bottom:1px solid #e5e7eb;padding:.55rem;vertical-align:top}.actions{display:flex;gap:.4rem;flex-wrap:wrap}.actions form{margin:0}
</style>'''


if __name__ == '__main__':
    raise SystemExit(main())
