import { useEffect, useId, useMemo, useRef, useState } from 'react'
import type { AppView } from './NavTabs'
import type { GraphNode, Resource, WikiDocument, WorkItem, Canvas } from '../types'
import { useModalDialog } from '../lib/useModalDialog'
import { EmptyState, Pill, type PillTone } from './ui'

export interface CommandPaletteProps {
  isOpen: boolean
  onClose: () => void
  onNavigate: (view: AppView) => void
  onSelectDocument: (documentId: string) => void
  onSelectWorkItem: (workItemId: string) => void
  onSelectResourceNode: (nodeId: string, isRepo?: boolean) => void
  onSelectNode: (nodeId: string) => void
  onSelectCanvas: (canvasId: string) => void
  onOpenCreateWiki?: () => void
  onOpenCreateTask?: () => void
  onOpenCreateCanvas?: () => void
  onOpenSchemaEditor?: () => void
  documents: WikiDocument[]
  workItems: WorkItem[]
  resources: Resource[]
  nodes: GraphNode[]
  canvases: Canvas[]
}

interface PaletteItem {
  id: string
  category: 'Commands' | 'Wiki' | 'Tasks' | 'Research' | 'Repositories' | 'Graph' | 'Canvases'
  title: string
  subtitle?: string
  badge?: string
  badgeTone?: PillTone
  icon?: string
  action: () => void
  score?: number
}

function matchScore(query: string, text: string): number {
  const q = query.toLowerCase().trim()
  const t = text.toLowerCase()
  if (!q) return 0
  if (t === q) return 100
  if (t.startsWith(q)) return 80
  const index = t.indexOf(q)
  if (index !== -1) return 60 - Math.min(index, 30)
  // Check word boundary matches
  const words = t.split(/[\s-_/]+/)
  if (words.some((w) => w.startsWith(q))) return 50
  return -1
}

