import { useEffect, useState } from 'react'
import Link from 'next/link'
import AppShell from '../components/AppShell'
import { apiFetch } from '../lib/api'
import styles from '../styles/Markets.module.css'

function formatPrice(value) {
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

export default function MarketsPage() {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    async function loadNews() {
      setLoading(true)
      setError('')
      try {
        const data = await apiFetch('/api/ml/v2/watchlist/news')
        if (!cancelled) setItems(Array.isArray(data) ? data : [])
      } catch (e) {
        if (!cancelled) setError(e.message || 'Unable to load market news.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    loadNews()
    return () => { cancelled = true }
  }, [])

  return (
    <AppShell title="Markets" subtitle="News and sentiment for your current watchlist">
      <div className={styles.page}>
        {loading ? <section className={styles.emptyState}>Loading watchlist news...</section> : null}
        {!loading && error ? <section className={styles.emptyState}>Error: {error}</section> : null}
        {!loading && !error && !items.length ? (
          <section className={styles.emptyState}>
            No watchlist assets found. Add assets on <Link href="/watchlist">Watchlist</Link>. To populate live articles, save a NewsAPI key on <Link href="/api-keys">API Management</Link>.
          </section>
        ) : null}

        {items.map((item) => (
          <section key={item.symbol} className={styles.assetCard}>
            <header className={styles.assetHeader}>
              <div>
                <h2 className={styles.symbol}>{item.symbol}</h2>
                <p className={styles.meta}>{item.sentiment_label} · {item.articles_count} articles</p>
              </div>
              <div className={styles.priceBlock}>
                <strong>{item.price ? formatPrice(item.price) : '—'}</strong>
                <span className={item.price_change_pct >= 0 ? styles.pos : styles.neg}>{item.price_change_pct?.toFixed(2)}%</span>
              </div>
            </header>

            {item.articles?.length ? (
              <div className={styles.articleGrid}>
                {item.articles.map((article, index) => (
                  <article key={`${item.symbol}-${index}`} className={styles.articleCard}>
                    <p className={styles.articleMeta}>{article.source} · {article.sentiment_label}</p>
                    <h3 className={styles.articleTitle}>{article.title}</h3>
                    <p className={styles.articleExcerpt}>{article.excerpt || 'No summary available.'}</p>
                    {article.url ? (
                      <a href={article.url} target="_blank" rel="noreferrer" className={styles.articleLink}>Read article</a>
                    ) : null}
                  </article>
                ))}
              </div>
            ) : (
              <p className={styles.noArticles}>No live articles were returned for this asset. Check your NewsAPI key in API Management.</p>
            )}
          </section>
        ))}
      </div>
    </AppShell>
  )
}