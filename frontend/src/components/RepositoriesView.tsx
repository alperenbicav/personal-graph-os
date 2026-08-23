import { useMemo, useState } from 'react'
import type { UpdateResourcePatch } from '../api/client'
import type { RepositoryLabel, Resource, ResourceLifecycleStatus } from '../types'
import { EmptyState, Pill, type PillTone } from './ui'
import { ResearchDetailPanel } from './ResearchDetailPanel'

interface RepositoriesViewProps {
  resources: Resource[]
  selectedNodeId: string | null
  onSelectNode: (nodeId: string) => void
  onClearSelection: () => void
  onSetLabel: (resourceId: string, label: RepositoryLabel) => void
  onUpdateDetail: (patch: UpdateResourcePatch) => Promise<boolean>
  onCreateResource: (title: string, rawSource: string) => Promise<Resource>
  /** Composed by App: the enrichment/evidence card for one resource. */
  renderEnrichment?: (resource: Resource) => React.ReactNode
}

function CreateRepositoryForm({
  onCreateResource,
}: {
  onCreateResource: RepositoriesViewProps['onCreateResource']
}) {
  const [isOpen, setIsOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [rawSource, setRawSource] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!isOpen) {
    return (
      <button type="button" className="primary-action" onClick={() => setIsOpen(true)}>
        + New repository
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
      await onCreateResource(trimmedTitle, trimmedSource)
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
    <div className="research-create-form" role="form" aria-label="Create repository">
      <label className="sr-only" htmlFor="repository-create-title">
        Title
      </label>
      <input
        id="repository-create-title"
        type="text"
        placeholder="Title"
        value={title}
        onChange={(event) => setTitle(event.target.value)}
      />
      <label className="sr-only" htmlFor="repository-create-source">
        GitHub URL
      </label>
      <input
        id="repository-create-source"
        type="text"
        placeholder="https://github.com/owner/repo"
        value={rawSource}
        onChange={(event) => setRawSource(event.target.value)}
      />
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

const LABEL_FILTERS: { value: '' | RepositoryLabel; label: string }[] = [
  { value: '', label: 'All' },
  { value: 'personal', label: 'Personal' },
  { value: 'apilex', label: 'Apilex' },
  { value: 'liked_external', label: 'Liked external' },
]

const LABEL_OPTIONS: { value: RepositoryLabel; label: string }[] = [
  { value: 'personal', label: 'Personal' },
  { value: 'apilex', label: 'Apilex' },
  { value: 'liked_external', label: 'Liked external' },
]

const LABEL_TONE: Record<RepositoryLabel, PillTone> = {
  personal: 'violet',
  apilex: 'brass',
  liked_external: 'teal',
}

const LIFECYCLE_TONE: Record<ResourceLifecycleStatus, PillTone> = {
  inbox: 'teal',
  to_review: 'violet',
  reading: 'brass',
  paused: 'neutral',
  reviewed: 'moss',
  applied: 'moss',
  archived: 'neutral',
}

function RepositoryRow({
  resource,
  isSelected,
  onSelect,
  onSetLabel,
}: {
  resource: Resource
  isSelected: boolean
  onSelect: (nodeId: string) => void
  onSetLabel: (resourceId: string, label: RepositoryLabel) => void
}) {
  return (
    <div className={`research-row${isSelected ? ' research-row-selected' : ''}`}>
      {/* Same structural invariant as research rows (S6-F02): the external link is a sibling
          of the row-selecting title button, never nested inside it. */}
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
            <a href={resource.source_url} target="_blank" rel="noreferrer" className="research-row-source">
              github ↗
            </a>
          )}
        </span>
        <span className="research-row-meta">
          {resource.repository_label ? (
            <Pill tone={LABEL_TONE[resource.repository_label]}>{resource.repository_label}</Pill>
          ) : (
            <Pill tone="neutral">unlabeled</Pill>
          )}
          <Pill tone={LIFECYCLE_TONE[resource.lifecycle_status]}>{resource.lifecycle_status}</Pill>
        </span>
      </div>
      <select
        aria-label={`Ownership label for ${resource.title}`}
        className="chip-select"
        value={resource.repository_label ?? ''}
        onChange={(event) => onSetLabel(resource.id, event.target.value as RepositoryLabel)}
      >
        <option value="" disabled>
          Set label…
        </option>
        {LABEL_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  )
}

export function RepositoriesView({
  resources,
  selectedNodeId,
  onSelectNode,
  onClearSelection,
  onSetLabel,
  onUpdateDetail,
  onCreateResource,
  renderEnrichment,
}: RepositoriesViewProps) {
  const [labelFilter, setLabelFilter] = useState<'' | RepositoryLabel>('')

  const repositories = useMemo(
    () => resources.filter((resource) => resource.kind === 'github_repository'),
    [resources],
  )

  const filtered = useMemo(
    () => repositories.filter((resource) => !labelFilter || resource.repository_label === labelFilter),
    [repositories, labelFilter],
  )

  const selectedRepository =
    (selectedNodeId && repositories.find((candidate) => candidate.node_id === selectedNodeId)) ||
    null

  if (selectedRepository) {
    return (
      <div className="research-detail-page" aria-label="Repository detail">
        <div className="tasks-detail-top">
          <button type="button" className="back-link" onClick={onClearSelection}>
            ← Back to shelf
          </button>
          {selectedRepository.repository_label && (
            <Pill tone={LABEL_TONE[selectedRepository.repository_label]}>
              {selectedRepository.repository_label}
            </Pill>
          )}
          <Pill tone={LIFECYCLE_TONE[selectedRepository.lifecycle_status]}>
            {selectedRepository.lifecycle_status}
          </Pill>
        </div>

        <h1 className="research-detail-title">{selectedRepository.title}</h1>

        <div className="wiki-editor-bar">
          {selectedRepository.source_url && (
            <a
              href={selectedRepository.source_url}
              target="_blank"
              rel="noreferrer"
              className="back-link"
            >
              Open on GitHub ↗
            </a>
          )}
        </div>

        <div className="research-detail-fields">
          <ResearchDetailPanel resource={selectedRepository} onUpdate={onUpdateDetail} />
        </div>

        {renderEnrichment?.(selectedRepository)}
      </div>
    )
  }

  return (
    <div className="repositories-view" aria-label="Repositories">
      <div className="page-header">
        <div className="page-header-text">
          <h1 className="page-header-title">Repository shelf</h1>
          <p className="page-header-description">
            GitHub repositories you are studying — labeled by how they matter to you.
          </p>
        </div>
        <div className="page-header-actions">
          <CreateRepositoryForm onCreateResource={onCreateResource} />
        </div>
      </div>

      <div className="research-tabs" role="group" aria-label="Filter by ownership label">
        {LABEL_FILTERS.map((option) => (
          <button
            key={option.value || 'all'}
            type="button"
            className={
              labelFilter === option.value ? 'research-tab research-tab-active' : 'research-tab'
            }
            aria-current={labelFilter === option.value}
            onClick={() => setLabelFilter(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <EmptyState
          icon="📦"
          title="No repositories tracked yet"
          hint="Adjust the label filter above, or paste a GitHub repository URL above."
        />
      ) : (
        <div className="list-view">
          {filtered.map((resource) => (
            <RepositoryRow
              key={resource.id}
              resource={resource}
              isSelected={resource.node_id === selectedNodeId}
              onSelect={onSelectNode}
              onSetLabel={onSetLabel}
            />
          ))}
        </div>
      )}
    </div>
  )
}
