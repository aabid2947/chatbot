import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import { CloseIcon, ExternalIcon, FileIcon, RetryIcon, TrashIcon } from './icons'

const STAGES = {
  reading: 'Reading…',
  indexing: 'Indexing…',
  summarizing: 'Summarizing…',
}

function documentState(doc) {
  if (doc.uploading) return { label: 'Uploading…', busy: true }
  if (doc.status === 'failed') return { label: "Couldn't read", failed: true }
  if (doc.status === 'processing') return { label: STAGES[doc.stage] ?? 'Processing…', busy: true }
  return { label: doc.pages === 1 ? '1 page' : `${doc.pages} pages` }
}

function formatSize(bytes) {
  if (bytes == null) return ''
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** The chat's PDFs as chips above the message box. */
export function DocumentChips({ documents, onOpen, onRemove }) {
  if (!documents.length) return null
  return (
    <ul className="doc-chips" aria-label="PDFs in this chat">
      {documents.map((doc) => {
        const state = documentState(doc)
        return (
          <li
            key={doc.id}
            className={`doc-chip ${state.failed ? 'failed' : ''} ${state.busy ? 'busy' : ''}`}
          >
            <button
              className="doc-chip-main"
              onClick={() => onOpen(doc)}
              disabled={doc.uploading}
              title={doc.one_line || doc.filename}
            >
              {state.busy ? <span className="spinner" aria-hidden="true" /> : <FileIcon />}
              <span className="doc-chip-name">{doc.filename}</span>
              <span className="doc-chip-state">{state.label}</span>
            </button>
            {!doc.uploading && (
              <button
                className="doc-chip-remove"
                onClick={() => onRemove(doc)}
                aria-label={`Remove ${doc.filename}`}
                title="Remove PDF"
              >
                <CloseIcon size={12} />
              </button>
            )}
          </li>
        )
      })}
    </ul>
  )
}

/** Details of one PDF: what it is, its summary, and actions. */
export function DocumentDialog({ document: doc, onClose, onRemove, onRetry }) {
  const ref = useRef(null)
  const [details, setDetails] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    const dialog = ref.current
    if (dialog && !dialog.open) dialog.showModal()
  }, [])

  // Load the summary, and again whenever the PDF moves on a stage.
  useEffect(() => {
    let cancelled = false
    api
      .getDocument(doc.id)
      .then((d) => !cancelled && setDetails(d))
      .catch((e) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [doc.id, doc.status, doc.stage])

  const state = documentState(doc)
  const summary = details?.summary

  return (
    <dialog
      ref={ref}
      className="dialog"
      onClose={onClose}
      onClick={(e) => e.target === ref.current && ref.current.close()}
      aria-labelledby="doc-dialog-title"
    >
      {/* Focus lands on the body, not the close button, so no ring flashes on open. */}
      <div className="dialog-body" tabIndex={-1} autoFocus>
        <header className="dialog-head">
          <FileIcon size={18} />
          <div className="dialog-title-wrap">
            <h2 id="doc-dialog-title">{doc.filename}</h2>
            <p className="dialog-sub">
              {[state.label, formatSize(doc.size_bytes)].filter(Boolean).join(' · ')}
            </p>
          </div>
          <button className="icon-btn" onClick={() => ref.current.close()} aria-label="Close">
            <CloseIcon size={16} />
          </button>
        </header>

        {doc.one_line && <p className="doc-one-line">{doc.one_line}</p>}

        {doc.status === 'failed' && <p className="dialog-error">{doc.error}</p>}
        {doc.status === 'ready' && doc.error && <p className="dialog-note">{doc.error}</p>}
        {error && <p className="dialog-error">{error}</p>}

        {summary ? (
          <div className="markdown doc-summary">
            <Markdown remarkPlugins={[remarkGfm]}>{summary}</Markdown>
          </div>
        ) : (
          doc.status === 'processing' && (
            <p className="dialog-note">
              {doc.stage === 'reading'
                ? 'Reading the pages. You can ask about it in a few seconds.'
                : 'You can already ask about it. The summary is still being written.'}
            </p>
          )
        )}

        <footer className="dialog-actions">
          {doc.status === 'failed' && (
            <button className="btn" onClick={() => onRetry(doc)}>
              <RetryIcon /> Try again
            </button>
          )}
          <button className="btn danger" onClick={() => onRemove(doc)}>
            <TrashIcon /> Remove
          </button>
          <a className="btn primary" href={api.documentUrl(doc.id)} target="_blank" rel="noreferrer">
            <ExternalIcon /> Open PDF
          </a>
        </footer>
      </div>
    </dialog>
  )
}
