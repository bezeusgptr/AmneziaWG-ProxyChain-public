import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'vpnchain-bootstrap.sh'


def run_script(*args):
    return subprocess.run([str(SCRIPT), *args], cwd=ROOT, text=True, capture_output=True, timeout=20)


def test_bootstrap_help():
    res = run_script('--help')
    assert res.returncode == 0
    assert 'dry-run' in res.stdout
    assert '--role ru|am' in res.stdout
    assert '--init-keys-only' not in res.stdout
    assert '--ru-public-key' not in res.stdout


def test_bootstrap_dry_run_am_plain_server():
    res = run_script('--role', 'am')
    assert res.returncode == 0
    assert 'Режим: dry-run' in res.stdout
    assert 'Будет выполнено:' in res.stdout
    assert '--profile am' in res.stdout


def test_bootstrap_rejects_removed_init_keys_only():
    res = run_script('--role', 'ru', '--init-keys-only')
    assert res.returncode != 0
    assert 'removed in v2 uplink flow' in res.stderr


def test_bootstrap_rejects_removed_ru_public_key():
    res = run_script('--role', 'am', '--ru-public-key', '<RU_NODE_PUBLIC_KEY>')
    assert res.returncode != 0
    assert 'removed in v2 uplink flow' in res.stderr


def test_bootstrap_dry_run_am_generate_ru_uplink(tmp_path):
    out = tmp_path / 'ru-awg1.conf'
    res = run_script('--role', 'am', '--generate-ru-uplink', '--am-endpoint', 'example.test:51821', '--output', str(out))
    assert res.returncode == 0
    assert 'generate-ru-uplink' in res.stdout
    assert 'Будет создан RU uplink config' in res.stdout


def test_bootstrap_dry_run_ru_uplink_conf(tmp_path):
    conf = tmp_path / 'ru-awg1.conf'
    conf.write_text('[Interface]\nPrivateKey = test\n')
    res = run_script('--role', 'ru', '--ru-uplink-conf', str(conf), '--server-endpoint', 'ru.example.test:51820')
    assert res.returncode == 0
    assert 'готовый uplink config' in res.stdout
    assert '--profile ru' in res.stdout


def test_bootstrap_requires_ru_uplink_conf():
    res = run_script('--role', 'ru')
    assert res.returncode != 0
    assert 'requires --ru-uplink-conf' in res.stderr


def test_bootstrap_requires_ru_server_endpoint(tmp_path):
    conf = tmp_path / 'ru-awg1.conf'
    conf.write_text('[Interface]\nPrivateKey = test\n')
    res = run_script('--role', 'ru', '--ru-uplink-conf', str(conf))
    assert res.returncode != 0
    assert 'requires --server-endpoint' in res.stderr
