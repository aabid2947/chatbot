import { CloseIcon, MemoryIcon, MoonIcon, PlusIcon, SunIcon, TrashIcon } from './icons'

const DAY = 24 * 60 * 60 * 1000

// Bucket chats like "Today", "Previous 7 days", newest first.
function groupByDate(conversations) {
  const startOfToday = new Date()
  startOfToday.setHours(0, 0, 0, 0)
  const t = startOfToday.getTime()
  const buckets = [
    ['Today', t],
    ['Yesterday', t - DAY],
    ['Previous 7 days', t - 7 * DAY],
    ['Previous 30 days', t - 30 * DAY],
    ['Older', -Infinity],
  ]
  const groups = buckets.map(([label]) => ({ label, items: [] }))
  for (const c of conversations) {
    const when = new Date(c.updated_at).getTime()
    const i = buckets.findIndex(([, since]) => when >= since)
    groups[i].items.push(c)
  }
  return groups.filter((g) => g.items.length)
}

export default function Sidebar({
  conversations,
  activeId,
  open,
  isDark,
  onSelect,
  onNew,
  onDelete,
  onClose,
  onToggleTheme,
  onOpenMemory,
}) {
  return (
    <>
      <div className={`sidebar-backdrop ${open ? 'show' : ''}`} onClick={onClose} />
      <aside className={`sidebar ${open ? 'open' : ''}`} aria-label="Chats">
        <div className="sidebar-top">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true" />
            Chatbot
          </div>
          <button className="icon-btn ghost sidebar-close" onClick={onClose} aria-label="Close menu">
            <CloseIcon size={16} />
          </button>
        </div>

        <button className="new-chat" onClick={onNew}>
          <PlusIcon /> New chat
        </button>

        <nav className="chat-list">
          {conversations.length === 0 && <p className="chat-list-empty">No chats yet</p>}
          {groupByDate(conversations).map((group) => (
            <div key={group.label}>
              <div className="chat-group-label">{group.label}</div>
              <ul>
                {group.items.map((c) => (
                  <li key={c.id} className={c.id === activeId ? 'active' : ''}>
                    <button
                      className="chat-title"
                      onClick={() => onSelect(c.id)}
                      title={c.title}
                      aria-current={c.id === activeId ? 'page' : undefined}
                    >
                      {c.title}
                    </button>
                    <button
                      className="chat-delete"
                      onClick={() => onDelete(c)}
                      aria-label={`Delete "${c.title}"`}
                      title="Delete chat"
                    >
                      <TrashIcon />
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>

        <div className="sidebar-foot">
          <button className="theme-btn" onClick={onOpenMemory}>
            <MemoryIcon />
            Memory
          </button>
          <button className="theme-btn" onClick={onToggleTheme}>
            {isDark ? <SunIcon /> : <MoonIcon />}
            {isDark ? 'Light mode' : 'Dark mode'}
          </button>
        </div>
      </aside>
    </>
  )
}
