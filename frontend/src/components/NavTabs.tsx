import { useState } from 'react'

export type AppView =
  | 'graph'
  | 'tasks'
  | 'search'
  | 'wiki'
  | 'research'
  | 'repositories'
  | 'activity'

interface TabDefinition {
  id: AppView
  label: string
  description: string
}

const TABS: TabDefinition[] = [
  {
    id: 'graph',
    label: 'Graph',
    description:
      'A visual graph canvas over every object in the workspace — drag nodes around, connect them with typed relationships, and select one to inspect or jump to its owning tab.',
  },
  {
    id: 'tasks',
    label: 'Tasks',
    description:
      'An Epic → Story → Task work hierarchy with Markdown descriptions, checklists, Wiki links, repository association, dates, priority, assignee, blockers, and progress.',
  },
  {
    id: 'wiki',
    label: 'Wiki',
    description:
      'An independent Markdown knowledge store for standalone notes, lessons, documentation, and generated plans — organized by collection and tags, with version history and optional links to repositories, work items, papers, or other pages.',
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
    id: 'search',
    label: 'Search',
    description: 'Full-text search across every node and research resource.',
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
