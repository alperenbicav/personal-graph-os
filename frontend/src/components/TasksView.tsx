import { useCallback, useEffect, useMemo, useState } from 'react'
import type {
  CreateWorkItemInput,
  UpdateChecklistItemPatch,
  UpdateWorkItemPatch,
} from '../api/client'
import type {
  WikiDocument,
  WorkItem,
  WorkItemChecklistItem,
  WorkItemDetail,
  WorkItemKind,
  WorkItemPriority,
  WorkItemStatus,
  WorkItemType,
} from '../types'
import { EmptyState, Pill, type PillTone, SegmentedControl } from './ui'
import { renderMarkdown } from '../lib/markdown'

const REST_ACTOR_NAME = 'human/local-user/rest'

const KIND_OPTIONS: { value: WorkItemKind; label: string }[] = [
  { value: 'epic', label: 'Epic' },
  { value: 'story', label: 'Story' },
  { value: 'task', label: 'Task' },
]

const TYPE_OPTIONS: { value: WorkItemType; label: string }[] = [
  { value: 'feature', label: 'Feature' },
  { value: 'fix', label: 'Fix' },
  { value: 'refactor', label: 'Refactor' },
  { value: 'research', label: 'Research' },
  { value: 'ops', label: 'Ops' },
  { value: 'docs', label: 'Docs' },
]

const STATUS_OPTIONS: { value: WorkItemStatus; label: string }[] = [
  { value: 'backlog', label: 'Backlog' },
  { value: 'planned', label: 'Planned' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'in_review', label: 'In review' },
  { value: 'done', label: 'Done' },
  { value: 'production', label: 'Production' },
  { value: 'blocked', label: 'Blocked' },
  { value: 'cancelled', label: 'Cancelled' },
]

const PRIORITY_OPTIONS: { value: WorkItemPriority; label: string }[] = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'critical', label: 'Critical' },
]

const STATUS_LABEL: Record<WorkItemStatus, string> = Object.fromEntries(
  STATUS_OPTIONS.map((option) => [option.value, option.label]),
) as Record<WorkItemStatus, string>

const PRIORITY_LABEL: Record<WorkItemPriority, string> = Object.fromEntries(
  PRIORITY_OPTIONS.map((option) => [option.value, option.label]),
) as Record<WorkItemPriority, string>

const STATUS_TONE: Record<WorkItemStatus, PillTone> = {
  backlog: 'neutral',
  planned: 'violet',
  in_progress: 'teal',
  in_review: 'brass',
  done: 'moss',
  production: 'moss',
  blocked: 'coral',
  cancelled: 'neutral',
}

const PRIORITY_TONE: Record<WorkItemPriority, PillTone> = {
  low: 'neutral',
  medium: 'teal',
  high: 'brass',
  critical: 'coral',
}

/** Statuses that still need work; the "Open only" filter hides everything else. */
const CLOSED_STATUSES: ReadonlySet<WorkItemStatus> = new Set([
  'done',
  'production',
  'cancelled',
])

interface TasksViewProps {
  workspaceId: string
  workItems: WorkItem[]
  documents: WikiDocument[]
  repositories: { node_id: string; title: string }[]
  selectedWorkItemId: string | null
  onSelectWorkItem: (id: string | null) => void
  onCreateWorkItem: (input: CreateWorkItemInput) => Promise<WorkItem>
  onUpdateWorkItem: (workItemId: string, patch: UpdateWorkItemPatch) => Promise<WorkItem>
  onLoadDetail: (workItemId: string) => Promise<WorkItemDetail>
  onEditBody: (workItemId: string, body: string) => Promise<void>
  onAddChecklistItem: (workItemId: string, label: string) => Promise<WorkItemChecklistItem>
  onUpdateChecklistItem: (
    checklistItemId: string,
    patch: UpdateChecklistItemPatch,
  ) => Promise<WorkItemChecklistItem>
  onRemoveChecklistItem: (checklistItemId: string) => Promise<void>
  onReorderChecklistItems: (
    workItemId: string,
    orderedIds: string[],
  ) => Promise<WorkItemChecklistItem[]>
  onAttachDocument: (workItemId: string, documentId: string) => Promise<void>
  onDetachDocument: (workItemId: string, documentId: string) => Promise<void>
  /** Renames via the backing Node (titles live there so search stays indexed), then keeps
   * the work-item projection in sync. */
  onRenameWorkItem: (item: WorkItem, title: string) => Promise<void>
  onNavigateDocument?: (documentId: string) => void
  onNavigateRepository?: (nodeId: string) => void
}

