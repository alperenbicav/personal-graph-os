from __future__ import annotations

import sqlite3

import pytest

from personal_graph_os.application.discovery import (
    DiscoveryCandidateInput,
    DiscoveryDecision,
    DiscoveryService,
)
from personal_graph_os.application.semantic_schema import ensure_semantic_schema
from personal_graph_os.application.services import (
    ResourceService,
    WorkspaceNotFoundError,
    new_workspace,
)
from personal_graph_os.domain.activity import DiscoveryOutcome
from personal_graph_os.domain.identifiers import WorkspaceId, new_id
from personal_graph_os.domain.resource import ResourceKind
from personal_graph_os.infrastructure.sqlite.repositories import (
    SqliteResourceRepository,
    SqliteWorkspaceRepository,
)
from personal_graph_os.infrastructure.sqlite.research_unit_of_work import (
    SqliteResearchUnitOfWork,
)


def _discovery_service(
    sqlite_connection: sqlite3.Connection,
) -> tuple[DiscoveryService, ResourceService, WorkspaceId]:
    workspace = ensure_semantic_schema(new_workspace("Personal"))
    workspace_repository = SqliteWorkspaceRepository(sqlite_connection)
    workspace_repository.save(workspace)
    resource_repository = SqliteResourceRepository(sqlite_connection)
    unit_of_work_factory = lambda: SqliteResearchUnitOfWork(sqlite_connection)  # noqa: E731
    resource_service = ResourceService(
        workspace_repository, resource_repository, unit_of_work_factory
    )
    discovery_service = DiscoveryService(
        workspace_repository, resource_repository, resource_service, unit_of_work_factory
    )
    return discovery_service, resource_service, workspace.id


def test_preview_is_side_effect_free_and_classifies_a_new_candidate_as_create(
    sqlite_connection: sqlite3.Connection,
) -> None:
    discovery_service, resource_service, workspace_id = _discovery_service(sqlite_connection)

    preview = discovery_service.preview(
        workspace_id,
        "find recent papers on X",
        [
            DiscoveryCandidateInput(
                identifier="https://arxiv.org/abs/2401.00001",
                title="A great paper",
            )
        ],
    )

    assert len(preview.candidates) == 1
    candidate_preview = preview.candidates[0]
    assert candidate_preview.decision is DiscoveryDecision.CREATE
    assert candidate_preview.canonical_identifier == "arxiv:2401.00001"
    assert resource_service.list_by_workspace(workspace_id) == ()


def test_preview_classifies_an_existing_candidate_as_reuse(
    sqlite_connection: sqlite3.Connection,
) -> None:
    discovery_service, resource_service, workspace_id = _discovery_service(sqlite_connection)
    existing, _ = resource_service.create_or_reuse(
        workspace_id, "A paper", "https://arxiv.org/abs/2401.00001"
    )

    preview = discovery_service.preview(
        workspace_id,
        "find recent papers on X",
        [
            DiscoveryCandidateInput(
                identifier="https://arxiv.org/abs/2401.00001v2",
                title="Same paper",
            )
        ],
    )

    candidate_preview = preview.candidates[0]
    assert candidate_preview.decision is DiscoveryDecision.REUSE
    assert candidate_preview.existing_resource_id == existing.id


def test_preview_classifies_an_in_batch_duplicate_as_reuse_not_a_second_create(
    sqlite_connection: sqlite3.Connection,
) -> None:
    """Neither URL is persisted yet, but they canonicalize to the same identity — apply()
    only ever writes the first one, so preview must not tell the caller both are creatable."""
    discovery_service, _resource_service, workspace_id = _discovery_service(sqlite_connection)

    preview = discovery_service.preview(
        workspace_id,
        "find recent papers on X",
        [
            DiscoveryCandidateInput(identifier="https://arxiv.org/abs/2401.00001", title="First"),
            DiscoveryCandidateInput(
                identifier="https://arxiv.org/abs/2401.00001v2", title="Duplicate"
            ),
        ],
    )

    first, second = preview.candidates
    assert first.decision is DiscoveryDecision.CREATE
    assert second.decision is DiscoveryDecision.REUSE
    assert second.duplicate_of_candidate_index == 0
    assert "candidate #1" in second.reason


def test_preview_rejects_a_candidate_that_cannot_be_canonicalized(
    sqlite_connection: sqlite3.Connection,
) -> None:
    discovery_service, _resource_service, workspace_id = _discovery_service(sqlite_connection)

    preview = discovery_service.preview(
        workspace_id,
        "find recent papers on X",
        [DiscoveryCandidateInput(identifier="not a url", title="Junk")],
    )

    candidate_preview = preview.candidates[0]
    assert candidate_preview.decision is DiscoveryDecision.REJECT
    assert candidate_preview.canonical_identifier is None


def test_preview_raises_for_unknown_workspace(sqlite_connection: sqlite3.Connection) -> None:
    discovery_service, _resource_service, _workspace_id = _discovery_service(sqlite_connection)

    with pytest.raises(WorkspaceNotFoundError):
        discovery_service.preview(WorkspaceId(new_id()), "instruction", [])


def test_apply_imports_new_candidates_and_persists_a_completed_discovery_run(
    sqlite_connection: sqlite3.Connection,
) -> None:
    discovery_service, resource_service, workspace_id = _discovery_service(sqlite_connection)

    run = discovery_service.apply(
        workspace_id,
        "manual-import",
        "import two papers",
        [
            DiscoveryCandidateInput(
                identifier="https://arxiv.org/abs/2401.00001", title="Paper one"
            ),
            DiscoveryCandidateInput(
                identifier="https://github.com/octocat/hello-world", title="A repo"
            ),
        ],
    )

    assert run.completed_at is not None
    assert run.imported_count == 2
    assert run.skipped_count == 0
    resources = resource_service.list_by_workspace(workspace_id)
    assert len(resources) == 2
    assert {candidate.was_imported for candidate in run.candidates} == {True}
    assert all(candidate.imported_node_id is not None for candidate in run.candidates)


