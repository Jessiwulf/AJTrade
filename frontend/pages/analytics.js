import { useCallback, useEffect, useState } from 'react'
import { AreaChart, Area, CartesianGrid, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import AppShell from '../components/AppShell'
import InfoTip from '../components/InfoTip'
import SentimentHeatmapGrid from '../components/SentimentHeatmapGrid'
import { apiFetch } from '../lib/api'
import { useBroker } from '../lib/broker'
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
    // 'alpaca' when the asset gets 30 days of saved Alpaca news; null for Thai stocks, FX, gold...
    news_coverage: row?.news_coverage || null,
  })).filter((row) => Boolean(row.symbol))
}

function optionalNumber(value) {
  if (value == null || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

function normalizeWinStats(src) {
  return {
    month: src?.month || null,
    win_rate: optionalNumber(src?.win_rate),
    closed_trades: Number(src?.closed_trades ?? 0),
    winning_trades: Number(src?.winning_trades ?? 0),
    losing_trades: Number(src?.losing_trades ?? 0),
    realized_pl: Number(src?.realized_pl ?? 0),
  }
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
    // null = nothing sold yet (shown as "--", not 0%).
    win_rate: optionalNumber(src?.win_rate ?? src?.winRate),
    total_trades: Number(src?.total_trades ?? src?.totalTrades ?? 0),
    closed_trades: Number(src?.closed_trades ?? src?.closedTrades ?? 0),
    winning_trades: Number(src?.winning_trades ?? src?.winningTrades ?? 0),
    losing_trades: Number(src?.losing_trades ?? 0),
    this_month: normalizeWinStats(src?.this_month),
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
    is_open: row?.is_open !== false,
    ...normalizeWinStats(row),
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

function formatWinRate(value) {
  return value == null ? '--' : `${Number(value).toFixed(1)}%`
}

function winRateClass(value) {
  if (value == null) return ''
  return value >= 50 ? styles.pos : styles.neg
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

// Explanations for each panel, worded for where the numbers come from (Alpaca or Settrade).
function buildTips(provider) {
  const settrade = provider === 'settrade'
  const broker = settrade ? 'Settrade' : 'Alpaca'
  const account = settrade ? 'Settrade account' : 'Alpaca paper account'
  const zero = settrade ? '฿0' : '$0'
  return {
    totalValue: (
      <>
        <span><strong>Total value</strong> is your {account}&apos;s value right now{settrade ? ': its cash plus the market value of its positions, from Settrade.' : ', as reported by Alpaca.'}</span>
        <span><code>cash + Σ (shares × current price)</code> over all positions.</span>
        <span>The line below it is just the positions part (market value).</span>
      </>
    ),
    cash: (
      <>
        <span><strong>Cash</strong> in your {account}, straight from {broker}.</span>
        <span>It goes down when you (or the bot) buy and up when you sell.{settrade ? '' : ' It can go negative if margin is used.'}</span>
      </>
    ),
    totalPl: (
      <>
        <span><strong>Total P&amp;L = Realized + Unrealized.</strong></span>
        <span><strong>Realized</strong>: profit or loss locked in by sells, from {settrade ? 'the Settrade trades AJTrade has recorded (Settrade lists only today’s trades, so this history starts when you connect)' : 'your Alpaca fill history'}, using average cost: <code>(sell price − average buy price) × shares sold</code>.</span>
        <span><strong>Unrealized</strong>: gain or loss on shares you still hold, from {broker}: <code>Σ (current price − average entry price) × shares</code>.</span>
      </>
    ),
    winRate: (
      <>
        <span><strong>Win rate = winning sells ÷ closed sells × 100.</strong></span>
        <span>A closed sell is a sell whose buy price is known in your recorded {broker} history; it &quot;wins&quot; if its realized P&amp;L is above {zero}. Selling at exactly the buy price counts as closed but not as a win.</span>
        <span>Buys are not counted, because a buy only opens a position. Many buys followed by one sell make one closed trade.</span>
        <span>The big number is all time. <strong>This month</strong> counts only the sells closed in the current calendar month (UTC).</span>
        <span>&quot;--&quot; means nothing has been sold yet.</span>
      </>
    ),
    dailyReturn: (
      <>
        <span><strong>Daily return</strong> = <code>(equity now − equity at previous close) ÷ equity at previous close × 100</code>.</span>
        {settrade ? (
          <span>&quot;Equity at previous close&quot; is the account value AJTrade recorded on the last earlier day it loaded your Settrade account (Settrade has no account-history feed). Deposits or withdrawals also move it.</span>
        ) : (
          <span>&quot;Equity at previous close&quot; is Alpaca&apos;s last-trading-day value, so on weekends it compares against Friday&apos;s close. Deposits or withdrawals also move it.</span>
        )}
      </>
    ),
    growth: (
      <>
        {settrade ? (
          <span><strong>Portfolio growth</strong> is your Settrade account value per day, from the daily snapshots AJTrade saves whenever it loads your account. It builds up from the day you connect; nothing is filled in.</span>
        ) : (
          <span><strong>Portfolio growth</strong> is your account equity per trading day, from Alpaca&apos;s portfolio history for the last 30 days.</span>
        )}
        <span>Each point is the latest value of that day (today&apos;s point is live). Days without data are left out.</span>
      </>
    ),
    holdings: (
      <>
        <span>Every position in your {account}.</span>
        <span><strong>Avg buy price</strong>: {broker}&apos;s average {settrade ? 'cost' : 'entry price'} across your buys.</span>
        <span><strong>Current price</strong>: {broker}&apos;s latest price.</span>
        <span><strong>Unrealized P&amp;L</strong>: gain or loss on what you still hold, if you sold it now: <code>(current − avg buy price) × quantity</code>. It changes with the price.</span>
        <span><strong>Return %</strong> = <code>Unrealized P&amp;L ÷ (avg buy price × quantity) × 100</code>.</span>
        <span><strong>Realized P&amp;L</strong>: profit or loss already locked in by your sells of this asset (all time), the same amounts as the P&amp;L column of Transaction History. So the two P&amp;L columns differ: after a partial sell, Realized is what that sell made or lost, Unrealized is what the rest is doing now.</span>
        <span><strong>Win rate</strong>: winning sells ÷ closed sells of this asset. &quot;--&quot; = not sold yet. Assets you have fully sold are listed at the bottom as &quot;Closed&quot;.</span>
      </>
    ),
    sentiment: (
      <>
        <span>News sentiment for each {settrade ? 'Thai SET' : 'US / crypto'} watchlist asset, scored by <strong>FinBERT</strong> from −1 (very negative) to +1 (very positive).</span>
        <span><strong>Tiles</strong>: average score of the asset&apos;s latest cached news articles. The % is the score × 100. Labels: ≥ +0.5 Very Bullish, ≥ +0.1 Bullish, between −0.1 and +0.1 Neutral, ≥ −0.5 Bearish, lower Very Bearish.</span>
        <span><strong>Daily heatmap</strong>: each cell is the average score of that day&apos;s articles. US stocks, ETFs and crypto get up to 10 Benzinga articles a day from <strong>Alpaca News</strong>, saved for the last 30 days, so past days fill in even if the app wasn&apos;t running. The articles behind the asset&apos;s latest AI insight are added on top. Days are UTC. Hatched = no news that day.</span>
      </>
    ),
    transactions: (
      <>
        <span>Filled orders from your {account}, with {broker}&apos;s actual fill price and quantity. Orders that never filled are not shown.{settrade ? ' Settrade lists only today’s trades, so earlier days appear once AJTrade has recorded them.' : ''}</span>
        <span><strong>Notional</strong> = <code>quantity × price</code>.</span>
        <span><strong>Realized P&amp;L</strong> (sells only) = <code>(fill price − average buy price) × quantity</code>, using average cost over your fills. &quot;--&quot; for buys, or when the matching buys are not in the history. This is locked-in profit or loss, so it differs from the Unrealized P&amp;L of what you still hold.</span>
        <span><strong>Source</strong>: Bot = AJTrade trading bot · AJTrade = the Portfolio trade ticket · {broker} = placed directly at {broker}.</span>
      </>
    ),
  }
}

// Set by AnalyticsPage on each render for the active provider.
let TIPS = buildTips('alpaca')
let ACCOUNT_NAME = 'Alpaca paper account'

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
    { label: 'Cash Balance', tip: TIPS.cash, value: formatCurrency(metrics?.cash_balance), sub: `${ACCOUNT_NAME} cash` },
    { label: 'Total P&L', tip: TIPS.totalPl, value: formatCurrency(metrics?.total_pl), sub: `Realized ${formatCurrency(metrics?.realized_pl)} · Unrealized ${formatCurrency(metrics?.unrealized_pl)}`, cls: Number(metrics?.total_pl) >= 0 ? styles.pos : styles.neg },
    {
      label: 'Win Rate',
      tip: TIPS.winRate,
      align: 'end',
      value: formatWinRate(metrics?.win_rate),
      sub: `${metrics?.winning_trades || 0} / ${metrics?.closed_trades || 0} closed trades · This month: ${formatWinRate(metrics?.this_month?.win_rate)}`,
    },
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
          <p className={styles.empty}>No account history yet. It builds up one point per trading day.</p>
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
              <th>Unrealized P&L</th>
              <th>Return (%)</th>
              <th>Realized P&L</th>
              <th>Win Rate</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.asset_symbol} className={row.is_open ? '' : styles.closedRow}>
                <td>
                  <strong>{row.asset_symbol}</strong>
                  {row.is_open ? null : <span className={styles.closedTag}>Closed</span>}
                </td>
                <td>{row.is_open ? formatQuantity(row.quantity) : '--'}</td>
                <td>{row.is_open ? formatCurrency(row.avg_buy_price) : '--'}</td>
                <td>{row.is_open ? formatCurrency(row.current_price) : '--'}</td>
                <td className={row.is_open ? (Number(row.individual_pl) >= 0 ? styles.pos : styles.neg) : ''}>{row.is_open ? formatCurrency(row.individual_pl) : '--'}</td>
                <td className={row.is_open ? (Number(row.return_pct) >= 0 ? styles.pos : styles.neg) : ''}>{row.is_open ? formatPercent(Number(row.return_pct)) : '--'}</td>
                <td className={row.closed_trades ? (row.realized_pl >= 0 ? styles.pos : styles.neg) : ''}>
                  {row.closed_trades ? formatCurrency(row.realized_pl) : '--'}
                </td>
                <td>
                  <span className={styles.winCell}>
                    <strong className={winRateClass(row.win_rate)}>{formatWinRate(row.win_rate)}</strong>
                    <small>{row.closed_trades ? `${row.winning_trades}/${row.closed_trades} won` : 'Not sold yet'}</small>
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.length ? <p className={styles.empty}>No positions in your {ACCOUNT_NAME}.</p> : null}
    </section>
  )
}

const SOURCE_LABELS = { bot: 'Bot', manual: 'AJTrade', alpaca: 'Alpaca', settrade: 'Settrade' }

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
              <th>Realized P&L</th>
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
  // Every panel shows the member's active broker only (Alpaca or Settrade), never both.
  const { provider, loaded: brokerLoaded } = useBroker()
  TIPS = buildTips(provider)
  ACCOUNT_NAME = provider === 'settrade' ? 'Settrade account' : 'Alpaca paper account'
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
      // Pull the latest cash, positions and filled orders from the broker first so every panel is current.
      try {
        await apiFetch('/api/portfolio/sync-paper', { method: 'POST' })
      } catch {
        // Not connected or the broker is unreachable: the broker-backed panels below report it.
      }
      // Each panel loads on its own, so one failing (e.g. broker not connected) does not blank the page.
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
    if (brokerLoaded) loadData()
  }, [brokerLoaded, provider, loadData])

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
