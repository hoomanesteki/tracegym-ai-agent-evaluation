"""Canonical serialization, hashing, and scrubbing.

These three functions are the determinism contract. Two runs that produce the
same logical object must produce the same bytes, the same hash, and (after
scrubbing volatile fields) the same fixture key. Everything downstream, from
content-addressed blobs to the replay engine, leans on that invariant, so this
module has no third-party imports and no hidden state.
"""

from __future__ import annotations

import hashlib
import json
import re

# (pattern, replacement) applied in order. Volatile values that legitimately
# change between otherwise-identical runs get normalized so their hashes match.
# Order matters: longer/more specific patterns first.
DEFAULT_SCRUBBERS: list[tuple[str, str]] = [
    # ISO-8601 timestamps, with optional fractional seconds and timezone.
    (
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?",
        "<TS>",
    ),
    # UUIDs (any version).
    (
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        "<UUID>",
    ),
]


def scrub(text: str, scrubbers: list[tuple[str, str]] | None = None) -> str:
    """Replace volatile substrings (timestamps, UUIDs) with stable placeholders.

    Idempotent: the placeholders (``<TS>``, ``<UUID>``) contain nothing the
    patterns match, so scrubbing already-scrubbed text is a no-op.
    """
    rules = DEFAULT_SCRUBBERS if scrubbers is None else scrubbers
    for pattern, replacement in rules:
        text = re.sub(pattern, replacement, text)
    return text


def canonical_json(obj: object) -> bytes:
    """Serialize ``obj`` to bytes that depend only on its logical content.

    Keys are sorted, whitespace is stripped, and non-ASCII is preserved as UTF-8.
    ``sort_keys=True`` recurses, so nested dict ordering is normalized too; do not
    reach for a custom encoder.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Hex SHA-256 of raw bytes. The content-address for blobs and fixtures."""
    return hashlib.sha256(data).hexdigest()
