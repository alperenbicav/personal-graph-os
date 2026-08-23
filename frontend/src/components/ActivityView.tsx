import { useEffect, useMemo, useState } from 'react'
import type { ActivityEvent, ActivityEventSummary } from '../types'
import { SkeletonRow } from './ui'

interface ActivityViewProps {
  onLoadPage: (
    cursor: string | null,
  ) => Promise<{ events: ActivityEventSummary[]; next_cursor: string | null }>
  onLoadDetail: (eventId: string) => Promise<ActivityEvent>
  onUndo: (eventId: string, reason: string) => Promise<void>
}

type UndoRowState =
  | { status: 'idle' }
  | { status: 'confirming' }
  | { status: 'submitting' }
  | { status: 'done' }
  | { status: 'error'; message: string }

type DetailState =
  | { status: 'loading' }
  | { status: 'loaded'; event: ActivityEvent }
  | { status: 'error'; message: string }

const DISABLED_REASON_LABELS: Record<string, string> = {
  snapshot_omitted_oversized: 'Not undoable: recorded snapshot was too large to store safely.',
  unsupported_action: 'Not undoable.',
  already_reversed: 'Already undone.',
  compensating_event: 'This is itself an undo action.',
}

function disabledReasonLabel(reason: ActivityEventSummary['disabled_reason']): string {
  if (reason === null) return 'Not undoable.'
  return DISABLED_REASON_LABELS[reason] ?? 'Not undoable.'
}

/** One event in the timeline: actor chip, action badge, entity, time; expandable detail with
 * before/after JSON and stale-safe undo (ST-07.1/07.3). Redo is out of scope. */
