import pytest

import vpnchain.peers as peers_module
from vpnchain.db import connect, init_db
from vpnchain.keys import KeyGenerator
from vpnchain.peers import (
    DEFAULT_CLIENT_DNS,
    ServerRuntimeConfigError,
    add_peer,
    get_peer,
    list_peers,
    render_amneziawg_ios_config,
    rotate_peer,
)


TEST_SERVER_PUBLIC_KEY = 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8='
TEST_SERVER_ENDPOINT = 'ru.example.test:51820'


@pytest.fixture(autouse=True)
def server_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv('VPNCHAIN_RUNTIME_CONFIG', str(tmp_path / 'missing-default-runtime.env'))
    monkeypatch.setenv('VPNCHAIN_SERVER_PUBLIC_KEY', TEST_SERVER_PUBLIC_KEY)
    monkeypatch.setenv('VPNCHAIN_SERVER_ENDPOINT', TEST_SERVER_ENDPOINT)


def test_add_peer_persists_public_state_not_private_key(tmp_path):
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)
    result = add_peer(db, 'alice', platform='ios', export_profile='qr', keygen=KeyGenerator(seed='test'))
    assert 'PrivateKey =' in result.client_config
    assert result.private_key in result.client_config
    peer = get_peer(db, 'alice')
    assert peer['public_key'] == result.peer['public_key']
    assert peer['platform'] == 'ios'
    assert peer['export_profile'] == 'qr'
    with connect(db) as conn:
        dump = '\n'.join(conn.iterdump())
    assert result.private_key not in dump
    assert 'private_key' not in {r['name'] for r in connect(db).execute('PRAGMA table_info(peers)')}


def test_list_and_rotate(tmp_path):
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)
    old = add_peer(db, 'alice', keygen=KeyGenerator(seed='old')).peer['public_key']
    assert len(list_peers(db)) == 1
    new = rotate_peer(db, 'alice', keygen=KeyGenerator(seed='new')).peer['public_key']
    assert old != new


def test_default_amneziawg_profile_includes_dns_mtu_and_obfuscation_params(tmp_path):
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)
    result = add_peer(db, 'android', platform='android', export_profile='amneziawg', keygen=KeyGenerator(seed='android'))

    config = result.client_config
    assert f'DNS = {DEFAULT_CLIENT_DNS}' in config
    assert 'MTU = 1280' in config
    assert 'AllowedIPs = 0.0.0.0/0' in config
    assert '::/0' not in config
    for expected in ('Jc = 4', 'Jmin = 50', 'Jmax = 1000', 'S1 = 80', 'S2 = 120', 'H1 = 1', 'H2 = 2', 'H3 = 3', 'H4 = 4'):
        assert expected in config


def test_amneziawg_ios_profile_is_strict_supported_conf(tmp_path):
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)
    result = add_peer(db, 'iphone', platform='ios', export_profile='amneziawg-ios', keygen=KeyGenerator(seed='ios'))

    assert result.peer['export_profile'] == 'amneziawg-ios'
    config = result.client_config
    assert '[Interface]' in config
    assert '[Peer]' in config
    assert f'PrivateKey = {result.private_key}' in config
    assert f'DNS = {DEFAULT_CLIENT_DNS}' in config
    assert 'MTU = 1280' in config
    assert 'AllowedIPs = 0.0.0.0/0' in config
    assert '::/0' not in config
    assert 'Jc = 4' in config
    assert 'Jmin = 50' in config
    assert 'Jmax = 1000' in config
    assert 'S1 = 80' in config
    assert 'S2 = 120' in config
    assert 'H1 = 1' in config
    assert 'H2 = 2' in config
    assert 'H3 = 3' in config
    assert 'H4 = 4' in config
    for unsupported in ('S3 =', 'S4 =', 'I1 =', 'I2 =', 'I3 =', 'I4 =', 'I5 =', 'vpn://'):
        assert unsupported not in config


def test_amneziawg_ios_renderer_does_not_emit_range_headers():
    config = render_amneziawg_ios_config({'address': '10.8.0.20/32'}, 'client-private-key')
    for line in config.splitlines():
        if line.startswith(('H1 =', 'H2 =', 'H3 =', 'H4 =')):
            assert '-' not in line


