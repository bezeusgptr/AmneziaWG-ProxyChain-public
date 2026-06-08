import subprocess

import pytest

from vpnchain.keys import KeyGenerationError, KeyGenerator


def test_key_generator_extracts_key_from_noisy_tool_output(monkeypatch):
    calls = []

    def fake_which(name):
        return f'/usr/bin/{name}' if name == 'awg' else None

    def fake_run(cmd, input=None, capture_output=True, text=True, check=False):
        calls.append((cmd, input))
        if cmd == ['awg', 'genkey']:
            return subprocess.CompletedProcess(cmd, 0, stdout='warning line\n' + 'a' * 43 + '=\n', stderr='')
        if cmd == ['awg', 'pubkey']:
            assert input == 'a' * 43 + '=\n'
            return subprocess.CompletedProcess(cmd, 0, stdout='b' * 43 + '=\n', stderr='')
        raise AssertionError(cmd)

    monkeypatch.setattr('vpnchain.keys.shutil.which', fake_which)
    monkeypatch.setattr('vpnchain.keys.subprocess.run', fake_run)

    pair = KeyGenerator().generate()

    assert pair.private_key == 'a' * 43 + '='
    assert pair.public_key == 'b' * 43 + '='
    assert calls[0][0] == ['awg', 'genkey']


def test_key_generator_falls_back_to_wg_when_awg_rejects_key(monkeypatch):
    def fake_which(name):
        return f'/usr/bin/{name}' if name in {'awg', 'wg'} else None

    def fake_run(cmd, input=None, capture_output=True, text=True, check=False):
        if cmd == ['awg', 'genkey']:
            return subprocess.CompletedProcess(cmd, 0, stdout='not-a-key\n', stderr='')
        if cmd == ['wg', 'genkey']:
            return subprocess.CompletedProcess(cmd, 0, stdout='c' * 43 + '=\n', stderr='')
        if cmd == ['wg', 'pubkey']:
            return subprocess.CompletedProcess(cmd, 0, stdout='d' * 43 + '=\n', stderr='')
        raise AssertionError(cmd)

    monkeypatch.setattr('vpnchain.keys.shutil.which', fake_which)
    monkeypatch.setattr('vpnchain.keys.subprocess.run', fake_run)

    pair = KeyGenerator().generate()

    assert pair.private_key == 'c' * 43 + '='
    assert pair.public_key == 'd' * 43 + '='


def test_key_generator_uses_docker_fallback_when_host_tools_are_missing(monkeypatch):
    def fake_which(name):
        return '/usr/bin/docker' if name == 'docker' else None

    def fake_run(cmd, input=None, capture_output=True, text=True, check=False):
        if cmd == ['docker', 'exec', '-i', 'awg-ru', 'awg', 'genkey']:
            return subprocess.CompletedProcess(cmd, 0, stdout='e' * 43 + '=\n', stderr='')
        if cmd == ['docker', 'exec', '-i', 'awg-ru', 'awg', 'pubkey']:
            return subprocess.CompletedProcess(cmd, 0, stdout='f' * 43 + '=\n', stderr='')
        raise AssertionError(cmd)

    monkeypatch.setattr('vpnchain.keys.shutil.which', fake_which)
    monkeypatch.setattr('vpnchain.keys.subprocess.run', fake_run)

    pair = KeyGenerator().generate()

    assert pair.private_key == 'e' * 43 + '='
    assert pair.public_key == 'f' * 43 + '='


def test_key_generator_raises_clear_error_without_key_tool(monkeypatch):
    monkeypatch.delenv('VPNCHAIN_TEST_KEY_SEED', raising=False)
    monkeypatch.setattr('vpnchain.keys.shutil.which', lambda name: None)

    with pytest.raises(KeyGenerationError, match='docker fallback'):
        KeyGenerator().generate()