interface CreateWorkItemDraft {
  kind: WorkItemKind
  title: string
  workType: WorkItemType
  parentId: string
}

const EMPTY_DRAFT: CreateWorkItemDraft = {
  kind: 'epic',
  title: '',
  workType: 'feature',
  parentId: '',
}

const PARENT_KIND_BY_CHILD: Partial<Record<WorkItemKind, WorkItemKind[]>> = {
  story: ['epic'],
  task: ['story'],
}

type ViewMode = 'board' | 'list'

export function TasksView({
  workspaceId,
  workItems,
  documents,
  repositories,
  selectedWorkItemId,
  onSelectWorkItem,
  onCreateWorkItem,
  onUpdateWorkItem,
  onLoadDetail,
  onEditBody,
  onAddChecklistItem,
  onUpdateChecklistItem,
  onRemoveChecklistItem,
  onReorderChecklistItems,
  onAttachDocument,
  onDetachDocument,
  onRenameWorkItem,
  onNavigateDocument,
  onNavigateRepository,
}: TasksViewProps) {
  const [viewMode, setViewMode] = useState<ViewMode>('board')
  const [filterQuery, setFilterQuery] = useState('')
  const [openOnly, setOpenOnly] = useState(true)
  const [dragOverStatus, setDragOverStatus] = useState<WorkItemStatus | null>(null)
  const [detail, setDetail] = useState<WorkItemDetail | null>(null)
  const [bodyDraft, setBodyDraft] = useState('')
  const [descriptionMode, setDescriptionMode] = useState<'write' | 'preview'>('write')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [createDraft, setCreateDraft] = useState<CreateWorkItemDraft>(EMPTY_DRAFT)
  const [createError, setCreateError] = useState<string | null>(null)
  const [newChecklistLabel, setNewChecklistLabel] = useState('')
  const [linkTargetId, setLinkTargetId] = useState('')

  const selectedWorkItem = useMemo(
    () => workItems.find((item) => item.id === selectedWorkItemId) ?? null,
    [workItems, selectedWorkItemId],
  )

  useEffect(() => {
    if (!selectedWorkItemId) {
      setDetail(null)
      return
    }
    let cancelled = false
    onLoadDetail(selectedWorkItemId)
      .then((loaded) => {
        if (cancelled) return
        setDetail(loaded)
        setBodyDraft(loaded.work_item.body)
      })
      .catch((error) => {
        if (!cancelled) {
          setLoadError(error instanceof Error ? error.message : String(error))
        }
      })
    return () => {
      cancelled = true
    }
  }, [selectedWorkItemId, onLoadDetail])

  const childrenOf = useCallback(
    (parentId: string | null) => workItems.filter((item) => item.parent_id === parentId),
    [workItems],
  )

  const roots = childrenOf(null)

  const parentOptions = useMemo(() => {
    const allowedKinds = PARENT_KIND_BY_CHILD[createDraft.kind] ?? []
    return workItems.filter((item) => allowedKinds.includes(item.kind))
  }, [workItems, createDraft.kind])

  const linkedDocumentIds = useMemo(
    () => new Set((detail?.linked_documents ?? []).map((link) => link.document_id)),
    [detail],
  )
  const linkableDocuments = useMemo(
    () => documents.filter((document) => !linkedDocumentIds.has(document.id)),
    [documents, linkedDocumentIds],
  )

  function selectWorkItem(workItemId: string | null) {
    onSelectWorkItem(workItemId)
    setLoadError(null)
  }

  const filteredItems = useMemo(() => {
    const query = filterQuery.trim().toLowerCase()
    return workItems.filter((item) => {
      if (openOnly && CLOSED_STATUSES.has(item.status)) return false
      if (!query) return true
      return (
        item.title.toLowerCase().includes(query) ||
        (item.assignee ?? '').toLowerCase().includes(query) ||
        (item.blockers ?? '').toLowerCase().includes(query)
      )
    })
  }, [workItems, filterQuery, openOnly])

  async function saveBody() {
    if (!selectedWorkItemId) return
    try {
      await onEditBody(selectedWorkItemId, bodyDraft)
      if (detail) setDetail({ ...detail, work_item: { ...detail.work_item, body: bodyDraft } })
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  async function commitUpdate(patch: UpdateWorkItemPatch) {
    if (!selectedWorkItemId) return
    try {
      const updated = await onUpdateWorkItem(selectedWorkItemId, patch)
      if (detail) setDetail({ ...detail, work_item: updated })
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  async function submitCreate() {
    if (!createDraft.title.trim()) return
    setCreateError(null)
    try {
      const created = await onCreateWorkItem({
        workspace_id: workspaceId,
        kind: createDraft.kind,
        work_type: createDraft.workType,
        title: createDraft.title.trim(),
        source: REST_ACTOR_NAME,
        parent_id: createDraft.parentId || null,
      })
      setIsCreateOpen(false)
      setCreateDraft(EMPTY_DRAFT)
      selectWorkItem(created.id)
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : String(error))
    }
  }

  async function quickAdd(status: WorkItemStatus, title: string) {
    if (!title.trim()) return
    try {
      await onCreateWorkItem({
        workspace_id: workspaceId,
        kind: 'task',
        work_type: 'feature',
        title: title.trim(),
        status,
        source: REST_ACTOR_NAME,
        parent_id: null,
      })
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  async function dropOnColumn(status: WorkItemStatus, itemId: string) {
    const item = workItems.find((candidate) => candidate.id === itemId)
    if (!item || item.status === status) return
    try {
      await onUpdateWorkItem(itemId, { status })
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  async function submitChecklistItem() {
    if (!selectedWorkItemId || !newChecklistLabel.trim()) return
    try {
      const created = await onAddChecklistItem(selectedWorkItemId, newChecklistLabel.trim())
      setNewChecklistLabel('')
      if (detail) {
        setDetail({ ...detail, checklist_items: [...detail.checklist_items, created] })
      }
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  async function toggleChecklistItem(checklistEntry: WorkItemChecklistItem) {
    try {
      const updated = await onUpdateChecklistItem(checklistEntry.id, {
        is_completed: !checklistEntry.is_completed,
      })
      if (detail) {
        setDetail({
          ...detail,
          checklist_items: detail.checklist_items.map((candidate) =>
            candidate.id === updated.id ? updated : candidate,
          ),
        })
      }
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  async function removeChecklistItem(itemId: string) {
    if (!selectedWorkItemId) return
    try {
      await onRemoveChecklistItem(itemId)
      if (detail) {
        setDetail({
          ...detail,
          checklist_items: detail.checklist_items
            .filter((item) => item.id !== itemId)
            .map((entry, index) => ({ ...entry, position: index })),
        })
      }
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  async function moveChecklistItem(itemId: string, direction: -1 | 1) {
    if (!selectedWorkItemId || !detail) return
    const items = detail.checklist_items
    const from = items.findIndex((entry) => entry.id === itemId)
    const to = from + direction
    if (from < 0 || to < 0 || to >= items.length) return
    const reordered = [...items]
    const [moved] = reordered.splice(from, 1)
    reordered.splice(to, 0, moved)
    try {
      const persisted = await onReorderChecklistItems(
        selectedWorkItemId,
        reordered.map((entry) => entry.id),
      )
      setDetail({ ...detail, checklist_items: persisted })
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  async function submitLink() {
    if (!selectedWorkItemId || !linkTargetId) return
    try {
      await onAttachDocument(selectedWorkItemId, linkTargetId)
      const reloaded = await onLoadDetail(selectedWorkItemId)
      setDetail(reloaded)
      setLinkTargetId('')
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  async function removeLink(documentId: string) {
    if (!selectedWorkItemId) return
    try {
      await onDetachDocument(selectedWorkItemId, documentId)
      const reloaded = await onLoadDetail(selectedWorkItemId)
      setDetail(reloaded)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  const item = selectedWorkItem ?? detail?.work_item ?? null

  // ---------- Detail overlay (Linear/Jira-style full-area page) ----------
  if (item) {
    return (
      <div className="tasks-view" aria-label="Tasks">
        <div className="tasks-detail">
          <div className="tasks-detail-inner reading-column">
            <div className="tasks-detail-top">
              <button type="button" className="tasks-back" onClick={() => selectWorkItem(null)}>
                ← Back
              </button>
              <Pill tone={item.kind === 'epic' ? 'violet' : item.kind === 'story' ? 'brass' : 'teal'}>
                {item.kind}
              </Pill>
              <Pill tone={STATUS_TONE[item.status]}>{STATUS_LABEL[item.status]}</Pill>
            </div>

            {/* Provenance ribbon (S6) */}
            {(() => {
              const parentItem = item.parent_id
                ? workItems.find((w) => w.id === item.parent_id)
                : null
              const repoItem = item.repository_node_id
                ? repositories.find((r) => r.node_id === item.repository_node_id)
                : null
              const hasProvenance =
                parentItem || repoItem || (detail && detail.linked_documents.length > 0)
              if (!hasProvenance) return null
              return (
                <div className="provenance-ribbon" aria-label="Work item lineage and connected provenance">
                  {parentItem && (
                    <>
                      <button
                        type="button"
                        className="provenance-chip"
                        onClick={() => selectWorkItem(parentItem.id)}
                        title={`Parent: ${parentItem.title}`}
                      >
                        <span className="provenance-icon">🏷️</span>
                        <span className="provenance-title">{parentItem.title}</span>
                      </button>
                      <span className="provenance-arrow">➔</span>
                    </>
                  )}
                  <span className="provenance-chip provenance-chip-current">
                    <span className="provenance-icon">☑️</span>
                    <span className="provenance-title">{item.title}</span>
                  </span>
                  {repoItem && (
                    <>
                      <span className="provenance-arrow">➔</span>
                      <button
                        type="button"
                        className="provenance-chip"
                        onClick={() => onNavigateRepository?.(repoItem.node_id)}
                        title={`Repository: ${repoItem.title}`}
                      >
                        <span className="provenance-icon">📦</span>
                        <span className="provenance-title">{repoItem.title}</span>
                      </button>
                    </>
                  )}
                  {detail &&
                    detail.linked_documents.map((link) => {
                      const doc = documents.find((d) => d.id === link.document_id)
                      if (!doc) return null
                      return (
                        <span
                          key={link.id}
                          style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--s2)' }}
                        >
                          <span className="provenance-arrow">➔</span>
                          <button
                            type="button"
                            className="provenance-chip"
                            onClick={() => onNavigateDocument?.(doc.id)}
                            title={`Wiki document: ${doc.title}`}
                          >
                            <span className="provenance-icon">📄</span>
                            <span className="provenance-title">{doc.title}</span>
                          </button>
                        </span>
                      )
                    })}
                </div>
              )
            })()}

            <input
              className="wiki-title-input"
              aria-label="Work item title"
              defaultValue={item.title}
              key={item.id}
              onBlur={(event) => {
                const trimmed = event.target.value.trim()
                if (trimmed && trimmed !== item.title) void onRenameWorkItem(item, trimmed)
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter') event.currentTarget.blur()
              }}
            />

            <div className="tasks-controls">
              <label className="tasks-control">
                <span>Status</span>
                <select
                  className="wiki-chip-select"
                  value={item.status}
                  onChange={(event) =>
                    commitUpdate({ status: event.target.value as WorkItemStatus })
                  }
                >
                  {STATUS_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="tasks-control">
                <span>Priority</span>
                <select
                  className="wiki-chip-select"
                  value={item.priority ?? ''}
                  onChange={(event) =>
                    event.target.value
                      ? commitUpdate({ priority: event.target.value as WorkItemPriority })
                      : commitUpdate({ clear_priority: true })
                  }
                >
                  <option value="">—</option>
                  {PRIORITY_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="tasks-control">
                <span>Assignee</span>
                <input
                  type="text"
                  className="wiki-tag-add-input"
                  style={{ width: 130 }}
                  defaultValue={item.assignee ?? ''}
                  onBlur={(event) => {
                    const value = event.target.value.trim()
                    commitUpdate(value ? { assignee: value } : { clear_assignee: true })
                  }}
                />
              </label>

              <label className="tasks-control">
                <span>Due date</span>
                <input
                  type="date"
                  value={item.due_date ?? ''}
                  onChange={(event) =>
                    event.target.value
                      ? commitUpdate({ due_date: event.target.value })
                      : commitUpdate({ clear_due_date: true })
                  }
                />
              </label>

              <label className="tasks-control tasks-progress">
                <span>Progress (%)</span>
                <input
                  type="number"
                  min={0}
                  max={100}
                  step={5}
                  value={item.progress_percent ?? 0}
                  aria-label="Progress (%)"
                  onBlur={(event) => {
                    const value = event.target.value.trim()
                    if (value === '') {
                      commitUpdate({ clear_progress_percent: true })
                    } else {
                      const parsed = Number(value)
                      if (!Number.isNaN(parsed)) commitUpdate({ progress_percent: parsed })
                    }
                  }}
                />
              </label>

              <label className="tasks-control">
                <span>Repository</span>
                <select
                  className="wiki-chip-select"
                  value={item.repository_node_id ?? ''}
                  onChange={(event) =>
                    event.target.value
                      ? commitUpdate({ repository_node_id: event.target.value })
                      : commitUpdate({ clear_repository_node_id: true })
                  }
                >
                  <option value="">—</option>
                  {repositories.map((repository) => (
                    <option key={repository.node_id} value={repository.node_id}>
                      {repository.title}
                    </option>
                  ))}
                </select>
              </label>

              <label className="tasks-control">
                <span>Type</span>
                <select
                  className="wiki-chip-select"
                  value={item.work_type}
                  onChange={(event) =>
                    commitUpdate({ work_type: event.target.value as WorkItemType })
                  }
                >
                  {TYPE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="tasks-control">
                <span>Blockers</span>
                <input
                  type="text"
                  className="wiki-tag-add-input"
                  style={{ width: 160 }}
                  defaultValue={item.blockers ?? ''}
                  onBlur={(event) => {
                    const value = event.target.value.trim()
                    commitUpdate(value ? { blockers: value } : { clear_blockers: true })
                  }}
                />
              </label>
            </div>

            {loadError && (
              <p className="view-error" role="alert">
                {loadError}
              </p>
            )}

            <div className="wiki-editor-bar">
              <SegmentedControl
                ariaLabel="Description mode"
                options={[
                  { value: 'write', label: 'Write' },
                  { value: 'preview', label: 'Preview' },
                ]}
                value={descriptionMode}
                onChange={setDescriptionMode}
              />
              <button
                type="button"
                onClick={saveBody}
                disabled={bodyDraft === item.body}
              >
                Save description
              </button>
            </div>

            {descriptionMode === 'write' ? (
              <textarea
                id="tasks-description"
                className="wiki-body-editor tasks-description-editor"
                aria-label="Work item description (Markdown)"
                value={bodyDraft}
                onChange={(event) => setBodyDraft(event.target.value)}
                placeholder="Add a description…"
              />
            ) : (
              <div
                className="wiki-preview"
                aria-label="Rendered description preview"
                dangerouslySetInnerHTML={{ __html: renderMarkdown(bodyDraft) }}
              />
            )}

            <section className="tasks-section">
              <h3 className="slideover-section-title">Checklist</h3>
              {detail && detail.checklist_items.length > 0 && (
                <ul className="wiki-link-list">
                  {detail.checklist_items.map((checklistEntry, index) => (
                    <li key={checklistEntry.id} className="tasks-checklist-row">
                      <label className="field-control-checkbox">
                        <input
                          type="checkbox"
                          checked={checklistEntry.is_completed}
                          onChange={() => toggleChecklistItem(checklistEntry)}
                        />
                        <span className={checklistEntry.is_completed ? 'tasks-done' : undefined}>
                          {checklistEntry.label}
                        </span>
                      </label>
                      <button
                        type="button"
                        aria-label="Move up"
                        disabled={index === 0}
                        onClick={() => moveChecklistItem(checklistEntry.id, -1)}
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        aria-label="Move down"
                        disabled={index === detail.checklist_items.length - 1}
                        onClick={() => moveChecklistItem(checklistEntry.id, 1)}
                      >
                        ↓
                      </button>
                      <button
                        type="button"
                        aria-label="Remove checklist item"
                        onClick={() => removeChecklistItem(checklistEntry.id)}
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {(!detail || detail.checklist_items.length === 0) && (
                <p className="view-empty">No checklist items yet.</p>
              )}
              <div className="wiki-add-link">
                <input
                  type="text"
                  placeholder="New checklist item…"
                  aria-label="New checklist item"
                  value={newChecklistLabel}
                  onChange={(event) => setNewChecklistLabel(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') submitChecklistItem()
                  }}
                />
                <button
                  type="button"
                  onClick={submitChecklistItem}
                  disabled={!newChecklistLabel.trim()}
                >
                  Add
                </button>
              </div>
            </section>

            <section className="tasks-section">
              <h3 className="slideover-section-title">Wiki links</h3>
              {detail && detail.linked_documents.length > 0 ? (
                <ul className="wiki-link-list">
                  {detail.linked_documents.map((link) => {
                    const linkedDocument = documents.find(
                      (candidate) => candidate.id === link.document_id,
                    )
                    return (
                      <li key={link.id}>
                        {linkedDocument?.title ?? link.document_id}
                        <button
                          type="button"
                          aria-label="Remove Wiki link"
                          onClick={() => removeLink(link.document_id)}
                        >
                          ×
                        </button>
                      </li>
                    )
                  })}
                </ul>
              ) : (
                <p className="view-empty">No Wiki pages linked yet.</p>
              )}
              <div className="wiki-add-link">
                <select
                  aria-label="Link a Wiki page"
                  value={linkTargetId}
                  onChange={(event) => setLinkTargetId(event.target.value)}
                >
                  <option value="">Link a page…</option>
                  {linkableDocuments.map((document) => (
                    <option key={document.id} value={document.id}>
                      {document.title}
                    </option>
                  ))}
                </select>
                <button type="button" onClick={submitLink} disabled={!linkTargetId}>
                  Add link
                </button>
              </div>
            </section>
          </div>
        </div>
      </div>
    )
  }

  // ---------- Board / List overview ----------
  return (
    <div className="tasks-view" aria-label="Tasks">
      <aside className="tasks-rail">
        <SegmentedControl
          ariaLabel="View mode"
          options={[
            { value: 'board', label: 'Board' },
            { value: 'list', label: 'List' },
          ]}
          value={viewMode}
          onChange={setViewMode}
        />
        <button type="button" className="wiki-new-page" onClick={() => setIsCreateOpen(true)}>
          + New work item
        </button>
        <input
          type="search"
          className="tasks-filter-input"
          aria-label="Filter work items"
          placeholder="Filter by title, assignee…"
          value={filterQuery}
          onChange={(event) => setFilterQuery(event.target.value)}
        />
        <label className="field-control-checkbox tasks-open-only">
          <input
            type="checkbox"
            checked={openOnly}
            onChange={(event) => setOpenOnly(event.target.checked)}
          />
          Open only
        </label>

        {loadError && (
          <p className="view-error" role="alert">
            {loadError}
          </p>
        )}

        {roots.length === 0 ? (
          <EmptyState
            title="No work yet"
            hint="Create your first epic, story, or task."
          />
        ) : (
          <div className="list-view">
            {roots.map((root) => (
              <WorkItemNode
                key={root.id}
                item={root}
                childrenOf={childrenOf}
                selectedId={selectedWorkItemId}
                onSelect={selectWorkItem}
              />
            ))}
          </div>
        )}
      </aside>

      <main className="tasks-main">
        {filteredItems.length === 0 ? (
          <EmptyState
            icon="📋"
            title="No work items found"
            hint="Adjust the filter, or create a new work item to start tracking."
          />
        ) : viewMode === 'board' ? (
          <div className="tasks-board" aria-label="Task board">
            {STATUS_OPTIONS.map((column) => {
              const columnItems = filteredItems.filter((entry) => entry.status === column.value)
              return (
                <section
                  key={column.value}
                  className={
                    dragOverStatus === column.value
                      ? 'tasks-column tasks-column-dragover'
                      : 'tasks-column'
                  }
                  aria-label={`${column.label} column`}
                  onDragOver={(event) => {
                    event.preventDefault()
                    setDragOverStatus(column.value)
                  }}
                  onDragLeave={() => setDragOverStatus(null)}
                  onDrop={(event) => {
                    event.preventDefault()
                    setDragOverStatus(null)
                    const droppedId = event.dataTransfer.getData('text/plain')
                    if (droppedId) void dropOnColumn(column.value, droppedId)
                  }}
                >
                  <header className={`tasks-column-header tasks-col-${column.value}`}>
                    <span className="tasks-column-dot" aria-hidden />
                    {column.label}
                    <span className="tasks-column-count">{columnItems.length}</span>
                  </header>
                  <div className="tasks-column-cards">
                    {columnItems.map((card) => (
                      <article
                        key={card.id}
                        className="task-card"
                        draggable
                        onDragStart={(event) => {
                          event.dataTransfer.setData('text/plain', card.id)
                          event.dataTransfer.effectAllowed = 'move'
                        }}
                      >
                        <button
                          type="button"
                          className="task-card-hit"
                          onClick={() => selectWorkItem(card.id)}
                          aria-label={`${card.kind}: ${card.title}`}
                        >
                          <span className="task-card-title">{card.title}</span>
                        </button>
                        <div className="task-card-meta">
                          {card.priority && (
                            <Pill tone={PRIORITY_TONE[card.priority]}>
                              {PRIORITY_LABEL[card.priority]}
                            </Pill>
                          )}
                          {card.due_date && (
                            <span className="task-card-due">due {card.due_date}</span>
                          )}
                          {card.assignee && (
                            <span className="pill pill-neutral">{card.assignee}</span>
                          )}
                        </div>
                      </article>
                    ))}
                  </div>
                  <input
                    type="text"
                    className="tasks-quick-add"
                    aria-label={`Quick add to ${column.label}`}
                    placeholder="+ Quick add"
                    onKeyDown={(event) => {
                      if (event.key !== 'Enter') return
                      const target = event.currentTarget
                      void quickAdd(column.value, target.value)
                      target.value = ''
                    }}
                  />
                </section>
              )
            })}
          </div>
        ) : (
          <div className="tasks-list" aria-label="Task list">
            {[...filteredItems]
              .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
              .map((row) => (
                <button
                  key={row.id}
                  type="button"
                  className="tasks-list-row"
                  onClick={() => selectWorkItem(row.id)}
                >
                  <span className="tasks-list-title">{row.title}</span>
                  <Pill tone={row.kind === 'epic' ? 'violet' : row.kind === 'story' ? 'brass' : 'teal'}>
                    {row.kind}
                  </Pill>
                  <Pill tone={STATUS_TONE[row.status]}>{STATUS_LABEL[row.status]}</Pill>
                  {row.priority ? (
                    <Pill tone={PRIORITY_TONE[row.priority]}>{PRIORITY_LABEL[row.priority]}</Pill>
                  ) : (
                    <span />
                  )}
                  <span className="tasks-list-side">{row.assignee ?? ''}</span>
                  <span className="tasks-list-side">{row.due_date ?? ''}</span>
                </button>
              ))}
          </div>
        )}
      </main>

      {isCreateOpen && (
        <div className="wiki-create-modal-backdrop" onClick={() => setIsCreateOpen(false)}>
          <div
            className="wiki-create-modal"
            role="dialog"
            aria-label="Create work item"
            onClick={(event) => event.stopPropagation()}
          >
            <h2>Create work item</h2>
            <label htmlFor="tasks-create-kind">Kind</label>
            <select
              id="tasks-create-kind"
              value={createDraft.kind}
              onChange={(event) =>
                setCreateDraft((current) => ({
                  ...current,
                  kind: event.target.value as WorkItemKind,
                  parentId: '',
                }))
              }
            >
              {KIND_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>

            {parentOptions.length > 0 && (
              <>
                <label htmlFor="tasks-create-parent">Parent</label>
                <select
                  id="tasks-create-parent"
                  value={createDraft.parentId}
                  onChange={(event) =>
                    setCreateDraft((current) => ({ ...current, parentId: event.target.value }))
                  }
                >
                  <option value="">None (standalone)</option>
                  {parentOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.title}
                    </option>
                  ))}
                </select>
              </>
            )}

            <label htmlFor="tasks-create-title">Title</label>
            <input
              id="tasks-create-title"
              type="text"
              value={createDraft.title}
              onChange={(event) =>
                setCreateDraft((current) => ({ ...current, title: event.target.value }))
              }
            />

            <label htmlFor="tasks-create-type">Type</label>
            <select
              id="tasks-create-type"
              value={createDraft.workType}
              onChange={(event) =>
                setCreateDraft((current) => ({
                  ...current,
                  workType: event.target.value as WorkItemType,
                }))
              }
            >
              {TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>

            {createError && (
              <p className="view-error" role="alert">
                {createError}
              </p>
            )}
            <div className="wiki-create-actions">
              <button type="button" onClick={submitCreate} disabled={!createDraft.title.trim()}>
                Create
              </button>
              <button
                type="button"
                onClick={() => {
                  setIsCreateOpen(false)
                  setCreateDraft(EMPTY_DRAFT)
                  setCreateError(null)
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function WorkItemNode({
  item,
  childrenOf,
  selectedId,
  onSelect,
}: {
  item: WorkItem
  childrenOf: (parentId: string | null) => WorkItem[]
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  const children = childrenOf(item.id)
  return (
    <div className="tasks-tree-node">
      <button
        type="button"
        className="node-row wiki-page-row"
        aria-current={item.id === selectedId}
        onClick={() => onSelect(item.id)}
      >
        <span className="node-row-title">{item.title}</span>
        <span className="node-row-meta">
          <span className="node-row-type">{item.kind}</span>
          <span className="node-row-type">{item.status}</span>
        </span>
      </button>
      {children.length > 0 && (
        <div className="tasks-tree-children">
          {children.map((child) => (
            <WorkItemNode
              key={child.id}
              item={child}
              childrenOf={childrenOf}
              selectedId={selectedId}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  )
}
