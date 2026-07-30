import sqlite3

from vpnchain.db import init_db, connect


def test_init_schema_has_required_peer_fields(tmp_path):
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)
    with connect(db) as conn:
        cols = {row['name'] for row in conn.execute('PRAGMA table_info(peers)')}
        assert {'client_type', 'platform', 'export_profile'}.issubset(cols)
        for table in ['nodes', 'peers', 'config_versions', 'cleanup_jobs']:
            assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()


def test_duplicate_peer_constraints(tmp_path):
    from vpnchain.peers import add_peer
    from vpnchain.keys import KeyGenerator
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)
    server = {
        'server_public_key': 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=',
        'server_endpoint': 'ru.example.test:51820',
    }
    add_peer(db, 'alice', keygen=KeyGenerator(seed='a'), **server)
    try:
        add_peer(db, 'alice', keygen=KeyGenerator(seed='b'), **server)
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError('duplicate name accepted')
