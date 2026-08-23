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

  const [nameTouched, setNameTouched] = useState(false)
  const [promptTouched, setPromptTouched] = useState(false)

  const nameError = (nameTouched || error) && !name.trim() ? 'Agent name is required' : null
  const promptError = (promptTouched || error) && !systemPrompt.trim() ? 'System prompt is required' : null

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
    setNameTouched(false)
    setPromptTouched(false)
  }, [agent, isOpen])

  useEffect(() => {
    if (!isOpen) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, onClose])

  if (!isOpen) return null

  function toggleTool(toolId: string) {
    setSelectedTools((prev) =>
      prev.includes(toolId) ? prev.filter((t) => t !== toolId) : [...prev, toolId]
    )
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setNameTouched(true)
    setPromptTouched(true)

    const trimmedName = name.trim()
    const trimmedPrompt = systemPrompt.trim()
    if (!trimmedName) {
      setError('Please provide an agent name.')
      return
    }
    if (!trimmedPrompt) {
      setError('Please provide a system prompt.')
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
    <div className="v2-modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="agent-form-title">
      <div className="v2-modal-card" onClick={(e) => e.stopPropagation()}>
        <h3 id="agent-form-title" className="v2-modal-title">{agent ? 'Edit Agent' : 'Create New Agent'}</h3>

        {error && (
          <div className="v2-form-error-alert" role="alert">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          <div style={{ display: 'flex', gap: '12px' }}>
            <div className="v2-form-group" style={{ width: '80px' }}>
              <label className="v2-form-label" htmlFor="agent-emoji-input">Emoji</label>
              <input
                id="agent-emoji-input"
                className="v2-form-input"
                value={emoji}
                onChange={(e) => setEmoji(e.target.value)}
                maxLength={4}
                aria-label="Agent Emoji"
              />
            </div>
            <div className="v2-form-group" style={{ flex: 1 }}>
              <label className="v2-form-label" htmlFor="agent-name-input">Name</label>
              <input
                id="agent-name-input"
                className={`v2-form-input ${nameError ? 'error' : ''}`}
                value={name}
                onChange={(e) => {
                  setName(e.target.value)
                  if (!nameTouched) setNameTouched(true)
                }}
                onBlur={() => setNameTouched(true)}
                placeholder="e.g. Synthesis-Agent"
                aria-invalid={Boolean(nameError)}
                aria-describedby={nameError ? 'agent-name-error' : undefined}
                aria-label="Agent Name"
              />
              {nameError && (
                <span id="agent-name-error" className="v2-field-error" role="alert">
                  {nameError}
                </span>
              )}
            </div>
          </div>

          <div className="v2-form-group">
            <label className="v2-form-label" htmlFor="agent-prompt-input">System Prompt / Instructions</label>
            <textarea
              id="agent-prompt-input"
              className={`v2-form-textarea ${promptError ? 'error' : ''}`}
              value={systemPrompt}
              onChange={(e) => {
                setSystemPrompt(e.target.value)
                if (!promptTouched) setPromptTouched(true)
              }}
              onBlur={() => setPromptTouched(true)}
              placeholder="Define the agent role, capabilities, boundaries, and persona..."
              rows={4}
              aria-invalid={Boolean(promptError)}
              aria-describedby={promptError ? 'agent-prompt-error' : undefined}
              aria-label="Agent System Prompt"
            />
            {promptError && (
              <span id="agent-prompt-error" className="v2-field-error" role="alert">
                {promptError}
              </span>
            )}
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
