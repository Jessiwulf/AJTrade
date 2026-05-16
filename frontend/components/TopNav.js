import Link from 'next/link'
import AJLogo from './AJLogo'
import styles from './TopNav.module.css'

function DisabledLink({ children }) {
  return (
    <span className={`${styles.link} ${styles.disabled}`} aria-disabled="true">
      {children}
    </span>
  )
}

export default function TopNav() {
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
          <Link href="/login" className={styles.login}>
            Login
          </Link>
          <Link href="/signup" className={styles.signup}>
            Sign up
          </Link>
        </div>
      </div>
    </header>
  )
}
