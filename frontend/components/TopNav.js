import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/router'
import AJLogo from './AJLogo'
import { apiFetch, setAccessToken } from '../lib/api'
import { loadStoredProfile } from '../lib/userProfile'
import styles from './TopNav.module.css'

export default function TopNav() {
  const [isAuthed, setIsAuthed] = useState(false)
  const [profile, setProfile] = useState(null)
  // Small screens: the links move into a menu opened from the bar.
  const [menuOpen, setMenuOpen] = useState(false)
  const router = useRouter()

  useEffect(() => {
    setMenuOpen(false)
  }, [router?.asPath])

  useEffect(() => {
    if (!menuOpen) return undefined
    function onKeyDown(event) {
      if (event.key === 'Escape') setMenuOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [menuOpen])

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

        <nav id="top-nav-links" className={`${styles.links} ${menuOpen ? styles.linksOpen : ''}`} aria-label="Primary">
          <Link href="/dashboard" className={styles.link}>
            Dashboard
          </Link>
          <Link href="/portfolio" className={styles.link}>
            Portfolio
          </Link>
          <Link href="/analytics" className={styles.link}>
            Analytics
          </Link>
          <Link href="/markets" className={styles.link}>
            Markets News
          </Link>
          <Link href="/insights" className={styles.link}>
            AI Insights
          </Link>
          <Link href="/automated" className={styles.link}>
            Auto Trade
          </Link>
          <Link href="/watchlist" className={`${styles.link} ${styles.mobileOnly}`}>
            Watchlist
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
          <button
            type="button"
            className={styles.menuButton}
            onClick={() => setMenuOpen((open) => !open)}
            aria-label={menuOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={menuOpen}
            aria-controls="top-nav-links"
          >
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
              {menuOpen ? <path d="M6 6l12 12M18 6L6 18" /> : <path d="M4 7h16M4 12h16M4 17h16" />}
            </svg>
          </button>
        </div>
      </div>
    </header>
  )
}
