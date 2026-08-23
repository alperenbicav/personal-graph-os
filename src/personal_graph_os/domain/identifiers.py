"""Typed identifiers shared across the domain model.

Every aggregate uses a distinct identifier type so a caller cannot accidentally pass a
`NodeId` where an `EdgeId` is expected, even though both are represented as UUID4 strings
at runtime.
"""

from __future__ import annotations

import uuid
from typing import NewType

WorkspaceId = NewType("WorkspaceId", str)
NodeTypeId = NewType("NodeTypeId", str)
FieldDefinitionId = NewType("FieldDefinitionId", str)
StatusDefinitionId = NewType("StatusDefinitionId", str)
EdgeTypeId = NewType("EdgeTypeId", str)
NodeId = NewType("NodeId", str)
EdgeId = NewType("EdgeId", str)
CanvasId = NewType("CanvasId", str)
CanvasPlacementId = NewType("CanvasPlacementId", str)
ResourceId = NewType("ResourceId", str)
SavedViewId = NewType("SavedViewId", str)
ContextPackId = NewType("ContextPackId", str)
AttachmentId = NewType("AttachmentId", str)
FileReferenceId = NewType("FileReferenceId", str)
ActivityEventId = NewType("ActivityEventId", str)
DiscoveryRunId = NewType("DiscoveryRunId", str)
IdempotencyReceiptId = NewType("IdempotencyReceiptId", str)
CollectionId = NewType("CollectionId", str)
TagId = NewType("TagId", str)
DocumentId = NewType("DocumentId", str)
DocumentVersionId = NewType("DocumentVersionId", str)
DocumentLinkId = NewType("DocumentLinkId", str)
IngestionJobId = NewType("IngestionJobId", str)
WorkItemId = NewType("WorkItemId", str)
WorkItemChecklistItemId = NewType("WorkItemChecklistItemId", str)
ResourceEnrichmentProfileId = NewType("ResourceEnrichmentProfileId", str)
ResourceEnrichmentProfileVersionId = NewType("ResourceEnrichmentProfileVersionId", str)
RelationProposalId = NewType("RelationProposalId", str)
WorkPlanningReceiptId = NewType("WorkPlanningReceiptId", str)
AgentId = NewType("AgentId", str)
AgentRunId = NewType("AgentRunId", str)


def new_id() -> str:
    """Generate a new opaque identifier for any aggregate."""
    return str(uuid.uuid4())
