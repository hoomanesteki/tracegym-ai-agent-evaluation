"""Determinism contract for canonical_json / scrub / sha256_hex."""

from __future__ import annotations

from tracegym.util.canon import canonical_json, scrub, sha256_hex


def test_canonical_json_is_key_order_invariant():
    a = {"b": 1, "a": {"y": 2, "x": 3}}
    b = {"a": {"x": 3, "y": 2}, "b": 1}
    assert canonical_json(a) == canonical_json(b)


def test_scrub_replaces_iso8601_timestamp():
    assert scrub("logged at 2026-07-25T10:00:00Z done") == "logged at <TS> done"


def test_scrub_replaces_uuid():
    text = "trace 123e4567-e89b-12d3-a456-426614174000 end"
    assert scrub(text) == "trace <UUID> end"


def test_scrub_is_idempotent():
    text = "at 2026-07-25T10:00:00.500Z id 123e4567-e89b-12d3-a456-426614174000"
    once = scrub(text)
    assert scrub(once) == once


def test_sha256_of_empty_bytes_is_known_constant():
    assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
