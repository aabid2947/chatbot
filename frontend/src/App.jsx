import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import Composer from './components/Composer'
import Message, { TypingIndicator } from './components/Message'
import Sidebar from './components/Sidebar'
import { CloseIcon, MenuIcon, PlusIcon } from './components/icons'
import './App.css'

const SUGGESTIONS = ['Plan a 3-day trip', 'Explain some code', 'Draft an email', 'Summarize an article']

function readSavedTheme() {
  try {
    return localStorage.getItem('theme')
  } catch {
    return null
  }
}

export default function App() {
  const [conversations, setConversations] = useState([])
  const [activeId, setActiveId] = useState(null) // null = a new, unsaved chat
  // Messages plus which chat they belong to, so switching never shows the wrong chat.
  const [thread, setThread] = useState({ id: null, list: [] })
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState(false) // waiting on the bot
  const [pendingThread, setPendingThread] = useState(null)
  const [error, setError] = useState(null) // { text, retry? }
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const [theme, setTheme] = useState(readSavedTheme) // 'light' | 'dark' | null (follow system)
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia('(prefers-color-scheme: dark)').matches,
  )
  const isDark = theme ? theme === 'dark' : systemDark

  const inputRef = useRef(null)
  const scrollRef = useRef(null)
  const activeRef = useRef(null) // current chat, readable inside async handlers
  const justCreatedRef = useRef(null) // chat we created locally; don't refetch over it

  useEffect(() => {
    activeRef.current = activeId
  }, [activeId])

  // ---- Theme ----
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (e) => setSystemDark(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    const root = document.documentElement
    if (theme) root.dataset.theme = theme
    else delete root.dataset.theme
    try {
      if (theme) localStorage.setItem('theme', theme)
    } catch {
      /* storage blocked: theme still applies for this visit */
    }
  }, [theme])

  // ---- Data loading ----
  const loadConversations = useCallback(() => {
    api
      .listConversations()
      .then((list) => {
        setConversations(list)
        setError(null)
      })
      .catch((e) => setError({ text: e.message, retry: loadConversations }))
  }, [])

  useEffect(loadConversations, [loadConversations])

  useEffect(() => {
    if (activeId == null || justCreatedRef.current === activeId) return
    let cancelled = false
    api
      .listMessages(activeId)
      .then((list) => !cancelled && setThread({ id: activeId, list }))
      .catch((e) => !cancelled && setError({ text: e.message }))
    return () => {
      cancelled = true
    }
  }, [activeId])

  const shown = thread.id === activeId ? thread.list : []
  const showTyping = pending && pendingThread === activeId

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [shown.length, showTyping, activeId])

  // ---- Actions ----
  function openChat(id) {
    justCreatedRef.current = null
    setActiveId(id)
    setError(null)
    setSidebarOpen(false)
  }

  function newChat() {
    justCreatedRef.current = null
    setActiveId(null)
    setThread({ id: null, list: [] })
    setError(null)
    setSidebarOpen(false)
    inputRef.current?.focus()
  }

  async function deleteChat(convo) {
    if (!window.confirm(`Delete "${convo.title}"? This can't be undone.`)) return
    try {
      await api.deleteConversation(convo.id)
      setConversations((list) => list.filter((c) => c.id !== convo.id))
      if (convo.id === activeRef.current) newChat()
    } catch (e) {
      setError({ text: e.message })
    }
  }

  async function send(text) {
    const content = text.trim()
    if (!content || pending) return
    setError(null)
    setDraft('')
    setPending(true)

    // Show the user's message straight away.
    const temp = { id: `temp-${Date.now()}`, role: 'user', content }
    let id = activeId
    setPendingThread(id)
    setThread((t) => ({ id, list: t.id === id ? [...t.list, temp] : [temp] }))

    try {
      if (id == null) {
        const convo = await api.createConversation()
        id = convo.id
        justCreatedRef.current = id
        setConversations((list) => [convo, ...list])
        if (activeRef.current == null) {
          setThread((t) => (t.id == null ? { ...t, id } : t))
          setActiveId(id)
          activeRef.current = id
        }
        setPendingThread(id)
      }

      const res = await api.sendMessage(id, content)
      setConversations((list) => [res.conversation, ...list.filter((c) => c.id !== id)])
      setThread((t) =>
        t.id === id
          ? {
              id,
              list: [
                ...t.list.filter((m) => m.id !== temp.id && m.id !== res.user_message.id),
                res.user_message,
                res.reply,
              ],
            }
          : t,
      )
    } catch (e) {
      // Put the text back so nothing is lost.
      setThread((t) => ({ ...t, list: t.list.filter((m) => m.id !== temp.id) }))
      if (activeRef.current === id) setDraft(content)
      setError({ text: e.message, retry: () => send(content) })
    } finally {
      setPending(false)
      inputRef.current?.focus()
    }
  }

  async function retryLast() {
    const id = activeId
    const last = shown[shown.length - 1]
    if (pending || id == null || last?.role !== 'assistant') return
    setError(null)
    setPending(true)
    setPendingThread(id)
    setThread((t) => ({ ...t, list: t.list.slice(0, -1) }))
    try {
      const res = await api.regenerate(id)
      setThread((t) => (t.id === id ? { ...t, list: [...t.list, res.reply] } : t))
      setConversations((list) => [res.conversation, ...list.filter((c) => c.id !== id)])
    } catch (e) {
      setThread((t) => (t.id === id ? { ...t, list: [...t.list, last] } : t))
      setError({ text: e.message, retry: retryLast })
    } finally {
      setPending(false)
    }
  }

  function pickSuggestion(text) {
    setDraft(text)
    inputRef.current?.focus()
  }

  const activeTitle = conversations.find((c) => c.id === activeId)?.title ?? 'New chat'
  const isEmpty = shown.length === 0 && !showTyping && (activeId == null || thread.id === activeId)
  const lastIndex = shown.length - 1

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        open={sidebarOpen}
        isDark={isDark}
        onSelect={openChat}
        onNew={newChat}
        onDelete={deleteChat}
        onClose={() => setSidebarOpen(false)}
        onToggleTheme={() => setTheme(isDark ? 'light' : 'dark')}
      />

      <main className="main">
        <header className="topbar">
          <button
            className="icon-btn ghost mobile-only"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open chats"
          >
            <MenuIcon />
          </button>
          <h1 className="topbar-title">{activeTitle}</h1>
          <button className="icon-btn ghost mobile-only" onClick={newChat} aria-label="New chat">
            <PlusIcon size={18} />
          </button>
        </header>

        <div className="thread" ref={scrollRef}>
          <div className="thread-inner">
            {isEmpty ? (
              <div className="empty">
                <h2>How can I help today?</h2>
                <div className="suggestions">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} onClick={() => pickSuggestion(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              shown.map((m, i) => (
                <Message
                  key={m.id}
                  message={m}
                  canRetry={i === lastIndex && m.role === 'assistant' && !pending}
                  onRetry={retryLast}
                />
              ))
            )}
            {showTyping && <TypingIndicator />}
          </div>
        </div>

        {error && (
          <div className="error-row" role="alert">
            <span>{error.text}</span>
            <div className="error-actions">
              {error.retry && (
                <button className="error-retry" onClick={error.retry}>
                  Retry
                </button>
              )}
              <button className="error-close" onClick={() => setError(null)} aria-label="Dismiss">
                <CloseIcon />
              </button>
            </div>
          </div>
        )}

        <Composer
          value={draft}
          onChange={setDraft}
          onSubmit={() => send(draft)}
          busy={pending}
          inputRef={inputRef}
        />
      </main>
    </div>
  )
}
