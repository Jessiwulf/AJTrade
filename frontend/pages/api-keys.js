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
  const [newsDataApiKey, setNewsDataApiKey] = useState('')
  const [alpacaKeyId, setAlpacaKeyId] = useState('')
  const [alpacaSecretKey, setAlpacaSecretKey] = useState('')
  const [settradeAppId, setSettradeAppId] = useState('')
  const [settradeAppSecret, setSettradeAppSecret] = useState('')
  const [settradeBrokerId, setSettradeBrokerId] = useState('')
  const [settradeAppCode, setSettradeAppCode] = useState('')
  const [settradeAccountNo, setSettradeAccountNo] = useState('')
  const [settradePin, setSettradePin] = useState('')
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

  async function saveNewsDataApiKey() {
    const k = newsDataApiKey.trim()
    if (!k) return
    setLoading(true)
    setMessage(null)
    try {
      await apiFetch('/api/vault/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service: 'newsdata', api_key: k }),
      })
      setNewsDataApiKey('')
      await refresh()
      setMessage('NewsData key saved')
    } catch (e) {
      setMessage(`Error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function testNewsDataApiKey() {
    setLoading(true)
    setMessage(null)
    try {
      await apiFetch('/api/vault/ping', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service: 'newsdata' }),
      })
      setMessage('NewsData: OK')
    } catch (e) {
      setMessage(`Error: NewsData: ${e.message}`)
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

  const settradeFields = [
    ['settrade_app_id', settradeAppId, 'App Id'],
    ['settrade_app_secret', settradeAppSecret, 'App Secret'],
    ['settrade_broker_id', settradeBrokerId, 'Broker ID'],
    ['settrade_app_code', settradeAppCode, 'App Code'],
    ['settrade_account_no', settradeAccountNo, 'Account number'],
    ['settrade_pin', settradePin, 'PIN'],
  ].filter(([, value]) => value.trim())

  async function saveSettradeKeys() {
    if (!settradeFields.length) return
    setLoading(true)
    setMessage(null)
    try {
      // Each field is saved on its own, so e.g. the PIN can be added later without re-entering the rest.
      for (const [service, value] of settradeFields) {
        await apiFetch('/api/vault/keys', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ service, api_key: value.trim() }),
        })
      }
      const saved = settradeFields.map(([, , label]) => label).join(', ')
      setSettradeAppId('')
      setSettradeAppSecret('')
      setSettradeBrokerId('')
      setSettradeAppCode('')
      setSettradeAccountNo('')
      setSettradePin('')
      await refresh()
      setMessage(`Settrade saved: ${saved}`)
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
      const result = await apiFetch('/api/vault/ping', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service: 'settrade' }),
      })
      const parts = [`logged in (${result.environment})`]
      if (result.account_no) parts.push(`account ${result.account_no}`)
      if (result.cash != null) parts.push(`cash ฿${Number(result.cash).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`)
      parts.push(result.trading_enabled ? 'trading enabled' : result.environment === 'production' ? 'read-only (live account)' : 'add PIN to trade')
      setMessage(`Settrade: ${parts.join(' · ')}${result.warning ? `. ${result.warning}` : ''}`)
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

        <section className={styles.card} aria-label="NewsData key">
          <p className={styles.cardTitle}>NewsData.io (Fallback)</p>
          <div className={styles.field}>
            <div className={styles.label}>API key</div>
            <div className={styles.rowWrap}>
              <input
                className={styles.input}
                type="password"
                value={newsDataApiKey}
                onChange={(e) => setNewsDataApiKey(e.target.value)}
                placeholder="Paste NewsData API key"
                aria-label="NewsData API key"
              />
              <button
                type="button"
                className={styles.primary}
                onClick={saveNewsDataApiKey}
                disabled={loading || !newsDataApiKey.trim()}
              >
                Save
              </button>
              <button
                type="button"
                className={styles.secondary}
                onClick={testNewsDataApiKey}
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
          <p className={styles.muted} style={{ margin: '6px 0 0' }}>
            From Settrade Open API (Sandbox → My Apps). Leave Broker ID and App Code empty for the sandbox.
            AJTrade only places orders on the sandbox; a live Settrade account is read-only.
          </p>
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
            </div>
          </div>
          <div className={styles.field}>
            <div className={styles.rowWrap}>
              <input
                className={styles.input}
                value={settradeBrokerId}
                onChange={(e) => setSettradeBrokerId(e.target.value)}
                placeholder="Broker ID (default SANDBOX)"
                aria-label="Settrade broker ID"
                autoComplete="off"
              />
              <input
                className={styles.input}
                value={settradeAppCode}
                onChange={(e) => setSettradeAppCode(e.target.value)}
                placeholder="App Code (default SANDBOX)"
                aria-label="Settrade app code"
                autoComplete="off"
              />
            </div>
          </div>
          <div className={styles.field}>
            <div className={styles.label}>Account number and trading PIN</div>
            <div className={styles.rowWrap}>
              <input
                className={styles.input}
                value={settradeAccountNo}
                onChange={(e) => setSettradeAccountNo(e.target.value)}
                placeholder="Account number, e.g. 12345678-E"
                aria-label="Settrade account number"
                autoComplete="off"
              />
              <input
                className={styles.input}
                type="password"
                value={settradePin}
                onChange={(e) => setSettradePin(e.target.value)}
                placeholder="PIN (needed to place orders)"
                aria-label="Settrade PIN"
                autoComplete="new-password"
              />
              <button
                type="button"
                className={styles.primary}
                onClick={saveSettradeKeys}
                disabled={loading || !settradeFields.length}
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
