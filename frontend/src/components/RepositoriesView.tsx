import { useMemo, useState } from 'react'
import type { RepositoryLabel, Resource } from '../types'

interface RepositoriesViewProps {
  resources: Resource[]
  selectedNodeId: string | null
  onSelectNode: (nodeId: string) => void
  onSetLabel: (resourceId: string, label: RepositoryLabel) => void
}

const LABEL_FILTERS: { value: '' | RepositoryLabel; label: string }[] = [
  { value: '', label: 'All repositories' },
  { value: 'personal', label: 'Personal' },
  { value: 'apilex', label: 'Apilex' },
  { value: 'liked_external', label: 'Liked external' },
]

const LABEL_OPTIONS: { value: RepositoryLabel; label: string }[] = [
  { value: 'personal', label: 'Personal' },
  { value: 'apilex', label: 'Apilex' },
  { value: 'liked_external', label: 'Liked external' },
]

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
        <select
          aria-label={`Ownership label for ${resource.title}`}
          className="repository-label-select"
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
      </span>
    </div>
  )
}

export function RepositoriesView({
  resources,
  selectedNodeId,
  onSelectNode,
  onSetLabel,
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

  return (
    <div className="repositories-view" aria-label="Repositories">
      <div className="repositories-filter-bar">
        {LABEL_FILTERS.map((option) => (
          <button
            key={option.value || 'all'}
            type="button"
            className="node-row-label"
            aria-current={labelFilter === option.value}
            onClick={() => setLabelFilter(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
      {filtered.length === 0 ? (
        <p className="view-empty">No repositories match this filter.</p>
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
