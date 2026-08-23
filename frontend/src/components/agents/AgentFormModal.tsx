import React, { useState, useEffect } from 'react'
import type { Agent, AgentWriteMode, CreateAgentInput, UpdateAgentInput } from '../../types'

export interface AgentFormModalProps {
  agent?: Agent | null
  isOpen: boolean
  onClose: () => void
  onSave: (input: CreateAgentInput | UpdateAgentInput) => Promise<void>
}

const AVAILABLE_TOOLS = [
  { id: 'search', label: 'Semantic & Fulltext Search (search)' },
  { id: 'list_nodes', label: 'Query Graph Nodes (list_nodes)' },
  { id: 'read_node', label: 'Inspect Node Detail (read_node)' },
  { id: 'list_resources', label: 'List Resources & Papers (list_resources)' },
  { id: 'list_work_items', label: 'List Work Items & Tasks (list_work_items)' },
]

export function AgentFormModal({
  agent,
  isOpen,
  onClose,
  onSave,
}: AgentFormModalProps) {
  const [name, setName] = useState('')
  const [emoji, setEmoji] = useState('🤖')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [model, setModel] = useState('')
  const [writeMode, setWriteMode] = useState<AgentWriteMode>('proposal')
  const [selectedTools, setSelectedTools] = useState<string[]>([])
  const [isSaving, setIsSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (agent) {
      setName(agent.name)
      setEmoji(agent.emoji || '🤖')
      setSystemPrompt(agent.system_prompt)
      setModel(agent.model || '')
      setWriteMode(agent.write_mode || 'proposal')
      setSelectedTools(agent.tool_allowlist || [])
    } else {
      setName('')
      setEmoji('🤖')
      setSystemPrompt('You are an autonomous assistant specializing in...')
      setModel('')
      setWriteMode('proposal')
      setSelectedTools(['search', 'list_nodes', 'read_node'])
    }
    setError(null)
  }, [agent, isOpen])

  if (!isOpen) return null

  function toggleTool(toolId: string) {
    setSelectedTools((prev) =>
      prev.includes(toolId) ? prev.filter((t) => t !== toolId) : [...prev, toolId]
    )
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const trimmedName = name.trim()
    const trimmedPrompt = systemPrompt.trim()
    if (!trimmedName) {
      setError('Agent name is required')
      return
    }
    if (!trimmedPrompt) {
      setError('System prompt is required')
      return
    }

    setIsSaving(true)
    setError(null)
    try {
      await onSave({
        name: trimmedName,
        emoji: emoji.trim() || '🤖',
        system_prompt: trimmedPrompt,
        model: model.trim() ? model.trim() : null,
        write_mode: writeMode,
        tool_allowlist: selectedTools,
      })
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <div className="v2-modal-overlay" onClick={onClose} role="dialog" aria-modal="true">
      <div className="v2-modal-card" onClick={(e) => e.stopPropagation()}>
        <h3 className="v2-modal-title">{agent ? 'Edit Agent' : 'Create New Agent'}</h3>

        {error && (
          <div style={{ color: 'var(--v2-rd)', fontSize: '13px' }} role="alert">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          <div style={{ display: 'flex', gap: '12px' }}>
            <div className="v2-form-group" style={{ width: '80px' }}>
              <label className="v2-form-label">Emoji</label>
              <input
                className="v2-form-input"
                value={emoji}
                onChange={(e) => setEmoji(e.target.value)}
                maxLength={4}
                aria-label="Agent Emoji"
              />
            </div>
            <div className="v2-form-group" style={{ flex: 1 }}>
              <label className="v2-form-label">Name</label>
              <input
                className="v2-form-input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Synthesis-Agent"
                required
                aria-label="Agent Name"
              />
            </div>
          </div>

          <div className="v2-form-group">
            <label className="v2-form-label">System Prompt / Instructions</label>
            <textarea
              className="v2-form-textarea"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="Define the agent role, capabilities, boundaries, and persona..."
              required
              rows={4}
              aria-label="Agent System Prompt"
            />
          </div>

          <div style={{ display: 'flex', gap: '12px' }}>
            <div className="v2-form-group" style={{ flex: 1 }}>
              <label className="v2-form-label">Model Override (optional)</label>
              <input
                className="v2-form-input"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="e.g. gpt-4o, claude-3-5-sonnet"
                aria-label="Agent Model"
              />
            </div>
            <div className="v2-form-group" style={{ flex: 1 }}>
              <label className="v2-form-label">Write Mode</label>
              <select
                className="v2-form-select"
                value={writeMode}
                onChange={(e) => setWriteMode(e.target.value as AgentWriteMode)}
                aria-label="Agent Write Mode"
              >
                <option value="proposal">Proposal (Review required)</option>
                <option value="direct">Direct (Write directly)</option>
              </select>
            </div>
          </div>

          <div className="v2-form-group">
            <label className="v2-form-label">Tool Allowlist (Read-Only Capabilities)</label>
            <div className="v2-checkbox-group">
              {AVAILABLE_TOOLS.map((tool) => (
                <label key={tool.id} className="v2-checkbox-label">
                  <input
                    type="checkbox"
                    checked={selectedTools.includes(tool.id)}
                    onChange={() => toggleTool(tool.id)}
                  />
                  <span>{tool.label}</span>
                </label>
              ))}
            </div>
          </div>

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '10px' }}>
            <button type="button" className="v2-chipbtn" onClick={onClose} disabled={isSaving}>
              Cancel
            </button>
            <button type="submit" className="v2-chipbtn prime" disabled={isSaving}>
              {isSaving ? 'Saving…' : agent ? 'Save Changes' : 'Create Agent'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
