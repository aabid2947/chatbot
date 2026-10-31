import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CheckIcon, CopyIcon, RetryIcon } from './icons'

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

export function TypingIndicator() {
  return (
    <div className="msg-bot" aria-live="polite" aria-label="Assistant is typing">
      <BotMark />
      <div className="typing">
        <span />
        <span />
        <span />
      </div>
    </div>
  )
}

export default function Message({ message, canRetry, onRetry }) {
  if (message.role === 'user') {
    return <div className="msg-user">{message.content}</div>
  }
  return (
    <div className="msg-bot">
      <BotMark />
      <div className="msg-body">
        <div className="markdown">
          <Markdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
            {message.content}
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
