"""Phase-two contracts only. No plugins are loaded and no network requests are made.

Any future retrieval adapter must apply scope/validity/forget filters before returning records.
ACL must cover every read/write/history/archive/status operation before multi-user transport.
Consolidation proposals require explicit application using existing revision-checked writes.
"""

from typing import Protocol

from .models import Memory, Search


class EmbeddingProvider(Protocol):
    model_id: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HybridRetriever(Protocol):
    def search(self, selection: Search) -> list[Memory]: ...


class AccessPolicy(Protocol):
    def authorize(self, principal: str, operation: str, scope: str, scope_id: str | None) -> None: ...


class Consolidator(Protocol):
    def propose(self, memories: list[Memory]) -> list[dict]: ...
