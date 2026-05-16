import { useEffect, useState } from 'react'
import Link from 'next/link'
import { apiFetch, BACKEND_URL, getAccessToken, setAccessToken } from '../lib/api'

function Section({ title, children }) {
  return (
    <section style={{ border: '1px solid #ddd', padding: 16, marginTop: 16 }}>
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      {children}
    </section>
  )
}

export default function MLPanel() {
  const [symbol, setSymbol] = useState('AAPL')
  const [lookbackDays, setLookbackDays] = useState(60)
  const [period, setPeriod] = useState('30d')
  const [newsApiKey, setNewsApiKey] = useState('')

  const [me, setMe] = useState(null)
  const [vaultKeys, setVaultKeys] = useState([])
  const [trainRes, setTrainRes] = useState(null)
  const [predRes, setPredRes] = useState(null)
  const [explRes, setExplRes] = useState(null)
  const [err, setErr] = useState(null)

  const keywordShap = explRes?.explanation?.keyword_shap
  const tokenPreview = typeof window !== 'undefined' ? getAccessToken() : null

  async function refreshMe() {
    try {
      setErr(null)
      const data = await apiFetch('/api/auth/me')
      setMe(data)
    } catch (e) {
      setMe(null)
    }
  }

  async function logout() {
    try {
      setErr(null)
      await apiFetch('/api/auth/logout', { method: 'POST' })
    } catch {
      // ignore
    }
    setAccessToken(null)
    setMe(null)
  }

  async function refreshVault() {
    try {
      setErr(null)
      const data = await apiFetch('/api/vault/keys')
      setVaultKeys(data)
    } catch (e) {
      setVaultKeys([])
      setErr(e.message)
    }
  }

  async function storeKey() {
    try {
      setErr(null)
      await apiFetch('/api/vault/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service: 'newsapi', api_key: newsApiKey }),
      })
      setNewsApiKey('')
      await refreshVault()
    } catch (e) {
      setErr(e.message)
    }
  }

  async function train() {
    try {
      setErr(null)
      setExplRes(null)
      const data = await apiFetch('/api/ml/fetch_and_train', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, lookback_days: Number(lookbackDays) }),
      })
      setTrainRes(data)
    } catch (e) {
      setErr(e.message)
    }
  }

  async function predict() {
    try {
      setErr(null)
      const data = await apiFetch('/api/ml/predict', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, period }),
      })
      setPredRes(data)
    } catch (e) {
      setErr(e.message)
    }
  }

  async function explain() {
    try {
      setErr(null)
      const data = await apiFetch('/api/ml/explain', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, period }),
      })
      setExplRes(data)
    } catch (e) {
      setErr(e.message)
    }
  }

  useEffect(() => {
    refreshMe()
    refreshVault()
  }, [])

  return (
    <main style={{ padding: 24, fontFamily: 'Arial' }}>
      <h2>ML Panel (Sentiment + LightGBM + Keyword SHAP)</h2>
      <p>
        <Link href="/">Home</Link> | <Link href="/login">Login</Link> |{' '}
        <Link href="/signup">Sign up</Link>
      </p>
      <p style={{ color: '#555' }}>
        Backend: <code>{BACKEND_URL}</code>
      </p>

      <Section title="Session">
        <button onClick={refreshMe}>Refresh session</button>
        <button onClick={logout} style={{ marginLeft: 8 }}>
          Logout
        </button>
        <p style={{ color: '#555' }}>
          Token: {tokenPreview ? `${tokenPreview.slice(0, 12)}...` : 'none'}
        </p>
        <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(me, null, 2)}</pre>
      </Section>

      <Section title="API Vault — NewsAPI key">
        <p style={{ marginTop: 0 }}>
          Store a NewsAPI key as <code>service=newsapi</code> (encrypted server-side).
        </p>
        <div>
          <input
            type="password"
            placeholder="NewsAPI key"
            value={newsApiKey}
            onChange={(e) => setNewsApiKey(e.target.value)}
            style={{ width: 420 }}
          />
          <button onClick={storeKey} style={{ marginLeft: 8 }}>
            Save
          </button>
          <button onClick={refreshVault} style={{ marginLeft: 8 }}>
            Refresh
          </button>
        </div>
        <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(vaultKeys, null, 2)}</pre>
      </Section>

      <Section title="Controls">
        <div style={{ display: 'grid', gridTemplateColumns: '160px 1fr', gap: 8, maxWidth: 520 }}>
          <label>Symbol</label>
          <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />

          <label>Lookback days (train)</label>
          <input
            type="number"
            min={7}
            value={lookbackDays}
            onChange={(e) => setLookbackDays(e.target.value)}
          />

          <label>Price period (predict/explain)</label>
          <input value={period} onChange={(e) => setPeriod(e.target.value)} />
        </div>

        <div style={{ marginTop: 12 }}>
          <button onClick={train}>Fetch + Train</button>
          <button onClick={predict} style={{ marginLeft: 8 }}>
            Predict
          </button>
          <button onClick={explain} style={{ marginLeft: 8 }}>
            Explain (Keyword SHAP)
          </button>
        </div>
        {err ? <p style={{ color: 'crimson' }}>Error: {err}</p> : null}
      </Section>

      <Section title="Results">
        <h4>Train</h4>
        <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(trainRes, null, 2)}</pre>

        <h4>Predict</h4>
        <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(predRes, null, 2)}</pre>

        <h4>Explain</h4>
        <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(explRes, null, 2)}</pre>

        {keywordShap ? (
          <>
            <h4>Keyword SHAP (top positive)</h4>
            <ul>
              {keywordShap.top_positive?.map((k) => (
                <li key={k.keyword}>
                  <b>{k.keyword}</b>: {k.value}
                </li>
              ))}
            </ul>

            <h4>Keyword SHAP (top negative)</h4>
            <ul>
              {keywordShap.top_negative?.map((k) => (
                <li key={k.keyword}>
                  <b>{k.keyword}</b>: {k.value}
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </Section>
    </main>
  )
}
