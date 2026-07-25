"""Runtime enrichment route (EP-2026-012 ST-04, review findings S4-R03/S4-R07).

The smallest bounded invocation that actually reaches the configured `EnrichmentProvider`
through the product: extract a resource's own content, classify it, and persist the resulting
profile version/relation proposals -- all through the same `ExtractionService`/`EnrichmentService`
the composition root (`api/app.py`) builds. Disabled and fail-closed by default: with no
`PGOS_ENRICHMENT_PROVIDER` configured, `get_enrichment_service` returns `None` and this route
raises rather than silently running unconfigured.

Always passes the fetched Resource's own `workspace_id` to `EnrichmentService`, never a
separately assumed default (review finding S4-R07): `EnrichmentService` itself now also verifies
this, but the route composing the *correct* value in the first place is what makes "the default
workspace" and "the resource's workspace" the same value by construction rather than by
coincidence.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_graph_os.api.dependencies import (
    get_enrichment_service,
    get_extraction_service,
    get_resource_service,
)
from personal_graph_os.api.schemas import EnrichmentOutcomeResponse, EnrichResourceRequest
from personal_graph_os.application.enrichment_service import (
    EnrichmentNotConfiguredError,
    EnrichmentService,
)
from personal_graph_os.application.extraction_service import ExtractionService
from personal_graph_os.application.services import ResourceService
from personal_graph_os.domain.identifiers import ResourceId

router = APIRouter(prefix="/resources", tags=["enrichment"])


@router.post("/{resource_id}/enrichment", response_model=EnrichmentOutcomeResponse)
def enrich_resource(
    resource_id: str,
    payload: EnrichResourceRequest,
    resource_service: ResourceService = Depends(get_resource_service),
    extraction_service: ExtractionService = Depends(get_extraction_service),
    enrichment_service: EnrichmentService | None = Depends(get_enrichment_service),
) -> EnrichmentOutcomeResponse:
    if enrichment_service is None:
        raise EnrichmentNotConfiguredError(
            "no PGOS_ENRICHMENT_PROVIDER is configured; enrichment stays disabled by default"
        )

    resource = resource_service.get(ResourceId(resource_id))
    extracted = extraction_service.extract(
        resource_kind=resource.kind,
        canonical_identifier=resource.canonical_identifier,
        source_url=resource.source_url,
    )
    outcome = enrichment_service.enrich_resource(
        workspace_id=resource.workspace_id,
        resource_id=resource.id,
        extracted=extracted,
        actor=payload.actor,
    )
    return EnrichmentOutcomeResponse.from_outcome(outcome)
