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


def new_id() -> str:
    """Generate a new opaque identifier for any aggregate."""
    return str(uuid.uuid4())
