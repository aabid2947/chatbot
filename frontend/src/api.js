async function request(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (res.status === 204) return null
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`)
  return data
}

export const api = {
  listConversations: () => request('/conversations'),
  createConversation: () => request('/conversations', { method: 'POST', body: '{}' }),
  deleteConversation: (id) => request(`/conversations/${id}`, { method: 'DELETE' }),
  listMessages: (id) => request(`/conversations/${id}/messages`),
  sendMessage: (id, content) =>
    request(`/conversations/${id}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    }),
}
