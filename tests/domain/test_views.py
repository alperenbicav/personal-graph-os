from __future__ import annotations

import pytest

from personal_graph_os.domain.errors import InvariantViolationError
from personal_graph_os.domain.identifiers import NodeId, WorkspaceId, new_id
from personal_graph_os.domain.views import ContextPack


def test_context_pack_rejects_selection_beyond_object_limit() -> None:
    with pytest.raises(InvariantViolationError):
        ContextPack(
            workspace_id=WorkspaceId(new_id()),
            name="pack",
            node_ids=(NodeId(new_id()), NodeId(new_id()), NodeId(new_id())),
            object_limit=2,
        )


def test_context_pack_accepts_selection_within_object_limit() -> None:
    pack = ContextPack(
        workspace_id=WorkspaceId(new_id()),
        name="pack",
        node_ids=(NodeId(new_id()), NodeId(new_id())),
        object_limit=5,
    )
    assert len(pack.node_ids) == 2
