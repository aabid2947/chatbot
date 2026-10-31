import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { CloseIcon, LockIcon, PencilIcon, PlusIcon, TrashIcon, UnlockIcon } from './icons'

const TIERS = [
  { key: 'pinned', label: 'Pinned', hint: 'Sent with every message' },
  { key: 'known', label: 'Known', hint: 'Looked up when it matters' },
  { key: 'archived', label: 'Archived', hint: 'Fading. Still searchable' },
]

const EVENT_LABELS = {
  created: 'Saved',
  used: 'Came up',
  promoted: 'You asked about it',
  edited: 'Edited',
  locked: 'Locked',
  unlocked: 'Unlocked',
}

const ORIGINS = {
  extracted: 'From a chat',
  remembered: 'You asked me to remember',
  promoted: 'You asked about it',
  manual: 'Added by you',
}

function when(iso) {
  if (!iso) return ''
  const date = new Date(iso)
  const today = new Date()
  const days = Math.round(
    (new Date(today.toDateString()) - new Date(date.toDateString())) / (24 * 60 * 60 * 1000),
  )
  if (days === 0) return 'today'
  if (days === 1) return 'yesterday'
  if (days < 7) return `${days} days ago`
  return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

function usage(fact) {
  const times = fact.use_count === 1 ? 'once' : `${fact.use_count} times`
  if (!fact.use_count) return `Saved ${when(fact.created_at)}`
  return `Came up ${times}, last ${when(fact.last_used_at)}`
}

function ScoreBar({ fact }) {
  return (
    <div
      className={`score-bar ${fact.tier}`}
      role="meter"
      aria-valuemin={0}
      aria-valuemax={1}
      aria-valuenow={fact.score}
      aria-label={`Confidence ${Math.round(fact.score * 100)}%`}
      title={`Confidence ${Math.round(fact.score * 100)}%`}
    >
      <span style={{ width: `${Math.round(fact.score * 100)}%` }} />
    </div>
  )
}

function FactRow({ fact, onChanged, onDeleted, onError }) {
  const [open, setOpen] = useState(false)
  const [history, setHistory] = useState(null)
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(fact.content)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    api
      .getFact(fact.id)
      .then((f) => !cancelled && setHistory(f.history))
      .catch((e) => onError(e.message))
    return () => {
      cancelled = true
    }
  }, [open, fact.id, fact.score, onError])

  async function save(e) {
    e.preventDefault()
    const content = text.trim()
    if (!content || content === fact.content) return setEditing(false)
    try {
      onChanged(await api.updateFact(fact.id, { content }))
      setEditing(false)
    } catch (err) {
      onError(err.message)
    }
  }

  async function toggleLock() {
    try {
      onChanged(await api.updateFact(fact.id, { locked: !fact.locked }))
    } catch (err) {
      onError(err.message)
    }
  }

  async function remove() {
    if (!window.confirm(`Forget "${fact.content}"?`)) return
    try {
      await api.deleteFact(fact.id)
      onDeleted(fact.id)
    } catch (err) {
      onError(err.message)
    }
  }

  return (
    <li className={`fact ${open ? 'open' : ''}`}>
      {editing ? (
        <form className="fact-edit" onSubmit={save}>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) save(e)
              if (e.key === 'Escape') {
                e.stopPropagation()
                setEditing(false)
                setText(fact.content)
              }
            }}
            rows={2}
            autoFocus
            aria-label="Fact"
          />
          <div className="fact-edit-actions">
            <button type="button" className="btn small" onClick={() => setEditing(false)}>
              Cancel
            </button>
            <button type="submit" className="btn small primary">
              Save
            </button>
          </div>
        </form>
      ) : (
        <button className="fact-main" onClick={() => setOpen(!open)} aria-expanded={open}>
          <span className="fact-text">{fact.content}</span>
          <span className="fact-meta">
            <ScoreBar fact={fact} />
            {fact.locked && (
              <span className="fact-lock" title="Locked: never fades">
                <LockIcon size={12} />
              </span>
            )}
            <span>{usage(fact)}</span>
          </span>
        </button>
      )}

      {open && !editing && (
        <div className="fact-details">
          <p className="fact-origin">
            {ORIGINS[fact.origin] ?? fact.origin}
            {fact.tier === 'pinned' && fact.in_prompt === false && ' · Over the size limit, so not sent right now'}
          </p>
          {history && (
            <ol className="fact-history">
              {history
                .slice()
                .reverse()
                .map((e, i) => (
                  <li key={i}>
                    <span>{EVENT_LABELS[e.kind] ?? e.kind}</span>
                    <span className="fact-history-score">
                      {e.delta && e.kind !== 'created' ? `${e.delta > 0 ? '+' : ''}${e.delta.toFixed(2)} → ` : ''}
                      {e.score_after.toFixed(2)}
                    </span>
                    <span className="fact-history-when">{when(e.created_at)}</span>
                  </li>
                ))}
            </ol>
          )}
          <div className="fact-actions">
            <button className="msg-action" onClick={() => setEditing(true)}>
              <PencilIcon /> Edit
            </button>
            <button className="msg-action" onClick={toggleLock}>
              {fact.locked ? <UnlockIcon /> : <LockIcon />} {fact.locked ? 'Unlock' : 'Lock'}
            </button>
            <button className="msg-action danger" onClick={remove}>
              <TrashIcon /> Forget
            </button>
          </div>
        </div>
      )}
    </li>
  )
}

