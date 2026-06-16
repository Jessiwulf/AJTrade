import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/router'
import AJLogo from './AJLogo'
import { apiFetch, setAccessToken } from '../lib/api'
import { loadStoredProfile } from '../lib/userProfile'
import styles from './AppShell.module.css'

const SessionContext = createContext({
  isAuthed: true,
  isGuest: false,
  role: 'authenticated_user',
  openAuthModal: () => {},
})

const TEMPORARILY_DISABLE_GUEST_MODE = true

const LOCKED_ROUTES = new Set([
  '/analytics',
  '/automated',
  '/insights',
  '/portfolio',
  '/profile',
  '/settings',
  '/api-keys',
  '/watchlist',
])

export function useSession() {
  return useContext(SessionContext)
}

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
  const [role, setRole] = useState('guest')
  const [profile, setProfile] = useState(null)
  const [authModalOpen, setAuthModalOpen] = useState(false)
  const [authModalSource, setAuthModalSource] = useState('locked feature')

  function getInitials(value) {
    const text = String(value || '').trim()
    if (!text) return 'U'
    const parts = text.split(/\s+/).filter(Boolean)
    if (!parts.length) return 'U'
    const first = parts[0][0] || ''
    const last = parts.length > 1 ? parts[parts.length - 1][0] || '' : ''
    return `${first}${last}`.toUpperCase() || 'U'
  }

  async function refreshSession() {
    try {
      const data = await apiFetch('/api/auth/me')
      const stored = loadStoredProfile(data?.user)
      setProfile({ ...(data?.profile || {}), ...(stored || {}) })
      setIsAuthed(true)
      setRole(data?.user?.role || 'authenticated_user')
    } catch {
      setProfile(null)
      if (TEMPORARILY_DISABLE_GUEST_MODE) {
        setIsAuthed(true)
        setRole('authenticated_user')
      } else {
        setIsAuthed(false)
        setRole('guest')
      }
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
    setRole('guest')
    router.push('/login')
  }

  function openAuthModal(source = 'locked feature') {
    setAuthModalSource(source)
    setAuthModalOpen(true)
  }

  function closeAuthModal() {
    setAuthModalOpen(false)
  }

  const session = useMemo(
    () => ({
      isAuthed,
      isGuest: TEMPORARILY_DISABLE_GUEST_MODE ? false : !isAuthed,
      role,
      openAuthModal,
    }),
    [isAuthed, role],
  )

  const isGuestLockedRoute = TEMPORARILY_DISABLE_GUEST_MODE ? false : !isAuthed && LOCKED_ROUTES.has(path)

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
          <Link
            href="/analytics"
            className={`${styles.navItem} ${isActive('/analytics') ? styles.active : ''}`}
          >
            Analytics
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
          <Link
            href="/profile"
            className={`${styles.navItem} ${(isActive('/profile') || isActive('/settings')) ? styles.active : ''}`}
          >
            Profile / Settings
          </Link>
        </nav>
      </aside>

      <SessionContext.Provider value={session}>
      <div className={styles.content}>
        <header className={styles.pageHeader}>
          <div>
            <h1 className={styles.title}>{title}</h1>
            {subtitle ? <p className={styles.subtitle}>{subtitle}</p> : null}
          </div>

          <div className={styles.actions}>
            {isAuthed ? (
              <>
                <Link href="/profile" className={styles.profileButton} aria-label="Open profile settings">
                  {profile?.avatar_url ? (
                    <img
                      src={profile.avatar_url}
                      alt=""
                      className={styles.profileAvatar}
                    />
                  ) : (
                    <span className={styles.profileInitials}>{getInitials(profile?.full_name || profile?.email)}</span>
                  )}
                </Link>
                <button type="button" className={styles.actionButton} onClick={logout}>
                  Logout
                </button>
              </>
            ) : (
              <Link href="/login" className={styles.actionLink}>
                Login
              </Link>
            )}
          </div>
        </header>
        <div className={`${styles.pageBody} ${isGuestLockedRoute ? styles.pageBodyLocked : ''}`}>
          {isGuestLockedRoute ? (
            <button
              type="button"
              className={styles.lockOverlay}
              onClick={() => openAuthModal('this page')}
              aria-label="Sign in or register to unlock this page"
            />
          ) : null}
          <div className={isGuestLockedRoute ? styles.lockedSurface : ''}>
            {children}
          </div>
        </div>

        {authModalOpen ? (
          <div className={styles.modalBackdrop} role="presentation" onClick={closeAuthModal}>
            <div
              className={styles.modalCard}
              role="dialog"
              aria-modal="true"
              aria-labelledby="ajtrade-auth-modal-title"
              onClick={(event) => event.stopPropagation()}
            >
              <p className={styles.modalEyebrow}>Locked feature</p>
              <h2 id="ajtrade-auth-modal-title" className={styles.modalTitle}>
                Sign In or Register
              </h2>
              <p className={styles.modalBody}>
                {`This ${authModalSource} is available after authentication. Public visitors can view the market snapshot, but actions and automation require an account.`}
              </p>
              <div className={styles.modalActions}>
                <button
                  type="button"
                  className={styles.primaryAction}
                  onClick={() => router.push(`/login?next=${encodeURIComponent(router.asPath || path)}`)}
                >
                  Sign In
                </button>
                <button
                  type="button"
                  className={styles.secondaryAction}
                  onClick={() => router.push(`/signup?next=${encodeURIComponent(router.asPath || path)}`)}
                >
                  Register
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </div>
      </SessionContext.Provider>
    </div>
  )
}
