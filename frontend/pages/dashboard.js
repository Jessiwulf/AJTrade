import { useEffect, useMemo, useState } from 'react'
import useSWR from 'swr'
import { AreaChart, Area, CartesianGrid, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts'
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

const ALLOCATION_COLORS = ['#10b981', '#3b82f6', '#f59e0b', '#ef4444', '#8b5cf6', '#22c55e', '#ec4899', '#14b8a6']

const WATCHLIST_SORT_MODES = {
  DEFAULT: 'default',
  ALPHABETICAL: 'alphabetical',
  PERFORMANCE: 'performance',
}

function getWatchlistSortLabel(mode) {
  switch (mode) {
    case WATCHLIST_SORT_MODES.ALPHABETICAL:
      return 'Alphabetical (A-Z)'
    case WATCHLIST_SORT_MODES.PERFORMANCE:
      return 'Performance (High to Low)'
    default:
      return 'Default'
  }
}

function compareWatchlistAssets(a, b, sortMode) {
  if (sortMode === WATCHLIST_SORT_MODES.ALPHABETICAL) {
    return String(a.ticker || '').localeCompare(String(b.ticker || ''))
  }

  if (sortMode === WATCHLIST_SORT_MODES.PERFORMANCE) {
    const performanceDelta = (Number(b.changePct) || 0) - (Number(a.changePct) || 0)
    if (performanceDelta !== 0) return performanceDelta
    return String(a.ticker || '').localeCompare(String(b.ticker || ''))
  }

  return 0
}

function sortWatchlistAssets(items, sortMode) {
  const clonedItems = [...items]
  if (sortMode === WATCHLIST_SORT_MODES.DEFAULT) return clonedItems
  return clonedItems.sort((left, right) => compareWatchlistAssets(left, right, sortMode))
}

function buildWatchlistSections(assets, sortMode, isGrouped, bookmarkedSymbols) {
  const bookmarkedSet = new Set((bookmarkedSymbols || []).map((symbol) => String(symbol || '').toUpperCase()))
  const bookmarkedAssets = assets.filter((asset) => bookmarkedSet.has(asset.ticker))
  const remainingAssets = assets.filter((asset) => !bookmarkedSet.has(asset.ticker))

  const sections = []

  if (bookmarkedAssets.length) {
    sections.push({
      key: 'bookmarked',
      label: 'Bookmarked',
      items: bookmarkedAssets,
    })
  }

  if (isGrouped) {
    const orderedRemaining = sortWatchlistAssets(remainingAssets, sortMode)
    const gaining = orderedRemaining.filter((asset) => asset.changePct > 0)
    const neutral = orderedRemaining.filter((asset) => asset.changePct === 0)
    const losing = orderedRemaining.filter((asset) => asset.changePct < 0)

    if (gaining.length) {
      sections.push({ key: 'gaining', label: 'Gaining', items: gaining })
    }
    if (neutral.length) {
      sections.push({ key: 'neutral', label: 'Neutral', items: neutral })
    }
    if (losing.length) {
      sections.push({ key: 'losing', label: 'Losing', items: losing })
    }
    return sections
  }

  sections.push({
    key: 'watchlist',
    label: 'Watchlist',
    items: sortWatchlistAssets(remainingAssets, sortMode),
  })

  return sections
}

function toListPayload(payload) {
  if (Array.isArray(payload)) return payload
  if (Array.isArray(payload?.items)) return payload.items
  if (Array.isArray(payload?.results)) return payload.results
  if (Array.isArray(payload?.data)) return payload.data
  return []
}

function normalizeWatchlist(payload) {
  return toListPayload(payload)
    .map((item) => ({
      ...item,
      symbol: String(item?.symbol || '').toUpperCase(),
      notes: item?.notes || '',
    }))
    .filter((item) => Boolean(item.symbol))
}

function normalizeKeyStatistics(payload) {
  const src = payload?.stats || payload?.data || payload?.quote || payload || {}
  return {
    currency: src?.currency || null,
    latest_price: toFiniteNumber(src?.latest_price ?? src?.latestPrice ?? src?.price ?? src?.currentPrice),
    previous_close: toFiniteNumber(src?.previous_close ?? src?.previousClose),
    open: toFiniteNumber(src?.open),
    day_low: toFiniteNumber(src?.day_low ?? src?.dayLow),
    day_high: toFiniteNumber(src?.day_high ?? src?.dayHigh),
    week_52_low: toFiniteNumber(src?.week_52_low ?? src?.week52Low),
    week_52_high: toFiniteNumber(src?.week_52_high ?? src?.week52High),
    volume: toFiniteNumber(src?.volume ?? src?.regularMarketVolume),
    market_cap: parseCompactNumber(src?.market_cap ?? src?.marketCap ?? src?.market_capitalization),
    revenue: parseCompactNumber(src?.revenue),
    net_income: parseCompactNumber(src?.net_income ?? src?.netIncome),
    eps: parseCompactNumber(src?.eps),
    pe_ratio: parseCompactNumber(src?.pe_ratio ?? src?.peRatio),
    beta: parseCompactNumber(src?.beta),
  }
}

function parseCompactNumber(value) {
  if (value == null) return null
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  const text = String(value).trim().toUpperCase().replace(/,/g, '')
  if (!text) return null
  let normalized = text
  let multiplier = 1
  if (normalized.endsWith('%')) normalized = normalized.slice(0, -1)
  const suffix = normalized.slice(-1)
  if (suffix === 'K') {
    multiplier = 1_000
    normalized = normalized.slice(0, -1)
  } else if (suffix === 'M') {
    multiplier = 1_000_000
    normalized = normalized.slice(0, -1)
  } else if (suffix === 'B') {
    multiplier = 1_000_000_000
    normalized = normalized.slice(0, -1)
  } else if (suffix === 'T') {
    multiplier = 1_000_000_000_000
    normalized = normalized.slice(0, -1)
  }
  const n = Number(normalized)
  return Number.isFinite(n) ? n * multiplier : null
}

function toMeaningfulFundamental(value) {
  if (!Number.isFinite(value)) return null
  if (value === 0) return null
  return value
}

function formatPrice(value) {
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

// Live rates come from /api/market/fx as units of each currency per 1 USD.
// Until they load (or if a rate is unavailable) only same-currency amounts are shown.
const DEFAULT_FX_RATES = { USD: 1 }
const DISPLAY_CURRENCIES = ['USD', 'THB']

// Yahoo quotes some exchanges in minor units (e.g. London in pence as "GBp").
const MINOR_UNIT_CURRENCIES = { GBp: 'GBP', GBX: 'GBP', ZAc: 'ZAR', ILA: 'ILS' }

function normalizeCurrencyCode(code, fallback = 'USD') {
  const text = String(code || '').trim()
  return MINOR_UNIT_CURRENCIES[text] || text.toUpperCase() || fallback
}

function minorUnitDivisor(code) {
  return MINOR_UNIT_CURRENCIES[String(code || '').trim()] ? 100 : 1
}

function canConvert(from, to, rates = DEFAULT_FX_RATES) {
  return from === to || (Number(rates?.[from]) > 0 && Number(rates?.[to]) > 0)
}

// Currency a value is actually shown in: the display currency when we have a rate,
// otherwise the asset's native currency (never relabel an unconverted number).
function resolveDisplayCurrency(to, from = 'USD', rates = DEFAULT_FX_RATES) {
  const source = normalizeCurrencyCode(from)
  const target = normalizeCurrencyCode(to)
  return canConvert(source, target, rates) ? target : source
}

// Converts an amount quoted in `from` into `to`. Unsupported pairs are returned unchanged.
function convertCurrency(value, to, from = 'USD', rates = DEFAULT_FX_RATES) {
  // Number(null) and Number('') are 0; treat missing values as missing, not zero.
  if (value == null || value === '') return null
  const amount = Number(value) / minorUnitDivisor(from)
  if (!Number.isFinite(amount)) return null
  const source = normalizeCurrencyCode(from)
  const target = normalizeCurrencyCode(to)
  if (!canConvert(source, target, rates) || source === target) return amount
  return (amount / Number(rates[source])) * Number(rates[target])
}

function formatCurrencyValue(value, currency, { from = 'USD', rates = DEFAULT_FX_RATES } = {}) {
  const converted = convertCurrency(value, currency, from, rates)
  if (!Number.isFinite(converted)) return '—'
  const displayCurrency = resolveDisplayCurrency(currency, from, rates)
  try {
    return converted.toLocaleString(undefined, {
      style: 'currency',
      currency: displayCurrency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
  } catch {
    // Non-ISO codes from the data feed (e.g. "GBp") would make Intl throw.
    return `${converted.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${displayCurrency}`
  }
}

const DAY_MS = 24 * 60 * 60 * 1000
// How old the latest FX observation may be for a price point (FX data is daily/monthly per range).
const FX_HISTORY_MAX_AGE_MS = { day: DAY_MS, month: 7 * DAY_MS, year: 7 * DAY_MS, all: 45 * DAY_MS }
// A price may borrow the first FX observation only if it is at most this much later (bar-alignment slack).
const FX_HISTORY_MAX_LEAD_MS = DAY_MS

function formatFxRate(value) {
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 4 })
}

// Builds time -> { multiplier, label } using the USD-based FX rate in effect at that time.
// Returns null when the needed history is missing; a point outside FX coverage maps to null.
function buildHistoricalConverter({ series, from, to, range }) {
  const source = normalizeCurrencyCode(from)
  const target = normalizeCurrencyCode(to)
  const divisor = minorUnitDivisor(from)
  if (source === target) return () => ({ multiplier: 1 / divisor, label: null })

  const observations = {}
  for (const code of [source, target]) {
    if (code === 'USD') continue
    const points = (Array.isArray(series?.[code]) ? series[code] : [])
      .map((point) => [Date.parse(point.t), Number(point.rate)])
      .filter(([time, rate]) => Number.isFinite(time) && rate > 0)
      .sort((a, b) => a[0] - b[0])
    if (!points.length) return null
    observations[code] = points
  }

  const maxAge = FX_HISTORY_MAX_AGE_MS[range] ?? 7 * DAY_MS
  const rateAt = (code, time) => {
    if (code === 'USD') return 1
    const obs = observations[code]
    let lo = 0
    let hi = obs.length - 1
    let idx = -1
    while (lo <= hi) {
      const mid = (lo + hi) >> 1
      if (obs[mid][0] <= time) {
        idx = mid
        lo = mid + 1
      } else {
        hi = mid - 1
      }
    }
    if (idx === -1) return obs[0][0] - time <= FX_HISTORY_MAX_LEAD_MS ? obs[0][1] : null
    return time - obs[idx][0] <= maxAge ? obs[idx][1] : null
  }

  const latestRate = (code) => (code === 'USD' ? 1 : observations[code][observations[code].length - 1][1])

  // `isLatest`: the chart's final bar carries the live price, so it takes the latest FX tick
  // (Yahoo stamps the in-progress FX bar with its live tick time, which sorts after the bar start).
  return (timeValue, { isLatest = false } = {}) => {
    const time = Date.parse(timeValue)
    if (!Number.isFinite(time)) return null
    const sourcePerUsd = isLatest ? latestRate(source) : rateAt(source, time)
    const targetPerUsd = isLatest ? latestRate(target) : rateAt(target, time)
    if (sourcePerUsd == null || targetPerUsd == null) return null
    let label
    if (source === 'USD') label = `1 USD = ${formatFxRate(targetPerUsd)} ${target}`
    else if (target === 'USD') label = `1 USD = ${formatFxRate(sourcePerUsd)} ${source}`
    else label = `1 ${source} = ${formatFxRate(targetPerUsd / sourcePerUsd)} ${target}`
    return { multiplier: targetPerUsd / sourcePerUsd / divisor, label }
  }
}

function toFiniteNumber(value, fallback = null) {
  if (value == null || value === '') return fallback
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

function formatLargeCurrency(value, currency, { from = 'USD', rates = DEFAULT_FX_RATES } = {}) {
  const converted = convertCurrency(value, currency, from, rates)
  if (!Number.isFinite(converted)) return '—'
  const displayCurrency = resolveDisplayCurrency(currency, from, rates)
  if (Math.abs(converted) >= 1_000_000_000_000) return `${(converted / 1_000_000_000_000).toFixed(2)}T ${displayCurrency}`
  if (Math.abs(converted) >= 1_000_000_000) return `${(converted / 1_000_000_000).toFixed(2)}B ${displayCurrency}`
  if (Math.abs(converted) >= 1_000_000) return `${(converted / 1_000_000).toFixed(2)}M ${displayCurrency}`
  return formatCurrencyValue(value, currency, { from, rates })
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

function getAssetPrice(detail) {
  const quote = getDetailQuote(detail)
  const fromQuote = toFiniteNumber(quote?.price)
  if (Number.isFinite(fromQuote)) return fromQuote
  const points = getDetailPoints(detail)
  const closes = points.map((point) => Number(point?.close)).filter((value) => Number.isFinite(value))
  return closes.length ? closes[closes.length - 1] : null
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
function ChartTooltip({ active, payload, currency }) {
  if (!active || !payload?.length) return null
  const point = payload[0]?.payload || {}
  return (
    <div className={styles.chartTooltip}>
      <span className={styles.tooltipTime}>{point.fullLabel}</span>
      <span className={styles.tooltipPrice}>{formatCurrencyValue(payload[0].value, currency, { from: currency })}</span>
      {point.fxLabel ? <span className={styles.tooltipFx}>{point.fxLabel}</span> : null}
    </div>
  )
}

function describeFxRate(fx, currency) {
  if (currency === 'USD') return null
  const detail = fx?.details?.[currency]
  if (!detail) {
    return fx?.loaded
      ? { text: `Live USD/${currency} rate unavailable. Amounts shown in their original currency.`, warn: true }
      : { text: 'Loading live exchange rate...', warn: false }
  }
  const time = detail.as_of
    ? new Date(detail.as_of).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    : 'unknown time'
  const rate = Number(detail.rate).toLocaleString(undefined, { maximumFractionDigits: 4 })
  return {
    text: `1 USD = ${rate} ${currency} · ${detail.source} · ${time}${detail.stale ? ' (stale)' : ''}`,
    warn: Boolean(detail.stale),
  }
}

function PortfolioOverviewCard({ summary, currency, onCurrencyChange, fx }) {
  const fxNote = describeFxRate(fx, currency)
  const hasData = Boolean(summary.allocation.length)

  return (
    <section className={styles.portfolioCard} aria-label="Portfolio overview">
      <div className={styles.portfolioHeader}>
        <h3 className={styles.portfolioTitle}>Portfolio Overview</h3>
        <select
          className={styles.currencySelect}
          value={currency}
          onChange={(event) => onCurrencyChange(event.target.value)}
          aria-label="Currency toggle"
        >
          {DISPLAY_CURRENCIES.map((code) => (
            <option key={code} value={code}>{code}</option>
          ))}
        </select>
      </div>
      {fxNote ? <p className={`${styles.fxNote} ${fxNote.warn ? styles.fxNoteWarn : ''}`}>{fxNote.text}</p> : null}
      <div className={styles.portfolioMetrics}>
        <p><span>Total Balance</span><strong>{formatCurrencyValue(summary.totalBalance, currency, { rates: fx?.rates })}</strong></p>
        <p><span>Total Value</span><strong>{formatCurrencyValue(summary.totalValue, currency, { rates: fx?.rates })}</strong></p>
        <p><span>Performance (%)</span><strong className={summary.performancePct >= 0 ? styles.pos : styles.neg}>{formatSigned(summary.performancePct)}%</strong></p>
      </div>
      <div className={styles.allocationArea}>
        {hasData ? (
          <>
            <div className={styles.allocationChartWrap}>
              <PieChart width={190} height={170}>
                <Pie
                  data={summary.allocation}
                  cx={94}
                  cy={84}
                  innerRadius={42}
                  outerRadius={66}
                  paddingAngle={2}
                  dataKey="value"
                  nameKey="symbol"
                >
                  {summary.allocation.map((entry, index) => (
                    <Cell key={`allocation-${entry.symbol}`} fill={ALLOCATION_COLORS[index % ALLOCATION_COLORS.length]} />
                  ))}
                </Pie>
              </PieChart>
            </div>
            <div className={styles.allocationLegend}>
              {summary.allocation.slice(0, 6).map((entry, index) => (
                <div key={entry.symbol} className={styles.legendRow}>
                  <span className={styles.legendDot} style={{ background: ALLOCATION_COLORS[index % ALLOCATION_COLORS.length] }} aria-hidden="true" />
                  <span>{entry.symbol}</span>
                  <strong>{entry.weight.toFixed(1)}%</strong>
                </div>
              ))}
            </div>
          </>
        ) : (
          <p className={styles.assetTicker}>No positions available for allocation yet.</p>
        )}
      </div>
    </section>
  )
}

function WatchlistSidebar({
  assets,
  selectedTicker,
  onSelect,
  loading,
  error,
  portfolioSummary,
  currency,
  fx,
  onCurrencyChange,
  sortMode,
  isGrouped,
  bookmarkedSymbols,
  onToggleSort,
  onToggleGroup,
  onToggleBookmark,
}) {
  const sections = useMemo(
    () => buildWatchlistSections(assets, sortMode, isGrouped, bookmarkedSymbols),
    [assets, sortMode, isGrouped, bookmarkedSymbols],
  )

  const bookmarkTarget = selectedTicker || assets[0]?.ticker || ''

  function renderWatchRow(asset) {
    const isPositive = asset.changePct >= 0
    const isSelected = selectedTicker === asset.ticker
    const isBookmarked = bookmarkedSymbols.includes(asset.ticker)

    return (
      <button
        key={asset.ticker}
        type="button"
        className={`${styles.watchRow} ${isSelected ? styles.watchRowActive : ''}`}
        onClick={() => onSelect(asset.ticker)}
      >
        <div className={styles.watchIdentity}>
          <strong>{asset.ticker}{isBookmarked ? ' ★' : ''}</strong>
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
          <strong>{formatCurrencyValue(asset.price, currency, { from: asset.currency, rates: fx?.rates })}</strong>
          <span className={isPositive ? styles.pos : styles.neg}>{formatSigned(asset.changePct)}%</span>
        </div>
      </button>
    )
  }

  return (
    <aside className={styles.leftSidebar} aria-label="Watchlist">
      <PortfolioOverviewCard summary={portfolioSummary} currency={currency} onCurrencyChange={onCurrencyChange} fx={fx} />
      <div className={styles.sidebarHeader}>
        <h3 className={styles.sidebarTitle}>Watchlist</h3>
      </div>
      <div className={styles.watchlistActionBar}>
        <WatchlistActionButton
          label="Sort"
          onClick={onToggleSort}
          active={sortMode !== WATCHLIST_SORT_MODES.DEFAULT}
          title={`Sort: ${getWatchlistSortLabel(sortMode)}`}
        >
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M2.5 11.5 11 3a1.4 1.4 0 0 1 2 2l-8.5 8.5-2.5.5.5-2.5Z" />
            <path d="M9.5 4.5 11.5 6.5" />
          </svg>
        </WatchlistActionButton>
        <WatchlistActionButton
          label="Group"
          onClick={onToggleGroup}
          active={isGrouped}
          title={isGrouped ? 'Grouping: Enabled' : 'Grouping: Disabled'}
        >
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect x="2.5" y="3" width="4" height="4" rx="1" />
            <rect x="9.5" y="3" width="4" height="4" rx="1" />
            <rect x="6" y="9" width="4" height="4" rx="1" />
          </svg>
        </WatchlistActionButton>
        <WatchlistActionButton
          label="Bookmark"
          onClick={() => onToggleBookmark(bookmarkTarget)}
          active={Boolean(bookmarkTarget && bookmarkedSymbols.includes(bookmarkTarget))}
          title={bookmarkTarget ? `Toggle bookmark for ${bookmarkTarget}` : 'Select a watchlist asset first'}
        >
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 2.5h8v11l-4-2.5-4 2.5v-11Z" />
          </svg>
        </WatchlistActionButton>
      </div>
      <div className={styles.watchlistScroll}>
        {loading ? <p className={styles.assetTicker}>Loading saved assets...</p> : null}
        {!loading && error ? <p className={styles.assetTicker}>{error}</p> : null}
        {!loading && !error && !assets.length ? <p className={styles.assetTicker}>No saved assets in your watchlist.</p> : null}
        {sections.map((section) => (
          <section key={section.key} className={styles.watchlistSection}>
            <div className={styles.watchlistSectionHeader}>
              <span>{section.label}</span>
              <span>{section.items.length}</span>
            </div>
            <div className={styles.watchlistSectionItems}>
              {section.items.map((asset) => renderWatchRow(asset))}
            </div>
          </section>
        ))}
      </div>
    </aside>
  )
}

function AssetChart({ chartData, timeframe, onTimeframeChange, currency, fxNote }) {
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
              content={<ChartTooltip currency={currency} />}
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
      {fxNote ? <p className={`${styles.chartFxNote} ${fxNote.warn ? styles.fxNoteWarn : ''}`}>{fxNote.text}</p> : null}
    </section>
  )
}

function formatPublishedTime(value) {
  if (!value) return 'Unknown time'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString()
}

function getArticleThumbnail(article) {
  const thumbnail = String(
    article?.thumbnail || article?.image || article?.image_url || article?.urlToImage || '',
  ).trim()
  if (!thumbnail || /^no image$/i.test(thumbnail)) return ''
  return thumbnail
}

function getArticleDescription(article) {
  const description = String(article?.description || article?.summary || article?.snippet || '').trim()
  return description || 'No summary available for this article yet.'
}

function WatchlistActionButton({ label, onClick, children, active = false, title }) {
  return (
    <button
      type="button"
      className={styles.watchlistActionBtn}
      onClick={onClick}
      aria-pressed={active}
      title={title || label}
      style={active ? { borderColor: 'var(--aj-accent-border)', background: 'var(--aj-accent-soft)', color: 'var(--aj-indigo)' } : undefined}
    >
      <span className={styles.actionIcon} aria-hidden="true">{children}</span>
      <span>{label}</span>
    </button>
  )
}

function NewsThumbnail({ article }) {
  const [imageError, setImageError] = useState(false)
  const [imageLoaded, setImageLoaded] = useState(false)
  const thumbnail = getArticleThumbnail(article)
  const showImage = Boolean(thumbnail) && !imageError

  return (
    <div className={styles.newsMedia}>
      {showImage ? (
        <img
          src={thumbnail}
          alt={article.title || 'news thumbnail'}
          className={styles.newsThumb}
          loading="lazy"
          style={{ visibility: imageLoaded ? 'visible' : 'hidden' }}
          onLoad={() => setImageLoaded(true)}
          onError={() => setImageError(true)}
        />
      ) : (
        <div className={styles.newsThumbFallback}>
          <span className={styles.newsPlaceholderMark}>AJ</span>
          <span className={styles.newsPlaceholderText}>Market News</span>
        </div>
      )}
    </div>
  )
}

function NewsAnalysisSection({ articles, loading, error }) {
  return (
    <section className={styles.newsSection} aria-label="News and analysis">
      <h3>News & Analysis</h3>
      {loading ? <p className={styles.assetTicker}>Loading latest news...</p> : null}
      {!loading && error ? <p className={styles.assetTicker}>{error}</p> : null}
      {!loading && !error && !articles.length ? <p className={styles.assetTicker}>No recent articles found for this symbol.</p> : null}
      {!loading && !error && articles.length ? (
        <div className={styles.newsGrid}>
          {articles.map((article, index) => (
            <a
              key={`${article.title}-${index}`}
              href={article.url || '#'}
              target="_blank"
              rel="noreferrer"
              className={styles.newsCard}
            >
              <NewsThumbnail article={article} />
              <div className={styles.newsBody}>
                <div className={styles.newsMeta}>
                  <span>{article.source || 'Unknown source'}</span>
                  <span>{formatPublishedTime(article.published_at)}</span>
                </div>
                <strong className={styles.newsTitle}>{article.title || 'Untitled article'}</strong>
                <p className={styles.newsDescription}>{getArticleDescription(article)}</p>
              </div>
            </a>
          ))}
        </div>
      ) : null}
    </section>
  )
}

const LIST_ITEM_RE = /^\s*(?:[-*•]|\d+[.)])\s+/

function normalizeChatText(raw) {
  return String(raw || '')
    .replace(/\r\n?/g, '\n')
    // Small models sometimes inline list items: "...forces. - **Metrics**: ..." -> new line per item.
    .replace(/([.!?:])[ \t]+[-•][ \t]+(?=\S)/g, '$1\n- ')
    .replace(/[ \t]+-[ \t]+(?=\*\*)/g, '\n- ')
    // Headings like "### Summary" read better as bold lines in a chat bubble.
    .replace(/^\s*#{1,6}\s+(.+)$/gm, '**$1**')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function renderInline(text, keyPrefix) {
  return text.split(/(\*\*[^*]+?\*\*)/g).filter(Boolean).map((part, index) => {
    const bold = part.match(/^\*\*([^*]+?)\*\*$/)
    if (bold) return <strong key={`${keyPrefix}-${index}`}>{bold[1]}</strong>
    // Drop stray emphasis markers such as "****6.8****" or a lone "*".
    return part.replace(/\*+/g, '')
  })
}

function ChatMessageText({ text }) {
  const lines = normalizeChatText(text).split('\n')
  const blocks = []
  let paragraph = []
  let list = null

  const flushParagraph = () => {
    if (paragraph.length) blocks.push({ type: 'p', lines: paragraph })
    paragraph = []
  }
  const flushList = () => {
    if (list) blocks.push(list)
    list = null
  }

  lines.forEach((line) => {
    if (!line.trim()) {
      flushParagraph()
      flushList()
      return
    }
    if (LIST_ITEM_RE.test(line)) {
      flushParagraph()
      const ordered = /^\s*\d/.test(line)
      if (!list || list.ordered !== ordered) {
        flushList()
        list = { type: 'list', ordered, items: [] }
      }
      list.items.push(line.replace(LIST_ITEM_RE, ''))
      return
    }
    if (list && /^\s{2,}\S/.test(line)) {
      // Indented continuation of the previous list item.
      list.items[list.items.length - 1] += ` ${line.trim()}`
      return
    }
    flushList()
    paragraph.push(line.trim())
  })
  flushParagraph()
  flushList()

  return (
    <div className={styles.chatText}>
      {blocks.map((block, blockIndex) => {
        if (block.type === 'list') {
          const ListTag = block.ordered ? 'ol' : 'ul'
          return (
            <ListTag key={blockIndex}>
              {block.items.map((item, itemIndex) => (
                <li key={itemIndex}>{renderInline(item, `${blockIndex}-${itemIndex}`)}</li>
              ))}
            </ListTag>
          )
        }
        return (
          <p key={blockIndex}>
            {block.lines.map((line, lineIndex) => (
              <span key={lineIndex}>
                {lineIndex > 0 ? <br /> : null}
                {renderInline(line, `${blockIndex}-${lineIndex}`)}
              </span>
            ))}
          </p>
        )
      })}
    </div>
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
            {msg.role === 'user' ? msg.text : <ChatMessageText text={msg.text} />}
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
          placeholder="Ask about this asset..."
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
  const [currency, setCurrency] = useState('USD')
  const [utcNow, setUtcNow] = useState(null)
  const [isWatchlistOpen, setIsWatchlistOpen] = useState(true)
  const [isAssistantOpen, setIsAssistantOpen] = useState(true)
  const [watchlistSortMode, setWatchlistSortMode] = useState(WATCHLIST_SORT_MODES.DEFAULT)
  const [isWatchlistGrouped, setIsWatchlistGrouped] = useState(false)
  const [bookmarkedSymbols, setBookmarkedSymbols] = useState([])

  function cycleWatchlistSortMode() {
    setWatchlistSortMode((currentMode) => {
      if (currentMode === WATCHLIST_SORT_MODES.DEFAULT) return WATCHLIST_SORT_MODES.ALPHABETICAL
      if (currentMode === WATCHLIST_SORT_MODES.ALPHABETICAL) return WATCHLIST_SORT_MODES.PERFORMANCE
      return WATCHLIST_SORT_MODES.DEFAULT
    })
  }

  function toggleWatchlistGrouping() {
    setIsWatchlistGrouped((currentValue) => !currentValue)
  }

  function toggleBookmark(symbol) {
    const normalizedSymbol = String(symbol || '').toUpperCase().trim()
    if (!normalizedSymbol) return

    setBookmarkedSymbols((currentBookmarks) => {
      if (currentBookmarks.includes(normalizedSymbol)) {
        return currentBookmarks.filter((bookmarkedSymbol) => bookmarkedSymbol !== normalizedSymbol)
      }

      return [normalizedSymbol, ...currentBookmarks]
    })
  }

  const swrOptions = useMemo(() => ({
    dedupingInterval: 5 * 60 * 1000,
    revalidateOnFocus: false,
    revalidateOnReconnect: false,
    revalidateIfStale: false,
    shouldRetryOnError: false,
    keepPreviousData: true,
  }), [])

  const { data: watchlistData, error: watchlistErr, isLoading: watchlistIsLoading } = useSWR(
    '/api/watchlist',
    (url) => apiFetch(url),
    swrOptions,
  )
  const watchlistItems = useMemo(() => normalizeWatchlist(watchlistData), [watchlistData])

  const { data: portfolioData } = useSWR(
    'dashboard-portfolio-state',
    async () => {
      // The Alpaca paper account is the source of truth; the local copy is only a fallback when Alpaca
      // cannot be reached (it is refreshed from Alpaca on every sync).
      try {
        const paperPortfolio = await apiFetch('/api/portfolio/paper-account')
        return {
          source: 'paper',
          cashBalance: toFiniteNumber(paperPortfolio?.cash, 0),
          totalValue: toFiniteNumber(paperPortfolio?.portfolio_value, null),
          positions: Array.isArray(paperPortfolio?.positions) ? paperPortfolio.positions.map((position) => ({
            symbol: String(position.symbol || '').toUpperCase(),
            quantity: toFiniteNumber(position.quantity, 0),
            avgPrice: toFiniteNumber(position.avg_entry_price, 0),
            marketValue: toFiniteNumber(position.market_value, null),
            unrealizedPl: toFiniteNumber(position.unrealized_pl, null),
          })) : [],
        }
      } catch {
        try {
          const localPortfolio = await apiFetch('/api/portfolio')
          return {
            source: 'local',
            cashBalance: toFiniteNumber(localPortfolio?.cash_balance, 0),
            totalValue: null,
            positions: Array.isArray(localPortfolio?.positions) ? localPortfolio.positions.map((position) => ({
              symbol: String(position.symbol || '').toUpperCase(),
              quantity: toFiniteNumber(position.quantity, 0),
              avgPrice: toFiniteNumber(position.avg_price, 0),
              marketValue: null,
              unrealizedPl: null,
            })) : [],
          }
        } catch {
          return { source: 'none', cashBalance: 0, totalValue: 0, positions: [] }
        }
      }
    },
    swrOptions,
  )
  const portfolioState = portfolioData || { source: 'none', cashBalance: 0, totalValue: 0, positions: [] }

  const portfolioSymbols = useMemo(
    () => [...new Set((portfolioState?.positions || []).map((position) => position.symbol).filter(Boolean))],
    [portfolioState],
  )

  const { data: portfolioQuotesData } = useSWR(
    portfolioSymbols.length ? `/api/market/quotes?symbols=${encodeURIComponent(portfolioSymbols.join(','))}` : null,
    (url) => apiFetch(url),
    swrOptions,
  )

  const portfolioQuotes = useMemo(
    () => (Array.isArray(portfolioQuotesData) ? portfolioQuotesData : []).reduce((acc, row) => {
      const symbol = String(row.symbol || '').toUpperCase()
      if (!symbol) return acc
      acc[symbol] = { price: toFiniteNumber(row.price) }
      return acc
    }, {}),
    [portfolioQuotesData],
  )

  const watchlistSymbols = useMemo(
    () => [...new Set(watchlistItems.map((item) => String(item.symbol || '').toUpperCase()).filter(Boolean))],
    [watchlistItems],
  )

  const { data: snapshotData } = useSWR(
    watchlistSymbols.length ? `dashboard-snapshots-${watchlistSymbols.join(',')}` : null,
    async () => {
      const results = await Promise.all(
        watchlistSymbols.map(async (symbol) => {
          try {
            const detail = await apiFetch(`/api/market/chart/${encodeURIComponent(symbol)}?range=month`)
            return [symbol, detail]
          } catch {
            return [symbol, null]
          }
        }),
      )
      return results.reduce((acc, [symbol, detail]) => {
        acc[symbol] = detail
        return acc
      }, {})
    },
    swrOptions,
  )
  const assetSnapshots = snapshotData || {}

  const currentRange = TIMEFRAME_TO_RANGE[timeframe] || 'month'
  const { data: selectedDetail, isLoading: selectedDetailIsLoading } = useSWR(
    selectedTicker ? `/api/market/chart/${encodeURIComponent(selectedTicker)}?range=${currentRange}` : null,
    (url) => apiFetch(url),
    swrOptions,
  )

  const { data: keyStatistics, isLoading: keyStatisticsIsLoading } = useSWR(
    selectedTicker ? `/api/market/stats/${encodeURIComponent(selectedTicker)}` : null,
    async (url) => normalizeKeyStatistics(await apiFetch(url)),
    {
      ...swrOptions,
      dedupingInterval: 30 * 1000,
      revalidateOnFocus: true,
      revalidateIfStale: true,
      refreshInterval: 45 * 1000,
    },
  )

  const { data: assetNewsData, error: assetNewsErr, isLoading: assetNewsIsLoading } = useSWR(
    selectedTicker ? `/api/ml/v2/watchlist/news/${encodeURIComponent(selectedTicker)}?days=7&page_size=6&page=1` : null,
    (url) => apiFetch(url),
    swrOptions,
  )

  const assetNews = useMemo(
    () => (Array.isArray(assetNewsData?.articles) ? assetNewsData.articles.slice(0, 6) : []),
    [assetNewsData],
  )

  const watchlistLoading = watchlistIsLoading && !watchlistItems.length
  const watchlistError = watchlistErr?.message || ''
  const selectedDetailLoading = selectedDetailIsLoading && !selectedDetail
  const keyStatisticsLoading = keyStatisticsIsLoading && !keyStatistics
  const assetNewsLoading = assetNewsIsLoading && !assetNews.length
  const assetNewsError = assetNewsErr?.message || ''

  useEffect(() => {
    setUtcNow(new Date())
    const timer = setInterval(() => setUtcNow(new Date()), 1000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    setSelectedTicker((current) => {
      const normalizedCurrent = String(current || '').toUpperCase()
      if (normalizedCurrent && watchlistItems.some((item) => item.symbol === normalizedCurrent)) return normalizedCurrent
      return watchlistItems[0]?.symbol || ''
    })
  }, [watchlistItems])

  const effectiveSnapshots = useMemo(() => {
    if (!(selectedTicker && currentRange === 'month' && selectedDetail)) return assetSnapshots
    return {
      ...assetSnapshots,
      [selectedTicker]: selectedDetail,
    }
  }, [assetSnapshots, selectedDetail, selectedTicker, currentRange])

  const watchlistAssets = useMemo(
    () => watchlistItems.map((item) => {
      const snapshot = effectiveSnapshots[item.symbol]
      const snapshotQuote = getDetailQuote(snapshot)
      const sparkline = getSparklineValues(snapshot)
      return {
        id: item.id,
        ticker: String(item.symbol || '').toUpperCase(),
        displayName: getAssetLabel(item.symbol),
        price: toFiniteNumber(snapshotQuote?.price, 0),
        currency: snapshotQuote?.currency || 'USD',
        changePct: toFiniteNumber(snapshotQuote?.change_percent, 0),
        sparkline,
        notes: item.notes,
      }
    }),
    [effectiveSnapshots, watchlistItems],
  )

  const selectedAsset = useMemo(
    () => watchlistAssets.find((item) => item.ticker === selectedTicker) || watchlistAssets[0] || null,
    [selectedTicker, watchlistAssets],
  )

  const chartData = useMemo(
    () => normalizeChartData(selectedDetail, timeframe),
    [selectedDetail, timeframe],
  )

  // Prices from the feed are in the asset's own currency (e.g. THB for SET stocks).
  const assetCurrency = keyStatistics?.currency || getDetailQuote(selectedDetail)?.currency || 'USD'

  // Every currency we may need to convert between: display options plus each asset's own.
  const fxSymbols = useMemo(() => {
    const codes = new Set(DISPLAY_CURRENCIES)
    watchlistAssets.forEach((asset) => codes.add(normalizeCurrencyCode(asset.currency)))
    codes.add(normalizeCurrencyCode(assetCurrency))
    codes.delete('USD')
    return [...codes].sort().join(',')
  }, [watchlistAssets, assetCurrency])

  const { data: fxData, error: fxError } = useSWR(
    fxSymbols ? `/api/market/fx?symbols=${fxSymbols}` : null,
    (url) => apiFetch(url),
    {
      ...swrOptions,
      keepPreviousData: true,
      refreshInterval: 60 * 1000,
      revalidateOnFocus: true,
      revalidateIfStale: true,
    },
  )
  const fx = useMemo(() => ({
    rates: { ...DEFAULT_FX_RATES, ...(fxData?.rates || {}) },
    details: fxData?.details || {},
    loaded: Boolean(fxData || fxError),
  }), [fxData, fxError])

  // Chart points are converted at the FX rate of their own date, not today's rate.
  const chartSourceCurrency = normalizeCurrencyCode(assetCurrency)
  const chartTargetCurrency = normalizeCurrencyCode(currency)
  const chartNeedsFx = chartSourceCurrency !== chartTargetCurrency
  const fxHistorySymbols = chartNeedsFx
    ? [chartSourceCurrency, chartTargetCurrency].filter((code) => code !== 'USD').sort().join(',')
    : ''
  const { data: fxHistoryData, error: fxHistoryError } = useSWR(
    selectedTicker && fxHistorySymbols ? `/api/market/fx/history?symbols=${fxHistorySymbols}&range=${currentRange}` : null,
    (url) => apiFetch(url),
    {
      ...swrOptions,
      refreshInterval: currentRange === 'day' ? 60 * 1000 : 0,
    },
  )
  const historicalConverter = useMemo(() => {
    if (!chartNeedsFx) return buildHistoricalConverter({ from: assetCurrency, to: currency })
    // keepPreviousData may still hold another range's series; only use a matching one.
    if (!fxHistoryData || fxHistoryData.range !== currentRange) return null
    return buildHistoricalConverter({ series: fxHistoryData.series, from: assetCurrency, to: currency, range: currentRange })
  }, [chartNeedsFx, fxHistoryData, assetCurrency, currency, currentRange])

  // Without historical rates we show the chart in the asset's own currency rather than guess.
  const chartCurrency = historicalConverter ? chartTargetCurrency : chartSourceCurrency

  const { convertedChartData, excludedPoints } = useMemo(() => {
    if (!historicalConverter) {
      return {
        convertedChartData: chartData.map((point) => ({ ...point, price: convertCurrency(point.price, chartSourceCurrency, assetCurrency) })),
        excludedPoints: 0,
      }
    }
    const converted = []
    chartData.forEach((point, index) => {
      const fxAtPoint = historicalConverter(point.time, { isLatest: index === chartData.length - 1 })
      // No FX observation for that date: leave the point out instead of using another date's rate.
      if (!fxAtPoint) return
      converted.push({ ...point, price: point.price * fxAtPoint.multiplier, fxLabel: fxAtPoint.label })
    })
    return { convertedChartData: converted, excludedPoints: chartData.length - converted.length }
  }, [chartData, historicalConverter, chartSourceCurrency, assetCurrency])

  const chartFxNote = useMemo(() => {
    if (!chartNeedsFx) return null
    const pair = `${chartSourceCurrency}/${chartTargetCurrency}`
    if (!historicalConverter) {
      return fxHistoryError || (fxHistoryData && fxHistoryData.range === currentRange)
        ? { text: `Historical ${pair} rates unavailable. Chart shown in ${chartSourceCurrency}.`, warn: true }
        : { text: `Loading historical ${pair} rates...`, warn: false }
    }
    const parts = [`Each point converted at that date's ${pair} rate (${fxHistoryData?.source || 'Yahoo Finance'}).`]
    if (excludedPoints > 0 && convertedChartData.length) {
      const since = new Date(convertedChartData[0].time).toLocaleDateString(undefined, { year: 'numeric', month: 'short' })
      parts.push(`${excludedPoints} earlier point${excludedPoints === 1 ? '' : 's'} hidden: no exchange-rate history before ${since}.`)
    } else if (excludedPoints > 0) {
      parts.push('No exchange-rate history covers this period.')
    }
    return { text: parts.join(' '), warn: excludedPoints > 0 }
  }, [chartNeedsFx, chartSourceCurrency, chartTargetCurrency, historicalConverter, fxHistoryError, fxHistoryData, currentRange, excludedPoints, convertedChartData])

  const selectedQuote = getDetailQuote(selectedDetail)
  const selectedChangePercent = toFiniteNumber(selectedQuote?.change_percent ?? selectedQuote?.changePercent)
  const livePrice = toFiniteNumber(selectedQuote?.price, 0)
  const convertedLivePrice = convertCurrency(livePrice, chartCurrency, assetCurrency, fx.rates) || 0
  const first = convertedChartData[0]?.price ?? convertedLivePrice
  const last = convertedChartData[convertedChartData.length - 1]?.price ?? convertedLivePrice
  const absChange = last - first
  const pctChange = first ? (absChange / first) * 100 : 0

  const portfolioOverview = useMemo(() => {
    if (!portfolioState) {
      return {
        totalBalance: 0,
        totalValue: 0,
        performancePct: 0,
        allocation: [],
      }
    }

    const cashBalance = toFiniteNumber(portfolioState.cashBalance, 0)
    const positions = Array.isArray(portfolioState.positions) ? portfolioState.positions : []

    const valuation = positions.map((position) => {
      const symbol = String(position.symbol || '').toUpperCase()
      const snapshot = assetSnapshots[symbol]
      const snapshotPrice = getAssetPrice(snapshot)
      const quotePrice = toFiniteNumber(portfolioQuotes[symbol]?.price)
      const avgPrice = toFiniteNumber(position.avgPrice, 0)
      const quantity = toFiniteNumber(position.quantity, 0)
      const price = quotePrice ?? snapshotPrice ?? avgPrice
      const marketValue = Number.isFinite(position.marketValue) ? Number(position.marketValue) : quantity * (Number.isFinite(price) ? price : 0)
      const costBasis = quantity * avgPrice
      const unrealized = Number.isFinite(position.unrealizedPl) ? Number(position.unrealizedPl) : marketValue - costBasis
      return { symbol, marketValue, costBasis, unrealized }
    })

    const positionsValue = valuation.reduce((sum, row) => sum + (Number.isFinite(row.marketValue) ? row.marketValue : 0), 0)
    const totalValue = Number.isFinite(portfolioState.totalValue) ? Number(portfolioState.totalValue) : cashBalance + positionsValue
    const totalCostBasis = valuation.reduce((sum, row) => sum + (Number.isFinite(row.costBasis) ? row.costBasis : 0), 0)
    const totalUnrealized = valuation.reduce((sum, row) => sum + (Number.isFinite(row.unrealized) ? row.unrealized : 0), 0)
    const performanceBase = totalCostBasis > 0 ? totalCostBasis : (totalValue - totalUnrealized)
    const performancePct = performanceBase ? (totalUnrealized / performanceBase) * 100 : 0

    const allocation = valuation
      .filter((row) => row.marketValue > 0)
      .map((row) => ({
        symbol: row.symbol,
        value: row.marketValue,
        weight: positionsValue ? (row.marketValue / positionsValue) * 100 : 0,
      }))
      .sort((a, b) => b.value - a.value)

    return {
      totalBalance: cashBalance,
      totalValue,
      performancePct,
      allocation,
    }
  }, [portfolioState, portfolioQuotes, effectiveSnapshots])

  const statRows = useMemo(() => {
    const detailPoints = getDetailPoints(selectedDetail)
    const detailQuote = selectedQuote || getDetailQuote(selectedDetail) || {}
    const statisticsPrice = keyStatistics?.latest_price ?? detailQuote?.price ?? selectedQuote?.price
    const resolvedPreviousClose = keyStatistics?.previous_close ?? detailQuote?.previous_close ?? detailQuote?.previousClose
    const chartOpen = detailPoints.find((point) => Number.isFinite(Number(point?.open)))?.open ?? detailPoints.find((point) => Number.isFinite(Number(point?.close)))?.close
    const chartDayLow = detailPoints.reduce((lowest, point) => {
      const low = Number(point?.low)
      return Number.isFinite(low) ? (lowest === null ? low : Math.min(lowest, low)) : lowest
    }, null)
    const chartDayHigh = detailPoints.reduce((highest, point) => {
      const high = Number(point?.high)
      return Number.isFinite(high) ? (highest === null ? high : Math.max(highest, high)) : highest
    }, null)
    const resolvedOpen = keyStatistics?.open ?? detailQuote?.open ?? chartOpen
    const resolvedDayLow = keyStatistics?.day_low ?? detailQuote?.day_low ?? detailQuote?.dayLow ?? chartDayLow
    const resolvedDayHigh = keyStatistics?.day_high ?? detailQuote?.day_high ?? detailQuote?.dayHigh ?? chartDayHigh
    const resolvedWeekLow = keyStatistics?.week_52_low ?? detailQuote?.week_52_low ?? detailQuote?.week52Low ?? detailQuote?.fiftyTwoWeekLow
    const resolvedWeekHigh = keyStatistics?.week_52_high ?? detailQuote?.week_52_high ?? detailQuote?.week52High ?? detailQuote?.fiftyTwoWeekHigh
    const resolvedVolume = keyStatistics?.volume ?? detailQuote?.volume ?? detailQuote?.regularMarketVolume
    const resolvedMarketCap = toMeaningfulFundamental(parseCompactNumber(keyStatistics?.market_cap ?? detailQuote?.market_cap ?? detailQuote?.marketCap))
    const resolvedRevenue = toMeaningfulFundamental(parseCompactNumber(keyStatistics?.revenue ?? detailQuote?.revenue))
    const resolvedNetIncome = toMeaningfulFundamental(parseCompactNumber(keyStatistics?.net_income ?? detailQuote?.net_income))
    const resolvedEps = parseCompactNumber(keyStatistics?.eps ?? detailQuote?.eps)
    const resolvedPeRatio = parseCompactNumber(keyStatistics?.pe_ratio ?? detailQuote?.pe_ratio ?? detailQuote?.peRatio)
    const resolvedBeta = parseCompactNumber(keyStatistics?.beta ?? detailQuote?.beta)
    const derivedDailyChangePct = Number.isFinite(selectedChangePercent)
      ? selectedChangePercent
      : (Number.isFinite(statisticsPrice) && Number.isFinite(resolvedPreviousClose) && resolvedPreviousClose !== 0)
        ? ((statisticsPrice - resolvedPreviousClose) / resolvedPreviousClose) * 100
        : null
    const dayRange = (Number.isFinite(resolvedDayLow) || Number.isFinite(resolvedDayHigh))
      ? `${formatCurrencyValue(resolvedDayLow, currency, { from: assetCurrency, rates: fx.rates })} - ${formatCurrencyValue(resolvedDayHigh, currency, { from: assetCurrency, rates: fx.rates })}`
      : '—'
    const weekRange = (Number.isFinite(resolvedWeekLow) || Number.isFinite(resolvedWeekHigh))
      ? `${formatCurrencyValue(resolvedWeekLow, currency, { from: assetCurrency, rates: fx.rates })} - ${formatCurrencyValue(resolvedWeekHigh, currency, { from: assetCurrency, rates: fx.rates })}`
      : '—'

    return [
      { label: 'Live Price', value: formatCurrencyValue(statisticsPrice, currency, { from: assetCurrency, rates: fx.rates }) },
      { label: 'Daily Change', value: Number.isFinite(derivedDailyChangePct) ? `${formatSigned(derivedDailyChangePct)}%` : '—' },
      { label: 'Volume', value: formatMetricNumber(resolvedVolume) },
      { label: 'Prev. Close', value: formatCurrencyValue(resolvedPreviousClose, currency, { from: assetCurrency, rates: fx.rates }) },
      { label: 'Open', value: formatCurrencyValue(resolvedOpen, currency, { from: assetCurrency, rates: fx.rates }) },
      { label: "Day's Range", value: dayRange },
      { label: '52 wk Range', value: weekRange },
      { label: 'Market Cap', value: formatLargeCurrency(resolvedMarketCap, currency, { from: assetCurrency, rates: fx.rates }) },
      { label: 'Revenue', value: formatLargeCurrency(resolvedRevenue, currency, { from: assetCurrency, rates: fx.rates }) },
      { label: 'Net Income', value: formatLargeCurrency(resolvedNetIncome, currency, { from: assetCurrency, rates: fx.rates }) },
      { label: 'EPS', value: formatCurrencyValue(resolvedEps, currency, { from: assetCurrency, rates: fx.rates }) },
      { label: 'P/E Ratio', value: Number.isFinite(resolvedPeRatio) ? resolvedPeRatio.toFixed(2) : '—' },
      { label: 'Beta', value: Number.isFinite(resolvedBeta) ? resolvedBeta.toFixed(2) : '—' },
    ]
  }, [keyStatistics, currency, assetCurrency, fx.rates, selectedQuote, selectedDetail, selectedChangePercent])

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
            portfolioSummary={portfolioOverview}
            currency={currency}
            fx={fx}
            onCurrencyChange={setCurrency}
            sortMode={watchlistSortMode}
            isGrouped={isWatchlistGrouped}
            bookmarkedSymbols={bookmarkedSymbols}
            onToggleSort={cycleWatchlistSortMode}
            onToggleGroup={toggleWatchlistGrouping}
            onToggleBookmark={toggleBookmark}
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
              <p className={styles.assetPath}>Home / Markets / {selectedAsset?.ticker || 'Watchlist'} / {chartCurrency}</p>
              <h2 className={styles.assetName}>{selectedAsset?.displayName || 'No asset selected'}</h2>
              <p className={styles.assetTicker}>{selectedAsset?.ticker ? `${selectedAsset.ticker} / ${chartCurrency}` : 'Add an asset from the Watchlist page'}</p>
            </div>
            <div className={styles.priceBlock}>
              <strong className={styles.currentPrice}>{formatCurrencyValue(last, chartCurrency, { from: chartCurrency })}</strong>
              <p className={pctChange >= 0 ? styles.pos : styles.neg}>
                {formatSigned(absChange)} ({formatSigned(pctChange)}%)
              </p>
              <span className={styles.priceTimestamp}>{utcNow ? utcNow.toUTCString() : 'Loading time...'}</span>
            </div>
          </header>

          {selectedDetailLoading && !chartData.length ? (
            <section className={styles.chartSection} aria-label="Price chart loading">
              <p className={styles.assetTicker}>Loading asset data...</p>
            </section>
          ) : (
            <AssetChart chartData={convertedChartData} timeframe={timeframe} onTimeframeChange={setTimeframe} currency={chartCurrency} fxNote={chartFxNote} />
          )}

          <section className={styles.statisticsSection} aria-label="Key statistics">
            <div className={styles.statisticsHeader}>
              <h3>Key Statistics</h3>
              {keyStatisticsLoading ? <span>Loading fundamentals...</span> : null}
            </div>
            <div className={styles.statisticsGrid}>
              {statRows.map((metric) => (
                <div key={metric.label} className={styles.statCard}><span>{metric.label}</span><strong>{metric.value}</strong></div>
              ))}
            </div>
          </section>

          <NewsAnalysisSection
            articles={assetNews}
            loading={assetNewsLoading}
            error={assetNewsError}
          />
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
