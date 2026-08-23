import type { AppView } from '../NavTabs'

export interface RailNavProps {
  activeView: AppView
  onSelectView: (view: AppView) => void
  onOpenSettings?: () => void
  onLock?: () => void
}

interface NavItem {
  id: AppView
  icon: string
  label: string
  shortcut: string
}

const NAV_ITEMS: NavItem[] = [
  { id: 'graph', icon: '◈', label: 'Graph', shortcut: '⌘1' },
  { id: 'tasks', icon: '◎', label: 'Tasks', shortcut: '⌘2' },
  { id: 'wiki', icon: '▤', label: 'Wiki', shortcut: '⌘3' },
  { id: 'research', icon: '❋', label: 'Research', shortcut: '⌘4' },
  { id: 'repositories', icon: '⌗', label: 'Repositories', shortcut: '⌘5' },
  { id: 'agents', icon: '🤖', label: 'Agents', shortcut: '⌘6' },
  { id: 'activity', icon: '⚡', label: 'Activity', shortcut: '⌘7' },
]

export function RailNav({
  activeView,
  onSelectView,
  onOpenSettings,
  onLock,
}: RailNavProps) {
  return (
    <nav className="v2-rail nav-tabs" aria-label="Workspace views">
      <div
        className="v2-rail-logo"
        title="Personal Graph OS"
        onClick={() => onSelectView('graph')}
        role="button"
        tabIndex={0}
        aria-label="Home"
      />
      {NAV_ITEMS.map((item) => {
        const isActive = activeView === item.id
        return (
          <button
            key={item.id}
            type="button"
            className={`v2-rail-item ${isActive ? 'on' : ''}`}
            onClick={() => onSelectView(item.id)}
            aria-current={isActive ? 'true' : 'false'}
            aria-label={item.label}
          >
            <span aria-hidden="true">{item.icon}</span>
            <span className="tip">{item.label}</span>
          </button>
        )
      })}
      <div className="v2-rail-spacer" />
      {onOpenSettings && (
        <button
          type="button"
          className="v2-rail-item"
          onClick={onOpenSettings}
          title="Settings & Schema"
          aria-label="Settings & Schema"
        >
          <span aria-hidden="true">⚙</span>
          <span className="tip">Settings</span>
        </button>
      )}
      <button
        type="button"
        className="v2-rail-item me"
        onClick={onLock}
        title="Workspace User · Click to Lock"
        aria-label="Workspace User"
      >
        A
        <span className="tip">Lock Session</span>
      </button>
    </nav>
  )
}

