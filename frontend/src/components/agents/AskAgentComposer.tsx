import React, { useState, useRef } from 'react'
import type { Agent } from '../../types'

export interface AskAgentComposerProps {
  agents: Agent[]
  selectedAgentId?: string | null
  onSelectAgent?: (id: string) => void
  onSubmit: (agentId: string, prompt: string) => Promise<void>
  isSubmitting?: boolean
}

export function AskAgentComposer({
  agents,
  selectedAgentId,
  onSelectAgent,
  onSubmit,
  isSubmitting = false,
}: AskAgentComposerProps) {
  const [prompt, setPrompt] = useState('')
  const currentAgentId = selectedAgentId || (agents.length > 0 ? agents[0].id : '')
  const inputRef = useRef<HTMLTextAreaElement>(null)

  async function handleSend() {
    const trimmed = prompt.trim()
    if (!trimmed || !currentAgentId || isSubmitting) return
    setPrompt('')
    await onSubmit(currentAgentId, trimmed)
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault()
      handleSend()
    } else if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="v2-ask-box">
      <div className="v2-ask-row">
        {agents.length > 1 && (
          <select
            className="v2-ask-select"
            value={currentAgentId}
            onChange={(e) => onSelectAgent?.(e.target.value)}
            disabled={isSubmitting}
            aria-label="Target Agent"
          >
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.emoji} {a.name}
              </option>
            ))}
          </select>
        )}
        <span className="v2-kbd">⌘↵</span>
      </div>
      <div className="v2-ask-row">
        <textarea
          ref={inputRef}
          rows={2}
          className="v2-ask-input"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={isSubmitting ? 'Agent is working…' : '✦ Ask an agent to research, plan, or execute…'}
          disabled={isSubmitting}
          aria-label="Ask Agent Prompt"
        />
      </div>
    </div>
  )
}
