import json
import os
import subprocess
import sys

from vpnchain import cli


TEST_SERVER_PUBLIC_KEY = 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8='
TEST_SERVER_ENDPOINT = 'ru.example.test:51820'


def run_cli(*args, db, with_server_runtime=True):
    env = {**os.environ, 'VPNCHAIN_TEST_KEY_SEED': 'cli-test'}
    env['VPNCHAIN_RUNTIME_CONFIG'] = str(db.parent / 'missing-vpnchain.env')
    if with_server_runtime:
        env['VPNCHAIN_SERVER_PUBLIC_KEY'] = TEST_SERVER_PUBLIC_KEY
        env['VPNCHAIN_SERVER_ENDPOINT'] = TEST_SERVER_ENDPOINT
    else:
        env.pop('VPNCHAIN_SERVER_PUBLIC_KEY', None)
        env.pop('VPNCHAIN_SERVER_ENDPOINT', None)

    return subprocess.run(
        [sys.executable, '-m', 'vpnchain', '--db', str(db), *args],
        text=True,
        capture_output=True,
        timeout=20,
        env=env,
    )


def test_cli_init_add_list_show_disable_enable_remove(tmp_path):
    db = tmp_path / 'vpnchain.sqlite'

    init = run_cli('init-db', db=db)
    assert init.returncode == 0
    assert 'initialized' in init.stdout

    add = run_cli('peer', 'add', 'alice', '--platform', 'ios', '--export-profile', 'amneziawg-ios', '--print-once', db=db)
    assert add.returncode == 0
    assert 'PrivateKey =' in add.stdout
    peer = json.loads(add.stdout[add.stdout.rfind('{'):])
    assert peer['name'] == 'alice'
    assert 'private_key' not in peer

    listed = run_cli('peer', 'list', db=db)
    assert listed.returncode == 0
    peers = json.loads(listed.stdout)
    assert peers[0]['name'] == 'alice'

    shown = run_cli('peer', 'show', 'alice', db=db)
    assert shown.returncode == 0
    assert json.loads(shown.stdout)['name'] == 'alice'

    disabled = run_cli('peer', 'disable', 'alice', db=db)
    assert disabled.returncode == 0
    assert 'disabled alice' in disabled.stdout

    enabled = run_cli('peer', 'enable', 'alice', db=db)
    assert enabled.returncode == 0
    assert 'enabled alice' in enabled.stdout

    removed = run_cli('peer', 'remove', 'alice', db=db)
    assert removed.returncode == 0
    assert 'removed alice' in removed.stdout


def test_cli_missing_peer_and_export_errors(tmp_path):
    db = tmp_path / 'vpnchain.sqlite'

    missing = run_cli('peer', 'show', 'ghost', db=db)
    assert missing.returncode == 1
    assert 'peer not found' in missing.stderr

    export = run_cli('peer', 'export', 'ghost', '--output', str(tmp_path / 'ghost.conf'), db=db)
    assert export.returncode == 2
    assert 'export requires a private key' in export.stderr


def test_cli_output_safety_preflight(tmp_path):
    db = tmp_path / 'vpnchain.sqlite'
    unsafe = run_cli('peer', 'add', 'alice', '--output', 'alice.conf', db=db)
    assert unsafe.returncode == 1
    assert 'git worktree' in unsafe.stderr


def test_cli_add_fails_cleanly_without_server_runtime(tmp_path):
    db = tmp_path / 'vpnchain.sqlite'

    result = run_cli('peer', 'add', 'alice', '--print-once', db=db, with_server_runtime=False)

    assert result.returncode == 1
    assert 'server PublicKey and Endpoint' in result.stderr
    assert 'Traceback' not in result.stderr
    assert '<server-' not in result.stdout


def test_cli_main_direct_peer_lifecycle(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv('VPNCHAIN_TEST_KEY_SEED', 'cli-direct')
    monkeypatch.setenv('VPNCHAIN_RUNTIME_CONFIG', str(tmp_path / 'missing-vpnchain.env'))
    monkeypatch.setenv('VPNCHAIN_SERVER_PUBLIC_KEY', TEST_SERVER_PUBLIC_KEY)
    monkeypatch.setenv('VPNCHAIN_SERVER_ENDPOINT', TEST_SERVER_ENDPOINT)
    db = str(tmp_path / 'vpnchain.sqlite')

    assert cli.main(['--db', db, 'init-db']) == 0
    assert cli.main(['--db', db, 'peer', 'add', 'direct', '--print-once']) == 0
    add_out = capsys.readouterr().out
    assert 'PrivateKey =' in add_out
    assert '"name": "direct"' in add_out

    assert cli.main(['--db', db, 'peer', 'list']) == 0
    peers = json.loads(capsys.readouterr().out)
    assert peers[0]['name'] == 'direct'

    assert cli.main(['--db', db, 'peer', 'show', 'direct']) == 0
    assert json.loads(capsys.readouterr().out)['name'] == 'direct'

    assert cli.main(['--db', db, 'peer', 'disable', 'direct']) == 0
    assert cli.main(['--db', db, 'peer', 'enable', 'direct']) == 0
    assert cli.main(['--db', db, 'peer', 'rotate', 'direct']) == 0
    rotate_out = capsys.readouterr().out
    assert 'PrivateKey =' in rotate_out

    assert cli.main(['--db', db, 'peer', 'remove', 'direct']) == 0
    assert cli.main(['--db', db, 'peer', 'show', 'direct']) == 1
    assert 'peer not found' in capsys.readouterr().err


def test_cli_main_repo_check_and_export_error(tmp_path, capsys):
    db = str(tmp_path / 'vpnchain.sqlite')
    assert cli.main(['--db', db, 'repo-check', str(tmp_path)]) == 0
    assert cli.main(['--db', db, 'peer', 'export', 'ghost', '--output', str(tmp_path / 'ghost.conf')]) == 2
    assert 'export requires a private key' in capsys.readouterr().err
