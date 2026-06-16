import { useEffect, useState } from 'react'
import Link from 'next/link'
import AJLogo from './AJLogo'
import { apiFetch, setAccessToken } from '../lib/api'
import { loadStoredProfile } from '../lib/userProfile'
import styles from './TopNav.module.css'

function DisabledLink({ children }) {
  return (
    <span className={`${styles.link} ${styles.disabled}`} aria-disabled="true">
      {children}
    </span>
  )
}

export default function TopNav() {
  const [isAuthed, setIsAuthed] = useState(false)
  const [profile, setProfile] = useState(null)

  function getInitials(value) {
    const text = String(value || '').trim()
    if (!text) return 'U'
    const parts = text.split(/\s+/).filter(Boolean)
    const first = parts[0]?.[0] || ''
    const last = parts.length > 1 ? parts[parts.length - 1]?.[0] || '' : ''
    return `${first}${last}`.toUpperCase() || 'U'
  }

  async function refreshSession() {
    try {
      const data = await apiFetch('/api/auth/me')
      const stored = loadStoredProfile(data?.user)
      setProfile({ ...(data?.profile || {}), ...(stored || {}) })
      setIsAuthed(true)
    } catch {
      setProfile(null)
      setIsAuthed(false)
    }
  }

  async function logout() {
    try {
      await apiFetch('/api/auth/logout', { method: 'POST' })
    } catch {
      // ignore
    }
    setAccessToken(null)
    setIsAuthed(false)
  }

  useEffect(() => {
    refreshSession()
  }, [])

  return (
    <header className={styles.nav}>
      <div className={styles.inner}>
        <Link href="/" className={styles.brand} aria-label="AJTrade home">
          <AJLogo size={22} />
          <span className={styles.brandText}>AJTrade</span>
        </Link>

        <nav className={styles.links} aria-label="Primary">
          <Link href="/dashboard" className={styles.link}>
            Dashboard
          </Link>
          <DisabledLink>Markets</DisabledLink>
          <Link href="/insights" className={styles.link}>
            Insights
          </Link>
          <Link href="/automated" className={styles.link}>
            Automated
          </Link>
          <Link href="/profile" className={styles.link}>
            Settings
          </Link>
        </nav>

        <div className={styles.actions}>
          {isAuthed ? (
            <>
              <Link href="/profile" className={styles.profileButton} aria-label="Open profile settings">
                {profile?.avatar_url ? (
                  <img src={profile.avatar_url} alt="" className={styles.profileAvatar} />
                ) : (
                  <span className={styles.profileInitials}>{getInitials(profile?.full_name || profile?.email)}</span>
                )}
              </Link>
              <button type="button" className={styles.login} onClick={logout}>
                Logout
              </button>
            </>
          ) : (
            <>
              <Link href="/login" className={styles.login}>
                Login
              </Link>
              <Link href="/signup" className={styles.signup}>
                Sign up
              </Link>
            </>
          )}
        </div>
      </div>
    </header>
  )
}
