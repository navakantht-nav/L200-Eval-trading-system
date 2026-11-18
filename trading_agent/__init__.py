"""AlphaPulse ADKv2 package entrypoint (`adk web`, `adk run`, `adk eval`, Agent Runtime)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent import app, harness_plugin, root_agent

__all__ = ["app", "harness_plugin", "root_agent"]


def __getattr__(name: str) -> object:
    if name in {"app", "harness_plugin", "root_agent"}:
        from . import agent as _agent_mod

        return getattr(_agent_mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
