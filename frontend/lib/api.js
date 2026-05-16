export const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000'

export function getAccessToken() {
  if (typeof window === 'undefined') return null
  try {
    return localStorage.getItem('ajtrade_access_token')
  } catch {
    return null
  }
}

export function setAccessToken(token) {
  if (typeof window === 'undefined') return
  try {
    if (!token) localStorage.removeItem('ajtrade_access_token')
    else localStorage.setItem('ajtrade_access_token', token)
  } catch {
    // ignore
  }
}

export async function apiFetch(path, options = {}) {
  const url = `${BACKEND_URL}${path}`
  const token = getAccessToken()
  const headers = {
    ...(options.headers || {}),
  }
  if (token && !headers.Authorization) {
    headers.Authorization = `Bearer ${token}`
  }
  const res = await fetch(url, {
    ...options,
    credentials: 'include',
    headers,
  })
  const text = await res.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!res.ok) {
    const msg =
      (data && data.detail) ||
      (typeof data === 'string' ? data : JSON.stringify(data))
    throw new Error(msg || `Request failed: ${res.status}`)
  }
  return data
}
