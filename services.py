"""ADKv2 Service Registry hooks wrapping Session & Memory services in PII scrubbing."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from google.adk.cli.service_registry import get_service_registry
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.sessions.sqlite_session_service import SqliteSessionService

from trading_agent.memory import (
    wrap_memory_service_with_pii_scrubber,
    wrap_session_service_with_pii_scrubber,
)


def _register_pii_scrubbing_services() -> None:
    registry = get_service_registry()

    def _scrubbed_memory_session_factory(uri: str, **_: Any) -> Any:
        return wrap_session_service_with_pii_scrubber(InMemorySessionService())

    def _scrubbed_sqlite_session_factory(uri: str, **kwargs: Any) -> Any:
        parsed = urlparse(uri)
        db_path = parsed.path.lstrip("/") if parsed.path else ""
        if not db_path:
            return wrap_session_service_with_pii_scrubber(InMemorySessionService())
        return wrap_session_service_with_pii_scrubber(SqliteSessionService(db_path=db_path))

    def _scrubbed_memory_bank_factory(uri: str, **_: Any) -> Any:
        return wrap_memory_service_with_pii_scrubber(InMemoryMemoryService())

    registry.register_session_service("memory", _scrubbed_memory_session_factory)
    registry.register_session_service("sqlite", _scrubbed_sqlite_session_factory)
    registry.register_memory_service("memory", _scrubbed_memory_bank_factory)


_register_pii_scrubbing_services()
