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
}: TasksViewProps) {
  const [detail, setDetail] = useState<WorkItemDetail | null>(null)
  const [bodyDraft, setBodyDraft] = useState('')
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

  async function toggleChecklistItem(item: WorkItemChecklistItem) {
    try {
      const updated = await onUpdateChecklistItem(item.id, {
        is_completed: !item.is_completed,
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
            .map((item, index) => ({ ...item, position: index })),
        })
      }
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  async function moveChecklistItem(itemId: string, direction: -1 | 1) {
    if (!selectedWorkItemId || !detail) return
    const items = detail.checklist_items
    const from = items.findIndex((item) => item.id === itemId)
    const to = from + direction
    if (from < 0 || to < 0 || to >= items.length) return
    const reordered = [...items]
    const [moved] = reordered.splice(from, 1)
    reordered.splice(to, 0, moved)
    try {
      const persisted = await onReorderChecklistItems(
        selectedWorkItemId,
        reordered.map((item) => item.id),
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

  return (
    <div className="wiki-view" aria-label="Tasks">
      <div className="wiki-tree">
        <button type="button" className="wiki-new-page" onClick={() => setIsCreateOpen(true)}>
          + New work item
        </button>

        {loadError && (
          <p className="view-error" role="alert">
            {loadError}
          </p>
        )}
        {roots.length === 0 ? (
          <p className="view-empty">No epics yet — start one with “+ New work item”.</p>
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
      </div>

      {!item ? (
        <div className="wiki-editor view-empty">Select a work item, or create a new one.</div>
      ) : (
        <div className="wiki-editor" key={item.id}>
          <div className="wiki-editor-toolbar">
            <h2 className="wiki-document-title">{item.title}</h2>
            <span className="node-row-type">{item.kind}</span>
            <button type="button" onClick={saveBody} disabled={bodyDraft === item.body}>
              Save description
            </button>
          </div>

          <div className="tasks-fields">
            <label htmlFor="tasks-type">Type</label>
            <select
              id="tasks-type"
              value={item.work_type}
              onChange={(event) => commitUpdate({ work_type: event.target.value as WorkItemType })}
            >
              {TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>

            <label htmlFor="tasks-status">Status</label>
            <select
              id="tasks-status"
              value={item.status}
              onChange={(event) => commitUpdate({ status: event.target.value as WorkItemStatus })}
            >
              {STATUS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>

            <label htmlFor="tasks-priority">Priority</label>
            <select
              id="tasks-priority"
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

            <label htmlFor="tasks-due-date">Due date</label>
            <input
              id="tasks-due-date"
              type="date"
              value={item.due_date ?? ''}
              onChange={(event) =>
                event.target.value
                  ? commitUpdate({ due_date: event.target.value })
                  : commitUpdate({ clear_due_date: true })
              }
            />

            <label htmlFor="tasks-assignee">Assignee</label>
            <input
              id="tasks-assignee"
              type="text"
              defaultValue={item.assignee ?? ''}
              onBlur={(event) => {
                const value = event.target.value.trim()
                commitUpdate(value ? { assignee: value } : { clear_assignee: true })
              }}
            />

            <label htmlFor="tasks-blockers">Blockers</label>
            <input
              id="tasks-blockers"
              type="text"
              defaultValue={item.blockers ?? ''}
              onBlur={(event) => {
                const value = event.target.value.trim()
                commitUpdate(value ? { blockers: value } : { clear_blockers: true })
              }}
            />

            <label htmlFor="tasks-progress">Progress (%)</label>
            <input
              id="tasks-progress"
              type="number"
              min={0}
              max={100}
              defaultValue={item.progress_percent ?? ''}
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

            <label htmlFor="tasks-repository">Repository</label>
            <select
              id="tasks-repository"
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
          </div>

          <label className="field-label" htmlFor="tasks-description">
            Description (Markdown)
          </label>
          <textarea
            id="tasks-description"
            className="wiki-editor-raw"
            aria-label="Work item description (Markdown)"
            value={bodyDraft}
            onChange={(event) => setBodyDraft(event.target.value)}
          />
        </div>
      )}

      {detail && (
        <div className="wiki-inspector">
          <h3>Checklist</h3>
          {detail.checklist_items.length === 0 ? (
            <p className="view-empty">No checklist items yet.</p>
          ) : (
            <ul className="wiki-link-list">
              {detail.checklist_items.map((checklistItem, index) => (
                <li key={checklistItem.id} className="tasks-checklist-row">
                  <label className="field-control-checkbox">
                    <input
                      type="checkbox"
                      checked={checklistItem.is_completed}
                      onChange={() => toggleChecklistItem(checklistItem)}
                    />
                    <span className={checklistItem.is_completed ? 'tasks-done' : undefined}>
                      {checklistItem.label}
                    </span>
                  </label>
                  <button
                    type="button"
                    aria-label="Move up"
                    disabled={index === 0}
                    onClick={() => moveChecklistItem(checklistItem.id, -1)}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    aria-label="Move down"
                    disabled={index === detail.checklist_items.length - 1}
                    onClick={() => moveChecklistItem(checklistItem.id, 1)}
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    aria-label="Remove checklist item"
                    onClick={() => removeChecklistItem(checklistItem.id)}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
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
            <button type="button" onClick={submitChecklistItem} disabled={!newChecklistLabel.trim()}>
              Add
            </button>
          </div>

          <h3>Wiki links</h3>
          {detail.linked_documents.length === 0 ? (
            <p className="view-empty">No Wiki pages linked yet.</p>
          ) : (
            <ul className="wiki-link-list">
              {detail.linked_documents.map((link) => {
                const document = documents.find((candidate) => candidate.id === link.document_id)
                return (
                  <li key={link.id}>
                    {document?.title ?? link.document_id}
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
        </div>
      )}

      {isCreateOpen && (
        <div className="wiki-create-modal" role="dialog" aria-label="Create work item">
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
