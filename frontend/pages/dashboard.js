import { useEffect, useMemo, useState } from 'react'
import AppShell from '../components/AppShell'
import { apiFetch } from '../lib/api'
import { useSession } from '../components/AppShell'
import styles from '../styles/Dashboard.module.css'

const GUEST_ASSETS = ['BTC', 'ETH', 'AAPL', 'MSFT', 'TSLA']

function formatDate(value) {
  if (!value) return ''
  try {
    return new Date(value).toLocaleString()
  } catch {
    return String(value)
  }
}

function DonutChart({ percent = 0.62 }) {
  const p = Math.max(0, Math.min(1, Number(percent) || 0))
  const r = 34
  const c = 2 * Math.PI * r
  const dash = c * p
  const gap = c - dash

  return (
    <svg width="90" height="90" viewBox="0 0 90 90" aria-hidden="true">
      <defs>
        <linearGradient id="ajDonut" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--aj-cta-from)" />
          <stop offset="1" stopColor="var(--aj-cta-to)" />
        </linearGradient>
      </defs>
      <circle cx="45" cy="45" r={r} fill="none" stroke="var(--aj-border)" strokeWidth="10" />
      <circle
        cx="45"
        cy="45"
        r={r}
        fill="none"
        stroke="url(#ajDonut)"
        strokeWidth="10"
        strokeLinecap="round"
        strokeDasharray={`${dash} ${gap}`}
        transform="rotate(-90 45 45)"
      />
    </svg>
  )
}

const PERIODS = [
  { label: '1D', value: 'day' },
  { label: '1M', value: 'month' },
  { label: '1Y', value: 'year' },
  { label: 'All', value: 'all' },
]

function priceToPoints(points, width = 700, height = 260) {
  const closes = points.map((point) => Number(point.close)).filter((value) => Number.isFinite(value))
  if (!closes.length) return ''
  const min = Math.min(...closes)
  const max = Math.max(...closes)
  const span = max - min || 1
  const step = points.length > 1 ? (width - 36) / (points.length - 1) : 0

  return points
    .map((point, index) => {
      const close = Number(point.close)
      if (!Number.isFinite(close)) return null
      const x = 18 + step * index
      const y = 18 + (1 - (close - min) / span) * (height - 36)
      return `${x},${y}`
    })
    .filter(Boolean)
    .join(' ')
}

function AreaChart({ points = [], label = 'Loading...' }) {
  const closes = points.map((point) => Number(point.close)).filter((value) => Number.isFinite(value))
  const first = closes[0]
  const last = closes[closes.length - 1]
  const change = first && last ? last - first : null
  const changePct = change !== null && first ? (change / first) * 100 : null
  const linePoints = priceToPoints(points)
  const fillPoints = linePoints ? `${linePoints} 682,242 18,242` : ''

  return (
    <div>
      <div className={styles.chartMetaRow}>
        <div>
          <p className={styles.cardTitle} style={{ marginBottom: 4 }}>{label}</p>
          <h2 className={styles.assetName} style={{ marginBottom: 6 }}>
            {last ? `$${last.toFixed(2)}` : '--'}
          </h2>
        </div>
        <div className={change >= 0 ? styles.changePos : styles.changeNeg}>
          {change !== null && Number.isFinite(change) ? `${change >= 0 ? '+' : ''}${change.toFixed(2)}` : '--'}
          {changePct !== null && Number.isFinite(changePct) ? ` (${changePct >= 0 ? '+' : ''}${changePct.toFixed(2)}%)` : ''}
        </div>
      </div>
      <svg width="100%" height="260" viewBox="0 0 700 260" aria-hidden="true">
      <defs>
        <linearGradient id="ajArea" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--aj-cta-to)" stopOpacity="0.32" />
          <stop offset="1" stopColor="var(--aj-cta-to)" stopOpacity="0" />
        </linearGradient>
      </defs>
      {linePoints ? (
        <>
          <polyline
            points={linePoints}
            fill="none"
            stroke="var(--aj-cta-to)"
            strokeWidth="4"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <polygon points={fillPoints} fill="url(#ajArea)" opacity="0.65" />
        </>
      ) : null}
      <g stroke="var(--aj-border-soft)" strokeWidth="1">
        <line x1="18" y1="242" x2="682" y2="242" />
        <line x1="18" y1="60" x2="682" y2="60" />
        <line x1="18" y1="120" x2="682" y2="120" />
        <line x1="18" y1="180" x2="682" y2="180" />
      </g>
    </svg>
    </div>
  )
}

