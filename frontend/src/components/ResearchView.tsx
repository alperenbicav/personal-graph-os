import { useMemo, useState } from 'react'
import type { ResearchDashboard, Resource, ResourceKind, ResourceLifecycleStatus } from '../types'

interface ResearchViewProps {
  dashboard: ResearchDashboard | null
  resources: Resource[]
  selectedNodeId: string | null
  onSelectNode: (nodeId: string) => void
}

type BucketKey = 'all' | keyof ResearchDashboard

// Guided-workflow buckets (ST-04.4) become filter chips over one canonical list rather than
// their own duplicate row lists (review finding S6-F03): a resource that is simultaneously
// "Inbox" and "Needs Takeaway" must still render as exactly one selectable row.
const BUCKETS: { key: BucketKey; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'inbox', label: 'Research Inbox' },
  { key: 'continue_reading', label: 'Continue Reading' },
  { key: 'stale', label: 'Stale Resources' },
  { key: 'needs_takeaway', label: 'Needs Takeaway' },
  { key: 'unlinked', label: 'Unlinked Research' },
  { key: 'applied', label: 'Applied Sources' },
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

function ResourceRow({
  resource,
  isSelected,
  onSelect,
}: {
  resource: Resource
  isSelected: boolean
  onSelect: (nodeId: string) => void
}) {
  return (
    <div className="node-row" aria-current={isSelected}>
      <button type="button" className="node-row-title" onClick={() => onSelect(resource.node_id)}>
        {resource.title}
      </button>
      <span className="node-row-meta">
        {resource.source_url && (
          <a href={resource.source_url} target="_blank" rel="noreferrer">
            source
          </a>
        )}
        <span className="node-row-type">{resource.kind}</span>
        <span className="node-row-lifecycle">{resource.lifecycle_status}</span>
      </span>
    </div>
  )
}

export function ResearchView({ dashboard, resources, selectedNodeId, onSelectNode }: ResearchViewProps) {
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

  if (!dashboard) {
    return <p className="view-empty">Loading research dashboard…</p>
  }

  return (
    <div className="research-view" aria-label="Research dashboard">
      <div className="research-filter-bar" role="group" aria-label="Filter by workflow bucket">
        {BUCKETS.map((option) => (
          <button
            key={option.key}
            type="button"
            className="node-row-label"
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
        <p className="view-empty">Nothing here.</p>
      ) : (
        <div className="list-view">
          {filtered.map((resource) => (
            <ResourceRow
              key={resource.id}
              resource={resource}
              isSelected={resource.node_id === selectedNodeId}
              onSelect={onSelectNode}
            />
          ))}
        </div>
      )}
    </div>
  )
}
