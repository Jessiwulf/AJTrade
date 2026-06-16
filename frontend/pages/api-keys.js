import { useEffect, useMemo, useState } from 'react'
import AppShell from '../components/AppShell'
import { apiFetch } from '../lib/api'
import styles from '../styles/Manage.module.css'

function formatDate(value) {
  try {
    if (!value) return ''
    const d = new Date(value)
    if (Number.isNaN(d.getTime())) return String(value)
    return d.toLocaleString()
  } catch {
    return String(value || '')
  }
}

export default function ApiKeysPage() {
  const [keys, setKeys] = useState([])
  const [newsApiKey, setNewsApiKey] = useState('')
  const [alpacaKeyId, setAlpacaKeyId] = useState('')
  const [alpacaSecretKey, setAlpacaSecretKey] = useState('')
  const [settradeAppId, setSettradeAppId] = useState('')
  const [settradeAppSecret, setSettradeAppSecret] = useState('')
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState(null)

  const messageClass = useMemo(() => {
    if (!message) return styles.msg
    if (String(message).toLowerCase().startsWith('error')) return `${styles.msg} ${styles.msgError}`
    return `${styles.msg} ${styles.msgSuccess}`
  }, [message])

  async function refresh() {
    setLoading(true)
    setMessage(null)
    try {
      const data = await apiFetch('/api/vault/keys')
      setKeys(Array.isArray(data) ? data : [])
    } catch (e) {
      setMessage(`Error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function saveNewsApiKey() {
    const k = newsApiKey.trim()
    if (!k) return
    setLoading(true)
    setMessage(null)
    try {
      await apiFetch('/api/vault/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service: 'newsapi', api_key: k }),
      })
      setNewsApiKey('')
      await refresh()
      setMessage('NewsAPI key saved')
    } catch (e) {
      setMessage(`Error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function testNewsApiKey() {
    setLoading(true)
    setMessage(null)
    try {
      await apiFetch('/api/vault/ping', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service: 'newsapi' }),
      })
      setMessage('NewsAPI: OK')
    } catch (e) {
      setMessage(`Error: NewsAPI: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function saveAlpacaKeys() {
    const id = alpacaKeyId.trim()
    const secret = alpacaSecretKey.trim()
    if (!id || !secret) return

    setLoading(true)
    setMessage(null)
    try {
      await apiFetch('/api/vault/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service: 'alpaca_key_id', api_key: id }),
      })
      await apiFetch('/api/vault/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service: 'alpaca_secret_key', api_key: secret }),
      })
      setAlpacaKeyId('')
      setAlpacaSecretKey('')
      await refresh()
      setMessage('Alpaca keys saved')
    } catch (e) {
      setMessage(`Error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function saveSettradeKeys() {
    const appId = settradeAppId.trim()
    const appSecret = settradeAppSecret.trim()
    if (!appId || !appSecret) return
    setLoading(true)
    setMessage(null)
    try {
      await apiFetch('/api/vault/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service: 'settrade_app_id', api_key: appId }),
      })
      await apiFetch('/api/vault/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service: 'settrade_app_secret', api_key: appSecret }),
      })
      setSettradeAppId('')
      setSettradeAppSecret('')
      await refresh()
      setMessage('Settrade App Id / App Secret saved')
    } catch (e) {
      setMessage(`Error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function testSettradeKeys() {
    setLoading(true)
    setMessage(null)
    try {
      await apiFetch('/api/vault/ping', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service: 'settrade' }),
      })
      setMessage('Settrade: OK')
    } catch (e) {
      setMessage(`Error: Settrade: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function testAlpacaKeys() {
    setLoading(true)
    setMessage(null)
    try {
      const data = await apiFetch('/api/vault/ping', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service: 'alpaca' }),
      })

      const parts = []
      if (data?.account_id) parts.push(`acct ${data.account_id}`)
      if (data?.cash) parts.push(`cash ${data.cash}`)
      if (data?.buying_power) parts.push(`bp ${data.buying_power}`)
      setMessage(`Alpaca: OK${parts.length ? ` (${parts.join(' • ')})` : ''}`)
    } catch (e) {
      setMessage(`Error: Alpaca: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function revokeKey(id) {
    if (!id) return
    setLoading(true)
    setMessage(null)
    try {
      await apiFetch(`/api/vault/keys/${id}`, { method: 'DELETE' })
      await refresh()
      setMessage('Key revoked')
    } catch (e) {
      setMessage(`Error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  return (
    <AppShell title="API Management" subtitle="Store and test API keys (encrypted)">
      <div className={styles.page}>
        <section className={styles.card} aria-label="NewsAPI key">
          <p className={styles.cardTitle}>NewsAPI</p>
          <div className={styles.field}>
            <div className={styles.label}>API key</div>
            <div className={styles.rowWrap}>
              <input
                className={styles.input}
                type="password"
                value={newsApiKey}
                onChange={(e) => setNewsApiKey(e.target.value)}
                placeholder="Paste NewsAPI key"
                aria-label="NewsAPI key"
              />
              <button
                type="button"
                className={styles.primary}
                onClick={saveNewsApiKey}
                disabled={loading || !newsApiKey.trim()}
              >
                Save
              </button>
              <button
                type="button"
                className={styles.secondary}
                onClick={testNewsApiKey}
                disabled={loading}
              >
                Test
              </button>
            </div>
          </div>
        </section>

        <section className={styles.card} aria-label="Alpaca keys">
          <p className={styles.cardTitle}>Alpaca</p>
          <div className={styles.field}>
            <div className={styles.label}>Key ID</div>
            <input
              className={styles.input}
              type="password"
              value={alpacaKeyId}
              onChange={(e) => setAlpacaKeyId(e.target.value)}
              placeholder="Paste Alpaca key ID"
              aria-label="Alpaca key ID"
            />
          </div>
          <div className={styles.field}>
            <div className={styles.label}>Secret key</div>
            <div className={styles.rowWrap}>
              <input
                className={styles.input}
                type="password"
                value={alpacaSecretKey}
                onChange={(e) => setAlpacaSecretKey(e.target.value)}
                placeholder="Paste Alpaca Secret"
                aria-label="Alpaca Secret"
              />
              <button
                type="button"
                className={styles.primary}
                onClick={saveAlpacaKeys}
                disabled={loading || !alpacaKeyId.trim() || !alpacaSecretKey.trim()}
              >
                Save
              </button>
              <button
                type="button"
                className={styles.secondary}
                onClick={testAlpacaKeys}
                disabled={loading}
              >
                Test
              </button>
            </div>
          </div>
        </section>

        <section className={styles.card} aria-label="Settrade credentials">
          <p className={styles.cardTitle}>Settrade (Thai market)</p>
          <div className={styles.field}>
            <div className={styles.label}>App Id</div>
            <input
              className={styles.input}
              type="password"
              value={settradeAppId}
              onChange={(e) => setSettradeAppId(e.target.value)}
              placeholder="Paste Settrade app ID"
              aria-label="Settrade app ID"
            />
          </div>
          <div className={styles.field}>
            <div className={styles.label}>App Secret</div>
            <div className={styles.rowWrap}>
              <input
                className={styles.input}
                type="password"
                value={settradeAppSecret}
                onChange={(e) => setSettradeAppSecret(e.target.value)}
                placeholder="Paste Settrade app Secret"
                aria-label="Settrade app Secret"
              />
              <button
                type="button"
                className={styles.primary}
                onClick={saveSettradeKeys}
                disabled={loading || !settradeAppId.trim() || !settradeAppSecret.trim()}
              >
                Save
              </button>
              <button
                type="button"
                className={styles.secondary}
                onClick={testSettradeKeys}
                disabled={loading}
              >
                Test
              </button>
            </div>
          </div>
        </section>

        <section className={styles.card} aria-label="Stored keys">
          <div className={styles.rowWrap} style={{ justifyContent: 'space-between' }}>
            <p className={styles.cardTitle}>Stored keys</p>
            <button type="button" className={styles.secondary} onClick={refresh} disabled={loading}>
              Refresh
            </button>
          </div>

          <div className={styles.list}>
            {keys.length ? (
              keys.map((k) => (
                <div key={k.id} className={styles.item}>
                  <div className={styles.itemLeft}>
                    <div className={styles.itemTitle}>{k.service}</div>
                    <div className={styles.itemMeta}>
                      {k.preview}
                      {k.created_at ? ` • Saved ${formatDate(k.created_at)}` : ''}
                    </div>
                  </div>
                  <div className={styles.rowWrap}>
                    <button
                      type="button"
                      className={styles.danger}
                      onClick={() => revokeKey(k.id)}
                      disabled={loading}
                    >
                      Revoke
                    </button>
                  </div>
                </div>
              ))
            ) : (
              <p className={styles.msg}>No keys stored yet.</p>
            )}
          </div>

          {message ? <p className={messageClass}>{message}</p> : null}
        </section>
      </div>
    </AppShell>
  )
}
