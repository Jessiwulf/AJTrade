import useSWR from 'swr'
import Link from 'next/link'
import AppShell from '../components/AppShell'
import styles from '../styles/Insights.module.css'

function sentimentClassName(label) {
  const v = String(label || '').trim().toLowerCase()
  if (v === 'positive' || v === 'bullish' || v === 'strong bullish') return 'text-green-600 font-bold'
  if (v === 'negative' || v === 'bearish' || v === 'strong bearish') return 'text-red-600 font-bold'
  if (v === 'neutral') return 'text-gray-500 font-bold'
  return 'text-gray-500 font-bold'
}

export default function Insights() {
  const { data, error, isLoading } = useSWR('/api/ml/v2/watchlist/insights')
  const items = Array.isArray(data) ? data : []
  const loading = isLoading && !items.length
  const errorMessage = error?.message || ''

  return (
    <AppShell
      title="AI Insights"
      subtitle="Sentiment & recommendations"
    >
      <div className={styles.list}>
        {loading ? <section className={styles.emptyState}>Loading live watchlist insights...</section> : null}
        {!loading && errorMessage ? <section className={styles.emptyState}>Error: {errorMessage}</section> : null}
        {!loading && !errorMessage && !items.length ? (
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
                  <p className={`${styles.reco} ${sentimentClassName(a.recommendation)}`}>{a.recommendation}</p>
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
                <span>Signal: <span className={sentimentClassName(a.recommendation)}>{a.signal}</span></span>
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
