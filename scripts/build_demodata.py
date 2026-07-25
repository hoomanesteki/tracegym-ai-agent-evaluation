#!/usr/bin/env python
"""Materialize the demo workspace and print its results bundle (for development).

The bundled demo builds itself at runtime, so this is only a convenience for
iterating on suites or the report:
    uv run python scripts/build_demodata.py
"""

from __future__ import annotations

import json
from pathlib import Path

from tracegym.demos.run import run_demo

DEST = Path(__file__).resolve().parent.parent / ".cache" / "demo-workspace"


def main() -> int:
    DEST.parent.mkdir(exist_ok=True)
    bundle = run_demo(DEST)
    print(f"built demo workspace at {DEST}")
    print(
        json.dumps(
            {
                "total_cases": bundle["manifest"]["total_cases"],
                "recall": f"{bundle['recall']['caught']}/{bundle['recall']['total']}",
                "gate_demo": bundle["gate_demo"]["verdict"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