export default function Dashboard() {
  const { isGuest } = useSession()
  const [asset, setAsset] = useState('AAPL')
  const [side, setSide] = useState('buy')
  const [amount, setAmount] = useState('1000')
  const [range, setRange] = useState('day')
  const [watchlist, setWatchlist] = useState([])
  const [watchlistLoading, setWatchlistLoading] = useState(true)
  const [watchlistError, setWatchlistError] = useState(null)
  const [quotes, setQuotes] = useState({})
  const [chart, setChart] = useState({ points: [], quote: null, symbol: asset })
  const [chartLoading, setChartLoading] = useState(true)
  const [chartError, setChartError] = useState(null)

  async function loadWatchlist() {
    setWatchlistLoading(true)
    setWatchlistError(null)
    if (isGuest) {
      setWatchlist(GUEST_ASSETS.map((symbol) => ({ symbol, notes: 'Public watchlist' })))
      setWatchlistLoading(false)
      return
    }
    try {
      const data = await apiFetch('/api/watchlist')
      setWatchlist(Array.isArray(data) ? data : [])
    } catch (error) {
      setWatchlist([])
      setWatchlistError(error.message || 'Failed to load watchlist')
    } finally {
      setWatchlistLoading(false)
    }
  }

  async function loadQuotes(symbols) {
    const clean = (symbols || []).map((s) => String(s || '').trim().toUpperCase()).filter(Boolean)
    if (!clean.length) {
      setQuotes({})
      return
    }
    try {
      const data = await apiFetch(`/api/market/quotes?symbols=${encodeURIComponent(clean.join(','))}`)
      const map = {}
      for (const row of Array.isArray(data) ? data : []) {
        if (row?.symbol) map[row.symbol] = row
      }
      setQuotes(map)
    } catch {
      // keep the dashboard usable even when live quotes are temporarily unavailable
      setQuotes({})
    }
  }

  async function loadChart(symbol, selectedRange) {
    if (!symbol) return
    setChartLoading(true)
    setChartError(null)
    try {
      const data = await apiFetch(
        `/api/market/chart/${encodeURIComponent(symbol)}?range=${encodeURIComponent(selectedRange)}`
      )
      setChart({
        points: Array.isArray(data?.points) ? data.points : [],
        quote: data?.quote || null,
        symbol: data?.symbol || symbol,
      })
    } catch (error) {
      setChart({ points: [], quote: null, symbol })
      setChartError(error.message || 'Failed to load chart')
    } finally {
      setChartLoading(false)
    }
  }

  useEffect(() => {
    if (isGuest) {
      setWatchlist(GUEST_ASSETS.map((symbol) => ({ symbol, notes: 'Public watchlist' })))
      setWatchlistLoading(false)
      setWatchlistError(null)
      return
    }
    loadWatchlist()
  }, [isGuest])

  useEffect(() => {
    if (isGuest) return
    loadQuotes(watchlist.map((item) => item.symbol))
  }, [watchlist, isGuest])

  useEffect(() => {
    if (watchlist.length && !watchlist.some((item) => item.symbol === asset)) {
      setAsset(watchlist[0].symbol)
    }
  }, [watchlist, asset])

  useEffect(() => {
    if (isGuest) return
    loadChart(asset, range)
  }, [asset, range, isGuest])

  const watchlistSummary = useMemo(() => {
    return {
      count: watchlist.length,
      latest: watchlist[0] || null,
    }
  }, [watchlist])

  const selectedQuote = quotes[asset] || chart.quote || null

  if (isGuest) {
    return (
      <AppShell
        title="Trade Dashboard"
        subtitle="Public read-only market snapshot"
      >
        <div className={styles.grid}>
          <section className={styles.card} aria-label="Public market overview">
            <p className={styles.cardTitle}>Public Dashboard</p>
            <p className={styles.bigValue}>Read only</p>
            <div className={styles.subRow}>
              <span>Five featured assets</span>
              <span className={styles.pill}>Guest access</span>
            </div>
            <p className={styles.msg} style={{ marginTop: 14 }}>
              Sign in to customize the watchlist, place trades, or run automation.
            </p>
          </section>

          <section className={styles.card} aria-label="Featured assets">
            <p className={styles.cardTitle}>Featured Assets</p>
            <div style={{ display: 'grid', gap: 12 }}>
              {GUEST_ASSETS.map((symbol) => (
                <div
                  key={symbol}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 12,
                    padding: '12px 14px',
                    borderRadius: 16,
                    border: '1px solid var(--aj-border)',
                    background: 'var(--aj-surface)',
                  }}
                >
                  <div>
                    <div style={{ fontWeight: 900, letterSpacing: '-0.02em' }}>{symbol}</div>
                    <div style={{ color: 'var(--aj-text-muted)', fontSize: 13 }}>Read only</div>
                  </div>
                  <div className={styles.pill}>Locked</div>
                </div>
              ))}
            </div>
          </section>

          <section className={styles.card} aria-label="Guest access notice">
            <p className={styles.cardTitle}>Guest Limits</p>
            <div style={{ display: 'grid', gap: 10, color: 'var(--aj-text-muted)' }}>
              <div>LLM analysis is limited to the five public symbols.</div>
              <div>Watchlist, portfolio, automation, and API key management require sign in.</div>
              <div>Admin controls are hidden behind an authenticated route guard.</div>
            </div>
          </section>
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell
      title="Trade Dashboard"
      subtitle="Portfolio monitoring"
    >
      <div className={styles.grid}>
        <div className={styles.columns}>
          <section className={styles.card} aria-label="Portfolio overview">
            <p className={styles.cardTitle}>Portfolio Overview</p>
            <p className={styles.bigValue}>$1,221,223.00</p>
            <div className={styles.subRow}>
              <span>Total value</span>
              <span className={styles.pill}>▲ 26.25%</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 12 }}>
              <DonutChart percent={0.62} />
              <div style={{ display: 'grid', gap: 8 }}>
                <div style={{ color: 'var(--aj-text-muted)', fontSize: 13 }}>
                  Balance
                </div>
                <div style={{ fontWeight: 900, letterSpacing: '-0.02em' }}>$109,735.00</div>
                <div style={{ color: 'var(--aj-text-muted)', fontSize: 13 }}>
                  Performance
                </div>
                <div style={{ fontWeight: 900, color: 'var(--aj-positive)' }}>+26.25%</div>
              </div>
            </div>
          </section>

          <section className={styles.card} aria-label="Watchlist">
            <div className={styles.centerHeader} style={{ marginBottom: 12 }}>
              <div>
                <p className={styles.cardTitle} style={{ margin: 0 }}>Watchlist</p>
                <div className={styles.watchSummary}>
                  {watchlistLoading ? 'Loading...' : `${watchlistSummary.count} tracked`}
                  {watchlistSummary.latest ? ` • latest ${watchlistSummary.latest.symbol}` : ''}
                </div>
              </div>
              <button type="button" className={styles.miniButton} onClick={loadWatchlist}>
                Refresh
              </button>
            </div>
            <div className={styles.watchlist}>
              {watchlistLoading ? (
                <div className={styles.watchItem}>
                  <span className={styles.symbol}>Loading...</span>
                </div>
              ) : watchlistError ? (
                <div className={styles.watchItem}>
                  <span className={styles.symbol}>Unavailable</span>
                  <span className={styles.changeNeg}>{watchlistError}</span>
                </div>
              ) : watchlist.length ? (
                watchlist.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`${styles.watchItem} ${asset === item.symbol ? styles.watchItemActive : ''}`}
                    onClick={() => setAsset(item.symbol)}
                  >
                    <div className={styles.watchItemLeft}>
                      <span className={styles.symbol}>{item.symbol}</span>
                      <span className={styles.watchMeta}>
                        {item.notes ? item.notes : 'No notes'}
                        {item.created_at ? ` • Added ${formatDate(item.created_at)}` : ''}
                      </span>
                    </div>
                    <div className={styles.watchItemRight}>
                      <span className={styles.watchPrice}>
                        {quotes[item.symbol]?.price != null
                          ? `$${Number(quotes[item.symbol].price).toFixed(2)}`
                          : '--'}
                      </span>
                      <span
                        className={
                          Number(quotes[item.symbol]?.change) >= 0 ? styles.changePos : styles.changeNeg
                        }
                      >
                        {quotes[item.symbol]?.change_percent != null
                          ? `${quotes[item.symbol].change_percent >= 0 ? '+' : ''}${Number(
                              quotes[item.symbol].change_percent
                            ).toFixed(2)}%`
                          : 'N/A'}
                      </span>
                    </div>
                  </button>
                ))
              ) : (
                <div className={styles.watchItem}>
                  <span className={styles.symbol}>No symbols</span>
                  <span className={styles.changeNeg}>Add one in Watchlist</span>
                </div>
              )}
            </div>
          </section>
        </div>

        <section className={styles.card} aria-label="Selected asset chart">
          <div className={styles.centerHeader}>
            <div>
              <p className={styles.cardTitle} style={{ marginBottom: 6 }}>
                {chart.symbol || asset}
              </p>
              <h2 className={styles.assetName}>{chart.symbol || asset}</h2>
            </div>
            <div className={styles.periodRow} role="tablist" aria-label="Chart range">
              {PERIODS.map((period) => (
                <button
                  key={period.value}
                  type="button"
                  className={`${styles.periodButton} ${range === period.value ? styles.periodButtonActive : ''}`}
                  onClick={() => setRange(period.value)}
                >
                  {period.label}
                </button>
              ))}
            </div>
          </div>
          {chartLoading ? (
            <div className={styles.chartLoading}>Loading chart...</div>
          ) : chartError ? (
            <div className={styles.chartLoading}>{chartError}</div>
          ) : (
            <AreaChart
              label={
                selectedQuote?.price != null
                  ? `Live price ${chart.symbol || asset}`
                  : `Chart ${chart.symbol || asset}`
              }
              points={chart.points}
            />
          )}
        </section>

        <section className={styles.card} aria-label="Trade now">
          <p className={styles.cardTitle}>Trade Now</p>
          <div className={styles.tradeGrid}>
            <div>
              <div className={styles.label}>Asset</div>
              <select
                className={styles.select}
                value={asset}
                onChange={(e) => setAsset(e.target.value)}
                aria-label="Select asset"
              >
                <option value="AAPL">AAPL</option>
                <option value="TSLA">TSLA</option>
                <option value="MSFT">MSFT</option>
              </select>
            </div>

            <div>
              <div className={styles.label}>Type</div>
              <div className={styles.toggleRow} role="group" aria-label="Buy or sell">
                <button
                  type="button"
                  className={`${styles.toggle} ${side === 'buy' ? styles.toggleActive : ''}`}
                  onClick={() => setSide('buy')}
                >
                  Buy
                </button>
                <button
                  type="button"
                  className={`${styles.toggle} ${side === 'sell' ? styles.toggleActive : ''}`}
                  onClick={() => setSide('sell')}
                >
                  Sell
                </button>
              </div>
            </div>

            <div>
              <div className={styles.label}>Amount</div>
              <input
                className={styles.input}
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                inputMode="numeric"
                aria-label="Amount"
              />
            </div>

            <button type="button" className={styles.primary}>
              PLACE ORDER
            </button>
          </div>
        </section>
      </div>
    </AppShell>
  )
}
