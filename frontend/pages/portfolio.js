import Link from 'next/link'
import { useEffect, useMemo, useRef, useState } from 'react'
import AppShell from '../components/AppShell'
import PortfolioAllocation from '../components/PortfolioAllocation'
import { apiFetch } from '../lib/api'
import { formatQuantity, unitWord, usdOptions } from '../lib/format'
import styles from '../styles/Portfolio.module.css'

const QUOTE_REFRESH_MS = 60 * 1000

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function toNumber(value, fallback = 0) {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

function formatMoney(value, { signed = false } = {}) {
  const n = toNumber(value)
  const text = Math.abs(n).toLocaleString(undefined, usdOptions(n))
  if (!signed) return n < 0 ? `-${text}` : text
  return `${n > 0 ? '+' : n < 0 ? '−' : ''}${text}`
}

function formatPct(value, { signed = true } = {}) {
  const n = toNumber(value)
  const text = `${Math.abs(n).toFixed(2)}%`
  if (!signed) return text
  return `${n > 0 ? '+' : n < 0 ? '−' : ''}${text}`
}

function formatQty(value) {
  return formatQuantity(toNumber(value))
}

function formatDate(value) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return String(value)
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function toneClass(value) {
  const n = toNumber(value)
  if (n > 0) return styles.up
  if (n < 0) return styles.down
  return styles.flat
}

function StatTile({ label, value, sub, tone, hint }) {
  return (
    <div className={styles.stat}>
      <div className={styles.statLabel}>{label}</div>
      <div className={`${styles.statValue} ${tone || ''}`}>{value}</div>
      {sub ? <div className={`${styles.statSub} ${tone || ''}`}>{sub}</div> : null}
      {hint ? <div className={styles.statHint}>{hint}</div> : null}
    </div>
  )
}

export default function PortfolioPage() {
  const [portfolio, setPortfolio] = useState(null)
  const [paperAccount, setPaperAccount] = useState(null)
  const [paperLoaded, setPaperLoaded] = useState(false)
  const [needsInit, setNeedsInit] = useState(false)
  const [quotes, setQuotes] = useState({})
  const [quotesAt, setQuotesAt] = useState(null)
  const [transactions, setTransactions] = useState([])
  const [botPositions, setBotPositions] = useState({})
  const [watchlist, setWatchlist] = useState([])

  const [orderSymbol, setOrderSymbol] = useState('')
  const [orderSide, setOrderSide] = useState('BUY')
  const [orderMode, setOrderMode] = useState('shares')
  const [orderAmount, setOrderAmount] = useState('')
  const [orderQuote, setOrderQuote] = useState(null)

  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState(null)
  const ticketRef = useRef(null)

  const isError = message && String(message).toLowerCase().startsWith('error')

  async function loadPortfolio(options = {}) {
    const { quiet = false } = options
    if (!quiet) {
      setLoading(true)
      setMessage(null)
    }
    try {
      const data = await apiFetch('/api/portfolio')
      setPortfolio(data)
      setNeedsInit(false)
      return data
    } catch (e) {
      const msg = String(e?.message || 'Unknown error')
      if (msg.toLowerCase().includes('not initialized')) {
        setPortfolio(null)
        setNeedsInit(true)
        return null
      }
      if (!quiet) setMessage(`Error: ${msg}`)
    } finally {
      if (!quiet) setLoading(false)
    }
    return null
  }

  async function loadPaperAccount(options = {}) {
    const { quiet = false } = options
    try {
      const data = await apiFetch('/api/portfolio/paper-account')
      setPaperAccount(data)
      return data
    } catch (e) {
      setPaperAccount(null)
      if (!quiet) setMessage(`Error: ${e?.message || 'Unknown error'}`)
    } finally {
      setPaperLoaded(true)
    }
    return null
  }

  // Only trades placed by the user (paper orders from this page); bot trades live on the Automated page.
  async function loadActivity() {
    try {
      const rows = await apiFetch('/api/analytics/transactions?limit=100')
      // Your own trades: from this page's trade ticket ("manual") or placed directly on Alpaca ("alpaca").
      const manual = (Array.isArray(rows) ? rows : []).filter((t) => String(t.signal_source || '').toLowerCase() !== 'bot')
      setTransactions(manual.slice(0, 8))
    } catch {
      setTransactions([])
    }
  }

  async function loadBotPositions() {
    try {
      const rules = await apiFetch('/api/bot/rules')
      const map = {}
      for (const p of rules?.activePositions || []) {
        if (p?.asset) map[String(p.asset).toUpperCase()] = toNumber(p.quantity)
      }
      setBotPositions(map)
    } catch {
      setBotPositions({})
    }
  }

  async function loadQuotes(symbols) {
    if (!symbols.length) return
    try {
      const rows = await apiFetch(`/api/market/quotes?symbols=${encodeURIComponent(symbols.join(','))}`)
      const map = {}
      for (const row of Array.isArray(rows) ? rows : []) {
        if (row?.symbol && row.price != null && !row.error) map[String(row.symbol).toUpperCase()] = row
      }
      setQuotes((prev) => ({ ...prev, ...map }))
      setQuotesAt(new Date())
    } catch {
      // Keep the last known prices; holdings fall back to average cost when never priced.
    }
  }

  // /api/analytics/transactions creates a default portfolio when none exists, so only ask for
  // activity once the user has one (otherwise it would skip the "Start your portfolio" step).
  async function loadPortfolioAndActivity(options = {}) {
    const data = await loadPortfolio(options)
    if (data) await loadActivity()
    return data
  }

  async function refreshAll() {
    await Promise.all([loadPortfolioAndActivity({ quiet: true }), loadPaperAccount({ quiet: true }), loadBotPositions()])
  }

  async function refreshOrderState() {
    for (let attempt = 0; attempt < 4; attempt += 1) {
      try {
        await apiFetch('/api/portfolio/sync-paper', { method: 'POST' })
        await refreshAll()
        return true
      } catch {
        if (attempt < 3) await wait(1500 * (attempt + 1))
      }
    }
    try {
      await refreshAll()
    } catch {
      // best-effort refresh only
    }
    return false
  }

  async function runAction(action, successMessage) {
    setLoading(true)
    setMessage(null)
    try {
      await action()
      if (successMessage) setMessage(successMessage)
    } catch (e) {
      setMessage(`Error: ${e?.message || 'Something went wrong'}`)
    } finally {
      setLoading(false)
    }
  }

  // The portfolio mirrors the Alpaca paper account: pull the latest cash and positions from Alpaca.
  async function syncFromAlpaca() {
    try {
      await apiFetch('/api/portfolio/sync-paper', { method: 'POST' })
      return true
    } catch {
      return false
    }
  }

  function syncPaperPortfolio() {
    runAction(async () => {
      await apiFetch('/api/portfolio/sync-paper', { method: 'POST' })
      await refreshAll()
    }, 'Portfolio synced from your Alpaca paper account')
  }

  function prefillOrder(symbol, side) {
    setOrderSymbol(symbol)
    setOrderSide(side)
    if (side === 'SELL') setOrderMode('shares')
    ticketRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  async function submitPaperOrder() {
    const s = orderSymbol.trim().toUpperCase()
    const amount = orderAmount.trim()
    if (!s || !amount) return
    const byShares = orderMode === 'shares' || orderSide === 'SELL'
    setLoading(true)
    setMessage(null)
    try {
      const result = await apiFetch('/api/portfolio/paper-orders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: s,
          side: orderSide,
          quantity: byShares ? amount : null,
          notional: byShares ? null : amount,
        }),
      })
      setOrderAmount('')
      let synced = false
      try {
        synced = result?.sync_status === 'synced' ? true : await refreshOrderState()
        if (result?.sync_status === 'synced') await refreshAll()
      } catch {
        synced = false
      }
      setMessage(
        synced
          ? `${orderSide} order for ${s} sent to Alpaca and your portfolio is up to date.`
          : `${orderSide} order for ${s} was accepted by Alpaca. Holdings will update once the sync finishes.`
      )
    } catch (e) {
      setMessage(`Error: ${e?.message || 'Order submission failed'}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    ;(async () => {
      setLoading(true)
      await syncFromAlpaca()
      await Promise.all([loadPortfolioAndActivity({ quiet: true }), loadPaperAccount({ quiet: true }), loadBotPositions()])
      setLoading(false)
    })()
    apiFetch('/api/watchlist')
      .then((rows) => setWatchlist((Array.isArray(rows) ? rows : []).map((r) => String(r.symbol || '').toUpperCase()).filter(Boolean)))
      .catch(() => setWatchlist([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const heldSymbols = useMemo(
    () => (portfolio?.positions || []).map((p) => String(p.symbol).toUpperCase()).filter(Boolean),
    [portfolio],
  )

  // Live prices for holdings, refreshed every minute.
  useEffect(() => {
    if (!heldSymbols.length) return undefined
    loadQuotes(heldSymbols)
    const timer = setInterval(() => loadQuotes(heldSymbols), QUOTE_REFRESH_MS)
    return () => clearInterval(timer)
  }, [heldSymbols])

  // Quote for the trade ticket (debounced while typing).
  useEffect(() => {
    const s = orderSymbol.trim().toUpperCase()
    if (!s) {
      setOrderQuote(null)
      return undefined
    }
    if (quotes[s]) {
      setOrderQuote(quotes[s])
      return undefined
    }
    const timer = setTimeout(async () => {
      try {
        const rows = await apiFetch(`/api/market/quotes?symbols=${encodeURIComponent(s)}`)
        const row = (Array.isArray(rows) ? rows : []).find((r) => String(r.symbol).toUpperCase() === s && r.price != null && !r.error)
        setOrderQuote(row || null)
      } catch {
        setOrderQuote(null)
      }
    }, 450)
    return () => clearTimeout(timer)
  }, [orderSymbol, quotes])

  const holdings = useMemo(() => {
    const rows = (portfolio?.positions || []).map((p) => {
      const symbol = String(p.symbol).toUpperCase()
      const qty = toNumber(p.quantity)
      const avg = toNumber(p.avg_price)
      const quote = quotes[symbol]
      const price = quote ? toNumber(quote.price, avg) : avg
      const value = qty * price
      const cost = qty * avg
      return {
        id: p.id,
        symbol,
        name: quote?.display_name && quote.display_name !== symbol ? quote.display_name : null,
        quantity: qty,
        avgPrice: avg,
        price,
        priced: Boolean(quote),
        value,
        cost,
        pl: value - cost,
        plPct: cost > 0 ? ((value - cost) / cost) * 100 : 0,
        dayChange: quote && quote.change != null ? qty * toNumber(quote.change) : null,
        dayPct: quote && quote.change_percent != null ? toNumber(quote.change_percent) : null,
        botQty: botPositions[symbol] || 0,
        updatedAt: p.updated_at,
      }
    })
    return rows.sort((a, b) => b.value - a.value)
  }, [portfolio, quotes, botPositions])

  const totals = useMemo(() => {
    const cashValue = toNumber(portfolio?.cash_balance)
    const invested = holdings.reduce((sum, h) => sum + h.value, 0)
    const cost = holdings.reduce((sum, h) => sum + h.cost, 0)
    const priced = holdings.filter((h) => h.dayChange != null)
    const dayChange = priced.reduce((sum, h) => sum + h.dayChange, 0)
    const prevInvested = priced.reduce((sum, h) => sum + (h.value - h.dayChange), 0)
    const total = cashValue + invested
    return {
      cash: cashValue,
      invested,
      cost,
      total,
      pl: invested - cost,
      plPct: cost > 0 ? ((invested - cost) / cost) * 100 : 0,
      dayChange,
      dayPct: prevInvested > 0 ? (dayChange / prevInvested) * 100 : 0,
      hasDay: priced.length > 0,
      cashPct: total > 0 ? (cashValue / total) * 100 : 0,
    }
  }, [portfolio, holdings])

  const orderSymbolUpper = orderSymbol.trim().toUpperCase()
  const heldForOrder = holdings.find((h) => h.symbol === orderSymbolUpper)
  const orderPrice = orderQuote ? toNumber(orderQuote.price) : null
  const orderByShares = orderMode === 'shares' || orderSide === 'SELL'
  const orderUnit = unitWord(orderSymbolUpper)
  const orderAmountNum = toNumber(orderAmount, 0)
  const orderEstimate = orderPrice && orderAmountNum > 0
    ? orderByShares
      ? { label: orderSide === 'BUY' ? 'Estimated cost' : 'Estimated proceeds', value: formatMoney(orderAmountNum * orderPrice) }
      : { label: `Estimated ${orderUnit}`, value: formatQty(orderAmountNum / orderPrice) }
    : null
  const cashAfter = orderSide === 'BUY' && orderPrice && orderAmountNum > 0
    ? totals.cash - (orderByShares ? orderAmountNum * orderPrice : orderAmountNum)
    : null
  const sellTooMuch = orderSide === 'SELL' && heldForOrder && orderAmountNum > heldForOrder.quantity
  const suggestions = Array.from(new Set([...heldSymbols, ...watchlist]))
  const paperReady = Boolean(paperAccount)

  // Sync copies Alpaca's cash and positions into the local portfolio, so "synced" means both already match.
  // null = unknown (Alpaca account or local portfolio not loaded).
  const inSync = useMemo(() => {
    if (!paperAccount || !portfolio) return null
    if (Math.abs(toNumber(portfolio.cash_balance) - toNumber(paperAccount.cash)) >= 0.01) return false
    const local = new Map((portfolio.positions || []).map((p) => [String(p.symbol).toUpperCase(), toNumber(p.quantity)]))
    const remote = (paperAccount.positions || []).filter((p) => toNumber(p.quantity) > 0)
    if (remote.length !== local.size) return false
    return remote.every((p) => Math.abs((local.get(String(p.symbol).toUpperCase()) ?? -1) - toNumber(p.quantity)) < 1e-6)
  }, [paperAccount, portfolio])

  return (
    <AppShell title="Portfolio" subtitle="Your holdings, performance and paper trading in one place">
      <div className={styles.page}>
        {message ? (
          <div className={`${styles.banner} ${isError ? styles.bannerError : styles.bannerOk}`} role={isError ? 'alert' : 'status'}>
            <span>{isError ? message.replace(/^Error:\s*/i, '') : message}</span>
            <button type="button" className={styles.bannerClose} onClick={() => setMessage(null)} aria-label="Dismiss message">×</button>
          </div>
        ) : null}

        {needsInit ? (
          <section className={`${styles.card} ${styles.initCard}`} aria-label="Connect Alpaca">
            <div className={styles.initIcon} aria-hidden="true">$</div>
            <h2 className={styles.initTitle}>Connect your Alpaca paper account</h2>
            <p className={styles.muted}>
              Your portfolio mirrors your Alpaca paper account: its cash, holdings and every order you or the bot place.
              Add your Alpaca paper keys, then sync.
            </p>
            <div className={styles.initRow}>
              <Link href="/api-keys" className={`${styles.primary} ${styles.linkButton}`}>Add Alpaca keys</Link>
              <button type="button" className={styles.secondary} onClick={syncPaperPortfolio} disabled={loading}>
                Sync from Alpaca
              </button>
            </div>
          </section>
        ) : (
          <>
            <section className={styles.stats} aria-label="Portfolio summary">
              <StatTile
                label="Total value"
                value={portfolio ? formatMoney(totals.total) : '—'}
                hint={portfolio ? `${formatMoney(totals.invested)} invested · ${formatMoney(totals.cash)} cash` : 'Loading…'}
              />
              <StatTile
                label="Today"
                value={totals.hasDay ? formatMoney(totals.dayChange, { signed: true }) : '—'}
                sub={totals.hasDay ? formatPct(totals.dayPct) : null}
                tone={totals.hasDay ? toneClass(totals.dayChange) : ''}
                hint={totals.hasDay ? 'Change in your holdings since the last close' : 'Waiting for live prices'}
              />
              <StatTile
                label="Unrealized P/L"
                value={formatMoney(totals.pl, { signed: true })}
                sub={totals.cost > 0 ? formatPct(totals.plPct) : null}
                tone={toneClass(totals.pl)}
                hint={`On ${formatMoney(totals.cost)} cost basis`}
              />
              <StatTile
                label="Cash"
                value={portfolio ? formatMoney(totals.cash) : '—'}
                sub={portfolio ? `${totals.cashPct.toFixed(1)}% of portfolio` : null}
                hint={paperAccount?.buying_power ? `Alpaca buying power ${formatMoney(paperAccount.buying_power)}` : null}
              />
            </section>

            <div className={styles.layout}>
              <div className={styles.mainCol}>
                <section className={styles.card} aria-label="Allocation">
                  <div className={styles.cardHead}>
                    <h2 className={styles.cardTitle}>Allocation</h2>
                    <span className={styles.muted}>{holdings.length} holding{holdings.length === 1 ? '' : 's'} + cash</span>
                  </div>
                  <PortfolioAllocation holdings={holdings} cash={totals.cash} total={totals.total} />
                </section>

                <section className={styles.card} aria-label="Holdings">
                  <div className={styles.cardHead}>
                    <h2 className={styles.cardTitle}>Holdings</h2>
                    <span className={styles.muted}>
                      {quotesAt ? `Prices updated ${quotesAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : 'Fetching prices…'}
                    </span>
                  </div>
                  {holdings.length ? (
                    <div className={styles.tableWrap}>
                      <table className={styles.table}>
                        <thead>
                          <tr>
                            <th>Asset</th>
                            <th className={styles.num}>Shares</th>
                            <th className={styles.num}>Avg cost</th>
                            <th className={styles.num}>Price</th>
                            <th className={styles.num}>Value</th>
                            <th className={styles.num}>Unrealized P/L</th>
                            <th>Weight</th>
                            <th aria-label="Actions" />
                          </tr>
                        </thead>
                        <tbody>
                          {holdings.map((h) => {
                            const weight = totals.total > 0 ? (h.value / totals.total) * 100 : 0
                            return (
                              <tr key={h.id}>
                                <td>
                                  <div className={styles.assetCell}>
                                    <span className={styles.ticker}>{h.symbol.replace(/-USD$/, '').slice(0, 4)}</span>
                                    <div>
                                      <div className={styles.assetSymbol}>
                                        {h.symbol}
                                        {h.botQty > 0 ? (
                                          <span className={styles.botBadge} title={`${formatQty(h.botQty)} of these ${unitWord(h.symbol)} are managed by the trading bot`}>
                                            Bot {formatQty(h.botQty)}
                                          </span>
                                        ) : null}
                                      </div>
                                      {h.name ? <div className={styles.assetName}>{h.name}</div> : null}
                                      {h.updatedAt ? <div className={styles.assetName}>Updated {formatDate(h.updatedAt)}</div> : null}
                                    </div>
                                  </div>
                                </td>
                                <td className={styles.num}>{formatQty(h.quantity)}</td>
                                <td className={styles.num}>{formatMoney(h.avgPrice)}</td>
                                <td className={styles.num}>
                                  <div>{h.priced ? formatMoney(h.price) : '—'}</div>
                                  {h.dayPct != null ? (
                                    <div className={`${styles.small} ${toneClass(h.dayPct)}`}>{formatPct(h.dayPct)} today</div>
                                  ) : !h.priced ? (
                                    <div className={styles.small}>no live price</div>
                                  ) : null}
                                </td>
                                <td className={styles.num}><strong>{formatMoney(h.value)}</strong></td>
                                <td className={`${styles.num} ${toneClass(h.pl)}`}>
                                  <div>{formatMoney(h.pl, { signed: true })}</div>
                                  <div className={styles.small}>{formatPct(h.plPct)}</div>
                                </td>
                                <td>
                                  <div className={styles.weight}>
                                    <div className={styles.weightTrack}><span style={{ width: `${Math.min(weight, 100)}%` }} /></div>
                                    <span className={styles.small}>{weight.toFixed(1)}%</span>
                                  </div>
                                </td>
                                <td>
                                  <div className={styles.rowActions}>
                                    <button type="button" className={styles.chipBtn} onClick={() => prefillOrder(h.symbol, 'BUY')}>Buy</button>
                                    <button type="button" className={styles.chipBtn} onClick={() => prefillOrder(h.symbol, 'SELL')}>Sell</button>
                                  </div>
                                </td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className={styles.empty}>
                      <p><strong>No holdings yet.</strong></p>
                      <p className={styles.muted}>Your Alpaca paper account has no positions. Place a paper order to buy your first stock.</p>
                      <div className={styles.emptyActions}>
                        <button type="button" className={styles.primary} onClick={() => ticketRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })}>
                          Place a paper order
                        </button>
                      </div>
                    </div>
                  )}
                </section>

                <section className={styles.card} aria-label="Recent activity">
                  <div className={styles.cardHead}>
                    <h2 className={styles.cardTitle}>Your recent trades</h2>
                    <Link href="/analytics" className={styles.link}>All transactions →</Link>
                  </div>
                  {transactions.length ? (
                    <ul className={styles.activity}>
                      {transactions.map((t) => {
                        const buy = String(t.trade_type).toUpperCase() === 'BUY'
                        return (
                          <li key={t.id} className={styles.activityItem}>
                            <span className={`${styles.sideTag} ${buy ? styles.sideBuy : styles.sideSell}`}>{buy ? 'BUY' : 'SELL'}</span>
                            <div className={styles.activityMain}>
                              <div>
                                <strong>{t.symbol}</strong> · {formatQty(t.quantity)} @ {formatMoney(t.price)}
                              </div>
                              <div className={styles.small}>{formatDate(t.created_at)}</div>
                            </div>
                            <div className={styles.activityRight}>
                              <div>{formatMoney(t.notional)}</div>
                              {!buy && t.pl != null ? <div className={`${styles.small} ${toneClass(t.pl)}`}>{formatMoney(t.pl, { signed: true })} P/L</div> : null}
                            </div>
                          </li>
                        )
                      })}
                    </ul>
                  ) : (
                    <p className={styles.muted}>No trades yet. Orders you place from the trade ticket show up here; bot trades are on the Automated page.</p>
                  )}
                </section>
              </div>

              <aside className={styles.sideCol}>
                <section className={`${styles.card} ${styles.ticket}`} aria-label="Paper trade" ref={ticketRef}>
                  <div className={styles.cardHead}>
                    <h2 className={styles.cardTitle}>Paper trade</h2>
                    <span className={styles.muted}>via Alpaca</span>
                  </div>

                  <div className={styles.sideToggle} role="group" aria-label="Order side">
                    <button type="button" className={orderSide === 'BUY' ? styles.sideBuyOn : ''} onClick={() => setOrderSide('BUY')} aria-pressed={orderSide === 'BUY'}>Buy</button>
                    <button
                      type="button"
                      className={orderSide === 'SELL' ? styles.sideSellOn : ''}
                      onClick={() => { setOrderSide('SELL'); setOrderMode('shares') }}
                      aria-pressed={orderSide === 'SELL'}
                    >
                      Sell
                    </button>
                  </div>

                  <label className={styles.fieldLabel} htmlFor="order-symbol">Symbol</label>
                  <input
                    id="order-symbol"
                    className={styles.input}
                    value={orderSymbol}
                    onChange={(e) => setOrderSymbol(e.target.value.toUpperCase())}
                    placeholder="AAPL"
                    autoCapitalize="characters"
                    list="portfolio-symbols"
                  />
                  <datalist id="portfolio-symbols">
                    {suggestions.map((s) => <option key={s} value={s} />)}
                  </datalist>
                  {orderQuote ? (
                    <div className={styles.quoteLine}>
                      <span>{orderQuote.display_name && orderQuote.display_name !== orderSymbolUpper ? orderQuote.display_name : orderSymbolUpper}</span>
                      <span>
                        <strong>{formatMoney(orderQuote.price)}</strong>{' '}
                        {orderQuote.change_percent != null ? <span className={toneClass(orderQuote.change_percent)}>{formatPct(orderQuote.change_percent)}</span> : null}
                      </span>
                    </div>
                  ) : orderSymbolUpper ? (
                    <div className={styles.quoteLine}><span className={styles.muted}>Looking up price…</span></div>
                  ) : null}

                  <div className={styles.amountHead}>
                    <label className={styles.fieldLabel} htmlFor="order-amount">{orderByShares ? unitWord(orderSymbolUpper, { capitalize: true }) : 'Amount ($)'}</label>
                    <div className={styles.modeToggle} role="group" aria-label="Order by">
                      <button type="button" className={orderByShares ? styles.modeOn : ''} onClick={() => setOrderMode('shares')} aria-pressed={orderByShares}>{unitWord(orderSymbolUpper, { capitalize: true })}</button>
                      <button
                        type="button"
                        className={!orderByShares ? styles.modeOn : ''}
                        onClick={() => setOrderMode('dollars')}
                        disabled={orderSide === 'SELL'}
                        title={orderSide === 'SELL' ? `Sell orders are placed in ${orderUnit}` : undefined}
                        aria-pressed={!orderByShares}
                      >
                        Dollars
                      </button>
                    </div>
                  </div>
                  <div className={styles.amountRow}>
                    <input
                      id="order-amount"
                      className={styles.input}
                      value={orderAmount}
                      onChange={(e) => setOrderAmount(e.target.value)}
                      inputMode="decimal"
                      placeholder={orderByShares ? '10' : '1000'}
                    />
                    {orderSide === 'SELL' && heldForOrder ? (
                      <button type="button" className={styles.chipBtn} onClick={() => setOrderAmount(String(heldForOrder.quantity))}>Max</button>
                    ) : null}
                  </div>
                  {orderSide === 'SELL' && orderSymbolUpper ? (
                    <div className={styles.small}>You hold {heldForOrder ? formatQty(heldForOrder.quantity) : 0} {orderSymbolUpper} {orderUnit}.</div>
                  ) : null}

                  <dl className={styles.estimate}>
                    <div>
                      <dt>{orderEstimate ? orderEstimate.label : 'Estimate'}</dt>
                      <dd>{orderEstimate ? orderEstimate.value : '—'}</dd>
                    </div>
                    {cashAfter != null ? (
                      <div>
                        <dt>Cash after</dt>
                        <dd className={cashAfter < 0 ? styles.down : ''}>{formatMoney(cashAfter)}</dd>
                      </div>
                    ) : null}
                  </dl>
                  {cashAfter != null && cashAfter < 0 ? <p className={styles.warn}>This is more than your cash balance.</p> : null}
                  {sellTooMuch ? <p className={styles.warn}>You are selling more {orderUnit} than you hold.</p> : null}

                  <button
                    type="button"
                    className={`${styles.submit} ${orderSide === 'SELL' ? styles.submitSell : styles.submitBuy}`}
                    onClick={submitPaperOrder}
                    disabled={loading || !orderSymbolUpper || !(orderAmountNum > 0)}
                  >
                    {loading ? 'Working…' : `${orderSide === 'BUY' ? 'Buy' : 'Sell'} ${orderSymbolUpper || ''}`.trim()}
                  </button>
                  <p className={styles.small}>
                    {paperReady || !paperLoaded
                      ? 'Paper money only. The order goes to your Alpaca paper account, then syncs back here.'
                      : 'Your Alpaca paper account did not respond. Orders need working Alpaca paper keys (API Management).'}
                  </p>
                </section>

                <section className={styles.card} aria-label="Alpaca paper account">
                  <div className={styles.cardHead}>
                    <h2 className={styles.cardTitle}>Alpaca paper account</h2>
                    {paperReady ? (
                      <span className={`${styles.statusPill} ${String(paperAccount.status).toUpperCase() === 'ACTIVE' ? styles.statusOn : ''}`}>
                        <i aria-hidden="true" />{paperAccount.status || 'Unknown'}
                      </span>
                    ) : null}
                  </div>
                  {paperReady ? (
                    <dl className={styles.kvList}>
                      <div><dt>Portfolio value</dt><dd>{formatMoney(paperAccount.portfolio_value)}</dd></div>
                      <div><dt>Buying power</dt><dd>{formatMoney(paperAccount.buying_power)}</dd></div>
                      <div><dt>Cash</dt><dd>{formatMoney(paperAccount.cash)}</dd></div>
                      <div><dt>Positions at Alpaca</dt><dd>{(paperAccount.positions || []).length}</dd></div>
                    </dl>
                  ) : (
                    <p className={styles.muted}>
                      {paperLoaded ? 'Not connected. ' : 'Checking connection… '}
                      {paperLoaded ? <Link href="/api-keys" className={styles.link}>Add Alpaca keys →</Link> : null}
                    </p>
                  )}
                  {inSync === false ? (
                    <p className={styles.small}>Your portfolio here differs from Alpaca. Syncing replaces the cash and holdings here with Alpaca&apos;s.</p>
                  ) : null}
                  <div className={styles.btnRow}>
                    <button
                      type="button"
                      className={inSync ? styles.syncDone : inSync === false ? styles.primary : styles.secondary}
                      onClick={syncPaperPortfolio}
                      disabled={loading}
                      title={inSync ? 'Cash and holdings already match Alpaca' : undefined}
                    >
                      {inSync ? 'Synced with Alpaca' : 'Sync from Alpaca'}
                    </button>
                    <button type="button" className={styles.secondary} onClick={() => runAction(refreshAll)} disabled={loading}>Refresh</button>
                  </div>
                </section>

              </aside>
            </div>
          </>
        )}
      </div>
    </AppShell>
  )
}
