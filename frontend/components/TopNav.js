import { useEffect, useState } from 'react'
import Link from 'next/link'
import AJLogo from './AJLogo'
import { apiFetch, setAccessToken } from '../lib/api'
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

  async function refreshSession() {
    try {
      await apiFetch('/api/auth/me')
      setIsAuthed(true)
    } catch {
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
          <DisabledLink>Settings</DisabledLink>
        </nav>

        <div className={styles.actions}>
          {isAuthed ? (
            <button type="button" className={styles.login} onClick={logout}>
              Logout
            </button>
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
