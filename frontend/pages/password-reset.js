import { useState } from 'react'
import Link from 'next/link'
import { apiFetch } from '../lib/api'
import TopNav from '../components/TopNav'
import styles from '../styles/Auth.module.css'

export default function PasswordReset() {
  const [email, setEmail] = useState('')
  const [msg, setMsg] = useState(null)

  async function handleSubmit(e) {
    e.preventDefault()
    setMsg('...')
    try {
      const data = await apiFetch('/auth/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      })
      setMsg(data?.message || 'If this email is registered, a reset link has been sent.')
    } catch (err) {
      setMsg('Error: ' + err.message)
    }
  }

  return (
    <div className={styles.page}>
      <TopNav />
      <main className={styles.center}>
        <section className={styles.card} aria-label="Password reset">
          <h2 className={styles.title}>Password reset</h2>
          <p className={styles.subtitle}>
            Request a reset email for your account.
          </p>

          <form className={styles.form} onSubmit={handleSubmit}>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="email">Email</label>
              <input
                id="email"
                className={styles.input}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
              />
            </div>
            <button className={styles.primary} type="submit">Send reset</button>
          </form>

          <div className={styles.links}>
            <Link href="/login">Back to login</Link>
            <Link href="/">Back to landing</Link>
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
