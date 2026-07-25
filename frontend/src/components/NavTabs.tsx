import { useState } from 'react'

export type AppView =
  | 'canvas'
  | 'table'
  | 'kanban'
  | 'timeline'
  | 'search'
  | 'research'
  | 'repositories'
  | 'discovery'
  | 'activity'

interface TabDefinition {
  id: AppView
  label: string
  description: string
}

const TABS: TabDefinition[] = [
  {
    id: 'canvas',
    label: 'Canvas',
    description:
      'Drag nodes around a visual graph canvas, connect them with typed relationships, and organize multiple canvases per workspace.',
  },
  {
    id: 'table',
    label: 'Table',
    description: 'Every node as a row. Also where you can save the current view to reopen it later.',
  },
  {
    id: 'kanban',
    label: 'Kanban',
    description: 'Nodes grouped into columns by their status.',
  },
  {
    id: 'timeline',
    label: 'Timeline',
    description: 'Nodes ordered by when they were created.',
  },
  {
    id: 'search',
    label: 'Search',
    description: 'Full-text search across every node and research resource.',
  },
  {
    id: 'research',
    label: 'Research',
    description:
      'A dashboard of research resources by lifecycle status, with a guided workflow that suggests the next step (e.g. turn a takeaway into a decision, then a task).',
  },
  {
    id: 'repositories',
    label: 'Repositories',
    description:
      'GitHub repositories you have captured, grouped by ownership (personal, Apilex, liked external), with purpose, capabilities, stack, license/activity, risks, and related graph objects.',
  },
  {
    id: 'discovery',
    label: 'Discovery',
    description:
      'Import external candidates (identifier/URL + title, one per line) as research resources — preview them first, then confirm. This never fetches or searches the web itself.',
  },
  {
    id: 'activity',
    label: 'Activity',
    description:
      'An append-only log of every change to your data. Supported actions can be undone, which adds a compensating entry rather than rewriting history.',
  },
]

interface NavTabsProps {
  activeView: AppView
  onSelectView: (view: AppView) => void
}

export function NavTabs({ activeView, onSelectView }: NavTabsProps) {
  const [openInfoFor, setOpenInfoFor] = useState<AppView | null>(null)

  return (
    <nav className="nav-tabs" aria-label="Workspace views">
      {TABS.map((tab) => (
        <div className="nav-tab-group" key={tab.id}>
          <button
            type="button"
            className="nav-tab"
            aria-current={tab.id === activeView}
            onClick={() => {
              onSelectView(tab.id)
              setOpenInfoFor(null)
            }}
          >
            {tab.label}
          </button>
          <button
            type="button"
            className="nav-tab-info"
            aria-label={`About ${tab.label}`}
            aria-expanded={openInfoFor === tab.id}
            onClick={() =>
              setOpenInfoFor((current) => (current === tab.id ? null : tab.id))
            }
            onKeyDown={(event) => {
              if (event.key === 'Escape') setOpenInfoFor(null)
            }}
          >
            ⓘ
          </button>
          {openInfoFor === tab.id && (
            <div className="nav-tab-info-popover" role="note">
              {tab.description}
            </div>
          )}
        </div>
      ))}
    </nav>
  )
}