export default function MemoryPanel({ open, refreshKey, onClose }) {
  const [facts, setFacts] = useState(null)
  const [error, setError] = useState(null)
  const [draft, setDraft] = useState('')
  const [showArchived, setShowArchived] = useState(false)
  const closeRef = useRef(null)

  const load = useCallback(() => {
    api
      .listFacts()
      .then((data) => {
        setFacts(data.facts)
        setError(null)
      })
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    if (open) load()
  }, [open, refreshKey, load])

  useEffect(() => {
    if (!open) return
    closeRef.current?.focus()
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  async function add(e) {
    e.preventDefault()
    const content = draft.trim()
    if (!content) return
    try {
      await api.addFact(content)
      setDraft('')
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  // Edits can move a fact between tiers, so reload rather than patch in place.
  const changed = () => load()
  const deleted = (id) => setFacts((list) => list.filter((f) => f.id !== id))

  return (
    <>
      <div className={`panel-backdrop ${open ? 'show' : ''}`} onClick={onClose} />
      <aside className={`memory-panel ${open ? 'open' : ''}`} aria-label="Memory" aria-hidden={!open} inert={!open}>
        <header className="panel-head">
          <div>
            <h2>Memory</h2>
            <p>What I remember about you, across every chat.</p>
          </div>
          <button ref={closeRef} className="icon-btn" onClick={onClose} aria-label="Close memory">
            <CloseIcon size={16} />
          </button>
        </header>

        <form className="fact-add" onSubmit={add}>
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Add something to remember…"
            aria-label="Add something to remember"
          />
          <button type="submit" className="icon-btn" disabled={!draft.trim()} aria-label="Add">
            <PlusIcon />
          </button>
        </form>

        {error && <p className="dialog-error panel-error">{error}</p>}

        <div className="panel-scroll">
          {facts?.length === 0 && (
            <p className="panel-empty">
              Nothing yet. Tell me about yourself in a chat, or say “remember that…”, and it shows up here.
            </p>
          )}
          {facts &&
            TIERS.map((tier) => {
              const list = facts.filter((f) => f.tier === tier.key)
              if (!list.length) return null
              const collapsed = tier.key === 'archived' && !showArchived
              return (
                <section key={tier.key} className="tier">
                  <h3>
                    {tier.key === 'archived' ? (
                      <button className="tier-toggle" onClick={() => setShowArchived(!showArchived)}>
                        {tier.label} <span className="tier-count">{list.length}</span>
                        <span className="tier-hint">{collapsed ? 'Show' : 'Hide'}</span>
                      </button>
                    ) : (
                      <>
                        {tier.label} <span className="tier-count">{list.length}</span>
                        <span className="tier-hint">{tier.hint}</span>
                      </>
                    )}
                  </h3>
                  {!collapsed && (
                    <ul>
                      {list.map((fact) => (
                        <FactRow
                          key={fact.id}
                          fact={fact}
                          onChanged={changed}
                          onDeleted={deleted}
                          onError={setError}
                        />
                      ))}
                    </ul>
                  )}
                </section>
              )
            })}
        </div>
      </aside>
    </>
  )
}
