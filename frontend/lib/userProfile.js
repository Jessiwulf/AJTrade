const STORAGE_PREFIX = 'ajtrade_profile:'

function getUserKey(user) {
  return String(user?.sub || user?.email || '').trim() || 'anonymous'
}

export function getProfileStorageKey(user) {
  return `${STORAGE_PREFIX}${getUserKey(user)}`
}

export function loadStoredProfile(user) {
  if (typeof window === 'undefined') return null
  try {
    const raw = localStorage.getItem(getProfileStorageKey(user))
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function saveStoredProfile(user, profile) {
  if (typeof window === 'undefined') return
  try {
    localStorage.setItem(getProfileStorageKey(user), JSON.stringify(profile || {}))
  } catch {
    // ignore
  }
}

export function clearStoredProfile(user) {
  if (typeof window === 'undefined') return
  try {
    localStorage.removeItem(getProfileStorageKey(user))
  } catch {
    // ignore
  }
}
