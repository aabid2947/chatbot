import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import { CheckIcon, CopyIcon, RetryIcon, SearchIcon } from './icons'

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * Turn page citations into links that open the PDF at that page:
 * "(report.pdf, p. 4)" and "(report.pdf, pp. 4-5)", and a bare "(p. 4)" when
 * the chat has only one PDF. All names are matched in one pass, so a
 * citation is never linked twice (e.g. two uploads with the same name).
 */
function linkCitations(text, documents = []) {
  // One document per file name; the newest upload wins.
  const byName = new Map()
  for (const doc of documents) if (doc.pages) byName.set(doc.filename.toLowerCase(), doc)
  if (!byName.size) return text
  const pages = String.raw`pp?\.\s*(\d+)(?:\s*[-–]\s*\d+)?`
  const link = (doc, label, page) =>
    Number(page) >= 1 && Number(page) <= doc.pages
      ? `[${label}](${api.documentUrl(doc.id, page)})`
      : label
  const names = [...byName.keys()].sort((a, b) => b.length - a.length).map(escapeRegExp)
  const named = new RegExp(`((${names.join('|')}),\\s*${pages})`, 'gi')
  let out = text.replace(named, (_, label, name, page) => link(byName.get(name.toLowerCase()), label, page))
  if (byName.size === 1) {
    const [only] = byName.values()
    const bare = new RegExp(`\\((${pages})\\)`, 'g')
    out = out.replace(bare, (_, label, page) => `(${link(only, label, page)})`)
  }
  return out
}

function ToolStatus({ label }) {
  return (
    <div className="tool-status" aria-live="polite">
      <SearchIcon /> {label}
    </div>
  )
}

function CopyButton({ text, label = 'Copy' }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef(null)
  useEffect(() => () => clearTimeout(timer.current), [])

  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      clearTimeout(timer.current)
      timer.current = setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard blocked — nothing useful to show */
    }
  }

  return (
    <button className="msg-action" onClick={copy} aria-label={copied ? 'Copied' : label}>
      {copied ? <CheckIcon /> : <CopyIcon />}
      {copied ? 'Copied' : label}
    </button>
  )
}

// Fenced code: a header with the language and a copy button, like the design.
function CodeBlock({ children }) {
  const code = children?.props ?? {}
  const lang = /language-([\w-]+)/.exec(code.className || '')?.[1] ?? 'text'
  const text = String(code.children ?? '').replace(/\n$/, '')
  return (
    <div className="code-block">
      <div className="code-head">
        <span>{lang}</span>
        <CopyButton text={text} />
      </div>
      <pre>
        <code>{text}</code>
      </pre>
    </div>
  )
}

const markdownComponents = {
  pre: CodeBlock,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  ),
}

export function BotMark() {
  return <div className="bot-mark" aria-hidden="true" />
}

export function TypingIndicator({ tool }) {
  return (
    <div className="msg-bot" aria-live="polite" aria-label="Assistant is typing">
      <BotMark />
      {tool ? (
        <ToolStatus label={tool} />
      ) : (
        <div className="typing">
          <span />
          <span />
          <span />
        </div>
      )}
    </div>
  )
}

/** A reply that is still being written. */
export function LiveReply({ text, tool, documents }) {
  if (!text) return <TypingIndicator tool={tool} />
  return (
    <div className="msg-bot" aria-busy="true">
      <BotMark />
      <div className="msg-body">
        <div className="markdown">
          <Markdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
            {linkCitations(text, documents)}
          </Markdown>
        </div>
        {tool && <ToolStatus label={tool} />}
      </div>
    </div>
  )
}

export default function Message({ message, canRetry, onRetry, documents }) {
  if (message.role === 'user') {
    return <div className="msg-user">{message.content}</div>
  }
  return (
    <div className="msg-bot">
      <BotMark />
      <div className="msg-body">
        <div className="markdown">
          <Markdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
            {linkCitations(message.content, documents)}
          </Markdown>
        </div>
        <div className="msg-actions">
          <CopyButton text={message.content} />
          {canRetry && (
            <button className="msg-action" onClick={onRetry}>
              <RetryIcon /> Retry
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
