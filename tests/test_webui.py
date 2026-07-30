import json
import subprocess
import pytest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from vpnchain.db import init_db
from vpnchain import webui
from vpnchain.webui import CommandPeerBackend, RemoteCommandError, SshPeerBackend, VpnchainWebUI, make_handler
from http.server import ThreadingHTTPServer


@pytest.fixture(autouse=True)
def server_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv('VPNCHAIN_RUNTIME_CONFIG', str(tmp_path / 'missing-default-runtime.env'))
    monkeypatch.setenv('VPNCHAIN_SERVER_PUBLIC_KEY', 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=')
    monkeypatch.setenv('VPNCHAIN_SERVER_ENDPOINT', 'ru.example.test:51820')


def _server(app):
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(app))
    return server


def test_webui_api_create_list_toggle_delete(tmp_path):
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)
    app = VpnchainWebUI(db)
    server = _server(app)
    try:
        import threading
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'

        data = urlencode({'name': 'alice', 'platform': 'ios', 'export_profile': 'amneziawg-ios', 'notes': 'phone profile'}).encode()
        req = Request(base + '/api/peers', data=data, method='POST')
        created = json.loads(urlopen(req, timeout=5).read().decode())
        assert created['peer']['name'] == 'alice'
        assert 'PrivateKey =' in created['client_config']

        listed = json.loads(urlopen(base + '/api/peers', timeout=5).read().decode())
        assert listed['peers'][0]['name'] == 'alice'
        assert listed['peers'][0]['notes'] == 'phone profile'
        assert listed['peers'][0]['activity']['online'] is None

        urlopen(Request(base + '/api/peers/alice/disable', data=b'', method='POST'), timeout=5).read()
        listed = json.loads(urlopen(base + '/api/peers', timeout=5).read().decode())
        assert listed['peers'][0]['enabled'] == 0

        urlopen(Request(base + '/api/peers/alice/delete', data=b'', method='POST'), timeout=5).read()
        listed = json.loads(urlopen(base + '/api/peers', timeout=5).read().decode())
        assert listed['peers'] == []
    finally:
        server.shutdown()
        server.server_close()


def test_webui_html_created_contains_one_time_warning(tmp_path):
    app = VpnchainWebUI(tmp_path / 'vpnchain.sqlite')
    result = app.create_peer({'name': ['bob']})
    assert 'PrivateKey =' in result.client_config


def test_ssh_backend_uses_remote_cli_and_merges_activity(monkeypatch):
    calls = []

    def fake_run(cmd, capture_output, text, timeout, check):
        calls.append(cmd)
        remote = cmd[-1]
        if 'peer list' in remote:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps([
                {'name': 'alice', 'public_key': 'peerpub1', 'address': '10.8.0.3/32', 'enabled': 1}
            ]), stderr='')
        if 'show awg0 dump' in remote:
            return subprocess.CompletedProcess(cmd, 0, stdout='peerpub1\t(none)\t198.51.100.9:51820\t10.8.0.3/32\t1710000000\t12\t34\t25\n', stderr='')
        raise AssertionError(remote)

    monkeypatch.setattr(subprocess, 'run', fake_run)
    backend = SshPeerBackend('vpnchain-ru', remote_db='/var/lib/vpnchain/vpnchain.sqlite', interface='awg0', activity_tool='awg')

    peers = backend.peers_with_activity()

    assert peers[0]['name'] == 'alice'
    assert peers[0]['activity']['endpoint'] == '198.51.100.9:51820'
    assert peers[0]['activity']['rx'] == 12
    assert calls[0][:2] == ['ssh', 'vpnchain-ru']
    assert "vpnchain --db /var/lib/vpnchain/vpnchain.sqlite peer list" in calls[0][-1]


def test_ssh_backend_create_peer_parses_one_time_config(monkeypatch):
    output = """# Generated once\n[Interface]\nPrivateKey = client-private\n\n{\n  \"name\": \"alice\",\n  \"public_key\": \"peerpub1\",\n  \"enabled\": 1\n}\n"""

    def fake_run(cmd, capture_output, text, timeout, check):
        return subprocess.CompletedProcess(cmd, 0, stdout=output, stderr='')

    monkeypatch.setattr(subprocess, 'run', fake_run)
    backend = SshPeerBackend('vpnchain-ru')

    result = backend.create_peer({'name': ['alice'], 'platform': ['ios'], 'export_profile': ['amneziawg-ios']})

    assert 'PrivateKey = client-private' in result.client_config
    assert result.peer['name'] == 'alice'


def test_webui_auth_and_not_found_paths(tmp_path):
    credentials = 'u:p'
    app = VpnchainWebUI(tmp_path / 'vpnchain.sqlite', basic_auth=credentials)
    server = _server(app)
    try:
        import base64
        import threading
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'

        try:
            urlopen(base + '/api/peers', timeout=5)
        except HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError('expected auth challenge')

        auth_value = base64.b64encode(credentials.encode('ascii')).decode('ascii')
        req = Request(base + '/missing', headers={'Authorization': f'Basic {auth_value}'})
        try:
            urlopen(req, timeout=5)
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError('expected not found')
    finally:
        server.shutdown()
        server.server_close()


