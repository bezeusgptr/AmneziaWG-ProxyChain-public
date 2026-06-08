from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SECRET_PATTERNS = [
    re.compile(r'^\s*PrivateKey\s*=\s*(?!<|REDACTED|example|xxx)[A-Za-z0-9+/=]{20,}\s*$', re.I | re.M),
    re.compile(r'^\s*PresharedKey\s*=\s*(?!<|REDACTED|example|xxx)[A-Za-z0-9+/=]{20,}\s*$', re.I | re.M),
]
DANGEROUS_NAMES = {'.env'}
DANGEROUS_SUFFIXES = {'.sqlite', '.sqlite3', '.db'}
DANGEROUS_PARTS = {'backups', 'generated', 'tmp'}
PRIVATE_KEY_RE = re.compile(r'(private[_-]?key|client.*\.conf$)', re.I)
SKIP_DIRS = {'.git', '__pycache__', '.pytest_cache'}

@dataclass(frozen=True)
class Finding:
    path: str
    reason: str


def scan_repo(root: str | Path = '.') -> list[Finding]:
    root = Path(root)
    findings: list[Finding] = []
    for p in root.rglob('*'):
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            if p.is_dir():
                continue
            continue
        if p.is_dir():
            continue
        name = p.name
        if name in DANGEROUS_NAMES or name.startswith('.env.'):
            findings.append(Finding(str(rel), 'environment file'))
        if p.suffix.lower() in DANGEROUS_SUFFIXES:
            findings.append(Finding(str(rel), 'SQLite/database file'))
        if any(part in DANGEROUS_PARTS for part in rel.parts):
            findings.append(Finding(str(rel), 'runtime/generated path'))
        if PRIVATE_KEY_RE.search(str(rel)) and not str(rel).endswith('.template'):
            findings.append(Finding(str(rel), 'private key or generated client config filename'))
        try:
            text = p.read_text(errors='ignore')
        except Exception:
            continue
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                findings.append(Finding(str(rel), 'WireGuard/AmneziaWG secret value'))
                break
    return findings
