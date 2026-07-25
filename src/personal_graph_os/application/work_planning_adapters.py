"""Work-planning classifier protocol: the only boundary `WorkPlanningService` depends on
(EP-2026-012 ST-05), mirroring `application/enrichment_adapters.py`.

A concrete real-provider implementation is separate, future, and separately approved scope --
live-cost model calls require their own recorded approval. Only the deterministic
`infrastructure.work_planning.FakeWorkPlanningProvider` is registered today.
"""

from __future__ import annotations

from typing import Protocol

from personal_graph_os.domain.work_planning import WorkPlanningError, WorkPlanResult


class WorkPlanningProvider(Protocol):
    """Classifies one captured piece of text into a typed `WorkPlanResult`.

    Receives only plain text and a title -- never database access or the capability to address
    a graph node, work item, or repository directly itself. Raises `WorkPlanningError` (or a
    more specific subclass) when classification cannot produce a result; never a partial or
    best-effort one.
    """

    @property
    def name(self) -> str:
        """Stable provider identity (e.g. `"fake"`, or a future real provider's
        `"<vendor>:<model>"`)."""
        ...

    def classify(self, *, source_text: str, title: str) -> WorkPlanResult:
        """Return a typed plan result for `source_text`/`title`. Raises `WorkPlanningError` on
        failure."""
        ...


class WorkPlanningProviderUnavailableError(WorkPlanningError):
    """Raised when a configured `WorkPlanningProvider` could not produce a result -- a
    transport failure, a rate limit, or an unusable response -- never returned as a partial
    result."""


__all__ = [
    "WorkPlanningProvider",
    "WorkPlanningProviderUnavailableError",
]
