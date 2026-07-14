import { useMemo, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/router'
import { apiFetch } from '../../lib/api'
import TopNav from '../../components/TopNav'
import styles from '../../styles/Auth.module.css'

export default function ResetPasswordPage() {
  const router = useRouter()
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [msg, setMsg] = useState('')

  const token = useMemo(() => {
    const raw = router.query?.token
    return typeof raw === 'string' ? raw : ''
  }, [router.query])

  async function handleSubmit(e) {
    e.preventDefault()
    setMsg('')

    if (newPassword.length < 6) {
      setMsg('Error: Password must be at least 6 characters.')
      return
    }

    if (newPassword !== confirmPassword) {
      setMsg('Passwords do not match')
      return
    }

    try {
      const data = await apiFetch('/auth/reset-password', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, newPassword, confirmPassword }),
      })
      const successMessage = data?.message || 'Password reset successfully. Please log in.'
      setMsg(successMessage)
      router.replace('/login')
    } catch (err) {
      const detail = String(err?.message || '')
      if (detail === 'Reset link has expired. Please request a new one.') {
        setMsg('Reset link has expired. Please request a new one.')
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
