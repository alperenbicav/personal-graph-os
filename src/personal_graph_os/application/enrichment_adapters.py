"""Enrichment classifier protocol: the only boundary `EnrichmentService` depends on
(EP-2026-012 ST-04), mirroring `application/extraction_adapters.py`.

`infrastructure.enrichment.HttpChatEnrichmentProvider` is the concrete runtime implementation,
composed by `infrastructure.enrichment.provider_factory` and wired into the product's composition
root (`api/app.py`, review finding S4-R03); `infrastructure.enrichment.FakeEnrichmentProvider`
remains a deterministic test double. Live-cost model calls still require their own separately
recorded approval -- this module only defines the boundary, never performs a call itself.
"""

from __future__ import annotations

from typing import Protocol

from personal_graph_os.domain.enrichment import EnrichmentError, EnrichmentResult
from personal_graph_os.domain.extraction import ExtractedContent


class EnrichmentProvider(Protocol):
    """Classifies one resource's extracted content into a typed `EnrichmentResult`.

    Receives only `ExtractedContent` -- already bounded, truncated, evidence-carrying fields
    (`domain.extraction`) -- never a raw fetch, database access, or any capability to resolve a
    graph node or execute a tool itself. Raises `EnrichmentError` (or a more specific subclass)
    when classification cannot produce a result; never a partial or best-effort one.
    """

    @property
    def name(self) -> str:
        """Stable provider identity recorded on every version it produces (e.g. `"fake"`, or a
        future real provider's `"<vendor>:<model>"`)."""
        ...

    def classify(self, *, extracted: ExtractedContent) -> EnrichmentResult:
        """Return a typed enrichment result for `extracted`. Raises `EnrichmentError` on
        failure."""
        ...


class EnrichmentProviderUnavailableError(EnrichmentError):
    """Raised when a configured `EnrichmentProvider` could not produce a result -- a transport
    failure, a rate limit, or an unusable response -- never returned as a partial result."""


__all__ = [
    "EnrichmentProvider",
    "EnrichmentProviderUnavailableError",
]
