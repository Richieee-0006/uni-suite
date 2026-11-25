"""Pluggable LanguageDriver registry and factory (zero subject-slug branching)."""

from __future__ import annotations

from typing import Any

from .base import DriverNotFoundError, LanguageDriver
from .drivers import CSharpDotnetDriver, RDockerDriver

TIER1_BROWSER_DRIVERS: tuple[dict[str, Any], ...] = (
    {
        "id": "webr",
        "language": "r",
        "name": "R (WebR WASM Worker)",
        "tier": "tier1_wasm",
        "worker": "/static/js/sandbox/webr_worker.js",
        "supports_plots": True,
        "supports_table_diff": True,
    },
    {
        "id": "pyodide",
        "language": "python",
        "name": "Python (Pyodide WASM Worker)",
        "tier": "tier1_wasm",
        "worker": "/static/js/sandbox/pyodide_worker.js",
        "supports_plots": False,
        "supports_table_diff": True,
    },
    {
        "id": "pglite",
        "language": "sql_pg",
        "name": "PostgreSQL (PGlite WASM Worker)",
        "tier": "tier1_wasm",
        "worker": "/static/js/sandbox/pglite_worker.js",
        "supports_plots": False,
        "supports_table_diff": True,
        "supports_schema_introspection": True,
    },
    {
        "id": "nosql_mem",
        "language": "nosql",
        "name": "NoSQL / MongoDB (In-Memory Worker)",
        "tier": "tier1_wasm",
        "worker": "/static/js/sandbox/nosql_worker.js",
        "supports_plots": False,
        "supports_table_diff": True,
        "supports_schema_introspection": True,
    },
)


class DriverRegistry:
    """Registry mapping driver IDs and language codes to ``LanguageDriver``s."""

    def __init__(self) -> None:
        self._by_id: dict[str, LanguageDriver] = {}
        self._by_language: dict[str, str] = {}

    def register(
        self,
        driver: LanguageDriver,
        *,
        set_default_for_language: bool = True,
    ) -> None:
        driver_id = driver.driver_id.strip().lower()
        language = driver.language.strip().lower()
        if not driver_id or not language:
            raise ValueError("Driver must define non-empty driver_id and language.")
        self._by_id[driver_id] = driver
        if set_default_for_language or language not in self._by_language:
            self._by_language[language] = driver_id

    def unregister(self, driver_id: str) -> None:
        key = driver_id.strip().lower()
        driver = self._by_id.pop(key, None)
        if driver is None:
            return
        lang = driver.language.strip().lower()
        if self._by_language.get(lang) == key:
            self._by_language.pop(lang, None)
            for other_id, other in self._by_id.items():
                if other.language.strip().lower() == lang:
                    self._by_language[lang] = other_id
                    break

    def get(self, key: str) -> LanguageDriver:
        """Resolve a driver by explicit ``driver_id`` or by ``language``."""
        norm = (key or "").strip().lower()
        if norm in self._by_id:
            return self._by_id[norm]
        mapped_id = self._by_language.get(norm)
        if mapped_id and mapped_id in self._by_id:
            return self._by_id[mapped_id]
        raise DriverNotFoundError(
            f"Sandbox driver '{key}' není registrován."
        )

    def list_tier2_drivers(self) -> list[dict[str, Any]]:
        return [d.describe() for d in self._by_id.values()]

    def list_tier1_drivers(self) -> list[dict[str, Any]]:
        return [dict(item) for item in TIER1_BROWSER_DRIVERS]

    def list_all(self) -> dict[str, Any]:
        return {
            "tier1": self.list_tier1_drivers(),
            "tier2": self.list_tier2_drivers(),
        }


def create_default_registry() -> DriverRegistry:
    """Create the default pluggable driver registry (R & C# .NET Tier 2)."""
    reg = DriverRegistry()
    reg.register(RDockerDriver())
    reg.register(CSharpDotnetDriver())
    return reg


default_registry: DriverRegistry = create_default_registry()
