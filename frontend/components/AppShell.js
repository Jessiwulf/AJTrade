import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/router'
import AJLogo from './AJLogo'
import { apiFetch, setAccessToken } from '../lib/api'
import { loadStoredProfile } from '../lib/userProfile'
import styles from './AppShell.module.css'

// --- Inline icon primitives (Heroicons-style stroke icons) ---
function NavIcon({ size = 18, children }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ flexShrink: 0 }}
    >
      {children}
    </svg>
  )
}

const NAV_ITEMS = [
  {
    href: '/dashboard',
    label: 'Dashboard',
    icon: (
      <NavIcon>
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
      </NavIcon>
    ),
  },
  {
    href: '/analytics',
    label: 'Analytics',
    icon: (
      <NavIcon>
        <polyline points="2 17 6 9 10 13 14 4 18 8" />
        <line x1="2" y1="17" x2="22" y2="17" />
      </NavIcon>
    ),
  },
  {
    href: '/markets',
    label: 'Markets',
    icon: (
      <NavIcon>
        <rect x="3" y="14" width="4" height="7" />
        <rect x="10" y="8" width="4" height="13" />
        <rect x="17" y="4" width="4" height="17" />
      </NavIcon>
    ),
  },
  {
    href: '/insights',
    label: 'Insights',
    icon: (
      <NavIcon>
        <circle cx="12" cy="9" r="4" />
        <path d="M9 14.5v2a1 1 0 001 1h4a1 1 0 001-1v-2" />
        <line x1="10" y1="17.5" x2="14" y2="17.5" />
      </NavIcon>
    ),
  },
  {
    href: '/automated',
    label: 'Automated',
    icon: (
      <NavIcon>
        <rect x="5" y="9" width="14" height="11" rx="2" />
        <path d="M8 9V7a4 4 0 018 0v2" />
        <circle cx="12" cy="14" r="1" fill="currentColor" stroke="none" />
      </NavIcon>
    ),
  },
  {
    href: '/watchlist',
    label: 'Watchlist',
    icon: (
      <NavIcon>
        <path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7z" />
        <circle cx="12" cy="12" r="3" />
      </NavIcon>
    ),
  },
  {
    href: '/portfolio',
    label: 'Portfolio',
    icon: (
      <NavIcon>
        <rect x="2" y="7" width="20" height="14" rx="2" />
        <path d="M7 7V5a2 2 0 012-2h6a2 2 0 012 2v2" />
      </NavIcon>
    ),
  },
  {
    href: '/api-keys',
    label: 'API Management',
    icon: (
      <NavIcon>
        <circle cx="8" cy="12" r="4" />
        <path d="M14 10h8M18 8v4" />
      </NavIcon>
    ),
  },
  {
    href: '/profile',
    label: 'Profile & Settings',
    matchPaths: ['/profile', '/settings'],
    icon: (
      <NavIcon>
        <circle cx="12" cy="8" r="4" />
        <path d="M4 20a8 8 0 0116 0" />
      </NavIcon>
    ),
  },
]

const SessionContext = createContext({
  isAuthed: true,
  isGuest: false,
  role: 'authenticated_user',
  openAuthModal: () => {},
})

function parseBooleanEnv(value, fallback) {
  const normalized = String(value || '').trim().toLowerCase()
  if (!normalized) return fallback
  return ['1', 'true', 'yes', 'on'].includes(normalized)
}

// Guest lock behavior is toggleable via env:
// - true  => keep guest lock enabled
// - false => temporarily disable guest lock for testing
const GUEST_LOCK_ENABLED = parseBooleanEnv(process.env.NEXT_PUBLIC_GUEST_LOCK_ENABLED, true)
const TEMPORARILY_DISABLE_GUEST_MODE = !GUEST_LOCK_ENABLED

const LOCKED_ROUTES = new Set([
  '/analytics',
  '/automated',
  '/insights',
  '/markets',
  '/portfolio',
  '/profile',
  '/settings',
  '/api-keys',
  '/watchlist',
])

export function useSession() {
  return useContext(SessionContext)
}

export default function AppShell({ title, subtitle, children }) {
  const router = useRouter()
  const path = router?.pathname || ''
  const [isAuthed, setIsAuthed] = useState(false)
  const [role, setRole] = useState('guest')
  const [profile, setProfile] = useState(null)
  const [authModalOpen, setAuthModalOpen] = useState(false)
  const [authModalSource, setAuthModalSource] = useState('locked feature')
  const [isNavOpen, setIsNavOpen] = useState(true)

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

  const isActive = (item) => {
    if (item.matchPaths) return item.matchPaths.includes(path)
    return item.href ? path === item.href : false
  }

  return (
    <div className={styles.shell}>
      <aside className={`${styles.sidebar} ${!isNavOpen ? styles.sidebarCollapsed : ''}`}>
        <div className={styles.navTop}>
          <Link href="/" className={styles.brand} aria-label="AJTrade home">
            <AJLogo size={22} />
            {isNavOpen && <span>AJTrade</span>}
          </Link>
        </div>

        <nav className={styles.nav} aria-label="Sidebar">
          {NAV_ITEMS.map((item) => {
            const active = isActive(item)

            if (item.disabled) {
              return (
                <span
                  key={item.label}
                  className={`${styles.navItem} ${styles.disabled}`}
                  aria-disabled="true"
                  title={item.label}
                >
                  {item.icon}
                  {isNavOpen && <span className={styles.navLabel}>{item.label}</span>}
                </span>
              )
            }

            return (
              <Link
                key={item.href}
                href={item.href}
                className={`${styles.navItem} ${active ? styles.active : ''}`}
                title={item.label}
              >
                {item.icon}
                {isNavOpen && <span className={styles.navLabel}>{item.label}</span>}
              </Link>
            )
          })}
        </nav>

        <button
          type="button"
          className={styles.navToggle}
          onClick={() => setIsNavOpen((p) => !p)}
          aria-label={isNavOpen ? 'Collapse navigation' : 'Expand navigation'}
          title={isNavOpen ? 'Collapse navigation' : 'Expand navigation'}
        >
          {isNavOpen ? '\u2039' : '\u203a'}
        </button>
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
