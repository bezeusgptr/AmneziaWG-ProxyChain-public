from vpnchain.db import connect, init_db
from vpnchain.keys import KeyGenerator
from vpnchain.peers import (
    DEFAULT_CLIENT_DNS,
    add_peer,
    get_peer,
    list_peers,
    render_amneziawg_ios_config,
    rotate_peer,
)


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
        server_public_key='ru-server-public-key',
        server_endpoint='ru.example.test:51820',
    )

    assert 'PublicKey = ru-server-public-key' in result.client_config
    assert 'Endpoint = ru.example.test:51820' in result.client_config
    assert '<server-public-key>' not in result.client_config
    assert '<server-endpoint>' not in result.client_config


def test_default_peer_addresses_match_ru_awg0_subnet(tmp_path):
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)
    first = add_peer(db, 'first', keygen=KeyGenerator(seed='first'))
    second = add_peer(db, 'second', keygen=KeyGenerator(seed='second'))
    assert first.peer['address'] == '10.8.0.3/32'
    assert second.peer['address'] == '10.8.0.4/32'
    assert 'Address = 10.8.0.3/32' in first.client_config
