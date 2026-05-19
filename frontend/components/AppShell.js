import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/router'
import AJLogo from './AJLogo'
import { apiFetch, setAccessToken } from '../lib/api'
import styles from './AppShell.module.css'

function DisabledNavItem({ children }) {
  return (
    <span className={`${styles.navItem} ${styles.disabled}`} aria-disabled="true">
      {children}
    </span>
  )
}

export default function AppShell({ title, subtitle, children }) {
  const router = useRouter()
  const path = router?.pathname || ''
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
    router.push('/login')
  }

  useEffect(() => {
    refreshSession()
  }, [])

  const isActive = (href) => path === href

  return (
    <div className={styles.shell}>
      <aside className={styles.sidebar}>
        <Link href="/" className={styles.brand} aria-label="AJTrade home">
          <AJLogo size={22} />
          <span>AJTrade</span>
        </Link>

        <nav className={styles.nav} aria-label="Sidebar">
          <Link
            href="/dashboard"
            className={`${styles.navItem} ${isActive('/dashboard') ? styles.active : ''}`}
          >
            Dashboard
          </Link>
          <DisabledNavItem>Markets</DisabledNavItem>
          <Link
            href="/insights"
            className={`${styles.navItem} ${isActive('/insights') ? styles.active : ''}`}
          >
            Insights
          </Link>
          <Link
            href="/automated"
            className={`${styles.navItem} ${isActive('/automated') ? styles.active : ''}`}
          >
            Automated
          </Link>
          <Link
            href="/watchlist"
            className={`${styles.navItem} ${isActive('/watchlist') ? styles.active : ''}`}
          >
            Watchlist
          </Link>
          <Link
            href="/portfolio"
            className={`${styles.navItem} ${isActive('/portfolio') ? styles.active : ''}`}
          >
            Portfolio
          </Link>
          <Link
            href="/api-keys"
            className={`${styles.navItem} ${isActive('/api-keys') ? styles.active : ''}`}
          >
            API Management
          </Link>
          <DisabledNavItem>Settings</DisabledNavItem>
        </nav>
      </aside>

      <div className={styles.content}>
        <header className={styles.pageHeader}>
          <div>
            <h1 className={styles.title}>{title}</h1>
            {subtitle ? <p className={styles.subtitle}>{subtitle}</p> : null}
          </div>

          <div className={styles.actions}>
            {isAuthed ? (
              <button type="button" className={styles.actionButton} onClick={logout}>
                Logout
              </button>
            ) : (
              <Link href="/login" className={styles.actionLink}>
                Login
              </Link>
            )}
          </div>
        </header>
        {children}
      </div>
    </div>
  )
}
