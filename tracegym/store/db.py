"""SQLite connection helper.

connect() opens a database, sets a Row factory so callers index columns by name,
and applies schema.sql. Every statement in the schema is idempotent, so applying
it on each connect is safe and keeps old databases migrated forward.
"""

from __future__ import annotations

import sqlite3
from importlib.resources import files

_SCHEMA = files("tracegym.store").joinpath("schema.sql").read_text(encoding="utf-8")


def connect(path: str = ":memory:") -> sqlite3.Connection:
    """Open ``path`` (or an in-memory DB), apply the schema, and return the connection."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def table_names(conn: sqlite3.Connection) -> set[str]:
    """Return the set of user table names currently in the database."""
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r["name"] for r in rows}
