from vpnchain.repo_check import scan_repo


def reasons(findings):
    return {f.reason for f in findings}


def test_repo_check_detects_secret_and_runtime_files(tmp_path):
    (tmp_path / 'leak.conf').write_text('PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n')
    (tmp_path / '.env').write_text('TOKEN=x')
    (tmp_path / 'state.sqlite').write_text('not really sqlite')
    found = scan_repo(tmp_path)
    rs = reasons(found)
    assert 'WireGuard/AmneziaWG secret value' in rs
    assert 'environment file' in rs
    assert 'SQLite/database file' in rs


def test_repo_check_allows_templates_and_redacted_values(tmp_path):
    (tmp_path / 'awg0.conf.template').write_text('PrivateKey = <server-private-key>\n')
    assert scan_repo(tmp_path) == []


def test_repo_check_flags_private_key_marker_inside_filename(tmp_path):
    (tmp_path / 'archived_private_key.txt').write_text('redacted')

    assert 'private key or generated client config filename' in reasons(scan_repo(tmp_path))


def test_repo_check_flags_generated_client_config_filename(tmp_path):
    (tmp_path / 'client-alice.conf').write_text('redacted')

    assert 'private key or generated client config filename' in reasons(scan_repo(tmp_path))


def test_repo_check_ignores_unrelated_client_filename(tmp_path):
    (tmp_path / 'client-notes.txt').write_text('redacted')

    assert scan_repo(tmp_path) == []
