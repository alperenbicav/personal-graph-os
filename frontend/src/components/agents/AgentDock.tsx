import { useState } from 'react'
import type { Agent, AgentRun } from '../../types'
import { AskAgentComposer } from './AskAgentComposer'

export interface AgentDockProps {
  agents: Agent[]
  runs: AgentRun[]
  onAskAgent: (agentId: string, prompt: string) => Promise<void>
  isAgentRunning?: boolean
  isLlmConfigured?: boolean
  onOpenAgent?: (agentId: string) => void
  onApproveRun?: (runId: string) => Promise<void>
  onRejectRun?: (runId: string) => Promise<void>
  lastError?: string | null
  onClearError?: () => void
}

function timeAgo(isoDate: string): string {
  try {
    const diffMs = Date.now() - new Date(isoDate).getTime()
    const diffSec = Math.floor(diffMs / 1000)
    if (diffSec < 45) return 'just now'
    const diffMin = Math.floor(diffSec / 60)
    if (diffMin < 60) return diffMin + 'm ago'
    const diffHrs = Math.floor(diffMin / 60)
    if (diffHrs < 24) return diffHrs + 'h ago'
    return Math.floor(diffHrs / 24) + 'd ago'
  } catch {
    return 'recently'
  }
}

export function AgentDock({
  agents,
  runs,
  onAskAgent,
  isAgentRunning = false,
  isLlmConfigured = true,
  onOpenAgent,
  onApproveRun,
  onRejectRun,
  lastError,
  onClearError,
}: AgentDockProps) {
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null)

  function getAgentInitials(agentName: string): string {
    const parts = agentName.split('-')
    if (parts.length >= 2) {
      return (parts[0][0] + parts[1][0]).toUpperCase()
    }
    return agentName.slice(0, 2).toUpperCase()
  }

  function getAgentColorClass(index: number): string {
    const classes = ['a1', 'a2', 'a3']
    return classes[index % classes.length]
  }

  const agentMap = new Map<string, Agent>(agents.map((a) => [a.id, a]))
  const isLlmUnconfigured =
    Boolean(lastError && (lastError.includes('LLM provider not configured') || lastError.includes('503')))

  return (
    <aside className="v2-dock" aria-label="Agent Stream & Dock">
      <div className="v2-dock-card">
        <div className="v2-dock-header">
          <span className="v2-pulse" style={{ background: 'var(--v2-ac)', boxShadow: 'none' }} />
          <span>AGENT STREAM</span>
        </div>

        <div className="v2-dock-events">
          {lastError && (
            <div className="v2-llm-error-card" role="alert">
              <div className="v2-llm-error-header">
                <span className="v2-llm-error-title">
                  {isLlmUnconfigured ? '⚠️ LLM Provider Not Configured' : 'Agent Error'}
                </span>
                {onClearError && (
                  <button
                    type="button"
                    className="v2-llm-error-dismiss"
                    onClick={onClearError}
                    aria-label="Dismiss error"
                  >
                    ✕
                  </button>
                )}
              </div>
              <p className="v2-llm-error-desc">
                {isLlmUnconfigured
                  ? 'Agent reasoning requires a configured LLM provider. Set these environment variables in your server environment:'
                  : lastError}
              </p>
              {isLlmUnconfigured && (
                <div className="v2-llm-error-code">
                  <div><code>PGOS_LLM_PROVIDER=openai</code> <span className="v2-comment"># openai | anthropic | openrouter</span></div>
                  <div><code>PGOS_LLM_API_KEY=your-api-key</code></div>
                  <div><code>PGOS_LLM_MODEL=gpt-4o-mini</code> <span className="v2-comment"># optional model override</span></div>
                </div>
              )}
            </div>
          )}

          {runs.length === 0 && !lastError && (
            <div className="v2-dock-empty-state">
              <div className="v2-dock-empty-icon" aria-hidden="true">🤖</div>
              <div className="v2-dock-empty-title">Agent Stream Idle</div>
              <p className="v2-dock-empty-desc">
                No recent agent events. Dispatch a task below to see live executions!
              </p>
            </div>
          )}

          {runs.slice(0, 15).map((run, idx) => {
            const agent = agentMap.get(run.agent_id)
            const agentName = agent?.name || 'Agent'
            const initials = getAgentInitials(agentName)
            const colorClass = getAgentColorClass(idx)

            return (
              <div key={run.id || idx} className="v2-ev">
                <div className={`v2-av2 ${colorClass}`} title={agentName}>
                  {agent?.emoji || initials}
                </div>
                <div className="v2-ev-body">
                  <div className="v2-ev-ln">
                    <b>{agentName}</b> {run.summary || run.action}
                    {run.diff_json && (
                      <span className="v2-diff">
                        <span className="v2-dm">+12</span>
                        <span className="v2-dp">−3</span>
                      </span>
                    )}
                  </div>
                  <div className="v2-when">
                    {timeAgo(run.created_at)} · {run.action}
                    {run.status === 'pending_review' && (
                      <span
                        style={{
                          marginLeft: '6px',
                          fontSize: '11px',
                          padding: '1px 6px',
                          borderRadius: '4px',
                          background: 'rgba(234, 179, 8, 0.15)',
                          color: '#eab308',
                          fontWeight: 500,
                        }}
                      >
                        Onay Bekliyor
                      </span>
                    )}
                  </div>
                  {run.status === 'pending_review' ? (
                    <div className="v2-acts" style={{ marginTop: '6px', display: 'flex', gap: '6px' }}>
                      <button
                        type="button"
                        className="v2-chipbtn prime"
                        onClick={() => onApproveRun && onApproveRun(run.id)}
                        style={{
                          background: 'var(--v2-ac)',
                          color: 'var(--v2-bg)',
                          borderColor: 'transparent',
                          fontWeight: 600,
                        }}
                      >
                        Kabul et
                      </button>
                      <button
                        type="button"
                        className="v2-chipbtn"
                        onClick={() => onRejectRun && onRejectRun(run.id)}
                        style={{ color: 'var(--v2-tx2)' }}
                      >
                        Reddet
                      </button>
                    </div>
                  ) : (
                    onOpenAgent && agent && (
                      <div className="v2-acts">
                        <button
                          type="button"
                          className="v2-chipbtn prime"
                          onClick={() => onOpenAgent(agent.id)}
                        >
                          Chat
                        </button>
                      </div>
                    )
                  )}
                </div>
              </div>
            )
          })}

          {isAgentRunning && (
            <div className="v2-ev">
              <div className="v2-av2 a1">✦</div>
              <div className="v2-ev-body">
                <div className="v2-ev-ln">
                  <b>Agent</b> is analyzing and executing…
                </div>
                <div className="v2-run-bar">
                  <i />
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      <AskAgentComposer
        agents={agents}
        selectedAgentId={selectedAgentId}
        onSelectAgent={setSelectedAgentId}
        onSubmit={onAskAgent}
        isSubmitting={isAgentRunning}
        isLlmConfigured={isLlmConfigured}
      />
    </aside>
  )
}

