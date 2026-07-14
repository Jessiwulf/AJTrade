import { useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/router'
import { apiFetch, setAccessToken } from '../lib/api'
import TopNav from '../components/TopNav'
import styles from '../styles/Auth.module.css'

export default function Login() {
  const router = useRouter()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [msg, setMsg] = useState(null)

  async function handleSubmit(e) {
    e.preventDefault()
    setMsg('...')
    try {
      const data = await apiFetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      if (data?.access_token) setAccessToken(data.access_token)
      setMsg('Logged in')
      router.push('/dashboard')
    } catch (err) {
      setMsg('Error: ' + err.message)
    }
  }

  return (
    <div className={styles.page}>
      <TopNav />
      <main className={styles.center}>
        <section className={styles.card} aria-label="Login">
          <h2 className={styles.title}>Login</h2>
          <p className={styles.subtitle}>
            Sign in to view insights and configure automation.
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
                autoComplete="current-password"
              />
            </div>
            <button className={styles.primary} type="submit">Login</button>
          </form>

          <div className={styles.links}>
            <Link href="/password-reset">Forgot Password?</Link>
            <Link href="/signup">Create account</Link>
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
