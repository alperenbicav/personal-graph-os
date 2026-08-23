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

/** Primary navigation (EP-2026-013 ST-10 review): descriptions moved from persistent ⓘ
 * buttons to native tooltips — less header noise for daily power users, same discoverability
 * on hover/focus. */
export function NavTabs({ activeView, onSelectView }: NavTabsProps) {
  return (
    <nav className="nav-tabs" aria-label="Workspace views">
      {TABS.map((tab) => (
        <button
          key={tab.id}
          type="button"
          className="nav-tab"
          aria-current={tab.id === activeView}
          title={tab.description}
          onClick={() => onSelectView(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  )
}
