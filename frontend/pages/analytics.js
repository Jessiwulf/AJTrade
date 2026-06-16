/**
 * Feature #5: Performance Analytics & Market Dashboard
 * Comprehensive dashboard showing:
 * - Portfolio performance charts (growth, P/L)
 * - Market sentiment heatmap
 * - Transaction history
 * - Asset details (Google Finance style)
 */

import { useEffect, useState, useCallback } from 'react'
import AppShell from '../components/AppShell'
import { apiFetch } from '../lib/api'
import {
  LineChart,
  Line,
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
  Cell
} from 'recharts'
import styles from '../styles/Analytics.module.css'

// ========== Utility Functions ==========

function formatCurrency(value) {
  if (!value) return '$0.00'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD'
  }).format(value)
}

function formatPercent(value) {
  if (value === null || value === undefined) return '0.00%'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

function getSentimentColor(sentiment) {
  if (!sentiment) return '#888888'
  if (sentiment >= 0.5) return '#00aa00'  // Very Bullish - green
  if (sentiment >= 0.1) return '#00dd00'  // Bullish - light green
  if (sentiment > -0.1) return '#888888'  // Neutral - gray
  if (sentiment >= -0.5) return '#dd3300'  // Bearish - orange
  return '#aa0000'  // Very Bearish - red
}

function getSentimentLabel(sentiment) {
  if (!sentiment && sentiment !== 0) return 'No Data'
  if (sentiment >= 0.5) return 'Very Bullish'
  if (sentiment >= 0.1) return 'Bullish'
  if (sentiment > -0.1) return 'Neutral'
  if (sentiment >= -0.5) return 'Bearish'
  return 'Very Bearish'
}

// ========== Components ==========

function MetricCard({ label, value, subtext, color = '#00aa00', trend = null }) {
  return (
    <div className={styles.metricCard}>
      <div className={styles.metricLabel}>{label}</div>
      <div className={styles.metricValue} style={{ color }}>
        {value}
      </div>
      {subtext && <div className={styles.metricSubtext}>{subtext}</div>}
      {trend !== null && (
        <div className={styles.metricTrend} style={{ color: trend >= 0 ? '#00aa00' : '#aa0000' }}>
          {trend >= 0 ? '↑' : '↓'} {Math.abs(trend).toFixed(2)}%
        </div>
      )}
    </div>
  )
}

function PortfolioMetricsPanel({ metrics, loading, error }) {
  if (loading) return <div className={styles.panel}><p>Loading metrics...</p></div>
  if (error) return <div className={styles.panel}><p style={{ color: 'red' }}>Error: {error}</p></div>
  if (!metrics) return <div className={styles.panel}><p>No data available</p></div>

  return (
    <div className={styles.metricsGrid}>
      <MetricCard
        label="Portfolio Value"
        value={formatCurrency(metrics.total_value)}
        subtext={`${formatCurrency(metrics.positions_value)} positions + ${formatCurrency(metrics.cash_balance)} cash`}
        color="#00aa00"
      />
      <MetricCard
        label="Total P/L"
        value={formatCurrency(metrics.total_pl)}
        subtext={`Realized: ${formatCurrency(metrics.realized_pl)}`}
        color={metrics.total_pl >= 0 ? '#00aa00' : '#aa0000'}
        trend={metrics.daily_return}
      />
      <MetricCard
        label="Win Rate"
        value={`${metrics.win_rate.toFixed(1)}%`}
        subtext={`${metrics.winning_trades} / ${metrics.total_trades} trades`}
        color="#0088dd"
      />
      <MetricCard
        label="Daily Return"
        value={formatPercent(metrics.daily_return)}
        subtext="Today's performance"
        color={metrics.daily_return >= 0 ? '#00aa00' : '#aa0000'}
      />
    </div>
  )
}

function PortfolioPerformanceChart({ history, loading, error }) {
  if (loading) return <div className={styles.chartContainer}><p>Loading chart...</p></div>
  if (error) return <div className={styles.chartContainer}><p style={{ color: 'red' }}>Error: {error}</p></div>
  if (!history || history.length === 0) return <div className={styles.chartContainer}><p>No historical data</p></div>

  return (
    <div className={styles.chartContainer}>
      <h3>Portfolio Growth</h3>
      <ResponsiveContainer width="100%" height={300}>
        <AreaChart data={history}>
          <defs>
            <linearGradient id="colorTotal" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#00aa00" stopOpacity={0.8} />
              <stop offset="95%" stopColor="#00aa00" stopOpacity={0.1} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#333" />
          <XAxis
            dataKey="date"
            stroke="#888"
            tick={{ fontSize: 12 }}
            tickFormatter={(val) => new Date(val).toLocaleDateString()}
          />
          <YAxis stroke="#888" tick={{ fontSize: 12 }} tickFormatter={(val) => `$${val / 1000}k`} />
          <Tooltip
            formatter={(value) => formatCurrency(value)}
            labelFormatter={(label) => new Date(label).toLocaleDateString()}
            contentStyle={{ backgroundColor: '#1a1a1a', border: '1px solid #444' }}
          />
          <Legend />
          <Area
            type="monotone"
            dataKey="total_value"
            stroke="#00aa00"
            fillOpacity={1}
            fill="url(#colorTotal)"
            name="Total Value"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

function SentimentHeatmap({ sentiment, loading, error }) {
  if (loading) return <div className={styles.chartContainer}><p>Loading sentiment...</p></div>
  if (error) return <div className={styles.chartContainer}><p style={{ color: 'red' }}>Error: {error}</p></div>
  if (!sentiment || sentiment.length === 0) return <div className={styles.chartContainer}><p>No sentiment data</p></div>

  return (
    <div className={styles.chartContainer}>
      <h3>Market Sentiment Heatmap</h3>
      <div className={styles.heatmapGrid}>
        {sentiment.map((item) => (
          <div
            key={item.symbol}
            className={styles.heatmapCell}
            style={{
              backgroundColor: getSentimentColor(item.avg_sentiment),
              borderColor: getSentimentColor(item.avg_sentiment)
            }}
            title={`${item.symbol}: ${getSentimentLabel(item.avg_sentiment)} (${item.total_articles} articles)`}
          >
            <div className={styles.heatmapSymbol}>{item.symbol}</div>
            <div className={styles.heatmapValue}>
              {item.avg_sentiment !== null ? (item.avg_sentiment * 100).toFixed(0) : '--'}%
            </div>
            <div className={styles.heatmapLabel}>{getSentimentLabel(item.avg_sentiment)}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function TransactionHistory({ transactions, loading, error, onRefresh }) {
  const [filterSymbol, setFilterSymbol] = useState('')

  const filtered = filterSymbol ? transactions.filter((t) => t.symbol === filterSymbol) : transactions

  if (loading) return <div className={styles.chartContainer}><p>Loading transactions...</p></div>
  if (error) return <div className={styles.chartContainer}><p style={{ color: 'red' }}>Error: {error}</p></div>
  if (!transactions || transactions.length === 0) {
    return <div className={styles.chartContainer}><p>No transactions yet</p></div>
  }

  const symbols = [...new Set(transactions.map((t) => t.symbol))]

  return (
    <div className={styles.chartContainer}>
      <div className={styles.transactionHeader}>
        <h3>Transaction History</h3>
        <select
          value={filterSymbol}
          onChange={(e) => setFilterSymbol(e.target.value)}
          className={styles.filterSelect}
        >
          <option value="">All Symbols</option>
          {symbols.map((sym) => (
            <option key={sym} value={sym}>
              {sym}
            </option>
          ))}
        </select>
      </div>
      <div className={styles.transactionTable}>
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Symbol</th>
              <th>Type</th>
              <th>Qty</th>
              <th>Price</th>
              <th>Notional</th>
              <th>P/L</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 20).map((tx) => (
              <tr key={tx.id} className={styles[`type_${tx.trade_type.toLowerCase()}`]}>
                <td>{new Date(tx.created_at).toLocaleDateString()}</td>
                <td className={styles.symbolCell}>{tx.symbol}</td>
                <td>
                  <span
                    className={styles.badge}
                    style={{ backgroundColor: tx.trade_type === 'BUY' ? '#0088dd' : '#dd8800' }}
                  >
                    {tx.trade_type}
                  </span>
                </td>
                <td>{tx.quantity.toFixed(2)}</td>
                <td>{formatCurrency(tx.price)}</td>
                <td>{formatCurrency(tx.notional)}</td>
                <td style={{ color: (tx.pl || 0) >= 0 ? '#00aa00' : '#aa0000' }}>
                  {tx.pl !== null ? formatCurrency(tx.pl) : '--'}
                </td>
                <td>{tx.signal_source}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function AssetDetail({ symbol, onClose }) {
  const [asset, setAsset] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    const loadAsset = async () => {
      try {
        setLoading(true)
        const data = await apiFetch(`/api/analytics/asset/${symbol}?range_=1mo`)
        setAsset(data)
        setError(null)
      } catch (err) {
        setError(err.message)
      } finally {
        setLoading(false)
      }
    }
    loadAsset()
  }, [symbol])

  if (loading)
    return (
      <div className={styles.assetDetailModal}>
        <div className={styles.assetDetailContent}>
          <button onClick={onClose} className={styles.closeButton}>
            ✕
          </button>
          <p>Loading asset details...</p>
        </div>
      </div>
    )

  if (error)
    return (
      <div className={styles.assetDetailModal}>
        <div className={styles.assetDetailContent}>
          <button onClick={onClose} className={styles.closeButton}>
            ✕
          </button>
          <p style={{ color: 'red' }}>Error: {error}</p>
        </div>
      </div>
    )

  if (!asset) return null

  const sentiment = asset.sentiment

  return (
    <div className={styles.assetDetailModal} onClick={onClose}>
      <div className={styles.assetDetailContent} onClick={(e) => e.stopPropagation()}>
        <button onClick={onClose} className={styles.closeButton}>
          ✕
        </button>
        <div className={styles.assetHeader}>
          <h2>{asset.symbol}</h2>
          <div style={{ display: 'flex', gap: '20px', alignItems: 'center' }}>
            <div>
              <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#00aa00' }}>
                {formatCurrency(asset.price)}
              </div>
              <div
                style={{
                  color: asset.price_change_pct >= 0 ? '#00aa00' : '#aa0000',
                  fontSize: '14px'
                }}
              >
                {asset.price_change >= 0 ? '+' : ''} {formatCurrency(asset.price_change)} ({formatPercent(asset.price_change_pct)})
              </div>
            </div>
            {sentiment && (
              <div>
                <div style={{ fontSize: '14px', color: '#888' }}>Market Sentiment</div>
                <div
                  style={{
                    fontSize: '18px',
                    fontWeight: 'bold',
                    color: getSentimentColor(sentiment.avg_sentiment),
                    padding: '4px 8px',
                    borderRadius: '4px',
                    backgroundColor: '#222'
                  }}
                >
                  {getSentimentLabel(sentiment.avg_sentiment)}
                </div>
                <div style={{ fontSize: '12px', color: '#888', marginTop: '4px' }}>
                  {sentiment.total_articles} articles analyzed
                </div>
              </div>
            )}
          </div>
        </div>

        {sentiment && (
          <div className={styles.sentimentDetail}>
            <h4>Sentiment Breakdown</h4>
            <div style={{ display: 'flex', gap: '20px', marginTop: '10px' }}>
              <div>
                <div style={{ color: '#00aa00' }}>Positive: {sentiment.positive_count}</div>
                <div style={{ color: '#888888' }}>Neutral: {sentiment.neutral_count}</div>
                <div style={{ color: '#aa0000' }}>Negative: {sentiment.negative_count}</div>
              </div>
              <div>
                <div>Sentiment Score: {(sentiment.avg_sentiment * 100).toFixed(1)}%</div>
              </div>
            </div>
          </div>
        )}

        {asset.historical_data && asset.historical_data.length > 0 && (
          <div className={styles.chartContainer} style={{ marginTop: '20px' }}>
            <h4>Price Chart (1 Month)</h4>
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={asset.historical_data}>
                <CartesianGrid strokeDasharray="3 3" stroke="#333" />
                <XAxis stroke="#888" tick={{ fontSize: 10 }} />
                <YAxis stroke="#888" tick={{ fontSize: 10 }} />
                <Tooltip
                  formatter={(value) => formatCurrency(value)}
                  contentStyle={{ backgroundColor: '#1a1a1a', border: '1px solid #444' }}
                />
                <Line
                  type="monotone"
                  dataKey="close"
                  stroke="#00aa00"
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  )
}

// ========== Main Dashboard Page ==========

export default function AnalyticsDashboard() {
  const [metrics, setMetrics] = useState(null)
  const [metricsLoading, setMetricsLoading] = useState(true)
  const [metricsError, setMetricsError] = useState(null)

  const [history, setHistory] = useState([])
  const [historyLoading, setHistoryLoading] = useState(true)
  const [historyError, setHistoryError] = useState(null)

  const [sentiment, setSentiment] = useState([])
  const [sentimentLoading, setSentimentLoading] = useState(true)
  const [sentimentError, setSentimentError] = useState(null)

  const [transactions, setTransactions] = useState([])
  const [transactionsLoading, setTransactionsLoading] = useState(true)
  const [transactionsError, setTransactionsError] = useState(null)

  const [selectedAsset, setSelectedAsset] = useState(null)

  const loadData = useCallback(async () => {
    try {
      setMetricsLoading(true)
      const metricsData = await apiFetch('/api/analytics/portfolio/metrics')
      setMetrics(metricsData)
      setMetricsError(null)
    } catch (err) {
      setMetricsError(err.message)
    } finally {
      setMetricsLoading(false)
    }

    try {
      setHistoryLoading(true)
      const historyData = await apiFetch('/api/analytics/portfolio/history?days=30')
      setHistory(Array.isArray(historyData) ? historyData : [])
      setHistoryError(null)
    } catch (err) {
      setHistoryError(err.message)
    } finally {
      setHistoryLoading(false)
    }

    try {
      setSentimentLoading(true)
      const sentimentData = await apiFetch('/api/analytics/sentiment-heatmap')
      setSentiment(Array.isArray(sentimentData) ? sentimentData : [])
      setSentimentError(null)
    } catch (err) {
      setSentimentError(err.message)
    } finally {
      setSentimentLoading(false)
    }

    try {
      setTransactionsLoading(true)
      const txData = await apiFetch('/api/analytics/transactions?limit=100')
      setTransactions(Array.isArray(txData) ? txData : [])
      setTransactionsError(null)
    } catch (err) {
      setTransactionsError(err.message)
    } finally {
      setTransactionsLoading(false)
    }
  }, [])

  useEffect(() => {
    loadData()
  }, [loadData])

  return (
    <AppShell>
      <div className={styles.analyticsContainer}>
        <div className={styles.header}>
          <h1>Performance Analytics & Dashboard</h1>
          <button onClick={loadData} className={styles.refreshButton}>
            🔄 Refresh
          </button>
        </div>

        <PortfolioMetricsPanel metrics={metrics} loading={metricsLoading} error={metricsError} />

        <PortfolioPerformanceChart history={history} loading={historyLoading} error={historyError} />

        <SentimentHeatmap sentiment={sentiment} loading={sentimentLoading} error={sentimentError} />

        <TransactionHistory
          transactions={transactions}
          loading={transactionsLoading}
          error={transactionsError}
          onRefresh={loadData}
        />

        {selectedAsset && <AssetDetail symbol={selectedAsset} onClose={() => setSelectedAsset(null)} />}
      </div>
    </AppShell>
  )
}
