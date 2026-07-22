"""Discovery routes: side-effect-free preview and provenance-tracked, idempotent import, both
through `DiscoveryService`. Payload sizes are bounded in `api.schemas` so a batch import cannot
carry an unbounded body.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_graph_os.api.dependencies import get_discovery_service
from personal_graph_os.api.schemas import (
    DiscoveryApplyRequest,
    DiscoveryCandidateRequest,
    DiscoveryPreviewRequest,
)
from personal_graph_os.application.discovery import (
    DiscoveryCandidateInput,
    DiscoveryPreview,
    DiscoveryService,
)
from personal_graph_os.domain.activity import DiscoveryRun
from personal_graph_os.domain.identifiers import WorkspaceId

router = APIRouter(prefix="/discovery", tags=["discovery"])


def _candidate_inputs(
    candidates: list[DiscoveryCandidateRequest],
) -> list[DiscoveryCandidateInput]:
    return [
        DiscoveryCandidateInput(
            identifier=candidate.identifier,
            title=candidate.title,
            kind=candidate.kind,
            description=candidate.description,
            evidence=tuple(candidate.evidence),
        )
        for candidate in candidates
    ]


@router.post("/preview", response_model=DiscoveryPreview)
def preview_discovery(
    payload: DiscoveryPreviewRequest,
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> DiscoveryPreview:
    return discovery_service.preview(
        WorkspaceId(payload.workspace_id),
        payload.instruction,
        _candidate_inputs(payload.candidates),
        sources_searched=payload.sources_searched,
        filters_interpreted=payload.filters_interpreted,
    )


@router.post("/apply", response_model=DiscoveryRun)
def apply_discovery(
    payload: DiscoveryApplyRequest,
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> DiscoveryRun:
    return discovery_service.apply(
        WorkspaceId(payload.workspace_id),
        payload.agent_identity,
        payload.instruction,
        _candidate_inputs(payload.candidates),
        sources_searched=payload.sources_searched,
        filters_interpreted=payload.filters_interpreted,
    )
