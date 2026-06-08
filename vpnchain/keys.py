from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

_KEY_RE = re.compile(r'^[A-Za-z0-9+/]{43}=$')


@dataclass(frozen=True)
class KeyPair:
    private_key: str
    public_key: str


class KeyGenerationError(RuntimeError):
    pass


class KeyGenerator:
    """Обёртка над генерацией ключей AmneziaWG/WireGuard.

    В production используется системная утилита `awg`, затем fallback на `wg`.
    Детерминированный mock включается только явным seed'ом и нужен для тестов.
    """

    def __init__(self, seed: str | None = None):
        self.seed = seed if seed is not None else os.environ.get('VPNCHAIN_TEST_KEY_SEED')
        self._counter = 0

    def generate(self) -> KeyPair:
        if self.seed:
            return self._mock_pair()
        errors: list[str] = []
        for tool in ('awg', 'wg'):
            if not shutil.which(tool):
                continue
            try:
                return self._tool_pair([tool])
            except KeyGenerationError as exc:
                errors.append(f'{tool}: {exc}')
        container = os.environ.get('VPNCHAIN_KEYGEN_CONTAINER', 'awg-ru')
        if shutil.which('docker') and container:
            try:
                return self._tool_pair(['docker', 'exec', '-i', container, 'awg'])
            except KeyGenerationError as exc:
                errors.append(f'docker:{container}: {exc}')
        detail = '; '.join(errors) if errors else 'awg/wg not found on host and docker fallback is unavailable'
        raise KeyGenerationError(f'failed to generate VPN keypair: {detail}')

    def _tool_pair(self, tool_cmd: list[str]) -> KeyPair:
        private_output = _run_key_tool([*tool_cmd, 'genkey'])
        private = _extract_key(private_output)
        if not private:
            raise KeyGenerationError('genkey did not return a valid 44-character base64 key')
        public_output = _run_key_tool([*tool_cmd, 'pubkey'], input_text=private + '\n')
        public = _extract_key(public_output)
        if not public:
            raise KeyGenerationError('pubkey did not return a valid 44-character base64 key')
        return KeyPair(private, public)

    def _mock_pair(self) -> KeyPair:
        self._counter += 1
        material = self.seed + f':{self._counter}'
        priv = _b64(hashlib.sha256(('priv:' + material).encode()).digest())
        pub = _b64(hmac.new(priv.encode(), b'public', hashlib.sha256).digest())
        return KeyPair(priv, pub)


def _run_key_tool(cmd: list[str], *, input_text: str | None = None) -> str:
    try:
        result = subprocess.run(cmd, input=input_text, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise KeyGenerationError(str(exc)) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        raise KeyGenerationError(detail or f'{cmd[0]} exited with code {result.returncode}')
    return '\n'.join(part for part in (result.stdout, result.stderr) if part)


def _extract_key(text: str) -> str | None:
    for token in re.split(r'\s+', text.strip()):
        if _KEY_RE.fullmatch(token):
            return token
    return None


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode().rstrip('=') + '='
