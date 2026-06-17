import { useEffect, useState } from 'react'
import Link from 'next/link'
import AppShell from '../components/AppShell'
import { apiFetch } from '../lib/api'
import styles from '../styles/Insights.module.css'

export default function Insights() {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    async function loadInsights() {
      setLoading(true)
      setError('')
      try {
        const data = await apiFetch('/api/ml/v2/watchlist/insights')
        if (!cancelled) setItems(Array.isArray(data) ? data : [])
      } catch (e) {
        if (!cancelled) setError(e.message || 'Unable to load insights.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    loadInsights()
    return () => { cancelled = true }
  }, [])

  return (
    <AppShell
      title="AI Insights"
      subtitle="Sentiment & recommendations"
    >
      <div className={styles.list}>
        {loading ? <section className={styles.emptyState}>Loading live watchlist insights...</section> : null}
        {!loading && error ? <section className={styles.emptyState}>Error: {error}</section> : null}
        {!loading && !error && !items.length ? (
          <section className={styles.emptyState}>
            No watchlist insights available yet. Add assets on <Link href="/watchlist">Watchlist</Link> and optionally save a NewsAPI key on <Link href="/api-keys">API Management</Link>.
          </section>
        ) : null}
        {items.map((a) => (
          <section key={a.symbol} className={styles.card} aria-label={`${a.symbol} insight`}>
            <div>
              <h2 className={styles.asset}>{a.symbol}</h2>
              <p className={styles.trendSummary}>{a.trend_summary}</p>

              <div className={styles.meta}>
                <div>
                  <p className={styles.label}>AI Recommendation</p>
                  <p className={`${styles.reco} ${a.signal === 'BUY' ? styles.recoStrongBuy : a.signal === 'SELL' ? styles.recoSell : styles.recoHold}`}>{a.recommendation}</p>
                </div>

                <div>
                  <p className={styles.label}>Rationale</p>
                  <ul className={styles.rationale}>
                    {a.rationale.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>

            <aside className={styles.side} aria-label="Automation">
              <div>
                <p className={styles.label}>Confidence Score</p>
                <p className={styles.confidence}>{a.confidence}%</p>
              </div>
              <div className={styles.sideStats}>
                <span>Signal: {a.signal}</span>
                <span>Up Probability: {(Number(a.probability_up || 0) * 100).toFixed(0)}%</span>
              </div>
              <Link href="/automated" className={styles.primary}>
                AUTOMATE
              </Link>
            </aside>
          </section>
        ))}
      </div>
    </AppShell>
  )
}
