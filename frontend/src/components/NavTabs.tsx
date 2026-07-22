export type AppView = 'canvas' | 'table' | 'kanban' | 'timeline' | 'search' | 'research' | 'discovery'

const TABS: { id: AppView; label: string }[] = [
  { id: 'canvas', label: 'Canvas' },
  { id: 'table', label: 'Table' },
  { id: 'kanban', label: 'Kanban' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'search', label: 'Search' },
  { id: 'research', label: 'Research' },
  { id: 'discovery', label: 'Discovery' },
]

interface NavTabsProps {
  activeView: AppView
  onSelectView: (view: AppView) => void
}

export function NavTabs({ activeView, onSelectView }: NavTabsProps) {
  return (
    <nav className="nav-tabs" aria-label="Workspace views">
      {TABS.map((tab) => (
        <button
          key={tab.id}
          type="button"
          className="nav-tab"
          aria-current={tab.id === activeView}
          onClick={() => onSelectView(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  )
}
