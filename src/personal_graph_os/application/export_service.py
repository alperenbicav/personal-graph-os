"""Deterministic, portable workspace export (ST-07.4, decision #10 `WORK.md`).

Builds a versioned `.pgos-export.zip` entirely in server-owned temporary storage; the caller
deletes it once the response has been sent. A canonical, stably ordered manifest records
every other entry's exact size and SHA-256. Excludes the bearer token, search index, MCP
idempotency receipts, the pending file-operation journal, staging/trash, caches, WAL/SHM, and
telemetry by construction -- none of those is ever read here. `FileReference`s keep their
machine/repository/relative-path/git-ref provenance labels but never `absolute_path` or
external file bytes. Any failure (a corrupt/missing managed attachment, an unwritable temp
file) aborts before any partial zip is returned -- this module never yields a truncated
export.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from personal_graph_os.application.file_service import FileService
from personal_graph_os.application.repositories import (
    AttachmentRepository,
    CanvasPlacementRepository,
    CanvasRepository,
    ContextPackRepository,
    DiscoveryRunRepository,
    EdgeRepository,
    FileReferenceRepository,
    NodeRepository,
    ResearchSettingsRepository,
    ResourceRepository,
    SavedViewRepository,
    WorkspaceRepository,
)
from personal_graph_os.application.services import WorkspaceNotFoundError
from personal_graph_os.domain.identifiers import WorkspaceId

EXPORT_FORMAT_VERSION = "1"
EXPORT_SCHEMA_VERSION = "1"

_MAX_FILE_NAME_LENGTH = 200
_ACTIVITY_PAGE_SIZE = 100


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    size_bytes: int
    sha256: str


def _sanitize_zip_file_name(file_name: str) -> str:
    """Reduce a user-supplied attachment file name to a safe, single zip path segment: no
    directory separators, no traversal, no control characters."""
    base = PurePosixPath(file_name.replace("\\", "/")).name
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in base)
    cleaned = cleaned.strip(".") or "file"
    return cleaned[:_MAX_FILE_NAME_LENGTH]


class ExportService:
    """The only path through which a workspace is exported."""

    def __init__(
        self,
        workspaces: WorkspaceRepository,
        nodes: NodeRepository,
        edges: EdgeRepository,
        canvases: CanvasRepository,
        placements: CanvasPlacementRepository,
        resources: ResourceRepository,
        saved_views: SavedViewRepository,
        context_packs: ContextPackRepository,
        research_settings: ResearchSettingsRepository,
        discovery_runs: DiscoveryRunRepository,
        attachments: AttachmentRepository,
        file_references: FileReferenceRepository,
        file_service: FileService,
        list_activity_events: Callable[[WorkspaceId, int, str | None], tuple[list, str | None]],
        export_temp_dir: Path,
    ) -> None:
        self._workspaces = workspaces
        self._nodes = nodes
        self._edges = edges
        self._canvases = canvases
        self._placements = placements
        self._resources = resources
        self._saved_views = saved_views
        self._context_packs = context_packs
        self._research_settings = research_settings
        self._discovery_runs = discovery_runs
        self._attachments = attachments
        self._file_references = file_references
        self._file_service = file_service
        self._list_activity_events = list_activity_events
        self._export_temp_dir = export_temp_dir

    def export_workspace(self, workspace_id: WorkspaceId) -> Path:
        """Build a complete, verified export zip in temp storage; returns its path. The
        caller owns deleting it once the response has been sent."""
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(f"workspace {workspace_id} does not exist")

        self._export_temp_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temp_path_str = tempfile.mkstemp(
            dir=self._export_temp_dir, suffix=".pgos-export.zip"
        )
        os.close(descriptor)
        temp_path = Path(temp_path_str)
        try:
            self._write_zip(temp_path, workspace_id, workspace)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        return temp_path

    def _write_zip(self, temp_path: Path, workspace_id: WorkspaceId, workspace: object) -> None:
        manifest_entries: list[ManifestEntry] = []

        nodes = sorted(
            self._nodes.list_by_workspace(workspace_id, include_archived=True),
            key=lambda node: node.id,
        )
        edges = sorted(self._edges.list_by_workspace(workspace_id), key=lambda edge: edge.id)
        canvases = sorted(
            self._canvases.list_by_workspace(workspace_id), key=lambda canvas: canvas.id
        )
        placements = sorted(
            (
                placement
                for canvas in canvases
                for placement in self._placements.list_by_canvas(canvas.id)
            ),
            key=lambda placement: placement.id,
        )
        resources = sorted(
            self._resources.list_by_workspace(workspace_id), key=lambda resource: resource.id
        )
        saved_views = sorted(
            self._saved_views.list_by_workspace(workspace_id), key=lambda view: view.id
        )
        context_packs = sorted(
            self._context_packs.list_by_workspace(workspace_id), key=lambda pack: pack.id
        )
        discovery_runs = sorted(
            self._discovery_runs.list_by_workspace(workspace_id), key=lambda run: run.id
        )
        research_settings = self._research_settings.get(workspace_id)

        attachments = sorted(
            (
                attachment
                for node in nodes
                for attachment in self._attachments.list_by_node(node.id)
            ),
            key=lambda attachment: attachment.id,
        )
        file_references = sorted(
            (
                reference
                for node in nodes
                for reference in self._file_references.list_by_node(node.id)
            ),
            key=lambda reference: reference.id,
        )

        activity_events = self._collect_all_activity_events(workspace_id)

        with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as archive:

            def add_json(path: str, payload: object) -> None:
                data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
                archive.writestr(path, data)
                manifest_entries.append(
                    ManifestEntry(path, len(data), hashlib.sha256(data).hexdigest())
                )

            add_json(
                "workspace.json",
                {
                    "workspace": workspace.model_dump(mode="json"),  # type: ignore[attr-defined]
                    "nodes": [node.model_dump(mode="json") for node in nodes],
                    "edges": [edge.model_dump(mode="json") for edge in edges],
                    "canvases": [canvas.model_dump(mode="json") for canvas in canvases],
                    "placements": [placement.model_dump(mode="json") for placement in placements],
                    "resources": [resource.model_dump(mode="json") for resource in resources],
                    "saved_views": [view.model_dump(mode="json") for view in saved_views],
                    "context_packs": [pack.model_dump(mode="json") for pack in context_packs],
                    "research_settings": (
                        research_settings.model_dump(mode="json")
                        if research_settings is not None
                        else None
                    ),
                    "discovery_runs": [run.model_dump(mode="json") for run in discovery_runs],
                },
            )
            add_json("activity.json", {"events": activity_events})
            add_json(
                "file_references.json",
                {
                    "file_references": [
                        {
                            key: value
                            for key, value in reference.model_dump(mode="json").items()
                            if key != "absolute_path"
                        }
                        for reference in file_references
                    ]
                },
            )
            add_json(
                "attachments.json",
                {
                    "attachments": [
                        {
                            "id": attachment.id,
                            "node_id": attachment.node_id,
                            "file_name": attachment.file_name,
                            "mime_type": attachment.mime_type,
                            "size_bytes": attachment.size_bytes,
                            "checksum_sha256": attachment.checksum_sha256,
                            "created_at": attachment.created_at.isoformat(),
                            "zip_path": (
                                f"attachments/{attachment.id}/"
                                f"{_sanitize_zip_file_name(attachment.file_name)}"
                            ),
                        }
                        for attachment in attachments
                    ]
                },
            )

            for attachment in attachments:
                # `download_attachment` raises `AttachmentContentMissingError`/
                # `AttachmentContentCorruptedError` if the managed bytes no longer match the
                # recorded size/checksum -- this aborts the whole export rather than
                # including stale or partial content.
                _, opened = self._file_service.download_attachment(attachment.id)
                with opened:
                    data = opened.read()
                entry_path = (
                    f"attachments/{attachment.id}/{_sanitize_zip_file_name(attachment.file_name)}"
                )
                archive.writestr(entry_path, data)
                manifest_entries.append(
                    ManifestEntry(entry_path, len(data), hashlib.sha256(data).hexdigest())
                )

            manifest = {
                "format_version": EXPORT_FORMAT_VERSION,
                "schema_version": EXPORT_SCHEMA_VERSION,
                "workspace_id": workspace_id,
                "generated_at": datetime.now(UTC).isoformat(),
                "entries": [
                    {"path": entry.path, "size_bytes": entry.size_bytes, "sha256": entry.sha256}
                    for entry in sorted(manifest_entries, key=lambda entry: entry.path)
                ],
            }
            archive.writestr(
                "manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
            )

    def _collect_all_activity_events(self, workspace_id: WorkspaceId) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        cursor: str | None = None
        while True:
            page_events, cursor = self._list_activity_events(
                workspace_id, _ACTIVITY_PAGE_SIZE, cursor
            )
            events.extend(event.model_dump(mode="json") for event in page_events)
            if cursor is None:
                break
        return events
