import { useCallback, useEffect, useMemo, useState } from 'react'
import AppShell from '../components/AppShell'
import { apiFetch } from '../lib/api'
import styles from '../styles/AIPerformance.module.css'

function formatNumber(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '0'
  return n.toLocaleString()
}

function formatPercent(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '0.00%'
  return `${n.toFixed(2)}%`
}

function formatLatency(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '0 ms'
  return `${n.toFixed(2)} ms`
}

function formatTime(epochSeconds) {
  const n = Number(epochSeconds)
  if (!Number.isFinite(n) || n <= 0) return '-'
  return new Date(n * 1000).toLocaleString()
}

function MetricCard({ label, value, sub }) {
  return (
    <article className={styles.metricCard}>
      <p>{label}</p>
      <strong>{value}</strong>
      {sub ? <span>{sub}</span> : null}
    </article>
  )
}

function FinbertMonitor({ data }) {
  const dist = data?.aggregated_sentiment_distribution || {}
  const bars = [
    { label: 'Positive', count: Number(dist?.positive?.count || 0), pct: Number(dist?.positive?.pct || 0), cls: styles.pos },
    { label: 'Neutral', count: Number(dist?.neutral?.count || 0), pct: Number(dist?.neutral?.pct || 0), cls: '' },
    { label: 'Negative', count: Number(dist?.negative?.count || 0), pct: Number(dist?.negative?.pct || 0), cls: styles.neg },
  ]

  return (
    <section className={styles.panel}>
      <h3>FinBERT Monitor</h3>
      <div className={styles.metricsGrid}>
        <MetricCard label="Total Articles Fetched" value={formatNumber(data?.total_articles_fetched)} />
        <MetricCard
          label="NewsAPI Rate Limit Usage"
          value={`${formatNumber(data?.newsapi_rate_limit_usage?.used)} / ${formatNumber(data?.newsapi_rate_limit_usage?.limit)}`}
          sub={formatPercent(data?.newsapi_rate_limit_usage?.usage_pct)}
        />
      </div>
      <div className={styles.distribution}>
        {bars.map((bar) => (
          <article key={bar.label} className={styles.distRow}>
            <div className={styles.distLabel}><span>{bar.label}</span><strong className={bar.cls}>{bar.count}</strong></div>
            <div className={styles.distTrack}><div className={`${styles.distFill} ${bar.cls}`} style={{ width: `${Math.min(100, Math.max(0, bar.pct))}%` }} /></div>
            <small>{formatPercent(bar.pct)}</small>
          </article>
        ))}
      </div>
    </section>
  )
}

function ForecasterMonitor({ data }) {
  const rows = Array.isArray(data?.rows) ? data.rows : []
  return (
    <section className={styles.panel}>
      <div className={styles.panelTitleRow}>
        <h3>Forecaster & XAI Monitor</h3>
        <span className={styles.infoWrap}>
          <button
            type="button"
            className={styles.infoIcon}
            aria-label="Explain Forecaster and XAI monitor metrics"
          >
            ?
          </button>
          <div role="tooltip" className={styles.infoTooltip}>
            <p><strong>How to read this table</strong></p>
            <p><strong>Asset:</strong> Ticker symbol used by the model run.</p>
            <p><strong>Raw Forecast Score:</strong> Model confidence mapped to a scale from -1 to +1. Values above 0 suggest bullish pressure, below 0 suggest bearish pressure.</p>
            <p><strong>Bull Threshold:</strong> Minimum score required before the system treats a signal as meaningful upside.</p>
            <p><strong>Bear Threshold:</strong> Maximum score (negative side) required before the system treats a signal as meaningful downside.</p>
            <p><strong>TreeSHAP Explainability Log:</strong> Top feature impacts from the model. This explains why the score moved and gives context for trust and risk review.</p>
            <p><strong>Why it matters:</strong> Compare Raw Forecast Score against Bull/Bear thresholds first. If the score stays between thresholds, the signal is usually weak and less actionable.</p>
          </div>
        </span>
      </div>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Asset</th>
              <th>Raw Forecast Score</th>
              <th>Bull Threshold</th>
              <th>Bear Threshold</th>
              <th>TreeSHAP Explainability Log</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.asset || 'asset'}-${index}`}>
                <td><strong>{row.asset || '-'}</strong></td>
                <td>{Number(row.raw_forecast_score || 0).toFixed(4)}</td>
                <td>{Number(row.bull_threshold || 0).toFixed(2)}</td>
                <td>{Number(row.bear_threshold || 0).toFixed(2)}</td>
                <td className={styles.logCell}>{row.treeshap_log || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.length ? <p className={styles.empty}>No forecaster telemetry yet. Run forecast/signal endpoints to populate this monitor.</p> : null}
    </section>
  )
}

function LlmMonitor({ data }) {
  const rows = Array.isArray(data?.recent_prompts_history) ? data.recent_prompts_history : []

  return (
    <section className={styles.panel}>
      <h3>LLM Assistant Monitor</h3>
      <div className={styles.metricsGrid}>
        <MetricCard label="Total Chat Prompts" value={formatNumber(data?.total_chat_prompts)} />
        <MetricCard label="Token Usage" value={formatNumber(data?.token_usage)} />
        <MetricCard label="Avg Response Latency (ms)" value={formatLatency(data?.avg_response_latency_ms)} />
      </div>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Time</th>
              <th>Symbol</th>
              <th>Prompt</th>
              <th>Model</th>
              <th>Tokens</th>
              <th>Latency (ms)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`prompt-${index}`}>
                <td>{formatTime(row.timestamp_epoch)}</td>
                <td>{row.symbol || '-'}</td>
                <td className={styles.promptCell}>{row.prompt || '-'}</td>
                <td>{row.model_used || '-'}</td>
                <td>{formatNumber(row.total_tokens)}</td>
                <td>{Number(row.latency_ms || 0).toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.length ? <p className={styles.empty}>No prompt history yet. Use AI explain endpoints to generate telemetry.</p> : null}
    </section>
  )
}

export default function AIPerformancePage() {
  const [finbert, setFinbert] = useState(null)
  const [forecaster, setForecaster] = useState(null)
  const [llm, setLlm] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const lastUpdated = useMemo(() => {
    const timestamp = finbert?.updated_at_epoch
    return formatTime(timestamp)
  }, [finbert])

  const loadData = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [finbertData, forecasterData, llmData] = await Promise.all([
        apiFetch('/api/ai-performance/finbert'),
        apiFetch('/api/ai-performance/forecaster'),
        apiFetch('/api/ai-performance/llm'),
      ])
      setFinbert(finbertData || null)
      setForecaster(forecasterData || null)
      setLlm(llmData || null)
    } catch (fetchError) {
      setError(fetchError.message || 'Unable to load AI monitoring telemetry.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadData()
  }, [loadData])

  return (
    <AppShell title="AI Performance" subtitle="Telemetry and observability across FinBERT, Forecaster/XAI, and LLM assistant">
      <div className={styles.page}>
        <header className={styles.header}>
          <h2>AI Performance</h2>
          <div className={styles.headerActions}>
            <span>Last update: {lastUpdated}</span>
            <button type="button" onClick={loadData} disabled={loading}>{loading ? 'Refreshing...' : 'Refresh'}</button>
          </div>
        </header>
        {error ? <p className={styles.error}>{error}</p> : null}
        <FinbertMonitor data={finbert} />
        <ForecasterMonitor data={forecaster} />
        <LlmMonitor data={llm} />
      </div>
    </AppShell>
  )
}
