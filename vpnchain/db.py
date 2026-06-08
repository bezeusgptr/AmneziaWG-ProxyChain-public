from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS nodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL CHECK (role IN ('ru','am')),
  tunnel_address TEXT,
  listen_port INTEGER,
  public_endpoint_ref TEXT,
  public_key TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS peers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  public_key TEXT NOT NULL UNIQUE,
  address TEXT NOT NULL UNIQUE,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
  client_type TEXT NOT NULL DEFAULT 'client',
  platform TEXT NOT NULL DEFAULT 'generic',
  export_profile TEXT NOT NULL DEFAULT 'amneziawg',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  disabled_at TEXT,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS config_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action TEXT NOT NULL,
  rendered_hash TEXT,
  backup_path TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  applied_at TEXT,
  status TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS cleanup_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT NOT NULL,
  delete_after TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  deleted_at TEXT
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if path != Path(':memory:'):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db(db_path: str | Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.execute('INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)', (SCHEMA_VERSION,))