def test_client_config_embeds_server_public_key_and_endpoint(tmp_path):
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)
    result = add_peer(
        db,
        'phone',
        keygen=KeyGenerator(seed='phone'),
        server_public_key=TEST_SERVER_PUBLIC_KEY,
        server_endpoint=TEST_SERVER_ENDPOINT,
    )

    assert f'PublicKey = {TEST_SERVER_PUBLIC_KEY}' in result.client_config
    assert f'Endpoint = {TEST_SERVER_ENDPOINT}' in result.client_config
    assert '<server-public-key>' not in result.client_config
    assert '<server-endpoint>' not in result.client_config


def test_client_config_reads_server_values_from_runtime_config(tmp_path, monkeypatch):
    runtime_config = tmp_path / 'vpnchain.env'
    runtime_config.write_text(
        f'VPNCHAIN_SERVER_PUBLIC_KEY={TEST_SERVER_PUBLIC_KEY}\n'
        f'VPNCHAIN_SERVER_ENDPOINT={TEST_SERVER_ENDPOINT}\n'
    )
    monkeypatch.delenv('VPNCHAIN_SERVER_PUBLIC_KEY')
    monkeypatch.delenv('VPNCHAIN_SERVER_ENDPOINT')
    monkeypatch.setenv('VPNCHAIN_RUNTIME_CONFIG', str(runtime_config))
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)

    result = add_peer(db, 'runtime-phone', keygen=KeyGenerator(seed='runtime-phone'))

    assert f'PublicKey = {TEST_SERVER_PUBLIC_KEY}' in result.client_config
    assert f'Endpoint = {TEST_SERVER_ENDPOINT}' in result.client_config
    assert '<server-' not in result.client_config


def test_client_config_fails_closed_without_server_runtime(tmp_path, monkeypatch):
    monkeypatch.delenv('VPNCHAIN_SERVER_PUBLIC_KEY')
    monkeypatch.delenv('VPNCHAIN_SERVER_ENDPOINT')
    monkeypatch.setenv('VPNCHAIN_RUNTIME_CONFIG', str(tmp_path / 'missing.env'))
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)

    with pytest.raises(ServerRuntimeConfigError, match='server PublicKey and Endpoint'):
        add_peer(db, 'incomplete', keygen=KeyGenerator(seed='incomplete'))

    assert get_peer(db, 'incomplete') is None


def test_rotate_fails_closed_without_changing_peer_key(tmp_path, monkeypatch):
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)
    original = add_peer(db, 'rotate-safe', keygen=KeyGenerator(seed='original')).peer['public_key']
    monkeypatch.delenv('VPNCHAIN_SERVER_PUBLIC_KEY')
    monkeypatch.delenv('VPNCHAIN_SERVER_ENDPOINT')
    monkeypatch.setenv('VPNCHAIN_RUNTIME_CONFIG', str(tmp_path / 'missing.env'))

    with pytest.raises(ServerRuntimeConfigError, match='server PublicKey and Endpoint'):
        rotate_peer(db, 'rotate-safe', keygen=KeyGenerator(seed='replacement'))

    current = get_peer(db, 'rotate-safe')
    assert current is not None
    assert current['public_key'] == original


def test_rotate_missing_peer_reports_key_error_before_runtime_error(tmp_path, monkeypatch):
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)
    monkeypatch.delenv('VPNCHAIN_SERVER_PUBLIC_KEY')
    monkeypatch.delenv('VPNCHAIN_SERVER_ENDPOINT')
    monkeypatch.setenv('VPNCHAIN_RUNTIME_CONFIG', str(tmp_path / 'missing.env'))

    with pytest.raises(KeyError, match='ghost'):
        rotate_peer(db, 'ghost', keygen=KeyGenerator(seed='unused'))


def test_add_rolls_back_when_client_config_rendering_fails(tmp_path, monkeypatch):
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)

    def fail_render(*args, **kwargs):
        raise RuntimeError('synthetic render failure')

    monkeypatch.setattr(peers_module, 'render_client_config', fail_render)

    with pytest.raises(RuntimeError, match='synthetic render failure'):
        add_peer(db, 'render-failure', keygen=KeyGenerator(seed='render-failure'))

    assert get_peer(db, 'render-failure') is None


def test_rotate_rolls_back_when_client_config_rendering_fails(tmp_path, monkeypatch):
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)
    original = add_peer(db, 'rotate-render-failure', keygen=KeyGenerator(seed='original')).peer['public_key']

    def fail_render(*args, **kwargs):
        raise RuntimeError('synthetic render failure')

    monkeypatch.setattr(peers_module, 'render_client_config', fail_render)

    with pytest.raises(RuntimeError, match='synthetic render failure'):
        rotate_peer(db, 'rotate-render-failure', keygen=KeyGenerator(seed='replacement'))

    assert get_peer(db, 'rotate-render-failure')['public_key'] == original


