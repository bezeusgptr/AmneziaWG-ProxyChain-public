import os
import stat

import pytest

from vpnchain.paths import UnsafeOutputPath, ensure_output_path_safe, write_private_file
from vpnchain.db import connect, init_db
from vpnchain.peers import schedule_cleanup


def test_refuses_output_inside_git_root(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    target = root / 'client.conf'
    with pytest.raises(UnsafeOutputPath):
        ensure_output_path_safe(target, root=root)


def test_write_private_file_mode_0600_and_cleanup_ttl(tmp_path):
    target = tmp_path / 'outside.conf'
    write_private_file(target, 'secret')
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    db = tmp_path / 'vpnchain.sqlite'
    init_db(db)
    with connect(db) as conn:
        schedule_cleanup(conn, str(target))
        row = conn.execute('SELECT path, status, delete_after FROM cleanup_jobs').fetchone()
    assert row['path'] == str(target)
    assert row['status'] == 'pending'
    assert row['delete_after']
