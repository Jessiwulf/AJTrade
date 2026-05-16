import { useState } from 'react'
import Link from 'next/link'
import { apiFetch } from '../lib/api'
import TopNav from '../components/TopNav'
import styles from '../styles/Auth.module.css'

export default function Signup() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [msg, setMsg] = useState(null)

  async function handleSubmit(e) {
    e.preventDefault()
    setMsg('...')
    try {
      await apiFetch('/api/auth/signup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      setMsg('Signup ok; check your email for confirmation (if enabled)')
    } catch (err) {
      setMsg('Error: ' + err.message)
    }
  }

  return (
    <div className={styles.page}>
      <TopNav />
      <main className={styles.center}>
        <section className={styles.card} aria-label="Sign up">
          <h2 className={styles.title}>Sign up</h2>
          <p className={styles.subtitle}>
            Create an account to start using AJTrade.
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
            <div className={styles.field}>
              <label className={styles.label} htmlFor="password">Password</label>
              <input
                id="password"
                className={styles.input}
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
              />
            </div>
            <button className={styles.primary} type="submit">Sign up</button>
          </form>

          <div className={styles.links}>
            <Link href="/login">Already have an account?</Link>
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
