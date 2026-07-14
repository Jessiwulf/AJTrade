import { useEffect, useMemo, useState } from 'react'
import AppShell from '../components/AppShell'
import { apiFetch } from '../lib/api'
import styles from '../styles/Manage.module.css'

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function formatDate(value) {
  try {
    if (!value) return ''
    const d = new Date(value)
    if (Number.isNaN(d.getTime())) return String(value)
    return d.toLocaleString()
  } catch {
    return String(value || '')
  }
}

export default function PortfolioPage() {
  const [portfolio, setPortfolio] = useState(null)
  const [paperAccount, setPaperAccount] = useState(null)
  const [needsInit, setNeedsInit] = useState(false)
  const [cash, setCash] = useState('')
  const [symbol, setSymbol] = useState('')
  const [quantity, setQuantity] = useState('')
  const [avgPrice, setAvgPrice] = useState('')
  const [orderSide, setOrderSide] = useState('BUY')
  const [orderQuantity, setOrderQuantity] = useState('')
  const [orderNotional, setOrderNotional] = useState('')
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState(null)

  const messageClass = useMemo(() => {
    if (!message) return styles.msg
    if (String(message).toLowerCase().startsWith('error')) return `${styles.msg} ${styles.msgError}`
    return `${styles.msg} ${styles.msgSuccess}`
  }, [message])

  async function fetchPortfolioSnapshot() {
    const data = await apiFetch('/api/portfolio')
    return {
      data,
      needsInit: false,
    }
  }

  async function fetchPaperAccountSnapshot() {
    return apiFetch('/api/portfolio/paper-account')
  }

  async function loadPortfolio(options = {}) {
    const { quiet = false } = options
    if (!quiet) {
      setLoading(true)
      setMessage(null)
    }
    try {
      const { data } = await fetchPortfolioSnapshot()
      setPortfolio(data)
      setNeedsInit(false)
      setCash(String(data?.cash_balance ?? ''))
      return data
    } catch (e) {
      const msg = String(e?.message || 'Unknown error')
      if (msg.toLowerCase().includes('not initialized')) {
        setPortfolio(null)
        setNeedsInit(true)
        setCash('')
        return null
      } else {
        if (!quiet) setMessage(`Error: ${msg}`)
      }
    } finally {
      if (!quiet) setLoading(false)
    }
    return null
  }

  async function loadPaperAccount(options = {}) {
    const { quiet = false } = options
    try {
      const data = await fetchPaperAccountSnapshot()
      setPaperAccount(data)
      return data
    } catch (e) {
      setPaperAccount(null)
      if (!quiet) setMessage(`Error: ${e?.message || 'Unknown error'}`)
    }
    return null
  }

  async function refreshOrderState() {
    for (let attempt = 0; attempt < 4; attempt += 1) {
      try {
        await apiFetch('/api/portfolio/sync-paper', { method: 'POST' })
        await Promise.all([
          loadPortfolio({ quiet: true }),
          loadPaperAccount({ quiet: true }),
        ])
        return true
      } catch {
        if (attempt < 3) {
          await wait(1500 * (attempt + 1))
        }
      }
    }

    try {
      await Promise.all([
        loadPortfolio({ quiet: true }),
        loadPaperAccount({ quiet: true }),
      ])
    } catch {
      // best-effort refresh only
    }
    return false
  }

  async function initialize() {
    const c = cash.trim()
    if (!c) return
    setLoading(true)
    setMessage(null)
    try {
      await apiFetch('/api/portfolio/initialize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cash_balance: c, positions: [] }),
      })
      await loadPortfolio()
      setMessage('Initialized')
    } catch (e) {
      setMessage(`Error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function updateCash() {
    const c = cash.trim()
    if (!c) return
    setLoading(true)
    setMessage(null)
    try {
      await apiFetch('/api/portfolio/cash', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cash_balance: c }),
      })
      await loadPortfolio()
      setMessage('Cash updated')
    } catch (e) {
      setMessage(`Error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function upsertPosition() {
    const s = symbol.trim()
    const q = quantity.trim()
    const a = avgPrice.trim() || '0'
    if (!s || !q) return

    setLoading(true)
    setMessage(null)
    try {
      await apiFetch('/api/portfolio/positions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: s, quantity: q, avg_price: a }),
      })
      setSymbol('')
      setQuantity('')
      setAvgPrice('')
      await loadPortfolio()
      setMessage('Position saved')
    } catch (e) {
      setMessage(`Error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function removePosition(positionId) {
    if (!positionId) return
    setLoading(true)
    setMessage(null)
    try {
      await apiFetch(`/api/portfolio/positions/${positionId}`, { method: 'DELETE' })
      await loadPortfolio()
      setMessage('Position removed')
    } catch (e) {
      setMessage(`Error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function syncPaperPortfolio() {
    setLoading(true)
    setMessage(null)
    try {
      await apiFetch('/api/portfolio/sync-paper', { method: 'POST' })
      await Promise.all([loadPortfolio(), loadPaperAccount()])
      setMessage('Paper portfolio synced from Alpaca')
    } catch (e) {
      setMessage(`Error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function submitPaperOrder() {
    const s = symbol.trim()
    const q = orderQuantity.trim()
    const n = orderNotional.trim()
    if (!s || (!q && !n)) return

    setLoading(true)
    setMessage(null)
    try {
      const result = await apiFetch('/api/portfolio/paper-orders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: s,
          side: orderSide,
          quantity: q || null,
          notional: n || null,
        }),
      })
      setOrderQuantity('')
      setOrderNotional('')
      
      let synced = false
      try {
        synced = result?.sync_status === 'synced' ? true : await refreshOrderState()
      } catch (syncError) {
        console.error('Portfolio sync failed:', syncError)
        synced = false
      }
      
      if (synced) {
        setMessage(`${orderSide} paper order submitted and portfolio refreshed`)
      } else {
        setMessage(
          `${orderSide} order was accepted by Alpaca. Portfolio sync is still pending, so holdings may update shortly.`
        )
      }
    } catch (e) {
      setMessage(`Error: ${e?.message || 'Order submission failed'}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadPortfolio()
    loadPaperAccount()
  }, [])

  return (
    <AppShell title="Portfolio" subtitle="Live paper trading with Alpaca + synced local portfolio">
      <div className={styles.page}>
        <section className={styles.card} aria-label="Paper trading account">
          <p className={styles.cardTitle}>Paper Trading</p>
          <div className={styles.field}>
            <div className={styles.rowWrap}>
              <button type="button" className={styles.primary} onClick={syncPaperPortfolio} disabled={loading}>
                Sync from Alpaca
              </button>
              <button type="button" className={styles.secondary} onClick={loadPaperAccount} disabled={loading}>
                Refresh paper account
              </button>
            </div>
            {paperAccount ? (
              <div className={styles.list}>
                <div className={styles.item}>
                  <div className={styles.itemLeft}>
                    <div className={styles.itemTitle}>Account Status</div>
                    <div className={styles.itemMeta}>
                      {paperAccount.status || 'Unknown'} • Buying power {paperAccount.buying_power || '0'} • Cash {paperAccount.cash || '0'}
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <p className={styles.msg}>Paper account unavailable. Save and test Alpaca keys first.</p>
            )}
          </div>
        </section>

        {needsInit ? (
          <section className={styles.card} aria-label="Initialize portfolio">
            <p className={styles.cardTitle}>Initialize</p>
            <div className={styles.field}>
              <div className={styles.label}>Starting cash balance</div>
              <div className={styles.rowWrap}>
                <input
                  className={styles.input}
                  value={cash}
                  onChange={(e) => setCash(e.target.value)}
                  inputMode="decimal"
                  placeholder="10000"
                  aria-label="Starting cash balance"
                />
                <button
                  type="button"
                  className={styles.primary}
                  onClick={initialize}
                  disabled={loading || !cash.trim()}
                >
                  Initialize
                </button>
                <button
                  type="button"
                  className={styles.secondary}
                  onClick={loadPortfolio}
                  disabled={loading}
                >
                  Refresh
                </button>
              </div>
              <p className={styles.msg}>
                No portfolio yet. Initialize once, then add positions.
              </p>
            </div>
            {message ? <p className={messageClass}>{message}</p> : null}
          </section>
        ) : (
          <>
            <section className={styles.card} aria-label="Submit paper order">
              <p className={styles.cardTitle}>Submit Paper Order</p>
              <div className={styles.field}>
                <div className={styles.label}>Live paper order</div>
                <div className={styles.rowWrap}>
                  <input
                    className={styles.input}
                    value={symbol}
                    onChange={(e) => setSymbol(e.target.value)}
                    placeholder="AAPL"
                    autoCapitalize="characters"
                    aria-label="Order symbol"
                  />
                  <select
                    className={styles.select}
                    value={orderSide}
                    onChange={(e) => setOrderSide(e.target.value)}
                    aria-label="Order side"
                  >
                    <option value="BUY">BUY</option>
                    <option value="SELL">SELL</option>
                  </select>
                  <input
                    className={styles.input}
                    value={orderQuantity}
                    onChange={(e) => setOrderQuantity(e.target.value)}
                    inputMode="decimal"
                    placeholder="Quantity"
                    aria-label="Order quantity"
                  />
                  <input
                    className={styles.input}
                    value={orderNotional}
                    onChange={(e) => setOrderNotional(e.target.value)}
                    inputMode="decimal"
                    placeholder="Notional (optional for BUY)"
                    aria-label="Order notional"
                  />
                  <button
                    type="button"
                    className={styles.primary}
                    onClick={submitPaperOrder}
                    disabled={loading || !symbol.trim() || (!orderQuantity.trim() && !orderNotional.trim())}
                  >
                    Submit paper order
                  </button>
                </div>
                <p className={styles.msg}>Orders are sent to your Alpaca paper account, then synced back into the app portfolio and watchlist.</p>
              </div>
              {message ? <p className={messageClass}>{message}</p> : null}
            </section>

            <section className={styles.card} aria-label="Cash balance">
              <p className={styles.cardTitle}>Cash</p>
              <div className={styles.field}>
                <div className={styles.label}>Cash balance</div>
                <div className={styles.rowWrap}>
                  <input
                    className={styles.input}
                    value={cash}
                    onChange={(e) => setCash(e.target.value)}
                    inputMode="decimal"
                    aria-label="Cash balance"
                  />
                  <button
                    type="button"
                    className={styles.primary}
                    onClick={updateCash}
                    disabled={loading || !cash.trim()}
                  >
                    Save
                  </button>
                  <button
                    type="button"
                    className={styles.secondary}
                    onClick={loadPortfolio}
                    disabled={loading}
                  >
                    Refresh
                  </button>
                </div>
                {portfolio?.updated_at ? (
                  <p className={styles.msg}>Last updated: {formatDate(portfolio.updated_at)}</p>
                ) : null}
              </div>
              {message ? <p className={messageClass}>{message}</p> : null}
            </section>

            <section className={styles.card} aria-label="Holdings">
              <p className={styles.cardTitle}>Holdings</p>
              <div className={styles.field}>
                <div className={styles.label}>Add / update position</div>
                <div className={styles.rowWrap}>
                  <input
                    className={styles.input}
                    value={symbol}
                    onChange={(e) => setSymbol(e.target.value)}
                    placeholder="AAPL"
                    autoCapitalize="characters"
                    aria-label="Symbol"
                  />
                  <input
                    className={styles.input}
                    value={quantity}
                    onChange={(e) => setQuantity(e.target.value)}
                    inputMode="decimal"
                    placeholder="10"
                    aria-label="Quantity"
                  />
                  <input
                    className={styles.input}
                    value={avgPrice}
                    onChange={(e) => setAvgPrice(e.target.value)}
                    inputMode="decimal"
                    placeholder="Avg price (optional)"
                    aria-label="Average price"
                  />
                  <button
                    type="button"
                    className={styles.primary}
                    onClick={upsertPosition}
                    disabled={loading || !symbol.trim() || !quantity.trim()}
                  >
                    Save position
                  </button>
                </div>
                <p className={styles.msg}>
                  Saving the same symbol overwrites that local position. Use paper orders above for live Alpaca paper trading.
                </p>
              </div>

              <div className={styles.list}>
                {portfolio?.positions?.length ? (
                  portfolio.positions.map((p) => (
                    <div key={p.id} className={styles.item}>
                      <div className={styles.itemLeft}>
                        <div className={styles.itemTitle}>{p.symbol}</div>
                        <div className={styles.itemMeta}>
                          Qty {p.quantity} • Avg {p.avg_price}
                          {p.updated_at ? ` • Updated ${formatDate(p.updated_at)}` : ''}
                        </div>
                      </div>
                      <div className={styles.rowWrap}>
                        <button
                          type="button"
                          className={styles.danger}
                          onClick={() => removePosition(p.id)}
                          disabled={loading}
                        >
                          Remove
                        </button>
                      </div>
                    </div>
                  ))
                ) : (
                  <p className={styles.msg}>No positions yet.</p>
                )}
              </div>
            </section>
          </>
        )}
      </div>
    </AppShell>
  )
}