def test_apply_reuses_an_existing_resource_without_creating_a_duplicate(
    sqlite_connection: sqlite3.Connection,
) -> None:
    discovery_service, resource_service, workspace_id = _discovery_service(sqlite_connection)
    existing, _ = resource_service.create_or_reuse(
        workspace_id, "A paper", "https://arxiv.org/abs/2401.00001"
    )

    run = discovery_service.apply(
        workspace_id,
        "manual-import",
        "import a duplicate",
        [
            DiscoveryCandidateInput(
                identifier="https://arxiv.org/abs/2401.00001v3", title="Same paper again"
            )
        ],
    )

    assert len(resource_service.list_by_workspace(workspace_id)) == 1
    candidate = run.candidates[0]
    assert candidate.was_imported is False
    assert candidate.imported_node_id == existing.node_id


def test_reapplying_the_same_request_is_idempotent(
    sqlite_connection: sqlite3.Connection,
) -> None:
    discovery_service, resource_service, workspace_id = _discovery_service(sqlite_connection)
    candidates = [
        DiscoveryCandidateInput(identifier="https://arxiv.org/abs/2401.00001", title="Paper")
    ]

    first_run = discovery_service.apply(workspace_id, "manual-import", "import", candidates)
    second_run = discovery_service.apply(workspace_id, "manual-import", "import", candidates)

    assert first_run.id != second_run.id
    assert len(resource_service.list_by_workspace(workspace_id)) == 1
    assert first_run.candidates[0].was_imported is True
    assert second_run.candidates[0].was_imported is False


def test_apply_records_a_failed_candidate_without_rolling_back_the_others_in_the_batch(
    sqlite_connection: sqlite3.Connection,
) -> None:
    discovery_service, resource_service, workspace_id = _discovery_service(sqlite_connection)

    run = discovery_service.apply(
        workspace_id,
        "manual-import",
        "import a mixed batch",
        [
            DiscoveryCandidateInput(
                identifier="https://arxiv.org/abs/2401.00001", title="Good candidate one"
            ),
            DiscoveryCandidateInput(identifier="not a url at all", title="Bad candidate"),
            DiscoveryCandidateInput(
                identifier="https://github.com/octocat/hello-world", title="Good candidate two"
            ),
        ],
    )

    outcomes = {candidate.title: candidate.was_imported for candidate in run.candidates}
    assert outcomes == {
        "Good candidate one": True,
        "Bad candidate": False,
        "Good candidate two": True,
    }
    failed_candidate = next(c for c in run.candidates if c.title == "Bad candidate")
    assert failed_candidate.outcome is DiscoveryOutcome.FAILED
    assert failed_candidate.reason is not None
    assert failed_candidate.imported_node_id is None
    assert len(resource_service.list_by_workspace(workspace_id)) == 2


_MALFORMED_URL_SHAPES = [
    "https://example.com:bad/path",
    "https://[bad",
    "https://example.com:99999/path",
]


@pytest.mark.parametrize("malformed_url", _MALFORMED_URL_SHAPES)
def test_preview_rejects_every_malformed_url_shape_without_raising(
    sqlite_connection: sqlite3.Connection, malformed_url: str
) -> None:
    discovery_service, _resource_service, workspace_id = _discovery_service(sqlite_connection)

    preview = discovery_service.preview(
        workspace_id,
        "import",
        [DiscoveryCandidateInput(identifier=malformed_url, title="Malformed")],
    )

    assert preview.candidates[0].decision is DiscoveryDecision.REJECT


@pytest.mark.parametrize("malformed_url", _MALFORMED_URL_SHAPES)
def test_apply_isolates_every_malformed_url_shape_from_valid_neighbors(
    sqlite_connection: sqlite3.Connection, malformed_url: str
) -> None:
    discovery_service, resource_service, workspace_id = _discovery_service(sqlite_connection)

    run = discovery_service.apply(
        workspace_id,
        "manual-import",
        "import a batch with a malformed URL",
        [
            DiscoveryCandidateInput(
                identifier="https://arxiv.org/abs/2401.00001", title="Good candidate"
            ),
            DiscoveryCandidateInput(identifier=malformed_url, title="Malformed port"),
        ],
    )

    outcomes = {c.title: c.was_imported for c in run.candidates}
    assert outcomes == {"Good candidate": True, "Malformed port": False}
    assert len(resource_service.list_by_workspace(workspace_id)) == 1


def test_apply_accepts_a_caller_supplied_kind_when_identity_does_not_detect_one(
    sqlite_connection: sqlite3.Connection,
) -> None:
    discovery_service, resource_service, workspace_id = _discovery_service(sqlite_connection)

    run = discovery_service.apply(
        workspace_id,
        "manual-import",
        "import a dataset page",
        [
            DiscoveryCandidateInput(
                identifier="https://example.com/dataset",
                title="A dataset",
                kind=ResourceKind.DATASET,
            )
        ],
    )

    node_id = run.candidates[0].imported_node_id
    assert node_id is not None
    resource = next(
        r for r in resource_service.list_by_workspace(workspace_id) if r.node_id == node_id
    )
    assert resource.kind is ResourceKind.DATASET