function ActivityRow({
  event,
  reversedEventIds,
  expandedEventId,
  detailState,
  undoState,
  reasonDraft,
  onToggle,
  onBeginUndo,
  onCancelUndo,
  onConfirmUndo,
  onReasonDraft,
}: {
  event: ActivityEventSummary
  reversedEventIds: Set<string>
  expandedEventId: string | null
  detailState: Record<string, DetailState>
  undoState: Record<string, UndoRowState>
  reasonDraft: Record<string, string>
  onToggle: (eventId: string) => void
  onBeginUndo: (eventId: string) => void
  onCancelUndo: (eventId: string) => void
  onConfirmUndo: (eventId: string) => void
  onReasonDraft: (eventId: string, value: string) => void
}) {
  const isExpanded = expandedEventId === event.id
  const isAlreadyReversed = reversedEventIds.has(event.id)
  const isCompensating = event.reverses_event_id !== null
  const rowUndoState = undoState[event.id] ?? { status: 'idle' }
  const canUndo = event.is_undoable && !isAlreadyReversed && !isCompensating

  return (
    <div className="activity-row-group">
      <button
        type="button"
        className="activity-row"
        data-undoable={event.is_undoable}
        onClick={() => onToggle(event.id)}
      >
        <span className="activity-timeline-dot" aria-hidden />
        <span className="pill pill-neutral">{event.actor_name}</span>
        <span className="activity-row-action">{event.action}</span>
        <span className="activity-row-entity">
          {event.entity_type} {event.entity_id}
        </span>
        <span className="activity-row-occurred-at">
          {new Date(event.occurred_at).toLocaleTimeString()}
        </span>
        {isCompensating && <span className="activity-row-badge">undo</span>}
        {isAlreadyReversed && <span className="activity-row-badge">reversed</span>}
      </button>

      {isExpanded && (
        <div className="activity-row-detail">
          {event.reason && <p>Reason: {event.reason}</p>}
          {(() => {
            const detail = detailState[event.id]
            if (!detail || detail.status === 'loading') {
              return <p>Loading detail…</p>
            }
            if (detail.status === 'error') {
              return (
                <p className="view-error" role="alert">
                  {detail.message}
                </p>
              )
            }
            return (
              <pre>
                {JSON.stringify(
                  { before: detail.event.before_state, after: detail.event.after_state },
                  null,
                  2,
                )}
              </pre>
            )
          })()}

          {canUndo && rowUndoState.status === 'idle' && (
            <button type="button" onClick={() => onBeginUndo(event.id)}>
              Undo
            </button>
          )}
          {!event.is_undoable && !isCompensating && (
            <p className="activity-row-disabled-reason" data-reason={event.disabled_reason}>
              {disabledReasonLabel(event.disabled_reason)}
            </p>
          )}

          {rowUndoState.status === 'confirming' && (
            <div className="activity-undo-confirm">
              <label htmlFor={`undo-reason-${event.id}`}>Reason for undo</label>
              <input
                id={`undo-reason-${event.id}`}
                type="text"
                value={reasonDraft[event.id] ?? ''}
                onChange={(inputEvent) => onReasonDraft(event.id, inputEvent.target.value)}
              />
              <button
                type="button"
                onClick={() => onConfirmUndo(event.id)}
                disabled={!(reasonDraft[event.id] ?? '').trim()}
              >
                Confirm undo
              </button>
              <button type="button" onClick={() => onCancelUndo(event.id)}>
                Cancel
              </button>
            </div>
          )}
          {rowUndoState.status === 'submitting' && <p role="status">Undoing…</p>}
          {rowUndoState.status === 'done' && <p role="status">Undone.</p>}
          {rowUndoState.status === 'error' && (
            <div>
              <p className="view-error" role="alert">
                {rowUndoState.message}
              </p>
              <button type="button" onClick={() => onBeginUndo(event.id)}>
                Retry
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** Read-only activity/audit feed as a day-grouped timeline (EP-2026-013 ST-07): entity-type
 * filter, lazy detail drill-in (the list page never carries a before/after snapshot, ST07-F06),
 * and per-row confirm/undo/retry feedback. */
export function ActivityView({ onLoadPage, onLoadDetail, onUndo }: ActivityViewProps) {
  const [events, setEvents] = useState<ActivityEventSummary[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [entityTypeFilter, setEntityTypeFilter] = useState<string>('all')
  const [expandedEventId, setExpandedEventId] = useState<string | null>(null)
  const [detailState, setDetailState] = useState<Record<string, DetailState>>({})
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

  // Day-grouped timeline (ST-07): consecutive events share one date header.
  const eventDayGroups = useMemo(() => {
    const groups: { day: string; label: string; events: typeof visibleEvents }[] = []
    for (const event of visibleEvents) {
      const day = event.occurred_at.slice(0, 10)
      const last = groups[groups.length - 1]
      if (last && last.day === day) {
        last.events.push(event)
      } else {
        groups.push({
          day,
          label: new Date(event.occurred_at).toLocaleDateString(undefined, {
            weekday: 'long',
            year: 'numeric',
            month: 'long',
            day: 'numeric',
          }),
          events: [event],
        })
      }
    }
    return groups
  }, [visibleEvents])

  function toggleExpand(eventId: string) {
    if (expandedEventId === eventId) {
      setExpandedEventId(null)
      return
    }
    setExpandedEventId(eventId)
    if (detailState[eventId]?.status === 'loaded') return
    setDetailState((current) => ({ ...current, [eventId]: { status: 'loading' } }))
    onLoadDetail(eventId)
      .then((event) => {
        setDetailState((current) => ({ ...current, [eventId]: { status: 'loaded', event } }))
      })
      .catch((error) => {
        setDetailState((current) => ({
          ...current,
          [eventId]: {
            status: 'error',
            message: error instanceof Error ? error.message : String(error),
          },
        }))
      })
  }

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
      <div className="page-header">
        <div className="page-header-text">
          <h1 className="page-header-title">Activity</h1>
          <p className="page-header-description">
            Every mutation, append-only. Select an entry for its before/after states and undo.
          </p>
        </div>
        <div className="page-header-actions">
          <select
            aria-label="Entity type"
            className="wiki-chip-select"
            value={entityTypeFilter}
            onChange={(event) => setEntityTypeFilter(event.target.value)}
          >
            <option value="all">All types</option>
            {entityTypes.map((entityType) => (
              <option key={entityType} value={entityType}>
                {entityType}
              </option>
            ))}
          </select>
        </div>
      </div>

      {loadError && (
        <p className="view-error" role="alert">
          {loadError}
        </p>
      )}
      {isLoading && visibleEvents.length === 0 && <SkeletonRow count={4} />}
      {!loadError && visibleEvents.length === 0 && !isLoading && (
        <p className="view-empty" role="status">
          No activity recorded yet.
        </p>
      )}

      <div className="activity-feed">
        {eventDayGroups.map((group) => (
          <div key={group.day} className="activity-day">
            <h3 className="activity-day-label">{group.label}</h3>
            {group.events.map((event) => (
              <ActivityRow
                key={event.id}
                event={event}
                reversedEventIds={reversedEventIds}
                expandedEventId={expandedEventId}
                detailState={detailState}
                undoState={undoState}
                reasonDraft={reasonDraft}
                onToggle={toggleExpand}
                onBeginUndo={beginUndo}
                onCancelUndo={cancelUndo}
                onConfirmUndo={confirmUndo}
                onReasonDraft={(id, value) =>
                  setReasonDraft((current) => ({ ...current, [id]: value }))
                }
              />
            ))}
          </div>
        ))}
      </div>

      {hasMore && (
        <button type="button" onClick={loadMore} disabled={isLoading}>
          Load more
        </button>
      )}
    </div>
  )
}
