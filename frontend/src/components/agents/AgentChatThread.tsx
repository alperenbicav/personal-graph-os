import { useState, useRef, useEffect, type KeyboardEvent } from 'react'
import type { Agent } from '../../types'
import * as api from '../../api/client'

export interface AgentChatThreadProps {
  agent: Agent
  onClose?: () => void
  onMessageSent?: () => void
  isLlmConfigured?: boolean
}

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: string
}

export function AgentChatThread({
  agent,
  onClose,
  onMessageSent,
  isLlmConfigured = true,
}: AgentChatThreadProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 'welcome',
      role: 'assistant',
      content: `Hello! I am ${agent.name} ${agent.emoji}. How can I assist you with the knowledge graph today?`,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    },
  ])
  const [input, setInput] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView?.({ behavior: 'smooth' })
  }, [messages])

  async function handleSend() {
    const trimmed = input.trim()
    if (!trimmed || isSending || !isLlmConfigured) return

    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: trimmed,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    }

    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setIsSending(true)
    setError(null)

    const assistantId = `assistant-${Date.now()}`
    let streamedContent = ''
    try {
      const res = await api.messageAgent(agent.id, trimmed, undefined, (delta) => {
        streamedContent += delta
        setMessages((prev) => {
          const last = prev[prev.length - 1]
          if (last && last.id === assistantId) {
            return [...prev.slice(0, -1), { ...last, content: streamedContent }]
          }
          return [
            ...prev,
            {
              id: assistantId,
              role: 'assistant',
              content: streamedContent,
              timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            },
          ]
        })
      })
      setMessages((prev) => {
        const last = prev[prev.length - 1]
        if (last && last.id === assistantId) {
          return [...prev.slice(0, -1), { ...last, content: res.reply || streamedContent }]
        }
        return [
          ...prev,
          {
            id: assistantId,
            role: 'assistant',
            content: res.reply,
            timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
          },
        ]
      })
      onMessageSent?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setIsSending(false)
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const isInputDisabled = isSending || !isLlmConfigured

  return (
    <div className="v2-chat-panel" aria-label={`Chat with ${agent.name}`}>
      <div
        style={{
          padding: '12px 16px',
          borderBottom: '1px solid var(--v2-line)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          background: 'var(--v2-s1)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{ fontSize: '20px' }}>{agent.emoji}</span>
          <div>
            <div style={{ fontWeight: 600, color: 'var(--v2-t1)', fontSize: '14px' }}>
              {agent.name}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--v2-t3)' }}>
              {agent.model || 'Default model'} · {agent.write_mode}
            </div>
          </div>
        </div>
        {onClose && (
          <button type="button" className="v2-chipbtn" onClick={onClose} title="Close Chat">
            ✕
          </button>
        )}
      </div>

      <div className="v2-chat-messages">
        {messages.map((msg) => (
          <div key={msg.id} className={`v2-chat-msg ${msg.role}`}>
            <div className="v2-chat-bubble">{msg.content}</div>
            <span style={{ fontSize: '10.5px', color: 'var(--v2-t3)', padding: '0 4px' }}>
              {msg.timestamp}
            </span>
          </div>
        ))}
        {isSending && (
          <div className="v2-chat-msg assistant">
            <div className="v2-chat-bubble" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span className="v2-pulse" />
              <span style={{ color: 'var(--v2-t2)' }}>{agent.name} is thinking…</span>
            </div>
          </div>
        )}
        {error && (
          <div style={{ color: 'var(--v2-rd)', fontSize: '12.5px', padding: '6px 12px' }} role="alert">
            Error: {error}
          </div>
        )}
        {!isLlmConfigured && (
          <div style={{ color: 'var(--v2-am)', fontSize: '12px', padding: '6px 12px' }} role="status">
            ⚠️ LLM provider is not configured. Set PGOS_LLM_API_KEY in server environment to enable live responses.
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div
        style={{
          padding: '12px 14px',
          borderTop: '1px solid var(--v2-line)',
          background: 'var(--v2-s1)',
          display: 'flex',
          gap: '8px',
        }}
      >
        <input
          className="v2-form-input"
          style={{ flex: 1 }}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            !isLlmConfigured
              ? 'LLM not configured (PGOS_LLM_* env required)'
              : `Message ${agent.name}…`
          }
          disabled={isInputDisabled}
          aria-label="Message Input"
        />
        <button
          type="button"
          className="v2-chipbtn prime"
          onClick={handleSend}
          disabled={isInputDisabled || !input.trim()}
        >
          Send
        </button>
      </div>
    </div>
  )
}

