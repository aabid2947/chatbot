import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import './App.css'

export default function App() {
  const [conversations, setConversations] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [messages, setMessages] = useState([])
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState(null)
  const bottomRef = useRef(null)
  const inputRef = useRef(null)
  // Which chat is on screen right now, readable from inside async handlers.
  const activeRef = useRef(null)
  useEffect(() => {
    activeRef.current = activeId
  }, [activeId])

  useEffect(() => {
    api
      .listConversations()
      .then((list) => {
        setConversations(list)
        if (list.length) setActiveId(list[0].id)
      })
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    if (activeId == null) return
    let cancelled = false
    api
      .listMessages(activeId)
      .then((m) => !cancelled && setMessages(m))
      .catch((e) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [activeId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, sending])

  async function newChat() {
    setError(null)
    try {
      const convo = await api.createConversation()
      setConversations((list) => [convo, ...list])
      setActiveId(convo.id)
      inputRef.current?.focus()
    } catch (e) {
      setError(e.message)
    }
  }

  async function removeChat(id) {
    setError(null)
    try {
      await api.deleteConversation(id)
      const rest = conversations.filter((c) => c.id !== id)
      setConversations(rest)
      if (id === activeId) setActiveId(rest[0]?.id ?? null)
    } catch (e) {
      setError(e.message)
    }
  }

  async function send(e) {
    e.preventDefault()
    const content = draft.trim()
    if (!content || sending) return
    setError(null)
    setSending(true)

    let id = activeId
    try {
      if (id == null) {
        const convo = await api.createConversation()
        setConversations((list) => [convo, ...list])
        setActiveId(convo.id)
        id = convo.id
        activeRef.current = id
      }
      // Show the user's message right away; replace it with the saved one after.
      const pending = { id: `pending-${Date.now()}`, role: 'user', content }
      setMessages((m) => [...m, pending])
      setDraft('')

      const res = await api.sendMessage(id, content)
      // Skip if the user moved to another chat meanwhile; it reloads from the server.
      if (activeRef.current === id) {
        setMessages((m) => [
          ...m.filter((x) => x.id !== pending.id && x.id !== res.user_message.id),
          res.user_message,
          res.reply,
        ])
      }
      setConversations((list) => [
        res.conversation,
        ...list.filter((c) => c.id !== res.conversation.id),
      ])
    } catch (e) {
      setError(e.message)
      setDraft(content)
      setMessages((m) => m.filter((x) => !String(x.id).startsWith('pending-')))
    } finally {
      setSending(false)
      inputRef.current?.focus()
    }
  }

  const shown = activeId == null ? [] : messages

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) send(e)
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <button className="new-chat" onClick={newChat}>
          + New chat
        </button>
        <ul className="chat-list">
          {conversations.map((c) => (
            <li key={c.id} className={c.id === activeId ? 'active' : ''}>
              <button className="chat-title" onClick={() => setActiveId(c.id)} title={c.title}>
                {c.title}
              </button>
              <button
                className="chat-delete"
                onClick={() => removeChat(c.id)}
                aria-label={`Delete ${c.title}`}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <main className="chat">
        <div className="messages">
          {shown.length === 0 && !sending && (
            <div className="empty">
              <h1>Chatbot</h1>
              <p>Send a message to start a conversation.</p>
            </div>
          )}
          {shown.map((m) => (
            <div key={m.id} className={`bubble ${m.role}`}>
              {m.content}
            </div>
          ))}
          {sending && <div className="bubble assistant typing">…</div>}
          <div ref={bottomRef} />
        </div>

        {error && <div className="error">{error}</div>}

        <form className="composer" onSubmit={send}>
          <textarea
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Type a message…"
            rows={1}
            autoFocus
          />
          <button type="submit" disabled={sending || !draft.trim()}>
            Send
          </button>
        </form>
      </main>
    </div>
  )
}
