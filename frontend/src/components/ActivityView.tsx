import { useEffect, useMemo, useState } from 'react'
import type { ActivityEvent } from '../types'

interface ActivityViewProps {
  onLoadPage: (
    cursor: string | null,
  ) => Promise<{ events: ActivityEvent[]; next_cursor: string | null }>
  onUndo: (eventId: string, reason: string) => Promise<void>
}

type UndoRowState =
  | { status: 'idle' }
  | { status: 'confirming' }
  | { status: 'submitting' }
  | { status: 'done' }
  | { status: 'error'; message: string }

/** Read-only activity/audit feed plus stale-safe undo (ST-07.1/07.3): entity-type filter,
 * detail drill-in, and per-row confirm/undo/retry feedback. Redo is out of scope. */
export function ActivityView({ onLoadPage, onUndo }: ActivityViewProps) {
  const [events, setEvents] = useState<ActivityEvent[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [entityTypeFilter, setEntityTypeFilter] = useState<string>('all')
  const [expandedEventId, setExpandedEventId] = useState<string | null>(null)
  const [undoState, setUndoState] = useState<Record<string, UndoRowState>>({})
  const [reasonDraft, setReasonDraft] = useState<Record<string, string>>({})

  async function loadFirstPage() {
    setIsLoading(true)
    setLoadError(null)
    try {
      const page = await onLoadPage(null)
      setEvents(page.events)
      setCursor(page.next_cursor)
      setHasMore(page.next_cursor !== null)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    } finally {
      setIsLoading(false)
    }
  }

  async function loadMore() {
    if (!cursor) return
    setIsLoading(true)
    setLoadError(null)
    try {
      const page = await onLoadPage(cursor)
      setEvents((previous) => [...previous, ...page.events])
      setCursor(page.next_cursor)
      setHasMore(page.next_cursor !== null)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    loadFirstPage()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const entityTypes = useMemo(
    () => Array.from(new Set(events.map((event) => event.entity_type))).sort(),
    [events],
  )
  const reversedEventIds = useMemo(
    () => new Set(events.map((event) => event.reverses_event_id).filter((id) => id !== null)),
    [events],
  )
  const visibleEvents =
    entityTypeFilter === 'all'
      ? events
      : events.filter((event) => event.entity_type === entityTypeFilter)

  function beginUndo(eventId: string) {
    setUndoState((current) => ({ ...current, [eventId]: { status: 'confirming' } }))
  }

  function cancelUndo(eventId: string) {
    setUndoState((current) => ({ ...current, [eventId]: { status: 'idle' } }))
  }

  async function confirmUndo(eventId: string) {
    const reason = (reasonDraft[eventId] ?? '').trim()
    if (!reason) return
    setUndoState((current) => ({ ...current, [eventId]: { status: 'submitting' } }))
    try {
      await onUndo(eventId, reason)
      setUndoState((current) => ({ ...current, [eventId]: { status: 'done' } }))
      await loadFirstPage()
    } catch (error) {
      setUndoState((current) => ({
        ...current,
        [eventId]: {
          status: 'error',
          message: error instanceof Error ? error.message : String(error),
        },
      }))
    }
  }

  return (
    <div className="activity-view" aria-label="Activity view">
      <div className="activity-view-filters">
        <label htmlFor="activity-entity-type-filter">Entity type</label>
        <select
          id="activity-entity-type-filter"
          value={entityTypeFilter}
          onChange={(event) => setEntityTypeFilter(event.target.value)}
        >
          <option value="all">All</option>
          {entityTypes.map((entityType) => (
            <option key={entityType} value={entityType}>
              {entityType}
            </option>
          ))}
        </select>
      </div>

      {loadError && <p className="view-error">{loadError}</p>}
      {!loadError && visibleEvents.length === 0 && !isLoading && (
        <p className="view-empty">No activity recorded yet.</p>
      )}

      <div className="list-view">
        {visibleEvents.map((event) => {
          const isExpanded = expandedEventId === event.id
          const isAlreadyReversed = reversedEventIds.has(event.id)
          const isCompensating = event.reverses_event_id !== null
          const rowUndoState = undoState[event.id] ?? { status: 'idle' }
          const canUndo = event.is_undoable && !isAlreadyReversed && !isCompensating

          return (
            <div key={event.id} className="activity-row-group">
              <button
                type="button"
                className="activity-row"
                data-undoable={event.is_undoable}
                onClick={() => setExpandedEventId(isExpanded ? null : event.id)}
              >
                <span className="activity-row-actor">{event.actor_name}</span>
                <span className="activity-row-action">{event.action}</span>
                <span className="activity-row-entity">
                  {event.entity_type} {event.entity_id}
                </span>
                <span className="activity-row-occurred-at">
                  {new Date(event.occurred_at).toLocaleString()}
                </span>
                {isCompensating && <span className="activity-row-badge">undo</span>}
                {isAlreadyReversed && <span className="activity-row-badge">reversed</span>}
              </button>

              {isExpanded && (
                <div className="activity-row-detail">
                  {event.reason && <p>Reason: {event.reason}</p>}
                  <pre>{JSON.stringify({ before: event.before_state, after: event.after_state }, null, 2)}</pre>

                  {canUndo && rowUndoState.status === 'idle' && (
                    <button type="button" onClick={() => beginUndo(event.id)}>
                      Undo
                    </button>
                  )}
                  {!event.is_undoable && !isCompensating && (
                    <p className="activity-row-disabled-reason">Not undoable.</p>
                  )}

                  {rowUndoState.status === 'confirming' && (
                    <div className="activity-undo-confirm">
                      <label htmlFor={`undo-reason-${event.id}`}>Reason for undo</label>
                      <input
                        id={`undo-reason-${event.id}`}
                        type="text"
                        value={reasonDraft[event.id] ?? ''}
                        onChange={(inputEvent) =>
                          setReasonDraft((current) => ({
                            ...current,
                            [event.id]: inputEvent.target.value,
                          }))
                        }
                      />
                      <button
                        type="button"
                        onClick={() => confirmUndo(event.id)}
                        disabled={!(reasonDraft[event.id] ?? '').trim()}
                      >
                        Confirm undo
                      </button>
                      <button type="button" onClick={() => cancelUndo(event.id)}>
                        Cancel
                      </button>
                    </div>
                  )}
                  {rowUndoState.status === 'submitting' && <p>Undoing…</p>}
                  {rowUndoState.status === 'done' && <p>Undone.</p>}
                  {rowUndoState.status === 'error' && (
                    <div>
                      <p className="view-error">{rowUndoState.message}</p>
                      <button type="button" onClick={() => beginUndo(event.id)}>
                        Retry
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {hasMore && (
        <button type="button" onClick={loadMore} disabled={isLoading}>
          Load more
        </button>
      )}
    </div>
  )
}
