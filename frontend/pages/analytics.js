import { useCallback, useEffect, useState } from 'react'
import { AreaChart, Area, CartesianGrid, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import AppShell from '../components/AppShell'
import InfoTip from '../components/InfoTip'
import SentimentHeatmapGrid from '../components/SentimentHeatmapGrid'
import { apiFetch } from '../lib/api'
import { formatQuantity, usdOptions } from '../lib/format'
import styles from '../styles/Analytics.module.css'

function asArray(payload) {
  if (Array.isArray(payload)) return payload
  if (Array.isArray(payload?.items)) return payload.items
  if (Array.isArray(payload?.results)) return payload.results
  if (Array.isArray(payload?.data)) return payload.data
  return []
}

function normalizeHistory(payload) {
  const normalized = asArray(payload)
    .map((row) => ({
      date: row?.date || row?.metric_date || row?.timestamp || null,
      total_value: Number(row?.total_value ?? row?.totalValue ?? 0),
      positions_value: Number(row?.positions_value ?? row?.positionsValue ?? 0),
      cash_balance: Number(row?.cash_balance ?? row?.cashBalance ?? 0),
      total_pl: Number(row?.total_pl ?? row?.totalPl ?? 0),
    }))
    .filter((row) => Boolean(row.date))

  const byDate = new Map()
  normalized.forEach((row) => {
    const key = new Date(row.date).toISOString().slice(0, 10)
    byDate.set(key, { ...row, date: key })
  })

  return Array.from(byDate.values()).sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime())
}

function normalizeSentiment(payload) {
  return asArray(payload).map((row) => ({
    symbol: String(row?.symbol || '').toUpperCase(),
    // null = no scored news for this asset (shown as "No data", not as neutral).
    avg_sentiment: row?.avg_sentiment == null ? NaN : Number(row.avg_sentiment),
    heatmap_label: row?.heatmap_label || row?.heatmapLabel || 'No data',
    positive_count: Number(row?.positive_count ?? row?.positiveCount ?? 0),
    negative_count: Number(row?.negative_count ?? row?.negativeCount ?? 0),
    neutral_count: Number(row?.neutral_count ?? row?.neutralCount ?? 0),
    total_articles: Number(row?.total_articles ?? row?.totalArticles ?? 0),
    date: row?.date || null,
    daily: Array.isArray(row?.daily) ? row.daily : [],
  })).filter((row) => Boolean(row.symbol))
}

function normalizeMetrics(payload) {
  const src = payload?.metrics || payload?.data || payload || {}
  return {
    total_value: Number(src?.total_value ?? src?.totalValue ?? 0),
    cash_balance: Number(src?.cash_balance ?? src?.cashBalance ?? 0),
    positions_value: Number(src?.positions_value ?? src?.positionsValue ?? 0),
    total_pl: Number(src?.total_pl ?? src?.totalPl ?? 0),
    unrealized_pl: Number(src?.unrealized_pl ?? src?.unrealizedPl ?? 0),
    realized_pl: Number(src?.realized_pl ?? src?.realizedPl ?? 0),
    win_rate: Number(src?.win_rate ?? src?.winRate ?? 0),
    total_trades: Number(src?.total_trades ?? src?.totalTrades ?? 0),
    closed_trades: Number(src?.closed_trades ?? src?.closedTrades ?? 0),
    winning_trades: Number(src?.winning_trades ?? src?.winningTrades ?? 0),
    daily_return: Number(src?.daily_return ?? src?.dailyReturn ?? 0),
  }
}

function normalizeTransactions(payload) {
  return asArray(payload)
}

function normalizeHoldings(payload) {
  return asArray(payload).map((row) => ({
    asset_symbol: row?.asset_symbol || row?.assetSymbol || '-',
    quantity: Number(row?.quantity ?? 0),
    avg_buy_price: Number(row?.avg_buy_price ?? row?.avgBuyPrice ?? 0),
    current_price: Number(row?.current_price ?? row?.currentPrice ?? 0),
    individual_pl: Number(row?.individual_pl ?? row?.individualPl ?? 0),
    return_pct: Number(row?.return_pct ?? row?.returnPct ?? 0),
  }))
}

