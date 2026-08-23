import { useState } from 'react'
import type { Agent, AgentRun } from '../../types'
import { AskAgentComposer } from './AskAgentComposer'

export interface AgentDockProps {
  agents: Agent[]
  runs: AgentRun[]
  onAskAgent: (agentId: string, prompt: string) => Promise<void>
  isAgentRunning?: boolean
  onOpenAgent?: (agentId: string) => void
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
  onOpenAgent,
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

  return (
    <aside className="v2-dock" aria-label="Agent Stream & Dock">
      <div className="v2-dock-card">
        <div className="v2-dock-header">
          <span className="v2-pulse" style={{ background: 'var(--v2-ac)', boxShadow: 'none' }} />
          <span>AGENT STREAM</span>
        </div>

        <div className="v2-dock-events">
          {runs.length === 0 && (
            <div style={{ color: 'var(--v2-t3)', fontSize: '12.5px', padding: '16px 0', textAlign: 'center' }}>
              No recent agent events. Send a task below!
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
                  </div>
                  {onOpenAgent && agent && (
                    <div className="v2-acts">
                      <button
                        type="button"
                        className="v2-chipbtn prime"
                        onClick={() => onOpenAgent(agent.id)}
                      >
                        Chat
                      </button>
                    </div>
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
      />
    </aside>
  )
}

