"""Source adapter interfaces. Adapters are injected; this module performs no implicit navigation."""

from typing import Any, Protocol


class SourceAdapter(Protocol):
    def lookup(self, destination_url: str, claim: dict[str, Any], *, run_id: str) -> dict[str, Any]:
        """Access the selected source and return evidence plus search completeness."""


class SourceRegistry:
    def __init__(self, adapters: dict[str, SourceAdapter] | None = None):
        self._adapters = dict(adapters or {})

    def lookup(self, source: str, destination_url: str, claim: dict[str, Any], *, run_id: str) -> dict[str, Any]:
        adapter = self._adapters.get(source)
        if adapter is None:
            raise LookupError(f"No adapter is configured for source {source!r}")
        return adapter.lookup(destination_url, claim, run_id=run_id)