export function CommandPalette({
  isOpen,
  onClose,
  onNavigate,
  onSelectDocument,
  onSelectWorkItem,
  onSelectResourceNode,
  onSelectNode,
  onSelectCanvas,
  onOpenCreateWiki,
  onOpenCreateTask,
  onOpenCreateCanvas,
  onOpenSchemaEditor,
  documents,
  workItems,
  resources,
  nodes,
  canvases,
}: CommandPaletteProps) {
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const listRef = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const dialogRef = useModalDialog<HTMLDivElement>(onClose)
  const searchInputId = useId()

  useEffect(() => {
    if (isOpen) {
      setQuery('')
      setActiveIndex(0)
      setTimeout(() => inputRef.current?.focus(), 10)

      function handleGlobalEscape(event: KeyboardEvent) {
        if (event.key === 'Escape') {
          event.preventDefault()
          event.stopPropagation()
          onClose()
        }
      }

      document.addEventListener('keydown', handleGlobalEscape, true)
      return () => document.removeEventListener('keydown', handleGlobalEscape, true)
    }
  }, [isOpen, onClose])

  const staticCommands = useMemo<PaletteItem[]>(() => {
    const cmds: PaletteItem[] = [
      {
        id: 'cmd-nav-graph',
        category: 'Commands',
        title: 'Go to Graph',
        subtitle: 'Interactive visual canvas & node relations',
        badge: 'View',
        badgeTone: 'neutral',
        icon: '🗺️',
        action: () => {
          onNavigate('graph')
          onClose()
        },
      },
      {
        id: 'cmd-nav-tasks',
        category: 'Commands',
        title: 'Go to Tasks',
        subtitle: 'Linear-style board, backlog, and checklists',
        badge: 'View',
        badgeTone: 'neutral',
        icon: '📋',
        action: () => {
          onNavigate('tasks')
          onClose()
        },
      },
      {
        id: 'cmd-nav-wiki',
        category: 'Commands',
        title: 'Go to Wiki',
        subtitle: 'Notion-style documentation, lessons & notes',
        badge: 'View',
        badgeTone: 'neutral',
        icon: '📖',
        action: () => {
          onNavigate('wiki')
          onClose()
        },
      },
      {
        id: 'cmd-nav-research',
        category: 'Commands',
        title: 'Go to Research',
        subtitle: 'Readwise-style paper library & takeaways',
        badge: 'View',
        badgeTone: 'neutral',
        icon: '🔬',
        action: () => {
          onNavigate('research')
          onClose()
        },
      },
      {
        id: 'cmd-nav-repos',
        category: 'Commands',
        title: 'Go to Repositories',
        subtitle: 'GitHub-flavored shelf & tracked codebases',
        badge: 'View',
        badgeTone: 'neutral',
        icon: '📦',
        action: () => {
          onNavigate('repositories')
          onClose()
        },
      },
      {
        id: 'cmd-nav-activity',
        category: 'Commands',
        title: 'Go to Activity',
        subtitle: 'Day-grouped audit timeline & undo feed',
        badge: 'View',
        badgeTone: 'neutral',
        icon: '⏱️',
        action: () => {
          onNavigate('activity')
          onClose()
        },
      },
      {
        id: 'cmd-create-task',
        category: 'Commands',
        title: 'New work item / task',
        subtitle: 'Create an Epic, Story, or Task',
        badge: 'Create',
        badgeTone: 'teal',
        icon: '➕',
        action: () => {
          onNavigate('tasks')
          onClose()
          onOpenCreateTask?.()
        },
      },
      {
        id: 'cmd-create-wiki',
        category: 'Commands',
        title: 'New Wiki document',
        subtitle: 'Create a standalone note or doc',
        badge: 'Create',
        badgeTone: 'brass',
        icon: '➕',
        action: () => {
          onNavigate('wiki')
          onClose()
          onOpenCreateWiki?.()
        },
      },
      {
        id: 'cmd-create-canvas',
        category: 'Commands',
        title: 'New Canvas',
        subtitle: 'Create a new graph whiteboard canvas',
        badge: 'Create',
        badgeTone: 'violet',
        icon: '➕',
        action: () => {
          onClose()
          onOpenCreateCanvas?.()
        },
      },
      {
        id: 'cmd-open-schema',
        category: 'Commands',
        title: 'Open Schema Editor',
        subtitle: 'Configure custom node and edge types',
        badge: 'Settings',
        badgeTone: 'neutral',
        icon: '⚙️',
        action: () => {
          onClose()
          onOpenSchemaEditor?.()
        },
      },
    ]
    return cmds
  }, [
    onNavigate,
    onClose,
    onOpenCreateTask,
    onOpenCreateWiki,
    onOpenCreateCanvas,
    onOpenSchemaEditor,
  ])

  const allItems = useMemo<PaletteItem[]>(() => {
    const trimmed = query.trim().toLowerCase()

    const resultList: PaletteItem[] = []

    // 1. Commands
    for (const cmd of staticCommands) {
      if (!trimmed) {
        resultList.push(cmd)
      } else {
        const score = Math.max(
          matchScore(trimmed, cmd.title),
          matchScore(trimmed, cmd.subtitle ?? ''),
        )
        if (score > 0) {
          resultList.push({ ...cmd, score: score + 10 })
        }
      }
    }

    // 2. Documents (Wiki)
    for (const doc of documents) {
      const titleScore = trimmed ? matchScore(trimmed, doc.title) : 1
      if (titleScore > 0) {
        resultList.push({
          id: `doc-${doc.id}`,
          category: 'Wiki',
          title: doc.title,
          subtitle: `${doc.kind} · updated ${doc.updated_at.slice(0, 10)}`,
          badge: doc.kind,
          badgeTone: 'brass',
          icon: '📄',
          score: titleScore,
          action: () => {
            onNavigate('wiki')
            onSelectDocument(doc.id)
            onClose()
          },
        })
      }
    }

    // 3. Work Items (Tasks)
    for (const item of workItems) {
      const titleScore = trimmed
        ? Math.max(
            matchScore(trimmed, item.title),
            matchScore(trimmed, item.assignee ?? ''),
            matchScore(trimmed, item.body ?? ''),
          )
        : 1
      if (titleScore > 0) {
        const priorityTone: PillTone =
          item.priority === 'critical'
            ? 'coral'
            : item.priority === 'high'
              ? 'brass'
              : item.priority === 'medium'
                ? 'teal'
                : 'neutral'
        resultList.push({
          id: `work-${item.id}`,
          category: 'Tasks',
          title: item.title,
          subtitle: `${item.kind} · ${item.status}${item.assignee ? ` · ${item.assignee}` : ''}`,
          badge: item.priority ?? item.status,
          badgeTone: priorityTone,
          icon: '☑️',
          score: titleScore,
          action: () => {
            onNavigate('tasks')
            onSelectWorkItem(item.id)
            onClose()
          },
        })
      }
    }

    // 4. Resources (Research & Repos)
    for (const res of resources) {
      const titleScore = trimmed
        ? Math.max(
            matchScore(trimmed, res.title),
            matchScore(trimmed, res.source_url ?? ''),
            matchScore(trimmed, res.canonical_identifier),
          )
        : 1
      if (titleScore > 0) {
        const isRepo = res.kind === 'github_repository'
        resultList.push({
          id: `res-${res.id}`,
          category: isRepo ? 'Repositories' : 'Research',
          title: res.title,
          subtitle: `${res.kind} · ${res.lifecycle_status}`,
          badge: isRepo ? res.repository_label ?? 'repo' : res.kind,
          badgeTone: isRepo ? 'violet' : 'teal',
          icon: isRepo ? '📦' : '🔬',
          score: titleScore,
          action: () => {
            onNavigate(isRepo ? 'repositories' : 'research')
            onSelectResourceNode(res.node_id, isRepo)
            onClose()
          },
        })
      }
    }

    // 5. Canvases
    for (const canvas of canvases) {
      const score = trimmed ? matchScore(trimmed, canvas.name) : 1
      if (score > 0) {
        resultList.push({
          id: `canvas-${canvas.id}`,
          category: 'Canvases',
          title: canvas.name,
          subtitle: 'Graph canvas placement surface',
          badge: 'Canvas',
          badgeTone: 'neutral',
          icon: '🗺️',
          score,
          action: () => {
            onNavigate('graph')
            onSelectCanvas(canvas.id)
            onClose()
          },
        })
      }
    }

    // 6. Generic Graph Nodes (that aren't already projected as workItems or resources)
    const projectedNodeIds = new Set([
      ...workItems.map((w) => w.node_id),
      ...resources.map((r) => r.node_id),
    ])
    for (const node of nodes) {
      if (projectedNodeIds.has(node.id)) continue
      const score = trimmed ? matchScore(trimmed, node.title) : 1
      if (score > 0) {
        resultList.push({
          id: `node-${node.id}`,
          category: 'Graph',
          title: node.title,
          subtitle: 'Graph node',
          badge: 'Node',
          badgeTone: 'neutral',
          icon: '⚪',
          score,
          action: () => {
            onNavigate('graph')
            onSelectNode(node.id)
            onClose()
          },
        })
      }
    }

    // Sort by score descending if query is active, otherwise preserve grouped order
    if (trimmed) {
      resultList.sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
    }

    return resultList
  }, [
    query,
    staticCommands,
    documents,
    workItems,
    resources,
    nodes,
    canvases,
    onNavigate,
    onSelectDocument,
    onSelectWorkItem,
    onSelectResourceNode,
    onSelectNode,
    onSelectCanvas,
    onClose,
  ])

  // Reset active index when items change
  useEffect(() => {
    setActiveIndex(0)
  }, [query])

  // Keep highlighted item visible
  useEffect(() => {
    const container = listRef.current
    if (!container) return
    const activeEl = container.querySelector<HTMLElement>(`[data-index="${activeIndex}"]`)
    if (typeof activeEl?.scrollIntoView === 'function') {
      activeEl.scrollIntoView({ block: 'nearest' })
    }
  }, [activeIndex])

  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActiveIndex((prev) => (allItems.length > 0 ? (prev + 1) % allItems.length : 0))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActiveIndex((prev) =>
        allItems.length > 0 ? (prev - 1 + allItems.length) % allItems.length : 0,
      )
    } else if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      onClose()
    } else if (event.key === 'Enter') {
      event.preventDefault()
      if (allItems[activeIndex]) {
        allItems[activeIndex].action()
      }
    }
  }

  if (!isOpen) return null

  return (
    <div className="command-palette-backdrop" onClick={onClose}>
      <div
        className="command-palette"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        ref={dialogRef}
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="command-palette-header">
          <span className="command-palette-icon" aria-hidden>
            ⌕
          </span>
          <input
            id={searchInputId}
            ref={inputRef}
            type="text"
            className="command-palette-input"
            placeholder="Type a command or search across your graph…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={handleKeyDown}
            aria-label="Command palette search input"
            autoComplete="off"
            spellCheck={false}
          />
          {query && (
            <button
              type="button"
              className="command-palette-clear"
              onClick={() => setQuery('')}
              aria-label="Clear search"
            >
              ×
            </button>
          )}
        </div>

        <div className="command-palette-list" role="listbox" aria-label="Suggestions" ref={listRef}>
          {allItems.length === 0 ? (
            <EmptyState
              icon="🔍"
              title="No matching results"
              hint={`No commands, notes, tasks, or research items match "${query}".`}
            />
          ) : (
            allItems.map((item, index) => {
              const isActive = index === activeIndex
              return (
                <button
                  key={item.id}
                  data-index={index}
                  type="button"
                  role="option"
                  aria-selected={isActive}
                  className={
                    isActive
                      ? 'command-palette-item command-palette-item-active'
                      : 'command-palette-item'
                  }
                  onClick={item.action}
                  onMouseEnter={() => setActiveIndex(index)}
                >
                  <span className="command-palette-item-icon" aria-hidden>
                    {item.icon ?? '•'}
                  </span>
                  <div className="command-palette-item-text">
                    <span className="command-palette-item-title">{item.title}</span>
                    {item.subtitle && (
                      <span className="command-palette-item-subtitle">{item.subtitle}</span>
                    )}
                  </div>
                  {item.badge && (
                    <Pill tone={item.badgeTone ?? 'neutral'}>{item.badge}</Pill>
                  )}
                </button>
              )
            })
          )}
        </div>

        <footer className="command-palette-footer">
          <span className="command-palette-footer-hint">
            <kbd>↑</kbd> <kbd>↓</kbd> navigate · <kbd>↵</kbd> select · <kbd>esc</kbd> close
          </span>
          <span className="command-palette-footer-count">
            {allItems.length} {allItems.length === 1 ? 'item' : 'items'}
          </span>
        </footer>
      </div>
    </div>
  )
}