function formatCurrency(value) {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return '$0.00'
  return amount.toLocaleString(undefined, usdOptions(amount))
}

function formatPercent(value) {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return '0.00%'
  return `${amount >= 0 ? '+' : ''}${amount.toFixed(2)}%`
}

function sentimentLabel(score) {
  if (!Number.isFinite(score)) return 'No Data'
  if (score >= 0.5) return 'Very Bullish'
  if (score >= 0.1) return 'Bullish'
  if (score > -0.1) return 'Neutral'
  if (score >= -0.5) return 'Bearish'
  return 'Very Bearish'
}

function sentimentClass(score) {
  if (!Number.isFinite(score)) return styles.sentimentNeutral
  if (score >= 0.1) return styles.sentimentPositive
  if (score <= -0.1) return styles.sentimentNegative
  return styles.sentimentNeutral
}

const TIPS = {
  totalValue: (
    <>
      <span><strong>Total value</strong> is your Alpaca paper account equity right now, as reported by Alpaca.</span>
      <span><code>cash + Σ (shares × current price)</code> over all positions.</span>
      <span>The line below it is just the positions part (market value).</span>
    </>
  ),
  cash: (
    <>
      <span><strong>Cash</strong> in your Alpaca paper account, straight from Alpaca.</span>
      <span>It goes down when you (or the bot) buy and up when you sell. It can go negative if margin is used.</span>
    </>
  ),
  totalPl: (
    <>
      <span><strong>Total P&amp;L = Realized + Unrealized.</strong></span>
      <span><strong>Realized</strong>: profit or loss locked in by sells, from your Alpaca fill history, using average cost: <code>(sell price − average buy price) × shares sold</code>.</span>
      <span><strong>Unrealized</strong>: gain or loss on shares you still hold, from Alpaca: <code>Σ (current price − average entry price) × shares</code>.</span>
    </>
  ),
  winRate: (
    <>
      <span><strong>Win rate = winning sells ÷ closed sells × 100.</strong></span>
      <span>A closed sell is a sell whose buy price is known in your Alpaca history; it &quot;wins&quot; if its realized P&amp;L is above $0.</span>
      <span>Buys are not counted, because a buy only opens a position.</span>
    </>
  ),
  dailyReturn: (
    <>
      <span><strong>Daily return</strong> = <code>(equity now − equity at previous close) ÷ equity at previous close × 100</code>.</span>
      <span>&quot;Equity at previous close&quot; is Alpaca&apos;s last-trading-day value, so on weekends it compares against Friday&apos;s close. Deposits or withdrawals also move it.</span>
    </>
  ),
  growth: (
    <>
      <span><strong>Portfolio growth</strong> is your account equity per trading day, from Alpaca&apos;s portfolio history for the last 30 days.</span>
      <span>Each point is the value at that day&apos;s close (today&apos;s point is live). Days before the account held any value are left out, and nothing is filled in.</span>
    </>
  ),
  holdings: (
    <>
      <span>Every position in your Alpaca paper account.</span>
      <span><strong>Avg buy price</strong>: Alpaca&apos;s average entry price across your buys.</span>
      <span><strong>Current price</strong>: Alpaca&apos;s latest price.</span>
      <span><strong>Individual P&amp;L</strong> = <code>(current − avg buy price) × quantity</code>.</span>
      <span><strong>Return %</strong> = <code>P&amp;L ÷ (avg buy price × quantity) × 100</code>.</span>
    </>
  ),
  sentiment: (
    <>
      <span>News sentiment for each watchlist asset, scored by <strong>FinBERT</strong> from −1 (very negative) to +1 (very positive).</span>
      <span><strong>Tiles</strong>: average score of the asset&apos;s latest cached news articles. The % is the score × 100. Labels: ≥ +0.5 Very Bullish, ≥ +0.1 Bullish, between −0.1 and +0.1 Neutral, ≥ −0.5 Bearish, lower Very Bearish.</span>
      <span><strong>Daily heatmap</strong>: each cell is the average score of that day&apos;s articles behind the asset&apos;s latest AI insight (up to 25 articles over 30 days). Hatched = no news that day.</span>
    </>
  ),
  transactions: (
    <>
      <span>Filled orders from your Alpaca paper account, with Alpaca&apos;s actual fill price and quantity. Orders that never filled are not shown.</span>
      <span><strong>Notional</strong> = <code>quantity × price</code>.</span>
      <span><strong>P&amp;L</strong> (sells only) = <code>(fill price − average buy price) × quantity</code>, using average cost over your fills. &quot;--&quot; for buys, or when the matching buys are not in the history.</span>
      <span><strong>Source</strong>: Bot = AJTrade trading bot · AJTrade = the Portfolio trade ticket · Alpaca = placed directly on Alpaca.</span>
    </>
  ),
}

