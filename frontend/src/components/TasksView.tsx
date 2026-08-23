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
  onRenameWorkItem: (item: WorkItem, title: string) => Promise<void>
  onNavigateDocument?: (documentId: string) => void
  onNavigateRepository?: (nodeId: string) => void
}

type ViewMode = 'board' | 'list'

export function TasksView({
  workspaceId,
  workItems,
  documents,
  repositories: _repositories,
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
  onNavigateRepository: _onNavigateRepository,
}: TasksViewProps) {
  const [viewMode, setViewMode] = useState<ViewMode>('board')
  const [filterQuery, setFilterQuery] = useState('')
  const [openOnly, setOpenOnly] = useState(true)
  const [dragOverStatus, setDragOverStatus] = useState<WorkItemStatus | null>(null)
  const [detail, setDetail] = useState<WorkItemDetail | null>(null)
  const [bodyDraft, setBodyDraft] = useState('')
  const [descriptionMode, setDescriptionMode] = useState<'write' | 'preview'>('write')
  const [loadError, setLoadError] = useState<string | null>(null)
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

  async function quickAdd(status: WorkItemStatus, title: string, kind: WorkItemKind = 'task', workType: WorkItemType = 'feature', parentId: string | null = null) {
    const trimmed = title.trim()
    if (!trimmed) return
    try {
      const created = await onCreateWorkItem({
        workspace_id: workspaceId,
        kind,
        work_type: workType,
        title: trimmed,
        status,
        source: REST_ACTOR_NAME,
        parent_id: parentId,
      })
      selectWorkItem(created.id)
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

  async function moveChecklistItem(fromIndex: number, toIndex: number) {
    if (!selectedWorkItemId || !detail) return
    const items = [...detail.checklist_items]
    if (toIndex < 0 || toIndex >= items.length) return
    const [moved] = items.splice(fromIndex, 1)
    items.splice(toIndex, 0, moved)
    const orderedIds = items.map((item) => item.id)
    try {
      const reordered = await onReorderChecklistItems(selectedWorkItemId, orderedIds)
      setDetail({ ...detail, checklist_items: reordered })
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  async function attachDocument() {
    if (!selectedWorkItemId || !linkTargetId) return
    try {
      await onAttachDocument(selectedWorkItemId, linkTargetId)
      setLinkTargetId('')
      const updated = await onLoadDetail(selectedWorkItemId)
      setDetail(updated)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  async function detachDocument(documentId: string) {
    if (!selectedWorkItemId) return
    try {
      await onDetachDocument(selectedWorkItemId, documentId)
      const updated = await onLoadDetail(selectedWorkItemId)
      setDetail(updated)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  const isDirty = detail !== null && bodyDraft !== detail.work_item.body

  return (
    <div className="tasks-layout" aria-label="Tasks Workspace">
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

        <div style={{ marginTop: 10, marginBottom: 10 }}>
          <input
            type="text"
            className="v2-rail-quickadd-input"
            placeholder="+ Quick add task… (Enter)"
            aria-label="Quick add task"
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                const val = e.currentTarget.value.trim()
                if (val) {
                  void quickAdd('backlog', val)
                  e.currentTarget.value = ''
                }
              }
            }}
          />
        </div>

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
            hint="Type above and press Enter to create your first task."
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
        {viewMode === 'list' && (
          <div className="v2-tasks-list-quickadd" style={{ padding: '0 0 14px 0' }}>
            <input
              type="text"
              className="v2-list-quickadd-input"
              aria-label="Add a work item to list"
              placeholder="+ Add a work item to backlog… (Press Enter to add)"
              onKeyDown={(event) => {
                if (event.key !== 'Enter') return
                const target = event.currentTarget
                if (target.value.trim()) {
                  void quickAdd('backlog', target.value)
                  target.value = ''
                }
              }}
            />
          </div>
        )}

        {filteredItems.length === 0 ? (
          <EmptyState
            title="No work items found"
            hint="Adjust the filter, or type in the composer above to start tracking."
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
                    placeholder={`+ Add to ${column.label}…`}
                    onKeyDown={(event) => {
                      if (event.key !== 'Enter') return
                      const target = event.currentTarget
                      if (target.value.trim()) {
                        void quickAdd(column.value, target.value)
                        target.value = ''
                      }
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

      {/* Detail Slide-Over */}
      {selectedWorkItem && (
        <aside className="tasks-detail" aria-label="Work item detail">
          <header className="tasks-detail-header">
            <input
              type="text"
              className="tasks-detail-title-input"
              aria-label="Work item title"
              value={selectedWorkItem.title}
              onChange={(event) => void onRenameWorkItem(selectedWorkItem, event.target.value)}
            />
            <button
              type="button"
              className="tasks-detail-close"
              aria-label="Close detail"
              onClick={() => selectWorkItem(null)}
            >
              ×
            </button>
          </header>

          <div className="tasks-detail-body">
            <div className="tasks-field-row">
              <label htmlFor="tasks-edit-status">Status</label>
              <select
                id="tasks-edit-status"
                aria-label="Status"
                value={selectedWorkItem.status}
                onChange={(event) =>
                  void commitUpdate({ status: event.target.value as WorkItemStatus })
                }
              >
                {STATUS_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="tasks-field-row">
              <label>Kind</label>
              <div style={{ display: 'flex', alignItems: 'center' }}>
                <Pill tone={selectedWorkItem.kind === 'epic' ? 'violet' : selectedWorkItem.kind === 'story' ? 'brass' : 'teal'}>
                  {selectedWorkItem.kind}
                </Pill>
              </div>
            </div>

            <div className="tasks-field-row">
              <label htmlFor="tasks-edit-priority">Priority</label>
              <select
                id="tasks-edit-priority"
                aria-label="Priority"
                value={selectedWorkItem.priority ?? ''}
                onChange={(event) =>
                  void commitUpdate({
                    priority: (event.target.value as WorkItemPriority) || null,
                  })
                }
              >
                <option value="">None</option>
                {PRIORITY_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="tasks-field-row">
              <label htmlFor="tasks-edit-assignee">Assignee</label>
              <input
                id="tasks-edit-assignee"
                type="text"
                value={selectedWorkItem.assignee ?? ''}
                placeholder="Assignee username"
                onChange={(event) =>
                  void commitUpdate({ assignee: event.target.value.trim() || null })
                }
              />
            </div>

            <div className="tasks-field-row">
              <label htmlFor="tasks-edit-due-date">Due date</label>
              <input
                id="tasks-edit-due-date"
                type="date"
                value={selectedWorkItem.due_date ?? ''}
                onChange={(event) =>
                  void commitUpdate({ due_date: event.target.value || null })
                }
              />
            </div>

            <div className="tasks-field-row">
              <label htmlFor="tasks-edit-progress">Progress %</label>
              <input
                id="tasks-edit-progress"
                type="number"
                min={0}
                max={100}
                value={selectedWorkItem.progress_percent ?? ''}
                onChange={(event) => {
                  const val = event.target.value === '' ? undefined : Number(event.target.value)
                  void commitUpdate({ progress_percent: val })
                }}
              />
            </div>

            <div className="tasks-field-row">
              <label htmlFor="tasks-edit-blockers">Blockers</label>
              <input
                id="tasks-edit-blockers"
                type="text"
                value={selectedWorkItem.blockers ?? ''}
                placeholder="Summary of blockers"
                onChange={(event) =>
                  void commitUpdate({ blockers: event.target.value.trim() || null })
                }
              />
            </div>

            {/* Checklist Section */}
            <section className="tasks-checklist-section" aria-label="Checklist">
              <h3>Checklist</h3>
              <div className="tasks-checklist-items">
                {(detail?.checklist_items ?? []).map((item, index) => (
                  <div key={item.id} className="tasks-checklist-row">
                    <input
                      type="checkbox"
                      checked={item.is_completed}
                      onChange={() => void toggleChecklistItem(item)}
                      aria-label={`Mark ${item.label} complete`}
                    />
                    <span className={item.is_completed ? 'tasks-checklist-done' : ''}>
                      {item.label}
                    </span>
                    <div className="tasks-checklist-actions">
                      <button
                        type="button"
                        disabled={index === 0}
                        onClick={() => void moveChecklistItem(index, index - 1)}
                        aria-label={`Move ${item.label} up`}
                      >
                        ▲
                      </button>
                      <button
                        type="button"
                        disabled={index === (detail?.checklist_items.length ?? 0) - 1}
                        onClick={() => void moveChecklistItem(index, index + 1)}
                        aria-label={`Move ${item.label} down`}
                      >
                        ▼
                      </button>
                      <button
                        type="button"
                        onClick={() => void removeChecklistItem(item.id)}
                        aria-label={`Delete ${item.label}`}
                      >
                        ×
                      </button>
                    </div>
                  </div>
                ))}
              </div>
              <div className="tasks-checklist-add">
                <input
                  type="text"
                  placeholder="New checklist item…"
                  aria-label="New checklist item"
                  value={newChecklistLabel}
                  onChange={(event) => setNewChecklistLabel(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') void submitChecklistItem()
                  }}
                />
                <button type="button" onClick={() => void submitChecklistItem()}>
                  Add
                </button>
              </div>
            </section>

            {/* Linked Documents */}
            <section className="tasks-links-section" aria-label="Linked documents">
              <h3>Linked Documents</h3>
              <div className="tasks-linked-docs">
                {(detail?.linked_documents ?? []).map((link) => {
                  const doc = documents.find((d) => d.id === link.document_id)
                  return (
                    <div key={link.id} className="tasks-link-row">
                      <button
                        type="button"
                        className="tasks-link-btn"
                        onClick={() => onNavigateDocument?.(link.document_id)}
                      >
                        📄 {doc?.title || link.document_id}
                      </button>
                      <button
                        type="button"
                        onClick={() => void detachDocument(link.document_id)}
                        aria-label={`Detach ${doc?.title || link.document_id}`}
                      >
                        ×
                      </button>
                    </div>
                  )
                })}
              </div>
              {linkableDocuments.length > 0 && (
                <div className="tasks-link-add">
                  <select
                    value={linkTargetId}
                    onChange={(event) => setLinkTargetId(event.target.value)}
                    aria-label="Document to attach"
                  >
                    <option value="">Attach document…</option>
                    {linkableDocuments.map((doc) => (
                      <option key={doc.id} value={doc.id}>
                        {doc.title}
                      </option>
                    ))}
                  </select>
                  <button type="button" onClick={() => void attachDocument()} disabled={!linkTargetId}>
                    Attach
                  </button>
                </div>
              )}
            </section>

            {/* Body / Description Section */}
            <section className="tasks-body-section" aria-label="Description">
              <div className="tasks-body-header">
                <h3>Description</h3>
                <SegmentedControl
                  ariaLabel="Description mode"
                  options={[
                    { value: 'write', label: 'Write' },
                    { value: 'preview', label: 'Preview' },
                  ]}
                  value={descriptionMode}
                  onChange={(val) => setDescriptionMode(val as 'write' | 'preview')}
                />
                {isDirty && (
                  <button type="button" className="primary-action" onClick={() => void saveBody()}>
                    Save description
                  </button>
                )}
              </div>

              {descriptionMode === 'write' ? (
                <textarea
                  className="tasks-body-textarea"
                  aria-label="Work item description"
                  value={bodyDraft}
                  onChange={(event) => setBodyDraft(event.target.value)}
                  placeholder="Add detailed markdown specifications, acceptance criteria, or logs…"
                />
              ) : (
                <div
                  className="tasks-body-preview markdown-body"
                  dangerouslySetInnerHTML={{ __html: renderMarkdown(bodyDraft || '*No description*') }}
                />
              )}
            </section>
          </div>
        </aside>
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
  const isSelected = selectedId === item.id

  return (
    <div className="tree-node">
      <button
        type="button"
        className={`tree-node-row ${isSelected ? 'tree-node-selected' : ''}`}
        onClick={() => onSelect(item.id)}
        aria-selected={isSelected}
      >
        <span className="tree-node-icon">
          {item.kind === 'epic' ? '🟣' : item.kind === 'story' ? '🟡' : '🟢'}
        </span>
        <span className="tree-node-title">{item.title}</span>
        <Pill tone={STATUS_TONE[item.status]}>{STATUS_LABEL[item.status]}</Pill>
      </button>
      {children.length > 0 && (
        <div className="tree-children">
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
