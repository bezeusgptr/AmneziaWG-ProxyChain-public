from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SECRET_PATTERNS = [
    re.compile(r'^\s*PrivateKey\s*=\s*(?!<|REDACTED|example|xxx)[A-Z0-9+/=]{20,}\s*$', re.I | re.M),
    re.compile(r'^\s*PresharedKey\s*=\s*(?!<|REDACTED|example|xxx)[A-Z0-9+/=]{20,}\s*$', re.I | re.M),
]
DANGEROUS_NAMES = {'.env'}
DANGEROUS_SUFFIXES = {'.sqlite', '.sqlite3', '.db'}
DANGEROUS_PARTS = {'backups', 'generated', 'tmp'}
PRIVATE_KEY_PATTERNS = (
    re.compile(r'private[_-]?key', re.I),
    re.compile(r'client.*\.conf$', re.I),
)
SKIP_DIRS = {'.git', '__pycache__', '.pytest_cache'}

@dataclass(frozen=True)
class Finding:
    path: str
    reason: str


def scan_repo(root: str | Path = '.') -> list[Finding]:
    root = Path(root)
    findings: list[Finding] = []
    for path in root.rglob('*'):
        relative_path = path.relative_to(root)
        if _should_skip(path, relative_path):
            continue
        findings.extend(_path_findings(path, relative_path))
        secret_finding = _secret_finding(path, relative_path)
        if secret_finding:
            findings.append(secret_finding)
    return findings


def _should_skip(path: Path, relative_path: Path) -> bool:
    return path.is_dir() or any(part in SKIP_DIRS for part in relative_path.parts)


def _path_findings(path: Path, relative_path: Path) -> list[Finding]:
    relative = str(relative_path)
    findings = []
    if path.name in DANGEROUS_NAMES or path.name.startswith('.env.'):
        findings.append(Finding(relative, 'environment file'))
    if path.suffix.lower() in DANGEROUS_SUFFIXES:
        findings.append(Finding(relative, 'SQLite/database file'))
    if any(part in DANGEROUS_PARTS for part in relative_path.parts):
        findings.append(Finding(relative, 'runtime/generated path'))
    if any(pattern.search(relative) for pattern in PRIVATE_KEY_PATTERNS) and not relative.endswith('.template'):
        findings.append(Finding(relative, 'private key or generated client config filename'))
    return findings


def _secret_finding(path: Path, relative_path: Path) -> Finding | None:
    try:
        text = path.read_text(errors='ignore')
    except OSError:
        return None
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        return Finding(str(relative_path), 'WireGuard/AmneziaWG secret value')
    return None
