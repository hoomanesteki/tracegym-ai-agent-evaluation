"""Execution-layer SQL guard: run a validated SELECT with no way to write.

Static analysis (sql_select_only) can be fooled by a clever payload, so execution
adds three independent defenses that do not trust the parser:
  1. open the database read-only and immutable via a file: URI,
  2. set PRAGMA query_only = ON,
  3. install an authorizer that permits only SELECT/READ/FUNCTION opcodes.
A write attempt fails even if it somehow slipped past the AST check.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tracegym.checks.sql_select_only import select_only

_ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}


def _authorizer(action, arg1, arg2, db_name, trigger):
    if action in _ALLOWED or action == sqlite3.SQLITE_RECURSIVE:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def open_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Open the database strictly read-only, with query_only and an authorizer."""
    uri = f"file:{Path(db_path)}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    conn.set_authorizer(_authorizer)
    return conn


def run_select(db_path: str | Path, sql: str) -> list[tuple]:
    """Validate then execute a SELECT, returning rows as tuples. Raises on refusal."""
    ok, detail = select_only(sql)
    if not ok:
        raise ValueError(f"refused unsafe SQL: {detail}")
    conn = open_readonly(db_path)
    try:
        return [tuple(row) for row in conn.execute(sql).fetchall()]
    finally:
        conn.close()
