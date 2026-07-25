import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'vpnchain-bootstrap.sh'
WEBUI_START_SCRIPT = ROOT / 'scripts' / 'vpnchain-webui-start.sh'
WEBUI_SERVICE = ROOT / 'systemd' / 'vpnchain-webui.service'


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

def test_bootstrap_uses_named_server_public_key_variable():
    script = SCRIPT.read_text()
    assert 'SERVER_PUBLIC_KEY="$(docker exec awg-am cat /etc/amnezia/amneziawg/server_public_key)"' in script
    assert 'PublicKey = $SERVER_PUBLIC_KEY' in script
    assert 'am_public_key' not in script


def test_bootstrap_ru_peer_persistence_is_non_fatal():
    script = SCRIPT.read_text()
    assert 'docker exec awg-am awg set awg0 peer "$ru_public_key" allowed-ips 10.9.0.2/32 \\' in script
    assert 'Не удалось добавить RU peer в runtime awg0' in script
    assert 'Не удалось дописать RU peer в awg0.conf внутри awg-am' in script

def test_bootstrap_has_guards_for_role_specific_apply_paths():
    script = SCRIPT.read_text()
    assert '[ "$GENERATE_RU_UPLINK" -eq 1 ] || return 0' in script
    assert '[ -n "$RU_UPLINK_CONF" ] || return 0' in script


def test_bootstrap_reads_existing_env_only_when_readable():
    script = SCRIPT.read_text()
    assert 'existing_ru_pub_key=""' in script
    assert 'if [ -r "$ENV_OUT" ]; then' in script
    assert 'existing_ru_pub_key="$(sed -n' in script


def test_webui_service_binds_to_loopback_only():
    script = WEBUI_START_SCRIPT.read_text()

    assert '--host 127.0.0.1' in script
    assert '--host 0.0.0.0' not in script


def test_webui_service_execstart_target_is_executable():
    exec_start = next(
        line.removeprefix('ExecStart=')
        for line in WEBUI_SERVICE.read_text().splitlines()
        if line.startswith('ExecStart=')
    )
    exec_start_target = Path(exec_start.replace('@@REPO@@', str(ROOT)))

    assert exec_start_target.is_file()
    assert os.access(exec_start_target, os.X_OK)


def test_bootstrap_webui_uses_ssh_tunnel_without_opening_firewall():
    script = SCRIPT.read_text()

    assert 'iptables -I INPUT' not in script
    assert 'ssh -L ${_webui_port}:127.0.0.1:${_webui_port}' in script
