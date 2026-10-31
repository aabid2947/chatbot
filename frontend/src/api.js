const OFFLINE = "Couldn't reach the server. Check that the backend is running."

async function request(path, options = {}) {
  let res
  try {
    res = await fetch(`/api${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    })
  } catch {
    throw new Error(OFFLINE)
  }
  if (res.status === 204) return null
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    // The dev proxy answers 5xx with no JSON body when Flask is down.
    throw new Error(data.error || (res.status >= 500 ? OFFLINE : `Request failed (${res.status})`))
  }
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
  regenerate: (id) => request(`/conversations/${id}/regenerate`, { method: 'POST', body: '{}' }),
}
