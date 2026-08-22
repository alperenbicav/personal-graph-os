import { useMemo, useState } from 'react'
import type { UpdateResourcePatch } from '../api/client'
import type { ResearchDashboard, Resource, ResourceKind, ResourceLifecycleStatus } from '../types'
import { EmptyState, Pill, type PillTone } from './ui'
import { ResearchDetailPanel as ResearchDetailSection } from './ResearchDetailPanel'

interface ResearchViewProps {
  dashboard: ResearchDashboard | null
  resources: Resource[]
  selectedNodeId: string | null
  onSelectNode: (nodeId: string) => void
  onClearSelection: () => void
  onCreateResource: (title: string, rawSource: string, kind: ResourceKind) => Promise<Resource>
  onArchiveResource: (resourceId: string) => Promise<void>
  onDeleteResource: (resourceId: string) => Promise<void>
  onUpdateDetail: (patch: UpdateResourcePatch) => Promise<boolean>
  /** Composed by App: the enrichment/evidence card for one resource. */
  renderEnrichment?: (resource: Resource) => React.ReactNode
  /** Composed by App: the guided workflow-chain proposal for the current selection. */
  workflowPanel?: React.ReactNode
}

function CreatePaperOrArticleForm({
  onCreateResource,
}: {
  onCreateResource: ResearchViewProps['onCreateResource']
}) {
  const [isOpen, setIsOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [rawSource, setRawSource] = useState('')
  const [kind, setKind] = useState<Extract<ResourceKind, 'paper' | 'article'>>('paper')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!isOpen) {
    return (
      <button type="button" className="primary-action" onClick={() => setIsOpen(true)}>
        + New paper/article
      </button>
    )
  }

  async function submit() {
    const trimmedTitle = title.trim()
    const trimmedSource = rawSource.trim()
    if (!trimmedTitle || !trimmedSource) return
    setIsSubmitting(true)
    setError(null)
    try {
      await onCreateResource(trimmedTitle, trimmedSource, kind)
      setIsOpen(false)
      setTitle('')
      setRawSource('')
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : String(submitError))
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="research-create-form" role="form" aria-label="Create paper or article">
      <label className="sr-only" htmlFor="research-create-title">
        Title
      </label>
      <input
        id="research-create-title"
        type="text"
        placeholder="Title"
        value={title}
        onChange={(event) => setTitle(event.target.value)}
      />
      <label className="sr-only" htmlFor="research-create-source">
        Source URL
      </label>
      <input
        id="research-create-source"
        type="text"
        placeholder="Source URL (arXiv, DOI, article link…)"
        value={rawSource}
        onChange={(event) => setRawSource(event.target.value)}
      />
      <select
        aria-label="Kind"
        className="wiki-chip-select"
        value={kind}
        onChange={(event) => setKind(event.target.value as 'paper' | 'article')}
      >
        <option value="paper">Paper</option>
        <option value="article">Article</option>
      </select>
      <button type="button" onClick={submit} disabled={isSubmitting || !title.trim() || !rawSource.trim()}>
        {isSubmitting ? 'Adding…' : 'Add'}
      </button>
      <button type="button" onClick={() => setIsOpen(false)}>
        Cancel
      </button>
      {error && (
        <p className="view-error" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

type BucketKey = 'all' | keyof ResearchDashboard

// Guided-workflow buckets (ST-04.4) become filter chips over one canonical list rather than
// their own duplicate row lists (review finding S6-F03): a resource that is simultaneously
// "Inbox" and "Needs Takeaway" must still render as exactly one selectable row.
const BUCKETS: { key: BucketKey; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'inbox', label: 'Inbox' },
  { key: 'continue_reading', label: 'Continue Reading' },
  { key: 'stale', label: 'Stale' },
  { key: 'needs_takeaway', label: 'Needs Takeaway' },
  { key: 'unlinked', label: 'Unlinked' },
  { key: 'applied', label: 'Applied' },
]

const TYPE_OPTIONS: { value: '' | ResourceKind; label: string }[] = [
  { value: '', label: 'All types' },
  { value: 'paper', label: 'Papers' },
  { value: 'article', label: 'Articles' },
]

// Research is the Papers/Articles workspace (ST-06); every other ResourceKind (GitHub repos,
// docs, specs, datasets, video, books) belongs to Repositories or a future workspace, never here
// (review finding S6-F04).
const ADMITTED_KINDS: ResourceKind[] = ['paper', 'article']

function isAdmittedKind(resource: Resource): boolean {
  return ADMITTED_KINDS.includes(resource.kind)
}

const READ_STATE_OPTIONS: { value: '' | ResourceLifecycleStatus; label: string }[] = [
  { value: '', label: 'Any read state' },
  { value: 'inbox', label: 'Inbox' },
  { value: 'to_review', label: 'To review' },
  { value: 'reading', label: 'Reading' },
  { value: 'paused', label: 'Paused' },
  { value: 'reviewed', label: 'Reviewed' },
  { value: 'applied', label: 'Applied' },
  { value: 'archived', label: 'Archived' },
]

const LIFECYCLE_TONE: Record<ResourceLifecycleStatus, PillTone> = {
  inbox: 'teal',
  to_review: 'violet',
  reading: 'brass',
  paused: 'neutral',
  reviewed: 'moss',
  applied: 'moss',
  archived: 'neutral',
}

function formatDay(iso: string): string {
  return iso.slice(0, 10)
}

/** Readwise-style rich row: prominent title, lifecycle/kind pills, progress bar, next-action
 * hint, and hover-revealed destructive actions. */
function ResourceRow({
  resource,
  isSelected,
  onSelect,
  onArchiveResource,
  onDeleteResource,
}: {
  resource: Resource
  isSelected: boolean
  onSelect: (nodeId: string) => void
  onArchiveResource: (resourceId: string) => Promise<void>
  onDeleteResource: (resourceId: string) => Promise<void>
}) {
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const isArchived = resource.lifecycle_status === 'archived'

  return (
    <div className={`research-row${isSelected ? ' research-row-selected' : ''}`}>
      {/* The canonical-source link must never nest inside the row-selecting control
          (S6-F02): the wrapper handles clicks, the title is its keyboard-accessible button,
          and the link sits beside it as a sibling. */}
      <div
        role="presentation"
        className="research-row-main"
        onClick={() => onSelect(resource.node_id)}
      >
        <span className="research-row-titleline">
          <button
            type="button"
            className="research-row-title"
            onClick={(event) => {
              event.stopPropagation()
              onSelect(resource.node_id)
            }}
          >
            {resource.title}
          </button>
          {resource.source_url && (
            <a
              href={resource.source_url}
              target="_blank"
              rel="noreferrer"
              className="research-row-source"
              onClick={(event) => event.stopPropagation()}
            >
              source ↗
            </a>
          )}
        </span>
        {resource.next_action && !resource.next_action_dismissed && (
          <span className="research-row-next">→ {resource.next_action}</span>
        )}
        <span className="research-row-meta">
          <Pill tone={LIFECYCLE_TONE[resource.lifecycle_status]}>{resource.lifecycle_status}</Pill>
          <span className="pill pill-neutral">{resource.kind}</span>
          {resource.progress_percent != null && (
            <span className="research-progress" aria-label={`Progress ${resource.progress_percent}%`}>
              <span
                className="research-progress-fill"
                style={{ width: `${resource.progress_percent}%` }}
              />
            </span>
          )}
          <span className="research-row-date">{formatDay(resource.last_activity_at)}</span>
        </span>
      </div>
      <span className="research-row-actions">
        {!isArchived && (
          <button type="button" onClick={() => void onArchiveResource(resource.id)}>
            Archive
          </button>
        )}
        {confirmingDelete ? (
          <span className="node-row-confirm">
            <button type="button" onClick={() => void onDeleteResource(resource.id)}>
              Delete forever
            </button>
            <button type="button" onClick={() => setConfirmingDelete(false)}>
              Cancel
            </button>
          </span>
        ) : (
          <button type="button" onClick={() => setConfirmingDelete(true)}>
            Delete…
          </button>
        )}
      </span>
    </div>
  )
}

export function ResearchView({
  dashboard,
  resources,
  selectedNodeId,
  onSelectNode,
  onClearSelection,
  onCreateResource,
  onArchiveResource,
  onDeleteResource,
  onUpdateDetail,
  renderEnrichment,
  workflowPanel,
}: ResearchViewProps) {
  const [bucket, setBucket] = useState<BucketKey>('all')
  const [kindFilter, setKindFilter] = useState<'' | ResourceKind>('')
  const [readStateFilter, setReadStateFilter] = useState<'' | ResourceLifecycleStatus>('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')

  const bucketMemberIds = useMemo(() => {
    if (bucket === 'all' || !dashboard) return null
    return new Set(dashboard[bucket].map((resource) => resource.id))
  }, [bucket, dashboard])

  const filtered = useMemo(() => {
    return resources
      .filter(isAdmittedKind)
      .filter((resource) => !bucketMemberIds || bucketMemberIds.has(resource.id))
      .filter((resource) => !kindFilter || resource.kind === kindFilter)
      .filter((resource) => !readStateFilter || resource.lifecycle_status === readStateFilter)
      .filter((resource) => !dateFrom || resource.last_activity_at.slice(0, 10) >= dateFrom)
      .filter((resource) => !dateTo || resource.last_activity_at.slice(0, 10) <= dateTo)
  }, [resources, bucketMemberIds, kindFilter, readStateFilter, dateFrom, dateTo])

  // The selected resource (by backing node id) gets a full-page detail view, Readwise-style.
  const selectedResource =
    (selectedNodeId && resources.find((candidate) => candidate.node_id === selectedNodeId)) ||
    null

  if (!dashboard) {
    return <p className="view-empty">Loading research dashboard…</p>
  }

  if (selectedResource && isAdmittedKind(selectedResource)) {
    const isArchived = selectedResource.lifecycle_status === 'archived'
    return (
      <div className="research-detail-page" aria-label="Research detail">
        <div className="tasks-detail-top">
          <button type="button" className="tasks-back" onClick={onClearSelection}>
            ← Back to library
          </button>
          <Pill tone={LIFECYCLE_TONE[selectedResource.lifecycle_status]}>
            {selectedResource.lifecycle_status}
          </Pill>
          <span className="pill pill-neutral">{selectedResource.kind}</span>
          {!isArchived && (
            <button
              type="button"
              className="wiki-options-button"
              style={{ marginLeft: 'auto' }}
              onClick={() => void onArchiveResource(selectedResource.id)}
            >
              Archive
            </button>
          )}
        </div>

        <h1 className="research-detail-title">{selectedResource.title}</h1>

        <div className="wiki-editor-bar">
          {workflowPanel}
          {selectedResource.source_url && (
            <a
              href={selectedResource.source_url}
              target="_blank"
              rel="noreferrer"
              className="tasks-back"
            >
              Open source ↗
            </a>
          )}
        </div>

        {/* Existing field editors (lifecycle, next action, progress, review date, takeaways,
            open questions) — reused verbatim, restyled by the shared design-system CSS. */}
        <div className="research-detail-fields">
          <ResearchDetailSection resource={selectedResource} onUpdate={onUpdateDetail} />
        </div>

        {renderEnrichment?.(selectedResource)}
      </div>
    )
  }

  return (
    <div className="research-view" aria-label="Research dashboard">
      <div className="page-header">
        <div className="page-header-text">
          <h1 className="page-header-title">Research library</h1>
          <p className="page-header-description">
            Papers and articles you are reading — capture, triage, and turn takeaways into
            decisions.
          </p>
        </div>
        <div className="page-header-actions">
          <CreatePaperOrArticleForm onCreateResource={onCreateResource} />
        </div>
      </div>

      <div className="research-tabs" role="group" aria-label="Filter by workflow bucket">
        {BUCKETS.map((option) => (
          <button
            key={option.key}
            type="button"
            className={
              bucket === option.key ? 'research-tab research-tab-active' : 'research-tab'
            }
            aria-current={bucket === option.key}
            onClick={() => setBucket(option.key)}
          >
            {option.label}
            {option.key !== 'all' && (
              <span className="research-section-count">
                {' '}
                {dashboard[option.key].filter(isAdmittedKind).length}
              </span>
            )}
          </button>
        ))}
      </div>

      <div className="research-filter-bar">
        <select
          aria-label="Filter by type"
          className="wiki-chip-select"
          value={kindFilter}
          onChange={(event) => setKindFilter(event.target.value as '' | ResourceKind)}
        >
          {TYPE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by read state"
          className="wiki-chip-select"
          value={readStateFilter}
          onChange={(event) => setReadStateFilter(event.target.value as '' | ResourceLifecycleStatus)}
        >
          {READ_STATE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <input
          type="date"
          aria-label="From date"
          value={dateFrom}
          onChange={(event) => setDateFrom(event.target.value)}
        />
        <input
          type="date"
          aria-label="To date"
          value={dateTo}
          onChange={(event) => setDateTo(event.target.value)}
        />
      </div>

      <div className="research-section-header">
        {BUCKETS.find((option) => option.key === bucket)?.label}{' '}
        <span className="research-section-count">{filtered.length}</span>
      </div>
      {filtered.length === 0 ? (
        <EmptyState
          title="Nothing here"
          hint="Adjust the filters above, or add a new paper/article."
        />
      ) : (
        <div className="list-view">
          {filtered.map((resource) => (
            <ResourceRow
              key={resource.id}
              resource={resource}
              isSelected={resource.node_id === selectedNodeId}
              onSelect={onSelectNode}
              onArchiveResource={onArchiveResource}
              onDeleteResource={onDeleteResource}
            />
          ))}
        </div>
      )}
    </div>
  )
}
