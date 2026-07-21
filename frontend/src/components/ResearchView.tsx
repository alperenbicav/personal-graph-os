import type { ResearchDashboard, Resource } from '../types'

interface ResearchViewProps {
  dashboard: ResearchDashboard | null
  selectedNodeId: string | null
  onSelectNode: (nodeId: string) => void
}

const SECTIONS: { key: keyof ResearchDashboard; label: string }[] = [
  { key: 'inbox', label: 'Research Inbox' },
  { key: 'continue_reading', label: 'Continue Reading' },
  { key: 'stale', label: 'Stale Resources' },
  { key: 'needs_takeaway', label: 'Needs Takeaway' },
  { key: 'unlinked', label: 'Unlinked Research' },
  { key: 'applied', label: 'Applied Sources' },
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
    <button
      type="button"
      className="node-row"
      aria-current={isSelected}
      onClick={() => onSelect(resource.node_id)}
    >
      <span className="node-row-title">{resource.title}</span>
      <span className="node-row-meta">
        <span className="node-row-lifecycle">{resource.lifecycle_status}</span>
      </span>
    </button>
  )
}

export function ResearchView({ dashboard, selectedNodeId, onSelectNode }: ResearchViewProps) {
  if (!dashboard) {
    return <p className="view-empty">Loading research dashboard…</p>
  }

  return (
    <div className="research-view" aria-label="Research dashboard">
      {SECTIONS.map((section) => {
        const resources = dashboard[section.key]
        return (
          <div className="research-section" key={section.key}>
            <div className="research-section-header">
              {section.label} <span className="research-section-count">{resources.length}</span>
            </div>
            {resources.length === 0 ? (
              <p className="view-empty">Nothing here.</p>
            ) : (
              <div className="list-view">
                {resources.map((resource) => (
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
      })}
    </div>
  )
}
