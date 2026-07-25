"""TraceGym: record, replay, and regression-test AI agents.

Public surface is intentionally small. Import the pieces you need from their
submodules; this top level only exposes the version and the tool decorator entry
point so `import tracegym as tg; @tg.tool` reads naturally.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
