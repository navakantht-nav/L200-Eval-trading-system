"""Root-level Agent Engine / Agent Runtime shim (`adk deploy agent_engine`).

Programmatically adds both `src/` (if present) and the repository root to
`sys.path` at runtime before importing `root_agent` and `app` from
`trading_agent`, preventing `ModuleNotFoundError` in managed GCP runtimes.
"""

from __future__ import annotations

from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parent
_SRC_DIR = _PROJECT_ROOT / "src"

for _candidate in (_SRC_DIR, _PROJECT_ROOT):
    if _candidate.exists():
        _path_str = str(_candidate)
        if _path_str not in sys.path:
            sys.path.insert(0, _path_str)

from trading_agent.agent import app, root_agent  # noqa: E402

__all__ = ["app", "root_agent"]
