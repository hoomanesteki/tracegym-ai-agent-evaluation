"""Content-addressed blob store.

Every payload is serialized to canonical JSON, hashed, and written once under a
sharded path keyed by its SHA-256. Because the name is the content, identical
payloads collapse to one file and re-writing is a no-op. This is what makes tool
and LLM outputs replayable: the fixture points at a blob, the blob never changes.
"""

from __future__ import annotations

import json
from pathlib import Path

import zstandard as zstd

from tracegym.util.canon import canonical_json, sha256_hex

_compressor = zstd.ZstdCompressor(level=10)
_decompressor = zstd.ZstdDecompressor()


def _path(root: Path, sha: str) -> Path:
    """Shard by the first two byte-pairs so no directory holds millions of files."""
    return Path(root) / sha[:2] / sha[2:4] / f"{sha}.zst"


def put(root: Path, obj: object) -> str:
    """Store ``obj`` and return its SHA-256. Skips the write if it already exists."""
    data = canonical_json(obj)
    sha = sha256_hex(data)
    p = _path(root, sha)
    if p.exists():
        return sha
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(_compressor.compress(data))
    return sha


def get(root: Path, sha: str) -> object:
    """Load and decode the object stored under ``sha``."""
    raw = _decompressor.decompress(_path(root, sha).read_bytes())
    return json.loads(raw)