def test_add_resolves_runtime_file_once_before_database_write(tmp_path, monkeypatch):
    runtime_config = tmp_path / 'vpnchain.env'
    runtime_config.write_text(
        f'VPNCHAIN_SERVER_PUBLIC_KEY={TEST_SERVER_PUBLIC_KEY}\n'
        f'VPNCHAIN_SERVER_ENDPOINT={TEST_SERVER_ENDPOINT}\n'
    )
    monkeypatch.delenv('VPNCHAIN_SERVER_PUBLIC_KEY')
    monkeypatch.delenv('VPNCHAIN_SERVER_ENDPOINT')
    monkeypatch.setenv('VPNCHAIN_RUNTIME_CONFIG', str(runtime_config))
    reads = 0
    original_read = peers_module._read_server_runtime_config

    def count_read():
        nonlocal reads
        reads += 1
        return original_read()

    monkeypatch.setattr(peers_module, '_read_server_runtime_config', count_read)
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)

    add_peer(db, 'single-read', keygen=KeyGenerator(seed='single-read'))

    assert reads == 1


def test_complete_explicit_runtime_does_not_read_runtime_file(tmp_path, monkeypatch):
    monkeypatch.setenv('VPNCHAIN_RUNTIME_CONFIG', str(tmp_path))  # A directory would fail if read.
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)

    result = add_peer(
        db,
        'explicit-only',
        keygen=KeyGenerator(seed='explicit-only'),
        server_public_key=TEST_SERVER_PUBLIC_KEY,
        server_endpoint=TEST_SERVER_ENDPOINT,
    )

    assert f'PublicKey = {TEST_SERVER_PUBLIC_KEY}' in result.client_config


@pytest.mark.parametrize('explicit_field', ['public_key', 'endpoint'])
def test_incomplete_explicit_runtime_fails_closed_without_mixing_sources(tmp_path, monkeypatch, explicit_field):
    runtime_config = tmp_path / 'vpnchain.env'
    runtime_config.write_text(
        f'VPNCHAIN_SERVER_PUBLIC_KEY={TEST_SERVER_PUBLIC_KEY}\n'
        f'VPNCHAIN_SERVER_ENDPOINT={TEST_SERVER_ENDPOINT}\n'
    )
    monkeypatch.setenv('VPNCHAIN_RUNTIME_CONFIG', str(runtime_config))
    kwargs = {
        'server_public_key': TEST_SERVER_PUBLIC_KEY if explicit_field == 'public_key' else None,
        'server_endpoint': TEST_SERVER_ENDPOINT if explicit_field == 'endpoint' else None,
    }
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)

    with pytest.raises(ServerRuntimeConfigError, match='explicit.*both'):
        add_peer(db, f'partial-{explicit_field}', keygen=KeyGenerator(seed=explicit_field), **kwargs)

    assert get_peer(db, f'partial-{explicit_field}') is None


def test_incomplete_runtime_file_fails_closed_without_mixing_environment(tmp_path, monkeypatch):
    runtime_config = tmp_path / 'vpnchain.env'
    runtime_config.write_text(f'VPNCHAIN_SERVER_PUBLIC_KEY={TEST_SERVER_PUBLIC_KEY}\n')
    monkeypatch.setenv('VPNCHAIN_RUNTIME_CONFIG', str(runtime_config))
    monkeypatch.setenv('VPNCHAIN_SERVER_ENDPOINT', TEST_SERVER_ENDPOINT)
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)

    with pytest.raises(ServerRuntimeConfigError, match='runtime config.*both'):
        add_peer(db, 'partial-file', keygen=KeyGenerator(seed='partial-file'))

    assert get_peer(db, 'partial-file') is None


@pytest.mark.parametrize(
    'endpoint',
    ['vpn.example.test:1', '192.0.2.10:65535', '[2001:db8::1]:51820'],
)
def test_server_endpoint_accepts_supported_host_forms(tmp_path, endpoint):
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)

    result = add_peer(
        db,
        f'valid-{endpoint}',
        keygen=KeyGenerator(seed=endpoint),
        server_public_key=TEST_SERVER_PUBLIC_KEY,
        server_endpoint=endpoint,
    )

    assert f'Endpoint = {endpoint}' in result.client_config


