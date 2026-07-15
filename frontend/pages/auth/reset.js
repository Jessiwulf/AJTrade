import { useMemo, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/router'
import { apiFetch } from '../../lib/api'
import TopNav from '../../components/TopNav'
import styles from '../../styles/Auth.module.css'

function getTokenFromUrl() {
  if (typeof window === 'undefined') return ''

  // Supabase recovery links commonly place tokens in URL hash.
  const hash = window.location.hash ? window.location.hash.slice(1) : ''
  if (hash) {
    const hashParams = new URLSearchParams(hash)
    const hashToken = hashParams.get('access_token') || hashParams.get('token')
    if (hashToken) return hashToken
  }

  // Fallback for query-param links.
  const queryParams = new URLSearchParams(window.location.search || '')
  return queryParams.get('token') || queryParams.get('access_token') || ''
}

export default function ResetPasswordPage() {
  const router = useRouter()
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [msg, setMsg] = useState('')

  const tokenFromQuery = useMemo(() => {
    const raw = router.query?.token
    return typeof raw === 'string' ? raw : ''
  }, [router.query])

  async function handleSubmit(e) {
    e.preventDefault()
    setMsg('')

    // Validation
    if (newPassword.length < 6) {
      setMsg('Error: Password must be at least 6 characters.')
      return
    }

    if (newPassword !== confirmPassword) {
      setMsg('Error: Passwords do not match')
      return
    }

    try {
      const token = tokenFromQuery || getTokenFromUrl()
      const data = await apiFetch('/auth/reset-password', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, newPassword, confirmPassword }),
      })

      setMsg(data?.message || 'Password reset successfully. Please log in.')
      setTimeout(() => {
        router.replace('/login')
      }, 800)
    } catch (err) {
      const detail = String(err?.message || '')
      if (detail.includes('expired') || detail.includes('invalid')) {
        setMsg('Error: Reset link has expired. Please request a new one.')
      } else {
        setMsg(`Error: ${detail || 'Unable to reset password.'}`)
      }
    }
  }

  return (
    <div className={styles.page}>
      <TopNav />
      <main className={styles.center}>
        <section className={styles.card} aria-label="Reset password">
          <h2 className={styles.title}>Set a new password</h2>
          <p className={styles.subtitle}>Enter and confirm your new password.</p>

          <form className={styles.form} onSubmit={handleSubmit}>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="new-password">New Password</label>
              <input
                id="new-password"
                className={styles.input}
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                autoComplete="new-password"
              />
            </div>

            <div className={styles.field}>
              <label className={styles.label} htmlFor="confirm-password">Confirm Password</label>
              <input
                id="confirm-password"
                className={styles.input}
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                autoComplete="new-password"
              />
            </div>

            <button className={styles.primary} type="submit">Reset password</button>
          </form>

          <div className={styles.links}>
            <Link href="/login">Back to login</Link>
            <Link href="/password-reset">Request new link</Link>
          </div>

          {msg ? (
            <p className={`${styles.msg} ${String(msg).startsWith('Error:') ? styles.error : ''}`}>
              {msg}
            </p>
          ) : null}
        </section>
      </main>
    </div>
  )
}
