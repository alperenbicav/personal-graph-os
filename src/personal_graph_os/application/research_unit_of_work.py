"""Atomic multi-table boundary for the research aggregate (Node + Resource + DiscoveryRun) and
for a workflow-chain step (Node + Edge).

`NodeRepository`, `ResourceRepository`, `EdgeRepository`, and `DiscoveryRunRepository` each
commit their own single-table write. Creating a Resource always writes its backing Node in the
same operation, importing a batch of discovery candidates writes many Node/Resource pairs plus
one DiscoveryRun record, and advancing the guided workflow chain (ST-04.4) writes a new semantic
Node together with the edge connecting it to the prior step — none of these may leave a partially
written aggregate behind if a later step fails. A `ResearchUnitOfWork` is a narrow port for
exactly that: entered once per top-level operation, with `savepoint()` nesting a partial-failure
boundary inside it so one invalid candidate in a batch does not roll back the candidates already
applied.

ST-06 reuses this same port for MCP mutations: an agent's write and its attributed
`ActivityEvent` (`activity_events`) must commit or roll back together, so `AgentGatewayService`
opens the identical unit of work rather than a second, parallel one.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from types import TracebackType
from typing import Protocol

from personal_graph_os.application.repositories import (
    ActivityEventRepository,
    ContextPackRepository,
    DiscoveryRunRepository,
    EdgeRepository,
    IdempotencyReceiptRepository,
    NodeRepository,
    ResourceRepository,
)


class ResearchUnitOfWork(Protocol):
    # Read-only properties (not plain attributes): a Protocol attribute is invariant, which
    # would reject any concrete repository implementation that is merely structurally
    # compatible rather than the exact same class; a property is covariant instead.
    @property
    def nodes(self) -> NodeRepository: ...

    @property
    def resources(self) -> ResourceRepository: ...

    @property
    def edges(self) -> EdgeRepository: ...

    @property
    def discovery_runs(self) -> DiscoveryRunRepository: ...

    @property
    def activity_events(self) -> ActivityEventRepository: ...

    @property
    def idempotency_receipts(self) -> IdempotencyReceiptRepository: ...

    @property
    def context_packs(self) -> ContextPackRepository: ...

    def __enter__(self) -> ResearchUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Commit on a clean exit; roll back the entire unit of work on any exception."""

    def savepoint(self) -> AbstractContextManager[None]:
        """A nested boundary: release on success, roll back to it (not the whole unit) on
        failure, so one candidate's failure inside a batch never undoes prior candidates."""
        ...