@pytest.mark.parametrize(
    'endpoint',
    [
        '999.999.999.999:51820',
        '192.0.2.1:0',
        '192.0.2.1:65536',
        '192.0.2.1:051820',
        '2001:db8::1:51820',
        '[192.0.2.1]:51820',
        '[2001:db8::1]51820',
        'host.example:',
    ],
)
def test_server_endpoint_rejects_malformed_forms(tmp_path, endpoint):
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)

    with pytest.raises(ServerRuntimeConfigError, match='Endpoint'):
        add_peer(
            db,
            f'invalid-{endpoint}',
            keygen=KeyGenerator(seed=endpoint),
            server_public_key=TEST_SERVER_PUBLIC_KEY,
            server_endpoint=endpoint,
        )

    assert get_peer(db, f'invalid-{endpoint}') is None


@pytest.mark.parametrize(
    ('public_key', 'endpoint', 'message'),
    [
        ('not-a-wireguard-key', TEST_SERVER_ENDPOINT, 'PublicKey'),
        (TEST_SERVER_PUBLIC_KEY, '<server-endpoint>', 'Endpoint'),
    ],
)
def test_client_config_rejects_invalid_server_runtime(tmp_path, public_key, endpoint, message):
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)

    with pytest.raises(ServerRuntimeConfigError, match=message):
        add_peer(
            db,
            'invalid-runtime',
            keygen=KeyGenerator(seed='invalid-runtime'),
            server_public_key=public_key,
            server_endpoint=endpoint,
        )

    assert get_peer(db, 'invalid-runtime') is None


def test_default_peer_addresses_match_ru_awg0_subnet(tmp_path):
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)
    first = add_peer(db, 'first', keygen=KeyGenerator(seed='first'))
    second = add_peer(db, 'second', keygen=KeyGenerator(seed='second'))
    assert first.peer['address'] == '10.8.0.3/32'
    assert second.peer['address'] == '10.8.0.4/32'
    assert 'Address = 10.8.0.3/32' in first.client_config


def test_sync_peer_runtime_updates_awg_runtime_and_persistent_config(tmp_path, monkeypatch):
    peer = {'public_key': 'PUBKEY123', 'address': '10.8.0.24/32'}
    config = tmp_path / 'awg0.conf'
    config.write_text('[Interface]\nAddress = 10.8.0.1/24\n\n')
    calls = []

    def fake_run(cmd, capture_output, text, check):
        calls.append(cmd)
        class Result:
            returncode = 0
            stdout = ''
            stderr = ''
        return Result()

    monkeypatch.setenv('VPNCHAIN_RUNTIME_SYNC', '1')
    monkeypatch.setenv('VPNCHAIN_RUNTIME_AWG_CONFIG', str(config))
    monkeypatch.setattr(peers_module.subprocess, 'run', fake_run)

    peers_module.sync_peer_runtime(peer)

    assert calls == [[
        'docker', 'exec', 'awg-ru', 'awg', 'set', 'awg0', 'peer', 'PUBKEY123',
        'allowed-ips', '10.8.0.24/32', 'persistent-keepalive', '25',
    ]]
    text = config.read_text()
    assert 'PublicKey = PUBKEY123' in text
    assert 'AllowedIPs = 10.8.0.24/32' in text


def test_sync_peer_runtime_remove_deletes_peer_block(tmp_path, monkeypatch):
    peer = {'public_key': 'PUBKEY123', 'address': '10.8.0.24/32'}
    config = tmp_path / 'awg0.conf'
    config.write_text(
        '[Interface]\nAddress = 10.8.0.1/24\n\n'
        '[Peer]\nPublicKey = PUBKEY123\nAllowedIPs = 10.8.0.24/32\nPersistentKeepalive = 25\n\n'
    )

    def fake_run(cmd, capture_output, text, check):
        class Result:
            returncode = 0
            stdout = ''
            stderr = ''
        return Result()

    monkeypatch.setenv('VPNCHAIN_RUNTIME_SYNC', '1')
    monkeypatch.setenv('VPNCHAIN_RUNTIME_AWG_CONFIG', str(config))
    monkeypatch.setattr(peers_module.subprocess, 'run', fake_run)

    peers_module.sync_peer_runtime(peer, remove=True)

    text = config.read_text()
    assert 'PublicKey = PUBKEY123' not in text
    assert 'AllowedIPs = 10.8.0.24/32' not in text
