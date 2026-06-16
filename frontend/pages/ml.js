import { useEffect, useState } from 'react'
import Link from 'next/link'
import { apiFetch, BACKEND_URL, getAccessToken, setAccessToken } from '../lib/api'
import { useSession } from '../components/AppShell'

function Section({ title, children }) {
  return (
    <section style={{ border: '1px solid #ddd', padding: 16, marginTop: 16 }}>
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      {children}
    </section>
  )
}

export default function MLPanel() {
  const { isGuest } = useSession()
  const [symbol, setSymbol] = useState('AAPL')
  const [lookbackDays, setLookbackDays] = useState(60)
  const [period, setPeriod] = useState('30d')
  const [newsApiKey, setNewsApiKey] = useState('')

  const [guestSymbol, setGuestSymbol] = useState('AAPL')
  const [guestPrompt, setGuestPrompt] = useState('Explain the public market context in plain language.')
  const [guestRange, setGuestRange] = useState('1mo')
  const [guestLlm, setGuestLlm] = useState(null)
  const [guestErr, setGuestErr] = useState(null)

  // V2 (FinBERT + LightGBM + SHAP + Dual-LLM)
  const [v2Text, setV2Text] = useState('Apple shares rise after strong earnings report...')
  const [v2Sentiment, setV2Sentiment] = useState(null)
  const [v2Train, setV2Train] = useState(null)
  const [v2Signal, setV2Signal] = useState(null)
  const [v2Shap, setV2Shap] = useState(null)
  const [v2Prompt, setV2Prompt] = useState('Explain what drove the prediction in plain language.')
  const [v2Preference, setV2Preference] = useState('open-source')
  const [v2Llm, setV2Llm] = useState(null)

  const [tokenPreview, setTokenPreview] = useState(null)

  const [me, setMe] = useState(null)
  const [vaultKeys, setVaultKeys] = useState([])
  const [trainRes, setTrainRes] = useState(null)
  const [predRes, setPredRes] = useState(null)
  const [explRes, setExplRes] = useState(null)
  const [err, setErr] = useState(null)

  const keywordShap = explRes?.explanation?.keyword_shap

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
    setTokenPreview(null)
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

  async function v2AnalyzeSentiment() {
    try {
      setErr(null)
      const data = await apiFetch('/api/ml/v2/sentiment/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: v2Text }),
      })
      setV2Sentiment(data)
    } catch (e) {
      setErr(e.message)
    }
  }

  async function v2TrainModel() {
    try {
      setErr(null)
      setV2Shap(null)
      setV2Signal(null)
      setV2Llm(null)
      const data = await apiFetch('/api/ml/v2/forecaster/train', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, lookback_days: Number(lookbackDays), max_articles: 60 }),
      })
      setV2Train(data)
    } catch (e) {
      setErr(e.message)
    }
  }

  async function v2GetSignal() {
    try {
      setErr(null)
      const data = await apiFetch('/api/ml/v2/forecaster/signal', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, period, max_articles: 60 }),
      })
      setV2Signal(data)
    } catch (e) {
      setErr(e.message)
    }
  }

  async function v2GetShap() {
    try {
      setErr(null)
      setV2Llm(null)
      const data = await apiFetch('/api/ml/v2/forecaster/shap', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, period, max_articles: 60 }),
      })
      setV2Shap(data)
    } catch (e) {
      setErr(e.message)
    }
  }

  async function v2ExplainWithLLM() {
    try {
      setErr(null)
      if (!v2Shap?.shap) {
        throw new Error('Run V2 SHAP first to produce shap_context')
      }
      const data = await apiFetch('/api/ml/v2/llm/explain', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_preference: v2Preference,
          shap_context: v2Shap,
          prompt: v2Prompt,
        }),
      })
      setV2Llm(data)
    } catch (e) {
      setErr(e.message)
    }
  }

  async function guestExplain() {
    try {
      setGuestErr(null)
      setGuestLlm(null)
      const data = await apiFetch('/api/ml/v2/public/explain', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: guestSymbol,
          prompt: guestPrompt,
          range: guestRange,
        }),
      })
      setGuestLlm(data)
    } catch (e) {
      setGuestErr(e.message)
    }
  }

  useEffect(() => {
    if (isGuest) {
      setTokenPreview(null)
      setMe(null)
      return
    }
    refreshMe()
    refreshVault()
    setTokenPreview(getAccessToken())
  }, [isGuest])

  if (isGuest) {
    return (
      <main style={{ padding: 24, fontFamily: 'Arial' }}>
        <h2>Guest Market Analysis</h2>
        <p>
          Public visitors can query the LLM for the five featured assets only: BTC, ETH, AAPL, MSFT, and TSLA.
        </p>
        <Section title="Guest LLM">
          <div style={{ display: 'grid', gridTemplateColumns: '160px 1fr', gap: 8, maxWidth: 520 }}>
            <label>Asset</label>
            <select value={guestSymbol} onChange={(e) => setGuestSymbol(e.target.value)}>
              {['BTC', 'ETH', 'AAPL', 'MSFT', 'TSLA'].map((asset) => (
                <option key={asset} value={asset}>
                  {asset}
                </option>
              ))}
            </select>

            <label>Chart range</label>
            <select value={guestRange} onChange={(e) => setGuestRange(e.target.value)}>
              <option value="1d">1D</option>
              <option value="1mo">1M</option>
              <option value="1y">1Y</option>
              <option value="max">MAX</option>
            </select>

            <label>Prompt</label>
            <textarea
              rows={4}
              value={guestPrompt}
              onChange={(e) => setGuestPrompt(e.target.value)}
              style={{ width: '100%' }}
            />
          </div>

          <div style={{ marginTop: 12 }}>
            <button onClick={guestExplain}>Ask LLM</button>
          </div>
          {guestErr ? <p style={{ color: 'crimson' }}>Error: {guestErr}</p> : null}
          <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(guestLlm, null, 2)}</pre>
        </Section>

        <p style={{ marginTop: 20 }}>
          <Link href="/login">Sign in</Link> | <Link href="/signup">Register</Link>
        </p>
      </main>
    )
  }

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

      <Section title="V2 AI (FinBERT + LightGBM + SHAP + Dual-LLM)">
        <p style={{ marginTop: 0, color: '#555' }}>
          Uses new backend endpoints under <code>/api/ml/v2</code>. Train first, then request SHAP, then optionally ask
          the LLM to summarize using only SHAP context.
        </p>

        <h4>FinBERT sentiment (single text)</h4>
        <div>
          <textarea
            value={v2Text}
            onChange={(e) => setV2Text(e.target.value)}
            rows={4}
            style={{ width: '100%', maxWidth: 760 }}
          />
        </div>
        <div style={{ marginTop: 8 }}>
          <button onClick={v2AnalyzeSentiment}>Analyze sentiment</button>
        </div>
        <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(v2Sentiment, null, 2)}</pre>

        <h4>Forecaster (v2)</h4>
        <div style={{ marginTop: 8 }}>
          <button onClick={v2TrainModel}>Train v2 model</button>
          <button onClick={v2GetSignal} style={{ marginLeft: 8 }}>
            Get signal
          </button>
          <button onClick={v2GetShap} style={{ marginLeft: 8 }}>
            Get SHAP
          </button>
        </div>
        <h5>Train result</h5>
        <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(v2Train, null, 2)}</pre>
        <h5>Signal result</h5>
        <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(v2Signal, null, 2)}</pre>
        <h5>SHAP result</h5>
        <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(v2Shap, null, 2)}</pre>

        <h4>LLM explanation (uses only SHAP context)</h4>
        <div style={{ display: 'grid', gridTemplateColumns: '160px 1fr', gap: 8, maxWidth: 760 }}>
          <label>LLM preference</label>
          <select value={v2Preference} onChange={(e) => setV2Preference(e.target.value)} style={{ maxWidth: 240 }}>
            <option value="open-source">open-source</option>
            <option value="custom">custom</option>
          </select>
          <label>Prompt</label>
          <input value={v2Prompt} onChange={(e) => setV2Prompt(e.target.value)} />
        </div>
        <div style={{ marginTop: 8 }}>
          <button onClick={v2ExplainWithLLM}>Generate explanation</button>
        </div>
        <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(v2Llm, null, 2)}</pre>
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