function PanelTitle({ children, tip, align }) {
  return (
    <h3 className={styles.titleRow}>
      {children}
      <InfoTip label={String(children)} align={align}>{tip}</InfoTip>
    </h3>
  )
}

function MetricsRow({ metrics }) {
  const cards = [
    { label: 'Total Value', tip: TIPS.totalValue, value: formatCurrency(metrics?.total_value), sub: `${formatCurrency(metrics?.positions_value)} in positions` },
    { label: 'Cash Balance', tip: TIPS.cash, value: formatCurrency(metrics?.cash_balance), sub: 'Alpaca paper account cash' },
    { label: 'Total P&L', tip: TIPS.totalPl, value: formatCurrency(metrics?.total_pl), sub: `Realized ${formatCurrency(metrics?.realized_pl)} · Unrealized ${formatCurrency(metrics?.unrealized_pl)}`, cls: Number(metrics?.total_pl) >= 0 ? styles.pos : styles.neg },
    { label: 'Win Rate', tip: TIPS.winRate, align: 'end', value: `${Number(metrics?.win_rate || 0).toFixed(1)}%`, sub: `${metrics?.winning_trades || 0} / ${metrics?.closed_trades || 0} closed trades` },
    { label: 'Daily Return', tip: TIPS.dailyReturn, align: 'end', value: formatPercent(metrics?.daily_return), sub: "Since the previous trading day's close", cls: Number(metrics?.daily_return) >= 0 ? styles.pos : styles.neg },
  ]

  return (
    <section className={styles.metricsGrid}>
      {cards.map((card) => (
        <article key={card.label} className={styles.metricCard}>
          <p className={styles.titleRow}>
            {card.label}
            <InfoTip label={card.label} align={card.align}>{card.tip}</InfoTip>
          </p>
          <strong className={card.cls || ''}>{card.value}</strong>
          <span>{card.sub}</span>
        </article>
      ))}
    </section>
  )
}

