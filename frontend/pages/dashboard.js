import { useEffect, useMemo, useState } from 'react'
import { AreaChart, Area, CartesianGrid, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import AppShell from '../components/AppShell'
import { apiFetch } from '../lib/api'
import styles from '../styles/Dashboard.module.css'

const TIMEFRAMES = ['1D', '5D', '1M', '6M', 'YTD', '1Y', '5Y', 'MAX']

const TIMEFRAME_TO_RANGE = {
  '1D': 'day',
  '5D': 'month',
  '1M': 'month',
  '6M': 'year',
  YTD: 'year',
  '1Y': 'year',
  '5Y': 'all',
  MAX: 'all',
}

const ASSET_LABELS = {
  BTC: 'Bitcoin',
  ETH: 'Ethereum',
  AAPL: 'Apple Inc.',
  MSFT: 'Microsoft',
  TSLA: 'Tesla',
  NVDA: 'NVIDIA',
  GOOGL: 'Alphabet',
  AMZN: 'Amazon',
  META: 'Meta',
}

function formatPrice(value) {
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function toFiniteNumber(value, fallback = null) {
  const amount = Number(value)
  return Number.isFinite(amount) ? amount : fallback
}

function formatSigned(value) {
  const n = Number(value) || 0
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}`
}

function formatMetricNumber(value) {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return '—'
  if (Math.abs(amount) >= 1_000_000_000_000) return `${(amount / 1_000_000_000_000).toFixed(2)}T`
  if (Math.abs(amount) >= 1_000_000_000) return `${(amount / 1_000_000_000).toFixed(2)}B`
  if (Math.abs(amount) >= 1_000_000) return `${(amount / 1_000_000).toFixed(2)}M`
  if (Math.abs(amount) >= 1_000) return `${(amount / 1_000).toFixed(2)}K`
  return amount.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

function formatPercentage(value) {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return '—'
  return `${amount.toFixed(2)}%`
}

function getAssetLabel(symbol) {
  return ASSET_LABELS[String(symbol || '').toUpperCase()] || String(symbol || '').toUpperCase()
}

function getDetailPoints(detail) {
  if (Array.isArray(detail?.points)) return detail.points
  if (Array.isArray(detail?.historical_data?.points)) return detail.historical_data.points
  return []
}

function getDetailQuote(detail) {
  if (detail?.quote && typeof detail.quote === 'object') return detail.quote
  if (detail?.historical_data?.quote && typeof detail.historical_data.quote === 'object') return detail.historical_data.quote
  return null
}

function sparklinePath(values, width = 88, height = 22) {
  if (!values?.length) return ''
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const step = values.length > 1 ? width / (values.length - 1) : 0

  return values
    .map((v, i) => {
      const x = i * step
      const y = height - ((v - min) / span) * height
      return `${x},${y}`
    })
    .join(' ')
}

function formatAxisTime(value, timeframe) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)

  if (timeframe === '1D') {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
  }

  if (timeframe === '5D' || timeframe === '1M') {
    return date.toLocaleDateString([], { month: 'short', day: 'numeric' })
  }

  if (timeframe === '6M' || timeframe === 'YTD' || timeframe === '1Y') {
    return date.toLocaleDateString([], { month: 'short' })
  }

  return date.toLocaleDateString([], { year: 'numeric' })
}

function formatTooltipTime(value, timeframe) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)

  if (timeframe === '1D') {
    return date.toLocaleString([], {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
  }

  return date.toLocaleDateString([], {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

function normalizeChartData(detail, timeframe) {
  const points = getDetailPoints(detail)
  if (!points.length) return []

  return points
    .filter((point) => Number.isFinite(Number(point?.close)))
    .map((point) => ({
      time: point.t,
      fullLabel: formatTooltipTime(point.t, timeframe),
      price: Number(point.close),
    }))
}

function getSparklineValues(detail) {
  const points = getDetailPoints(detail)
  if (!points.length) return []
  return points
    .map((point) => Number(point?.close))
    .filter((value) => Number.isFinite(value))
}

// Compact Y-axis formatter (2,135,452 → 2.14M, 57,860 → 57.9K)
function formatAxisPrice(value) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return value.toFixed(2)
}

// Custom recharts tooltip
function ChartTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const point = payload[0]?.payload || {}
  return (
    <div className={styles.chartTooltip}>
      <span className={styles.tooltipTime}>{point.fullLabel}</span>
      <span className={styles.tooltipPrice}>{formatPrice(payload[0].value)}</span>
    </div>
  )
}

function WatchlistSidebar({ assets, selectedTicker, onSelect, loading, error }) {
  return (
    <aside className={styles.leftSidebar} aria-label="Watchlist">
      <div className={styles.sidebarHeader}>
        <h3 className={styles.sidebarTitle}>Watchlist</h3>
      </div>
      <div className={styles.watchlistScroll}>
        {loading ? <p className={styles.assetTicker}>Loading saved assets...</p> : null}
        {!loading && error ? <p className={styles.assetTicker}>{error}</p> : null}
        {!loading && !error && !assets.length ? <p className={styles.assetTicker}>No saved assets in your watchlist.</p> : null}
        {assets.map((asset) => {
          const isPositive = asset.changePct >= 0
          const isSelected = selectedTicker === asset.ticker
          return (
            <button
              key={asset.ticker}
              type="button"
              className={`${styles.watchRow} ${isSelected ? styles.watchRowActive : ''}`}
              onClick={() => onSelect(asset.ticker)}
            >
              <div className={styles.watchIdentity}>
                <strong>{asset.ticker}</strong>
                <span>{asset.displayName}</span>
              </div>
              <svg className={styles.sparkline} viewBox="0 0 88 22" aria-hidden="true">
                <polyline
                  points={sparklinePath(asset.sparkline.length ? asset.sparkline : [0, 0, 0, 0])}
                  fill="none"
                  stroke={isPositive ? 'var(--aj-positive)' : 'var(--aj-negative)'}
                  strokeWidth="2"
                  strokeLinecap="round"
                />
              </svg>
              <div className={styles.watchNumbers}>
                <strong>{Number.isFinite(asset.price) ? formatPrice(asset.price) : '—'}</strong>
                <span className={isPositive ? styles.pos : styles.neg}>{formatSigned(asset.changePct)}%</span>
              </div>
            </button>
          )
        })}
      </div>
    </aside>
  )
}

function AssetChart({ chartData, timeframe, onTimeframeChange }) {
  return (
    <section className={styles.chartSection} aria-label="Price chart">
      <div className={styles.timeframeRow}>
        {TIMEFRAMES.map((frame) => (
          <button
            key={frame}
            type="button"
            className={`${styles.timeframeBtn} ${timeframe === frame ? styles.timeframeBtnActive : ''}`}
            onClick={() => onTimeframeChange(frame)}
          >
            {frame}
          </button>
        ))}
      </div>
      <div className={styles.chartWrapper} style={{ height: 320 }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 10, right: 8, left: 0, bottom: 18 }}>
            <defs>
              <linearGradient id="colorPrice" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#10b981" stopOpacity={0.34} />
                <stop offset="100%" stopColor="#10b981" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke="#333333" strokeDasharray="3 3" />
            <XAxis
              dataKey="time"
              tick={{ fontSize: 11, fill: 'var(--aj-text-muted)' }}
              axisLine={false}
              tickLine={false}
              minTickGap={30}
              interval="preserveStartEnd"
              tickFormatter={(value) => formatAxisTime(value, timeframe)}
              angle={-35}
              textAnchor="end"
              tickMargin={10}
              height={56}
            />
            <YAxis
              domain={['auto', 'auto']}
              tick={{ fontSize: 11, fill: 'var(--aj-text-muted)' }}
              axisLine={false}
              tickLine={false}
              width={78}
              tickFormatter={formatAxisPrice}
            />
            <Tooltip
              content={<ChartTooltip />}
              cursor={{ stroke: 'var(--aj-accent-border)', strokeWidth: 1, strokeDasharray: '4 2' }}
            />
            <Area
              type="linear"
              dataKey="price"
              stroke="#10b981"
              strokeWidth={2}
              fill="url(#colorPrice)"
              fillOpacity={1}
              dot={false}
              activeDot={{ r: 4, fill: '#10b981', stroke: 'var(--aj-bg)', strokeWidth: 2 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}

function AIAssistantSidebar({ selectedAsset }) {
  const [messages, setMessages] = useState([
    { id: 1, role: 'assistant', text: 'Hello. Ask me anything about your selected watchlist asset or the market in general.' },
  ])
  const [input, setInput] = useState('')
  const [isSending, setIsSending] = useState(false)

  const quickPrompts = [
    'How is the market doing today?',
    'Analyze my watchlist',
    `What is driving ${selectedAsset}?`,
    'Summarize top risks this week',
  ]

  async function pushMessage(content) {
    const text = String(content || '').trim()
    if (!text || isSending || !selectedAsset || selectedAsset === 'this asset') return

    const userMsg = { id: Date.now(), role: 'user', text }
    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setIsSending(true)

    try {
      const data = await apiFetch('/api/ml/v2/assistant/explain', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: selectedAsset,
          prompt: text,
          range: '1mo',
          user_preference: 'open-source',
        }),
      })
      setMessages((prev) => [...prev, {
        id: Date.now() + 1,
        role: 'assistant',
        text: data?.explanation || 'No explanation available.',
      }])
    } catch (error) {
      setMessages((prev) => [...prev, {
        id: Date.now() + 1,
        role: 'assistant',
        text: `Unable to load AI explanation: ${error.message}`,
      }])
    } finally {
      setIsSending(false)
    }
  }

  return (
    <aside className={styles.rightSidebar} aria-label="AI finance chat">
      <div className={styles.sidebarHeader}>
        <h3 className={styles.copilotTitle}>Ask the AI assistant</h3>
      </div>
      <p className={styles.copilotSubtitle}>Natural market assistant · Ollama</p>
      <div className={styles.promptGrid}>
        {quickPrompts.map((prompt) => (
          <button key={prompt} type="button" className={styles.promptBtn} onClick={() => pushMessage(prompt)} disabled={isSending || !selectedAsset || selectedAsset === 'this asset'}>
            {prompt}
          </button>
        ))}
      </div>
      <div className={styles.chatHistory}>
        {messages.map((msg) => (
          <div key={msg.id} className={`${styles.chatBubble} ${msg.role === 'user' ? styles.chatUser : styles.chatAi}`}>
            {msg.text}
          </div>
        ))}
      </div>
      <form
        className={styles.chatComposer}
        onSubmit={(e) => { e.preventDefault(); pushMessage(input) }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about this asset or anything general..."
          aria-label="Chat input"
          disabled={isSending || !selectedAsset || selectedAsset === 'this asset'}
        />
        <button type="submit" disabled={isSending || !selectedAsset || selectedAsset === 'this asset'}>{isSending ? '...' : 'Send'}</button>
      </form>
    </aside>
  )
}

export default function Dashboard() {
  const [selectedTicker, setSelectedTicker] = useState('')
  const [timeframe, setTimeframe] = useState('1D')
  const [utcNow, setUtcNow] = useState(new Date())
  const [isWatchlistOpen, setIsWatchlistOpen] = useState(true)
  const [isAssistantOpen, setIsAssistantOpen] = useState(true)
  const [watchlistItems, setWatchlistItems] = useState([])
  const [watchlistError, setWatchlistError] = useState('')
  const [watchlistLoading, setWatchlistLoading] = useState(false)
  const [assetSnapshots, setAssetSnapshots] = useState({})
  const [selectedDetail, setSelectedDetail] = useState(null)
  const [selectedDetailLoading, setSelectedDetailLoading] = useState(false)

  useEffect(() => {
    const timer = setInterval(() => setUtcNow(new Date()), 1000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    let cancelled = false

    async function refreshWatchlist() {
      setWatchlistLoading(true)
      setWatchlistError('')
      try {
        const data = await apiFetch('/api/watchlist')
        if (cancelled) return
        const items = Array.isArray(data) ? data : []
        setWatchlistItems(items)
        setSelectedTicker((current) => {
          if (current && items.some((item) => item.symbol === current)) return current
          return items[0]?.symbol || ''
        })
      } catch (error) {
        if (cancelled) return
        setWatchlistItems([])
        setSelectedTicker('')
        setWatchlistError(error.message || 'Unable to load watchlist.')
      } finally {
        if (!cancelled) setWatchlistLoading(false)
      }
    }

    refreshWatchlist()
    const handleFocus = () => refreshWatchlist()
    window.addEventListener('focus', handleFocus)

    return () => {
      cancelled = true
      window.removeEventListener('focus', handleFocus)
    }
  }, [])

  useEffect(() => {
    let cancelled = false

    async function loadSidebarSnapshots() {
      if (!watchlistItems.length) {
        setAssetSnapshots({})
        return
      }

      const results = await Promise.all(
        watchlistItems.map(async (item) => {
          try {
            const detail = await apiFetch(`/api/market/chart/${encodeURIComponent(item.symbol)}?range=month`)
            return [item.symbol, detail]
          } catch {
            return [item.symbol, null]
          }
        }),
      )

      if (cancelled) return
      setAssetSnapshots(
        results.reduce((acc, [symbol, detail]) => {
          acc[symbol] = detail
          return acc
        }, {}),
      )
    }

    loadSidebarSnapshots()

    return () => {
      cancelled = true
    }
  }, [watchlistItems])

  useEffect(() => {
    let cancelled = false

    async function loadSelectedDetail() {
      if (!selectedTicker) {
        setSelectedDetail(null)
        return
      }

      setSelectedDetailLoading(true)
      try {
        const range = TIMEFRAME_TO_RANGE[timeframe] || 'month'
        const detail = await apiFetch(`/api/market/chart/${encodeURIComponent(selectedTicker)}?range=${range}`)
        if (cancelled) return
        setSelectedDetail(detail)
        setAssetSnapshots((current) => ({
          ...current,
          [selectedTicker]: range === 'month' ? detail : (current[selectedTicker] || detail),
        }))
      } catch {
        if (cancelled) return
        setSelectedDetail(null)
      } finally {
        if (!cancelled) setSelectedDetailLoading(false)
      }
    }

    loadSelectedDetail()

    return () => {
      cancelled = true
    }
  }, [selectedTicker, timeframe])

  const watchlistAssets = useMemo(
    () => watchlistItems.map((item) => {
      const snapshot = assetSnapshots[item.symbol]
      const snapshotQuote = getDetailQuote(snapshot)
      const sparkline = getSparklineValues(snapshot)
      return {
        id: item.id,
        ticker: item.symbol,
        displayName: getAssetLabel(item.symbol),
        price: toFiniteNumber(snapshotQuote?.price),
        changePct: toFiniteNumber(snapshotQuote?.change_percent, 0),
        sparkline,
        notes: item.notes,
      }
    }),
    [assetSnapshots, watchlistItems],
  )

  const selectedAsset = useMemo(
    () => watchlistAssets.find((item) => item.ticker === selectedTicker) || watchlistAssets[0] || null,
    [selectedTicker, watchlistAssets],
  )

  const chartData = useMemo(
    () => normalizeChartData(selectedDetail, timeframe),
    [selectedDetail, timeframe],
  )

  const selectedQuote = getDetailQuote(selectedDetail)
  const livePrice = toFiniteNumber(selectedQuote?.price, 0)
  const first = chartData[0]?.price ?? livePrice
  const last = chartData[chartData.length - 1]?.price ?? livePrice
  const absChange = last - first
  const pctChange = first ? (absChange / first) * 100 : 0

  const metricCards = useMemo(() => {
    const points = getDetailPoints(selectedDetail)
    const volumes = points.map((point) => Number(point?.volume)).filter((value) => Number.isFinite(value))
    const highs = points.map((point) => Number(point?.high)).filter((value) => Number.isFinite(value))
    const lows = points.map((point) => Number(point?.low)).filter((value) => Number.isFinite(value))
    return [
      { label: 'Market Cap', value: selectedQuote?.market_cap ? formatMetricNumber(selectedQuote.market_cap) : '—' },
      { label: 'Volume', value: volumes.length ? formatMetricNumber(volumes[volumes.length - 1]) : '—' },
      { label: 'P/E Ratio', value: '—' },
      { label: '52W High', value: highs.length ? formatPrice(Math.max(...highs)) : '—' },
      { label: '52W Low', value: lows.length ? formatPrice(Math.min(...lows)) : '—' },
      { label: 'Sentiment', value: '—' },
    ]
  }, [selectedDetail, selectedQuote])

  const relatedAssets = useMemo(
    () => watchlistAssets.filter((asset) => asset.ticker !== selectedTicker).slice(0, 4),
    [selectedTicker, watchlistAssets],
  )

  return (
    <AppShell title="Trade Dashboard" subtitle="Market deep dive and AI assistant">
      <div className={styles.layoutFlex}>

        {/* Left sidebar — hidden completely when collapsed */}
        {isWatchlistOpen && (
          <WatchlistSidebar
            assets={watchlistAssets}
            selectedTicker={selectedTicker}
            onSelect={setSelectedTicker}
            loading={watchlistLoading}
            error={watchlistError}
          />
        )}

        {/* Left edge tab — always visible slim chevron toggle */}
        <button
          type="button"
          className={styles.edgeTab}
          onClick={() => setIsWatchlistOpen((p) => !p)}
          aria-label={isWatchlistOpen ? 'Collapse watchlist' : 'Expand watchlist'}
          title={isWatchlistOpen ? 'Collapse watchlist' : 'Expand watchlist'}
        >
          {isWatchlistOpen ? '\u2039' : '\u203a'}
        </button>

        {/* Center column */}
        <main className={styles.centerColumn} aria-label="Asset deep dive">
          <header className={styles.assetHeader}>
            <div className={styles.assetMeta}>
              <p className={styles.assetPath}>Home / Markets / {selectedAsset?.ticker || 'Watchlist'} / THB</p>
              <h2 className={styles.assetName}>{selectedAsset?.displayName || 'No asset selected'}</h2>
              <p className={styles.assetTicker}>{selectedAsset?.ticker ? `${selectedAsset.ticker} / THB` : 'Add an asset from the Watchlist page'}</p>
            </div>
            <div className={styles.priceBlock}>
              <strong className={styles.currentPrice}>{Number.isFinite(last) ? formatPrice(last) : '—'}</strong>
              <p className={pctChange >= 0 ? styles.pos : styles.neg}>
                {formatSigned(absChange)} ({formatSigned(pctChange)}%) Today
              </p>
              <span className={styles.priceTimestamp}>{utcNow.toUTCString()}</span>
            </div>
          </header>

          {selectedDetailLoading && !chartData.length ? (
            <section className={styles.chartSection} aria-label="Price chart loading">
              <p className={styles.assetTicker}>Loading asset data...</p>
            </section>
          ) : (
            <AssetChart chartData={chartData} timeframe={timeframe} onTimeframeChange={setTimeframe} />
          )}

          <section className={styles.metricsGrid} aria-label="Overview metrics">
            {metricCards.map((metric) => (
              <div key={metric.label} className={styles.metricCard}><span>{metric.label}</span><strong>{metric.value}</strong></div>
            ))}
          </section>

          <section className={styles.relatedSection} aria-label="Related assets">
            <h3>Related Assets</h3>
            <div className={styles.relatedGrid}>
              {relatedAssets.map((asset) => (
                <article key={asset.ticker} className={styles.relatedCard}>
                  <strong>{asset.ticker}</strong>
                  <span>{Number.isFinite(asset.price) ? formatPrice(asset.price) : '—'}</span>
                  <span className={asset.changePct >= 0 ? styles.pos : styles.neg}>{formatSigned(asset.changePct)}%</span>
                </article>
              ))}
            </div>
          </section>
        </main>

        {/* Right edge tab — always visible */}
        <button
          type="button"
          className={styles.edgeTab}
          onClick={() => setIsAssistantOpen((p) => !p)}
          aria-label={isAssistantOpen ? 'Collapse AI assistant' : 'Expand AI assistant'}
          title={isAssistantOpen ? 'Collapse AI assistant' : 'Expand AI assistant'}
        >
          {isAssistantOpen ? '\u203a' : '\u2039'}
        </button>

        {/* Right sidebar — hidden completely when collapsed */}
        {isAssistantOpen && (
          <AIAssistantSidebar selectedAsset={selectedAsset?.ticker || 'this asset'} />
        )}

      </div>
    </AppShell>
  )
}
