"""Built-in resurfacing views over the research library (ST-04.4).

Every view is a plain filter over `ResourceRepository.list_by_workspace()` — no new persisted
state, so a view's membership always reflects the current `Resource`/`Node`/edge state. `as_of`
is caller-supplied (never `datetime.now()` internally) so resurfacing-boundary tests are
deterministic.
"""

from __future__ import annotations

from datetime import datetime

from personal_graph_os.application.repositories import (
    EdgeRepository,
    ResearchSettingsRepository,
    ResourceRepository,
    WorkspaceRepository,
)
from personal_graph_os.application.semantic_keys import RESOURCE_YIELDS_TAKEAWAY_EDGE_KEY
from personal_graph_os.domain.errors import UnknownSchemaReferenceError
from personal_graph_os.domain.identifiers import WorkspaceId
from personal_graph_os.domain.research_settings import WorkspaceResearchSettings
from personal_graph_os.domain.resource import Resource, ResourceLifecycleStatus


def get_or_default_research_settings(
    settings: ResearchSettingsRepository, workspace_id: WorkspaceId
) -> WorkspaceResearchSettings:
    existing = settings.get(workspace_id)
    if existing is not None:
        return existing
    return WorkspaceResearchSettings(workspace_id=workspace_id)


class ResearchDashboardService:
    """Read-only built-in views: Research Inbox, Continue Reading, Stale Resources, Needs
    Takeaway, Unlinked Research, Applied Sources."""

    def __init__(
        self,
        workspaces: WorkspaceRepository,
        resources: ResourceRepository,
        edges: EdgeRepository,
        research_settings: ResearchSettingsRepository,
    ) -> None:
        self._workspaces = workspaces
        self._resources = resources
        self._edges = edges
        self._research_settings = research_settings

    def _resources_for(self, workspace_id: WorkspaceId) -> tuple[Resource, ...]:
        # Archived research is dismissed: it must leave every built-in view (ST-04.4), so the
        # resurfacing/filter logic never has to special-case it per bucket. `needs_takeaway`
        # already excluded ARCHIVED; this makes the exclusion uniform for inbox/continue/stale/
        # unlinked/applied too, which previously let an archived item linger in "All Research".
        return tuple(
            r
            for r in self._resources.list_by_workspace(workspace_id)
            if r.lifecycle_status is not ResourceLifecycleStatus.ARCHIVED
        )

    def inbox(self, workspace_id: WorkspaceId) -> tuple[Resource, ...]:
        return tuple(
            r
            for r in self._resources_for(workspace_id)
            if r.lifecycle_status is ResourceLifecycleStatus.INBOX
        )

    def continue_reading(self, workspace_id: WorkspaceId) -> tuple[Resource, ...]:
        active = (ResourceLifecycleStatus.READING, ResourceLifecycleStatus.PAUSED)
        return tuple(r for r in self._resources_for(workspace_id) if r.lifecycle_status in active)

    def stale(self, workspace_id: WorkspaceId, *, as_of: datetime) -> tuple[Resource, ...]:
        settings = get_or_default_research_settings(self._research_settings, workspace_id)
        return tuple(
            r
            for r in self._resources_for(workspace_id)
            if r.is_due_for_resurfacing(as_of=as_of, stale_after_days=settings.stale_after_days)
        )

    def needs_takeaway(self, workspace_id: WorkspaceId) -> tuple[Resource, ...]:
        return tuple(
            r
            for r in self._resources_for(workspace_id)
            if not r.takeaways and r.lifecycle_status is not ResourceLifecycleStatus.ARCHIVED
        )

    def unlinked(self, workspace_id: WorkspaceId) -> tuple[Resource, ...]:
        """Resources whose backing node has no outgoing `resource_yields_takeaway` edge yet —
        i.e. research that hasn't started the guided workflow chain."""
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise UnknownSchemaReferenceError(f"workspace {workspace_id} does not exist")
        edge_type = workspace.edge_type_by_system_key(RESOURCE_YIELDS_TAKEAWAY_EDGE_KEY)
        linked_node_ids = (
            {
                edge.source_node_id
                for edge in self._edges.list_by_workspace(workspace_id)
                if edge.edge_type_id == edge_type.id
            }
            if edge_type is not None
            else set()
        )
        return tuple(
            r
            for r in self._resources_for(workspace_id)
            if r.node_id not in linked_node_ids
            and r.lifecycle_status is not ResourceLifecycleStatus.ARCHIVED
        )

    def applied(self, workspace_id: WorkspaceId) -> tuple[Resource, ...]:
        return tuple(
            r
            for r in self._resources_for(workspace_id)
            if r.lifecycle_status is ResourceLifecycleStatus.APPLIED
        )
