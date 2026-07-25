"""The SQL execution-accuracy oracle and the read-only execution guard."""

from __future__ import annotations

import sqlite3

import pytest

from tracegym.checks import run_checks
from tracegym.checks.sql_guard import run_select


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sales(region TEXT, amt INTEGER)")
    conn.executemany("INSERT INTO sales VALUES (?, ?)", [("west", 10), ("east", 20), ("west", 5)])
    conn.commit()
    conn.close()


GOLD = "SELECT region, SUM(amt) FROM sales GROUP BY region"


def test_exec_accuracy_matches_gold_regardless_of_row_order(tmp_path):
    db = str(tmp_path / "d.sqlite")
    _make_db(db)
    trace = {"output": {"sql": GOLD + " ORDER BY region DESC"}}
    r = run_checks(trace, {"checks": [{"type": "sql_exec_accuracy", "db": db, "gold_sql": GOLD}]})[
        0
    ]
    assert r.passed


def test_exec_accuracy_fails_on_wrong_answer(tmp_path):
    db = str(tmp_path / "d.sqlite")
    _make_db(db)
    trace = {
        "output": {"sql": "SELECT region, SUM(amt) FROM sales WHERE region='west' GROUP BY region"}
    }
    r = run_checks(trace, {"checks": [{"type": "sql_exec_accuracy", "db": db, "gold_sql": GOLD}]})[
        0
    ]
    assert not r.passed


def test_guard_refuses_write_before_execution(tmp_path):
    db = str(tmp_path / "d.sqlite")
    _make_db(db)
    with pytest.raises(ValueError):
        run_select(db, "DELETE FROM sales")


def test_guard_reads_rows(tmp_path):
    db = str(tmp_path / "d.sqlite")
    _make_db(db)
    rows = run_select(db, "SELECT COUNT(*) FROM sales")
    assert rows == [(3,)]
