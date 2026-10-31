import { useLayoutEffect } from 'react'
import { ArrowUpIcon } from './icons'

export default function Composer({ value, onChange, onSubmit, busy, inputRef }) {
  // Grow with the text, up to the max height set in CSS.
  useLayoutEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [value, inputRef])

  const canSend = !busy && value.trim().length > 0

  function submit(e) {
    e.preventDefault()
    if (canSend) onSubmit()
  }

  return (
    <div className="composer-wrap">
      <form className="composer" onSubmit={submit}>
        <textarea
          ref={inputRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) submit(e)
          }}
          placeholder="Message the assistant…"
          rows={1}
          aria-label="Message"
          autoFocus
        />
        <button type="submit" className="send-btn" disabled={!canSend} aria-label="Send">
          <ArrowUpIcon />
        </button>
      </form>
      <div className="composer-hint">Enter to send · Shift + Enter for a new line</div>
    </div>
  )
}
