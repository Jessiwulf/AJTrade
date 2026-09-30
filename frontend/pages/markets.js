import { useEffect, useMemo, useState } from 'react'
import useSWR from 'swr'
import Link from 'next/link'
import AppShell from '../components/AppShell'
import { apiFetch } from '../lib/api'
import styles from '../styles/Markets.module.css'

const LOOKBACK_OPTIONS = [7, 30, 90]

function todayDateString() {
  return new Date().toISOString().slice(0, 10)
}

function subtractDaysDateString(days) {
  const date = new Date()
  date.setDate(date.getDate() - Number(days || 0))
  return date.toISOString().slice(0, 10)
}

function formatPrice(value) {
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function sentimentClassName(label) {
  const v = String(label || '').trim().toLowerCase()
  if (v === 'positive' || v === 'bullish' || v === 'strong bullish') return 'text-green-600 font-bold'
  if (v === 'negative' || v === 'bearish' || v === 'strong bearish') return 'text-red-600 font-bold'
  if (v === 'neutral') return 'text-gray-500 font-bold'
  return 'text-gray-500 font-bold'
}

function sentimentScoreText(score) {
  const n = Number(score)
  if (Number.isNaN(n)) return '0.0'
  return (n * 100).toFixed(1)
}

export default function MarketsPage() {
  const [items, setItems] = useState([])
  const [lookbackDays, setLookbackDays] = useState(7)
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [loadingMoreBySymbol, setLoadingMoreBySymbol] = useState({})
  const [assetErrors, setAssetErrors] = useState({})

  function formatPublishedAt(value) {
    if (!value) return 'Unknown time'
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return String(value)
    return date.toLocaleString()
  }

  function buildNewsQuery({ page, pageSize } = {}) {
    const params = new URLSearchParams()
    params.set('page_size', String(pageSize || 6))
    if (page) params.set('page', String(page))

    if (fromDate || toDate) {
      if (fromDate) params.set('from_date', fromDate)
      if (toDate) params.set('to_date', toDate)
    } else {
      params.set('days', String(lookbackDays))
    }

    return params.toString()
  }

  function applyPreset(days) {
    setLookbackDays(days)
    setFromDate('')
    setToDate('')
  }

  function useRecentNews() {
    applyPreset(7)
  }

  const newsQuery = useMemo(() => buildNewsQuery({ pageSize: 6 }), [lookbackDays, fromDate, toDate])
  const newsKey = `/api/ml/v2/watchlist/news?${newsQuery}`
  const { data: newsData, error, isLoading, mutate } = useSWR(newsKey)

  useEffect(() => {
    if (Array.isArray(newsData)) {
      setItems(newsData)
    }
  }, [newsData])

  const loading = isLoading && !items.length
  const errorMessage = error?.message || ''

  async function loadOlderNews(symbol) {
    const current = items.find((item) => item.symbol === symbol)
    if (!current || loadingMoreBySymbol[symbol]) return

    const nextPage = Number(current.page || 1) + 1
    setLoadingMoreBySymbol((state) => ({ ...state, [symbol]: true }))
    setAssetErrors((state) => ({ ...state, [symbol]: '' }))

    try {
      const data = await apiFetch(
        `/api/ml/v2/watchlist/news/${encodeURIComponent(symbol)}?${buildNewsQuery({
          page: nextPage,
          pageSize: current.page_size || 6,
        })}`
      )
      setItems((state) => {
        const updated = state.map((item) => {
          if (item.symbol !== symbol) return item
          return {
            ...item,
            ...data,
            articles: [...(item.articles || []), ...(data.articles || [])],
            articles_count: (item.articles || []).length + (data.articles || []).length,
          }
        })
        mutate(updated, false)
        return updated
      })
    } catch (e) {
      setAssetErrors((state) => ({
        ...state,
        [symbol]: e.message || 'Unable to load older news.',
      }))
    } finally {
      setLoadingMoreBySymbol((state) => ({ ...state, [symbol]: false }))
    }
  }

  async function loadRecentNews(symbol) {
    const current = items.find((item) => item.symbol === symbol)
    if (!current || loadingMoreBySymbol[symbol]) return

    setLoadingMoreBySymbol((state) => ({ ...state, [symbol]: true }))
    setAssetErrors((state) => ({ ...state, [symbol]: '' }))

    try {
      const data = await apiFetch(
        `/api/ml/v2/watchlist/news/${encodeURIComponent(symbol)}?${buildNewsQuery({
          page: 1,
          pageSize: current.page_size || 6,
        })}`
      )
      setItems((state) => {
        const updated = state.map((item) => (item.symbol === symbol ? data : item))
        mutate(updated, false)
        return updated
      })
    } catch (e) {
      setAssetErrors((state) => ({
        ...state,
        [symbol]: e.message || 'Unable to load recent news.',
      }))
    } finally {
      setLoadingMoreBySymbol((state) => ({ ...state, [symbol]: false }))
    }
  }

  const hasCustomDateRange = Boolean(fromDate || toDate)
  const isRecentNewsActive = !hasCustomDateRange && lookbackDays === 7

  return (
    <AppShell title="Markets News" subtitle="News and sentiment for your current watchlist">
      <div className={styles.page}>
        <section className={styles.toolbar}>
          <div className={styles.filterGroup}>
            <span className={styles.filterLabel}>Look back</span>
            <button
              type="button"
              className={`${styles.filterButton} ${isRecentNewsActive ? styles.filterButtonActive : ''}`}
              onClick={useRecentNews}
              disabled={loading}
            >
              Recent news
            </button>
            {LOOKBACK_OPTIONS.filter(d => d !== 7).map((days) => (
              <button
                key={days}
                type="button"
                className={`${styles.filterButton} ${!hasCustomDateRange && lookbackDays === days ? styles.filterButtonActive : ''}`}
                onClick={() => applyPreset(days)}
                disabled={loading}
              >
                {days}D
              </button>
            ))}
          </div>
          <div className={styles.dateGroup}>
            <label className={styles.dateField}>
              <span className={styles.filterLabel}>From</span>
              <input
                type="date"
                className={styles.dateInput}
                value={fromDate}
                max={toDate || todayDateString()}
                onChange={(e) => setFromDate(e.target.value)}
              />
            </label>
            <label className={styles.dateField}>
              <span className={styles.filterLabel}>To</span>
              <input
                type="date"
                className={styles.dateInput}
                value={toDate}
                min={fromDate || subtractDaysDateString(365)}
                max={todayDateString()}
                onChange={(e) => setToDate(e.target.value)}
              />
            </label>
            <button
              type="button"
              className={styles.secondaryButton}
              onClick={() => {
                setFromDate('')
                setToDate('')
              }}
              disabled={loading || (!fromDate && !toDate)}
            >
              Clear dates
            </button>
          </div>
        </section>

        {loading ? <section className={styles.emptyState}>Loading watchlist news...</section> : null}
        {!loading && errorMessage ? <section className={styles.emptyState}>Error: {errorMessage}</section> : null}
        {!loading && !errorMessage && !items.length ? (
          <section className={styles.emptyState}>
            No watchlist assets found. Add assets on <Link href="/watchlist">Watchlist</Link>. To populate live articles, save a NewsAPI key on <Link href="/api-keys">API Management</Link>.
          </section>
        ) : null}

        {items.map((item) => (
          <section key={item.symbol} className={styles.assetCard}>
            <header className={styles.assetHeader}>
              <div className={styles.assetTitleBlock}>
                <h2 className={styles.symbol}>{item.symbol}</h2>
                <p className={styles.meta}>
                  {item.display_name || item.symbol} · <span className={sentimentClassName(item.sentiment_label)}>{item.sentiment_label}</span> · {item.articles_count} articles
                </p>
              </div>
              <div className={styles.priceBlock}>
                <div>
                  <span className="text-xs text-gray-500">Live Price:</span>{' '}
                  <strong>{item.price ? formatPrice(item.price) : '—'}</strong>
                </div>
                <div>
                  <span className="text-xs text-gray-500">Daily Change:</span>{' '}
                  <span className={item.price_change_pct >= 0 ? styles.pos : styles.neg}>{item.price_change_pct?.toFixed(2)}%</span>
                </div>
              </div>
            </header>

            {item.news_note ? (
              <p className={styles.noArticles}>
                {item.news_note}
                {item.stale && item.saved_at ? ` Last fetched ${formatPublishedAt(item.saved_at)}.` : ''}
              </p>
            ) : null}

            {item.articles?.length ? (
              <div className={styles.articleGrid}>
                {item.articles.map((article, index) => (
                  <article key={`${item.symbol}-${index}`} className={styles.articleCard}>
                    <p className={styles.articleMeta}>
                      {article.source} · <span className={sentimentClassName(article.sentiment_label)}>{article.sentiment_label} (Score: {sentimentScoreText(article.sentiment_score)})</span> · {formatPublishedAt(article.published_at)}
                    </p>
                    <h3 className={styles.articleTitle}>{article.title}</h3>
                    <p className={styles.articleExcerpt}>{article.excerpt || 'No summary available.'}</p>
                    {article.url ? (
                      <a href={article.url} target="_blank" rel="noreferrer" className={styles.articleLink}>Read article</a>
                    ) : null}
                  </article>
                ))}
              </div>
            ) : (
              <p className={styles.noArticles}>
                No articles found for this asset in the selected range. The free NewsAPI plan only searches about the last 30 days.
              </p>
            )}

            <div className={styles.articleActions}>
              {(Number(item.page || 1) > 1 || (item.articles || []).length > (item.page_size || 6)) ? (
                <button
                  type="button"
                  className={styles.secondaryButton}
                  onClick={() => loadRecentNews(item.symbol)}
                  disabled={Boolean(loadingMoreBySymbol[item.symbol])}
                >
                  {loadingMoreBySymbol[item.symbol] ? 'Loading...' : 'Recent news'}
                </button>
              ) : null}
              {item.has_more ? (
                <button
                  type="button"
                  className={styles.loadMoreButton}
                  onClick={() => loadOlderNews(item.symbol)}
                  disabled={Boolean(loadingMoreBySymbol[item.symbol])}
                >
                  {loadingMoreBySymbol[item.symbol] ? 'Loading...' : 'Load older news'}
                </button>
              ) : null}
              {assetErrors[item.symbol] ? <p className={styles.inlineError}>Error: {assetErrors[item.symbol]}</p> : null}
            </div>
          </section>
        ))}
      </div>
    </AppShell>
  )
}