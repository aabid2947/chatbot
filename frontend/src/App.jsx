import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import Composer from './components/Composer'
import { DocumentChips, DocumentDialog } from './components/Documents'
import MemoryPanel from './components/MemoryPanel'
import Message, { BotMark, LiveReply, TypingIndicator } from './components/Message'
import Sidebar from './components/Sidebar'
import { CloseIcon, FileIcon, MenuIcon, PlusIcon, RetryIcon } from './components/icons'
import './App.css'

const SUGGESTIONS = ['Plan a 3-day trip', 'Explain some code', 'Draft an email', 'Summarize an article']
const DOC_POLL_MS = 1500

function readSavedTheme() {
  try {
    return localStorage.getItem('theme')
  } catch {
    return null
  }
}

const isPdf = (file) => file.type === 'application/pdf' || /\.pdf$/i.test(file.name)
const hasFiles = (e) => [...(e.dataTransfer?.types ?? [])].includes('Files')

export default function App() {
  const [conversations, setConversations] = useState([])
  const [activeId, setActiveId] = useState(null) // null = a new, unsaved chat
  // Messages plus which chat they belong to, so switching never shows the wrong chat.
  const [thread, setThread] = useState({ id: null, list: [] })
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState(false) // waiting on the bot
  const [pendingThread, setPendingThread] = useState(null)
  const [live, setLive] = useState(null) // the reply being streamed: { chatId, text, tool }
  const [docs, setDocs] = useState({ chatId: null, list: [] }) // this chat's PDFs
  const [openDocId, setOpenDocId] = useState(null)
  const [dragging, setDragging] = useState(false)
  const [memoryOpen, setMemoryOpen] = useState(false)
  const [memoryVersion, setMemoryVersion] = useState(0)
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
  const threadRef = useRef(thread)
  const busyRef = useRef(false) // set synchronously, so double clicks can't send twice
  const justCreatedRef = useRef(null) // chat we created locally; don't refetch over it
  const dragDepth = useRef(0)
  const timers = useRef([])

  useEffect(() => {
    activeRef.current = activeId
  }, [activeId])
  useEffect(() => {
    threadRef.current = thread
  }, [thread])
  useEffect(() => () => timers.current.forEach(clearTimeout), [])

  const later = (fn, ms) => timers.current.push(setTimeout(fn, ms))

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
        setError((e) => (e?.retry === loadConversations ? null : e))
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

  // The chat's PDFs. Uploads still in flight are kept while the list reloads.
  const loadDocs = useCallback((chatId) => {
    return api.listDocuments(chatId).then((list) =>
      setDocs((d) => ({
        chatId,
        list: [...list, ...(d.chatId === chatId ? d.list.filter((x) => x.uploading) : [])],
      })),
    )
  }, [])

  useEffect(() => {
    // A new, unsaved chat shows no PDFs: shownDocs only shows the active chat's.
    if (activeId == null) return
    loadDocs(activeId).catch((e) => setError({ text: e.message }))
  }, [activeId, loadDocs])

  const shownDocs = docs.chatId === activeId ? docs.list : []
  const docsBusy = shownDocs.some((d) => d.status === 'processing')

  // Watch PDFs that are still being read, so their chips move on by themselves.
  useEffect(() => {
    if (!docsBusy || activeId == null) return
    const timer = setInterval(() => loadDocs(activeId).catch(() => {}), DOC_POLL_MS)
    return () => clearInterval(timer)
  }, [docsBusy, activeId, loadDocs])

  const shown = thread.id === activeId ? thread.list : []
  const liveHere = live && live.chatId === activeId ? live : null
  const showTyping = pending && pendingThread === activeId && !liveHere

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [shown.length, showTyping, activeId, liveHere?.text, liveHere?.tool])

  // ---- Actions ----
  function openChat(id) {
    justCreatedRef.current = null
    setActiveId(id)
    setError(null)
    setSidebarOpen(false)
    setOpenDocId(null)
  }

  function newChat() {
    justCreatedRef.current = null
    setActiveId(null)
    setThread({ id: null, list: [] })
    setError(null)
    setSidebarOpen(false)
    setOpenDocId(null)
    inputRef.current?.focus()
  }

  async function deleteChat(convo) {
    if (!window.confirm(`Delete "${convo.title}"? Its PDFs are deleted too. This can't be undone.`)) return
    try {
      await api.deleteConversation(convo.id)
      setConversations((list) => list.filter((c) => c.id !== convo.id))
      if (convo.id === activeRef.current) newChat()
    } catch (e) {
      setError({ text: e.message })
    }
  }

  /** Create the chat on the server when the first thing happens in a new one. */
  async function createChat() {
    const convo = await api.createConversation()
    justCreatedRef.current = convo.id
    setConversations((list) => [convo, ...list])
    if (activeRef.current == null) {
      setThread((t) => (t.id == null ? { ...t, id: convo.id } : t))
      setActiveId(convo.id)
      activeRef.current = convo.id
    }
    return convo.id
  }

  /** Stream events for one reply into `live`, and the finished reply into the thread. */
  function replyHandler(id, onDone) {
    let failure = null
    const handle = (event, data) => {
      if (event === 'tool') setLive((l) => l && { ...l, tool: data.label })
      else if (event === 'delta') setLive((l) => l && { ...l, text: l.text + data.text, tool: null })
      else if (event === 'error') failure = data.error
      else if (event === 'done') {
        setThread((t) => (t.id === id ? { id, list: [...t.list, data.reply] } : t))
        setConversations((list) => [data.conversation, ...list.filter((c) => c.id !== id)])
        // Facts are picked out in the background a few seconds after a reply.
        later(() => setMemoryVersion((v) => v + 1), 9000)
        onDone?.()
      }
    }
    return { handle, failed: () => failure }
  }

  function finishReply() {
    setLive(null)
    setPending(false)
    busyRef.current = false
    inputRef.current?.focus()
  }

  async function send(text) {
    const content = text.trim()
    if (!content || busyRef.current) return
    busyRef.current = true
    setError(null)
    setDraft('')
    setPending(true)

    // Show the user's message straight away.
    const temp = { id: `temp-${Date.now()}`, role: 'user', content }
    let id = activeRef.current
    const isFirst = id == null || threadRef.current.list.length === 0
    let saved = false // the server has the message, so retrying must not send it again
    setPendingThread(id)
    setThread((t) => ({ id, list: t.id === id ? [...t.list, temp] : [temp] }))

    try {
      if (id == null) id = await createChat()
      setPendingThread(id)
      setLive({ chatId: id, text: '', tool: null })
      const reply = replyHandler(id, () => {
        // The title is written in the background just after the first reply.
        if (isFirst) [7000, 15000].forEach((ms) => later(loadConversations, ms))
      })
      await api.streamMessage(id, content, (event, data) => {
        if (event === 'user_message') {
          saved = true
          setThread((t) =>
            t.id === id ? { id, list: t.list.map((m) => (m.id === temp.id ? data : m)) } : t,
          )
        } else reply.handle(event, data)
      })
      if (reply.failed()) throw new Error(reply.failed())
    } catch (e) {
      if (saved) {
        setError({ text: e.message, retry: retryLast })
      } else {
        // Put the text back so nothing is lost.
        setThread((t) => ({ ...t, list: t.list.filter((m) => m.id !== temp.id) }))
        if (activeRef.current === id) setDraft(content)
        setError({ text: e.message, retry: () => send(content) })
      }
    } finally {
      finishReply()
    }
  }

  /** A fresh reply to the last message: replaces the last reply, or answers a
   * message whose reply failed. Reads refs, so a stale Retry button still works. */
  async function retryLast() {
    const id = activeRef.current
    const t = threadRef.current
    const last = t.id === id ? t.list[t.list.length - 1] : null
    if (busyRef.current || id == null || !last) return
    busyRef.current = true
    const replacing = last.role === 'assistant'
    setError(null)
    setPending(true)
    setPendingThread(id)
    if (replacing) setThread((cur) => (cur.id === id ? { ...cur, list: cur.list.slice(0, -1) } : cur))
    setLive({ chatId: id, text: '', tool: null })
    try {
      const reply = replyHandler(id)
      await api.streamRegenerate(id, reply.handle)
      if (reply.failed()) throw new Error(reply.failed())
    } catch (e) {
      if (replacing) {
        setThread((cur) =>
          cur.id === id && !cur.list.includes(last) ? { ...cur, list: [...cur.list, last] } : cur,
        )
      }
      setError({ text: e.message, retry: retryLast })
    } finally {
      finishReply()
    }
  }

  // ---- PDFs ----
  async function upload(files) {
    const pdfs = files.filter(isPdf)
    if (pdfs.length < files.length) setError({ text: 'Only PDF files can be added.' })
    if (!pdfs.length) return
    let id = activeRef.current
    try {
      if (id == null) id = await createChat()
    } catch (e) {
      setError({ text: e.message })
      return
    }
    for (const file of pdfs) {
      const temp = { id: `upload-${Date.now()}-${file.name}`, filename: file.name, uploading: true }
      setDocs((d) => (d.chatId === id ? { ...d, list: [...d.list, temp] } : { chatId: id, list: [temp] }))
      try {
        const doc = await api.uploadDocument(id, file)
        setDocs((d) =>
          d.chatId === id ? { ...d, list: d.list.map((x) => (x.id === temp.id ? doc : x)) } : d,
        )
      } catch (e) {
        setDocs((d) => ({ ...d, list: d.list.filter((x) => x.id !== temp.id) }))
        setError({ text: `${file.name}: ${e.message}` })
      }
    }
    loadConversations() // an upload moves the chat to the top
  }

  async function removeDoc(doc) {
    if (!window.confirm(`Remove "${doc.filename}" from this chat?`)) return
    try {
      await api.deleteDocument(doc.id)
      setDocs((d) => ({ ...d, list: d.list.filter((x) => x.id !== doc.id) }))
      if (openDocId === doc.id) setOpenDocId(null)
    } catch (e) {
      setError({ text: e.message })
    }
  }

  async function retryDoc(doc) {
    try {
      const fresh = await api.retryDocument(doc.id)
      setDocs((d) => ({ ...d, list: d.list.map((x) => (x.id === doc.id ? fresh : x)) }))
    } catch (e) {
      setError({ text: e.message })
    }
  }

  const dropHandlers = {
    onDragEnter: (e) => {
      if (!hasFiles(e)) return
      dragDepth.current += 1
      setDragging(true)
    },
    onDragLeave: (e) => {
      if (!hasFiles(e)) return
      dragDepth.current = Math.max(0, dragDepth.current - 1)
      if (dragDepth.current === 0) setDragging(false)
    },
    onDragOver: (e) => {
      if (hasFiles(e)) e.preventDefault()
    },
    onDrop: (e) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      dragDepth.current = 0
      setDragging(false)
      upload([...e.dataTransfer.files])
    },
  }

  function pickSuggestion(text) {
    setDraft(text)
    inputRef.current?.focus()
  }

  const activeTitle = conversations.find((c) => c.id === activeId)?.title ?? 'New chat'
  const isEmpty =
    shown.length === 0 && !showTyping && !liveHere && (activeId == null || thread.id === activeId)
  const lastIndex = shown.length - 1
  const last = shown[lastIndex]
  const replyMissing =
    !pending && last?.role === 'user' && typeof last.id === 'number' && !error
  const openDoc = shownDocs.find((d) => d.id === openDocId)

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
        onOpenMemory={() => {
          setMemoryOpen(true)
          setSidebarOpen(false)
        }}
      />

      <main className="main" {...dropHandlers}>
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
                <p className="empty-hint">Or add a PDF with + and ask about it.</p>
              </div>
            ) : (
              shown.map((m, i) => (
                <Message
                  key={m.id}
                  message={m}
                  documents={shownDocs}
                  canRetry={i === lastIndex && m.role === 'assistant' && !pending}
                  onRetry={retryLast}
                />
              ))
            )}
            {liveHere && <LiveReply text={liveHere.text} tool={liveHere.tool} documents={shownDocs} />}
            {showTyping && <TypingIndicator />}
            {replyMissing && (
              <div className="msg-bot">
                <BotMark />
                <div className="msg-missing">
                  No reply to this yet.
                  <button className="msg-action" onClick={retryLast}>
                    <RetryIcon /> Get a reply
                  </button>
                </div>
              </div>
            )}
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
          onAttach={upload}
          busy={pending}
          inputRef={inputRef}
        >
          <DocumentChips
            documents={shownDocs}
            onOpen={(doc) => setOpenDocId(doc.id)}
            onRemove={removeDoc}
          />
        </Composer>

        {dragging && (
          <div className="drop-overlay" aria-hidden="true">
            <div className="drop-card">
              <FileIcon size={28} />
              <b>Drop a PDF to add it to this chat</b>
              <span>Then ask anything about it</span>
            </div>
          </div>
        )}
      </main>

      {openDoc && (
        <DocumentDialog
          key={openDoc.id}
          document={openDoc}
          onClose={() => setOpenDocId(null)}
          onRemove={removeDoc}
          onRetry={retryDoc}
        />
      )}

      <MemoryPanel
        open={memoryOpen}
        refreshKey={memoryVersion}
        onClose={() => setMemoryOpen(false)}
      />
    </div>
  )
}
