import Link from 'next/link'
import { useRouter } from 'next/router'
import AJLogo from './AJLogo'
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
          <DisabledNavItem>Settings</DisabledNavItem>
        </nav>
      </aside>

      <div className={styles.content}>
        <header className={styles.pageHeader}>
          <div>
            <h1 className={styles.title}>{title}</h1>
            {subtitle ? <p className={styles.subtitle}>{subtitle}</p> : null}
          </div>
        </header>
        {children}
      </div>
    </div>
  )
}
