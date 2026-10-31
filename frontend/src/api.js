const OFFLINE = "Couldn't reach the server. Check that the backend is running."

async function request(path, options = {}) {
  let res
  try {
    res = await fetch(`/api${path}`, {
      headers: options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
      ...options,
    })
  } catch {
    throw new Error(OFFLINE)
  }
  return readJson(res)
}

async function readJson(res) {
  if (res.status === 204) return null
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    // The dev proxy answers 5xx with no JSON body when Flask is down.
    if (res.status === 413) throw new Error(data.error || 'That file is too big.')
    throw new Error(data.error || (res.status >= 500 ? OFFLINE : `Request failed (${res.status})`))
  }
  return data
}

/**
 * POST to a streaming endpoint and call handlers as Server-Sent Events arrive:
 * onEvent(name, data) for user_message, tool, delta, done and error.
 * Resolves when the stream ends. Throws only if the request itself fails.
 */
async function stream(path, body, onEvent) {
  let res
  try {
    res = await fetch(`/api${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    })
  } catch {
    throw new Error(OFFLINE)
  }
  if (!res.ok || !res.body) {
    await readJson(res)
    throw new Error(OFFLINE)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let ended = false
  const emit = (block) => {
    let event = 'message'
    const data = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event: ')) event = line.slice(7)
      else if (line.startsWith('data: ')) data.push(line.slice(6))
    }
    if (!data.length) return
    if (event === 'done' || event === 'error') ended = true
    onEvent(event, JSON.parse(data.join('\n')))
  }
  for (;;) {
    let chunk
    try {
      chunk = await reader.read()
    } catch {
      break
    }
    if (chunk.done) break
    buffer += decoder.decode(chunk.value, { stream: true })
    let cut
    while ((cut = buffer.indexOf('\n\n')) !== -1) {
      emit(buffer.slice(0, cut))
      buffer = buffer.slice(cut + 2)
    }
  }
  if (buffer.trim()) emit(buffer)
  if (!ended) onEvent('error', { error: 'The connection dropped before the reply finished.' })
}

export const api = {
  listConversations: () => request('/conversations'),
  createConversation: () => request('/conversations', { method: 'POST', body: '{}' }),
  deleteConversation: (id) => request(`/conversations/${id}`, { method: 'DELETE' }),
  listMessages: (id) => request(`/conversations/${id}/messages`),
  streamMessage: (id, content, onEvent) =>
    stream(`/conversations/${id}/messages/stream`, { content }, onEvent),
  streamRegenerate: (id, onEvent) => stream(`/conversations/${id}/regenerate/stream`, {}, onEvent),

  listDocuments: (chatId) => request(`/conversations/${chatId}/documents`),
  uploadDocument: (chatId, file) => {
    const form = new FormData()
    form.append('file', file)
    return request(`/conversations/${chatId}/documents`, { method: 'POST', body: form })
  },
  getDocument: (id) => request(`/documents/${id}`),
  retryDocument: (id) => request(`/documents/${id}/retry`, { method: 'POST', body: '{}' }),
  deleteDocument: (id) => request(`/documents/${id}`, { method: 'DELETE' }),
  documentUrl: (id, page) => `/api/documents/${id}/file${page ? `#page=${page}` : ''}`,

  listFacts: () => request('/memory/facts'),
  getFact: (id) => request(`/memory/facts/${id}`),
  addFact: (content) => request('/memory/facts', { method: 'POST', body: JSON.stringify({ content }) }),
  updateFact: (id, changes) =>
    request(`/memory/facts/${id}`, { method: 'PATCH', body: JSON.stringify(changes) }),
  deleteFact: (id) => request(`/memory/facts/${id}`, { method: 'DELETE' }),
}