function PortfolioGrowthChart({ history }) {
  const chartData = Array.isArray(history) ? history : []
  const values = chartData.map((row) => Number(row.total_value)).filter((value) => Number.isFinite(value))
  const minValue = values.length ? Math.min(...values) : 0
  const maxValue = values.length ? Math.max(...values) : 0
  const spread = Math.max(maxValue - minValue, Math.max(Math.abs(maxValue), 1) * 0.01)
  const yMin = Math.max(0, minValue - spread * 0.25)
  const yMax = maxValue + spread * 0.25

  function formatXAxis(value) {
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return String(value || '')
    return date.toLocaleDateString(undefined, { month: 'numeric', day: 'numeric' })
  }

  // Enough decimals that nearby ticks don't all read the same (e.g. $100.1k / $100.4k, not $100k x3).
  const kDecimals = yMax - yMin < 2_000 ? 2 : yMax - yMin < 20_000 ? 1 : 0

  function formatYAxis(value) {
    const amount = Number(value || 0)
    if (Math.abs(amount) >= 1_000_000) return `$${(amount / 1_000_000).toFixed(kDecimals + 1)}M`
    return `$${(amount / 1000).toFixed(kDecimals)}k`
  }

  return (
    <section className={styles.panel}>
      <div className={styles.panelHeader}>
        <PanelTitle tip={TIPS.growth}>Portfolio Growth</PanelTitle>
      </div>
      <div className={styles.chartWrap}>
        {chartData.length ? (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData} margin={{ top: 10, right: 12, left: 0, bottom: 8 }}>
              <defs>
                <linearGradient id="analyticsValue" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#10b981" stopOpacity={0.38} />
                  <stop offset="100%" stopColor="#10b981" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid vertical={false} stroke="#2f3338" strokeDasharray="3 3" />
              <XAxis
                dataKey="date"
                tick={{ fill: 'var(--aj-text-muted)', fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                tickFormatter={formatXAxis}
                minTickGap={24}
                interval="preserveStartEnd"
              />
              <YAxis
                dataKey="total_value"
                tick={{ fill: 'var(--aj-text-muted)', fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                tickFormatter={formatYAxis}
                width={72}
                domain={[yMin, yMax]}
              />
              <Tooltip formatter={(value) => formatCurrency(value)} labelFormatter={(label) => new Date(label).toLocaleDateString()} />
              <Area type="monotone" dataKey="total_value" stroke="#10b981" strokeWidth={2} fill="url(#analyticsValue)" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <p className={styles.empty}>No account history from Alpaca yet. It builds up one point per trading day.</p>
        )}
      </div>
    </section>
  )
}

function SentimentHeatmap({ sentiment }) {
  const rows = Array.isArray(sentiment) ? sentiment : []
  return (
    <section className={styles.panel}>
      <div className={styles.panelHeader}><PanelTitle tip={TIPS.sentiment}>Market Sentiment Heatmap</PanelTitle></div>
      {rows.length ? (
        <div className={styles.heatmapGrid}>
          {rows.map((item) => (
            <article key={item.symbol} className={`${styles.heatmapCell} ${sentimentClass(Number(item.avg_sentiment))}`}>
              <strong>{item.symbol}</strong>
              <p>{Number.isFinite(item.avg_sentiment) ? sentimentLabel(item.avg_sentiment) : 'No data'}</p>
              <span>{Number.isFinite(item.avg_sentiment) ? `${(item.avg_sentiment * 100).toFixed(0)}%` : 'No scored news yet'}</span>
            </article>
          ))}
        </div>
      ) : (
        <p className={styles.empty}>No sentiment data available for watchlist assets.</p>
      )}
      <SentimentHeatmapGrid rows={rows} />
    </section>
  )
}

function HoldingsPerformanceTable({ rows }) {
  return (
    <section className={styles.panel}>
      <div className={styles.panelHeader}><PanelTitle tip={TIPS.holdings}>Individual Holdings Performance</PanelTitle></div>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Asset Symbol</th>
              <th>Quantity</th>
              <th>Avg Buy Price</th>
              <th>Current Price</th>
              <th>Individual P&L ($)</th>
              <th>Return (%)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.asset_symbol}>
                <td><strong>{row.asset_symbol}</strong></td>
                <td>{formatQuantity(row.quantity)}</td>
                <td>{formatCurrency(row.avg_buy_price)}</td>
                <td>{formatCurrency(row.current_price)}</td>
                <td className={Number(row.individual_pl) >= 0 ? styles.pos : styles.neg}>{formatCurrency(row.individual_pl)}</td>
                <td className={Number(row.return_pct) >= 0 ? styles.pos : styles.neg}>{formatPercent(Number(row.return_pct))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.length ? <p className={styles.empty}>No positions in your Alpaca paper account.</p> : null}
    </section>
  )
}

const SOURCE_LABELS = { bot: 'Bot', manual: 'AJTrade', alpaca: 'Alpaca' }

function TransactionHistory({ transactions }) {
  const [filterSymbol, setFilterSymbol] = useState('')
  const symbols = [...new Set((transactions || []).map((tx) => tx.symbol))]
  const filtered = filterSymbol ? (transactions || []).filter((tx) => tx.symbol === filterSymbol) : (transactions || [])

  return (
    <section className={styles.panel}>
      <div className={styles.panelHeader}>
        <PanelTitle tip={TIPS.transactions}>Transaction History</PanelTitle>
        <select value={filterSymbol} onChange={(event) => setFilterSymbol(event.target.value)} className={styles.filterSelect}>
          <option value="">All Symbols</option>
          {symbols.map((symbol) => <option key={symbol} value={symbol}>{symbol}</option>)}
        </select>
      </div>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Date</th>
              <th>Symbol</th>
              <th>Type</th>
              <th>Qty</th>
              <th>Price</th>
              <th>Notional</th>
              <th>P&L</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 30).map((tx) => (
              <tr key={tx.id}>
                <td>{tx.created_at ? new Date(tx.created_at).toLocaleDateString() : '-'}</td>
                <td><strong>{tx.symbol}</strong></td>
                <td>{tx.trade_type}</td>
                <td>{formatQuantity(tx.quantity)}</td>
                <td>{formatCurrency(tx.price)}</td>
                <td>{formatCurrency(tx.notional)}</td>
                <td className={Number(tx.pl || 0) >= 0 ? styles.pos : styles.neg}>{tx.pl != null ? formatCurrency(tx.pl) : '--'}</td>
                <td>{SOURCE_LABELS[tx.signal_source] || tx.signal_source || '--'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export default function AnalyticsPage() {
  const [metrics, setMetrics] = useState(null)
  const [history, setHistory] = useState([])
  const [sentiment, setSentiment] = useState([])
  const [transactions, setTransactions] = useState([])
  const [holdings, setHoldings] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadData = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      // Pull the latest cash, positions and filled orders from Alpaca first so every panel is current.
      try {
        await apiFetch('/api/portfolio/sync-paper', { method: 'POST' })
      } catch {
        // Not connected or Alpaca unreachable: the Alpaca-backed panels below report it.
      }
      // Each panel loads on its own, so one failing (e.g. Alpaca not connected) does not blank the page.
      const [metricsRes, historyRes, sentimentRes, transactionRes, holdingsRes] = await Promise.allSettled([
        apiFetch('/api/analytics/portfolio/metrics'),
        apiFetch('/api/analytics/portfolio/history?days=30'),
        apiFetch('/api/analytics/sentiment-heatmap'),
        apiFetch('/api/analytics/transactions?limit=100'),
        apiFetch('/api/analytics/holdings'),
      ])
      setMetrics(metricsRes.status === 'fulfilled' ? normalizeMetrics(metricsRes.value) : null)
      setHistory(historyRes.status === 'fulfilled' ? normalizeHistory(historyRes.value) : [])
      setSentiment(sentimentRes.status === 'fulfilled' ? normalizeSentiment(sentimentRes.value) : [])
      setTransactions(transactionRes.status === 'fulfilled' ? normalizeTransactions(transactionRes.value) : [])
      setHoldings(holdingsRes.status === 'fulfilled' ? normalizeHoldings(holdingsRes.value) : [])
      const failed = [metricsRes, historyRes, sentimentRes, transactionRes, holdingsRes].find((r) => r.status === 'rejected')
      if (failed) setError(failed.reason?.message || 'Some analytics could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadData()
  }, [loadData])

  return (
    <AppShell title="Analytics" subtitle="Portfolio performance and risk monitoring">
      <div className={styles.analyticsContainer}>
        <header className={styles.headerRow}>
          <h2>Financial Analytics</h2>
          <button type="button" className={styles.refreshButton} onClick={loadData} disabled={loading}>{loading ? 'Refreshing...' : 'Refresh'}</button>
        </header>
        {error ? <p className={styles.errorText}>{error}</p> : null}
        {metrics ? <MetricsRow metrics={metrics} /> : null}
        <PortfolioGrowthChart history={history} />
        <HoldingsPerformanceTable rows={holdings} />
        <SentimentHeatmap sentiment={sentiment} />
        <TransactionHistory transactions={transactions} />
      </div>
    </AppShell>
  )
}
