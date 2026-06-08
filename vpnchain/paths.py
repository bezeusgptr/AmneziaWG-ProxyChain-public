from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

DEFAULT_DB = Path(os.environ.get('VPNCHAIN_DB', Path.home() / '.local/share/vpnchain/vpnchain.sqlite'))
DEFAULT_TTL_MINUTES = 15


class UnsafeOutputPath(ValueError):
    pass


def git_root(start: Path | None = None) -> Path | None:
    try:
        out = subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], cwd=start or Path.cwd(), text=True, stderr=subprocess.DEVNULL).strip()
        return Path(out).resolve()
    except Exception:
        return None


def ensure_output_path_safe(path: str | Path, root: Path | None = None) -> Path:
    p = Path(path).expanduser().resolve()
    root = root.resolve() if root else git_root()
    if root is not None:
        try:
            p.relative_to(root)
            raise UnsafeOutputPath(f'refusing to write client config inside git worktree: {p}')
        except ValueError as exc:
            if isinstance(exc, UnsafeOutputPath):
                raise
    return p


def write_private_file(path: str | Path, content: str) -> Path:
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(content)
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    return p
