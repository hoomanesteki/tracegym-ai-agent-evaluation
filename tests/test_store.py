"""Blob roundtrips, content addressing, and schema application."""

from __future__ import annotations

from tracegym.store import connect, get, put
from tracegym.store.db import table_names

EXPECTED_TABLES = {
    "sessions",
    "traces",
    "spans",
    "fixtures",
    "suites",
    "cases",
    "runs",
    "results",
    "judgments",
    "labels",
    "baselines",
}


def test_blob_put_get_roundtrip(tmp_path):
    obj = {"messages": [{"role": "user", "content": "hi"}], "n": 3}
    sha = put(tmp_path, obj)
    assert get(tmp_path, sha) == obj


def test_same_object_same_sha(tmp_path):
    a = {"x": 1, "y": 2}
    b = {"y": 2, "x": 1}  # different key order, same content
    assert put(tmp_path, a) == put(tmp_path, b)


def test_connect_applies_all_eleven_tables():
    conn = connect()
    assert table_names(conn) == EXPECTED_TABLES
    assert len(EXPECTED_TABLES) == 11


def test_connect_is_idempotent_on_same_file(tmp_path):
    db = str(tmp_path / "t.db")
    c1 = connect(db)
    c1.execute("INSERT INTO suites (id, name, created_at) VALUES ('s', 'S', '2026-01-01')")
    c1.commit()
    c1.close()
    # Re-applying the schema must not wipe or error on existing data.
    c2 = connect(db)
    assert c2.execute("SELECT COUNT(*) FROM suites").fetchone()[0] == 1