def test_webui_json_create_and_missing_peer_errors(tmp_path):
    app = VpnchainWebUI(tmp_path / 'vpnchain.sqlite')
    server = _server(app)
    try:
        import threading
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'

        body = json.dumps({'name': 'carol', 'platform': 'android'}).encode()
        req = Request(base + '/api/peers', data=body, method='POST', headers={'Content-Type': 'application/json'})
        created = json.loads(urlopen(req, timeout=5).read().decode())
        assert created['peer']['name'] == 'carol'

        for action in ('disable', 'delete'):
            try:
                urlopen(Request(base + f'/api/peers/ghost/{action}', data=b'', method='POST'), timeout=5)
            except HTTPError as exc:
                assert exc.code == 404
            else:
                raise AssertionError('expected not found')
    finally:
        server.shutdown()
        server.server_close()


def test_command_backend_handles_manager_errors(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, capture_output, text, timeout, check):
        calls.append(cmd)
        if cmd[-2:] == ['peer', 'list']:
            return subprocess.CompletedProcess(cmd, 0, stdout='[]', stderr='')
        return subprocess.CompletedProcess(cmd, 1, stdout='', stderr='peer not found')

    monkeypatch.setattr(subprocess, 'run', fake_run)
    backend = CommandPeerBackend('/usr/local/bin/vpnchain', db_path=str(tmp_path / 'vpnchain.sqlite'))

    assert backend.peers_with_activity() == []
    assert backend.set_peer_enabled('ghost', False) is False
    assert backend.delete_peer('ghost') is False
    assert calls[0][0] == '/usr/local/bin/vpnchain'


def test_remote_command_error_string():
    err = RemoteCommandError(['vpnchain'], 7, 'stdout text', '')
    assert 'exit code 7' in str(err)
    assert 'stdout text' in str(err)


def test_webui_render_helpers_cover_status_and_formatting():
    html = webui.render_index([])
    assert 'No profiles yet' in html

    created = webui.render_created({'name': 'client<1>'}, '[Interface]\nPrivateKey = x')
    assert 'client&lt;1&gt;' in created
    assert '<script>' not in created
    assert 'Select the text above' in created

    online_row = webui.render_peer_row({
        'name': 'alice',
        'enabled': 1,
        'address': '10.8.0.3/32',
        'platform': 'ios',
        'export_profile': 'amneziawg-ios',
        'activity': {'online': True, 'latest_handshake': 1710000000, 'rx': 1200, 'tx': 3400, 'endpoint': '198.51.100.9:51820'},
    })
    assert 'enabled / online' in online_row
    assert '1,200' in online_row

    offline_row = webui.render_peer_row({'name': 'bob', 'enabled': 0, 'activity': {'online': False}})
    assert 'disabled / offline' in offline_row

    assert webui._parse_peer_action_path('/api/peers/alice/disable') == (True, 'alice', 'disable')
    assert webui._parse_peer_action_path('/peers/alice/delete') == (False, 'alice', 'delete')
    assert webui._parse_peer_action_path('/api/peers/alice') is None
    assert webui._split_ssh_options(['-o BatchMode=yes', '-p 2222']) == ['-o', 'BatchMode=yes', '-p', '2222']


def test_webui_main_invokes_serve(monkeypatch, tmp_path):
    calls = []

    def fake_serve(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(webui, 'serve', fake_serve)
    rc = webui.main(['--db', str(tmp_path / 'vpnchain.sqlite'), '--host', '127.0.0.2', '--port', '9090', '--ssh-option', '-o BatchMode=yes'])

    assert rc == 0
    assert calls[0][1]['host'] == '127.0.0.2'
    assert calls[0][1]['port'] == 9090
    assert calls[0][1]['ssh_options'] == ['-o', 'BatchMode=yes']


def test_webui_main_passes_activity_command(monkeypatch, tmp_path):
    calls = []

    def fake_serve(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(webui, 'serve', fake_serve)
    rc = webui.main([
        '--db', str(tmp_path / 'vpnchain.sqlite'),
        '--interface', 'awg0',
        '--activity-tool', 'awg',
        '--activity-command', 'docker',
        '--activity-command', 'exec',
        '--activity-command', 'awg-ru',
        '--activity-command', 'awg',
    ])

    assert rc == 0
    assert calls[0][1]['activity_command'] == ['docker', 'exec', 'awg-ru', 'awg']


def test_cli_webui_passes_activity_command(monkeypatch, tmp_path):
    from vpnchain import cli

    calls = []

    def fake_serve(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(cli, 'serve_webui', fake_serve)
    rc = cli.main([
        '--db', str(tmp_path / 'vpnchain.sqlite'),
        'webui',
        '--interface', 'awg0',
        '--activity-tool', 'awg',
        '--activity-command', 'docker',
        '--activity-command', 'exec',
        '--activity-command', 'awg-ru',
        '--activity-command', 'awg',
    ])

    assert rc == 0
    assert calls[0][1]['activity_command'] == ['docker', 'exec', 'awg-ru', 'awg']
