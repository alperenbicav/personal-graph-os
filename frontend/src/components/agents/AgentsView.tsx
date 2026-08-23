import { useState } from 'react'
import type { Agent, CreateAgentInput, UpdateAgentInput } from '../../types'
import { AgentFormModal } from './AgentFormModal'
import { AgentChatThread } from './AgentChatThread'

export interface AgentsViewProps {
  agents: Agent[]
  onRefresh: () => Promise<void>
  onCreateAgent: (input: CreateAgentInput) => Promise<void>
  onUpdateAgent: (id: string, input: UpdateAgentInput) => Promise<void>
  onDeleteAgent: (id: string) => Promise<void>
  activeChatAgentId?: string | null
  onSelectChatAgent?: (id: string | null) => void
}

export function AgentsView({
  agents,
  onRefresh,
  onCreateAgent,
  onUpdateAgent,
  onDeleteAgent,
  activeChatAgentId: controlledChatAgentId,
  onSelectChatAgent,
}: AgentsViewProps) {
  const [internalChatAgentId, setInternalChatAgentId] = useState<string | null>(null)
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const activeChatId = controlledChatAgentId !== undefined ? controlledChatAgentId : internalChatAgentId
  const setActiveChatId = onSelectChatAgent || setInternalChatAgentId

  const activeChatAgent = agents.find((a) => a.id === activeChatId)

  async function handleSaveAgent(input: CreateAgentInput | UpdateAgentInput) {
    if (editingAgent) {
      await onUpdateAgent(editingAgent.id, input)
    } else {
      await onCreateAgent(input as CreateAgentInput)
    }
    await onRefresh()
  }

  async function handleDelete(agentId: string, agentName: string) {
    if (!window.confirm(`Are you sure you want to delete ${agentName}?`)) return
    try {
      await onDeleteAgent(agentId)
      if (activeChatId === agentId) {
        setActiveChatId(null)
      }
      await onRefresh()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="v2-agents-page" aria-label="Agents Fleet Management">
      <div className="v2-agents-header">
        <div>
          <h1 className="v2-agents-title">Autonomous Agents Fleet</h1>
          <p className="v2-agents-desc">
            Configure and deploy AI agents for research synthesis, graph ingestion, planning, and task execution.
            All agents respect workspace tools and safety boundaries.
          </p>
        </div>
        <button
          type="button"
          className="v2-chipbtn prime"
          style={{ padding: '8px 16px', fontSize: '13.5px', borderRadius: '10px' }}
          onClick={() => {
            setEditingAgent(null)
            setIsCreateModalOpen(true)
          }}
        >
          + Create Agent
        </button>
      </div>

      {actionError && (
        <div style={{ color: 'var(--v2-rd)', background: 'rgba(255,107,122,0.1)', padding: '10px 14px', borderRadius: '8px' }} role="alert">
          {actionError}
        </div>
      )}

      {activeChatAgent ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 380px', gap: '20px', alignItems: 'start' }}>
          <div className="v2-agents-grid">
            {agents.map((agent) => (
              <AgentCard
                key={agent.id}
                agent={agent}
                isSelected={agent.id === activeChatId}
                onChat={() => setActiveChatId(agent.id)}
                onEdit={() => {
                  setEditingAgent(agent)
                  setIsCreateModalOpen(true)
                }}
                onDelete={() => handleDelete(agent.id, agent.name)}
              />
            ))}
          </div>

          <AgentChatThread
            agent={activeChatAgent}
            onClose={() => setActiveChatId(null)}
            onMessageSent={onRefresh}
          />
        </div>
      ) : (
        <div className="v2-agents-grid">
          {agents.map((agent) => (
            <AgentCard
              key={agent.id}
              agent={agent}
              isSelected={false}
              onChat={() => setActiveChatId(agent.id)}
              onEdit={() => {
                setEditingAgent(agent)
                setIsCreateModalOpen(true)
              }}
              onDelete={() => handleDelete(agent.id, agent.name)}
            />
          ))}
        </div>
      )}

      <AgentFormModal
        agent={editingAgent}
        isOpen={isCreateModalOpen}
        onClose={() => {
          setIsCreateModalOpen(false)
          setEditingAgent(null)
        }}
        onSave={handleSaveAgent}
      />
    </div>
  )
}

interface AgentCardProps {
  agent: Agent
  isSelected: boolean
  onChat: () => void
  onEdit: () => void
  onDelete: () => void
}

function AgentCard({ agent, isSelected, onChat, onEdit, onDelete }: AgentCardProps) {
  const isSeed = agent.id.startsWith('agent-') && agent.id.endsWith('-default')

  return (
    <div
      className="v2-agent-card"
      style={isSelected ? { borderColor: 'var(--v2-ac)', boxShadow: '0 0 0 1px var(--v2-ac)' } : undefined}
    >
      <div className="v2-agent-card-top">
        <div className="v2-agent-identity">
          <span className="v2-agent-emoji">{agent.emoji}</span>
          <div>
            <div className="v2-agent-name">{agent.name}</div>
            <div style={{ fontSize: '11px', color: 'var(--v2-t3)' }}>
              Created {new Date(agent.created_at).toLocaleDateString()}
            </div>
          </div>
        </div>
        <div className="v2-agent-badges">
          {agent.model && <span className="v2-badge model">{agent.model}</span>}
          <span className={`v2-badge mode-${agent.write_mode}`}>{agent.write_mode}</span>
        </div>
      </div>

      <div className="v2-agent-prompt-box">
        {agent.system_prompt}
      </div>

      <div>
        <div style={{ fontSize: '11px', color: 'var(--v2-t3)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Allowed Tools ({agent.tool_allowlist?.length || 0})
        </div>
        <div className="v2-agent-tools">
          {agent.tool_allowlist && agent.tool_allowlist.length > 0 ? (
            agent.tool_allowlist.map((tool) => (
              <span key={tool} className="v2-tool-tag">
                {tool}
              </span>
            ))
          ) : (
            <span style={{ fontSize: '11.5px', color: 'var(--v2-t3)' }}>No tools allowed</span>
          )}
        </div>
      </div>

      <div className="v2-agent-actions">
        <button type="button" className="v2-chipbtn prime" onClick={onChat}>
          💬 Chat
        </button>
        <button type="button" className="v2-chipbtn" onClick={onEdit}>
          Edit
        </button>
        {!isSeed && (
          <button type="button" className="v2-chipbtn" style={{ color: 'var(--v2-rd)' }} onClick={onDelete}>
            Delete
          </button>
        )}
      </div>
    </div>
  )
}

