import { useEffect, useMemo, useState } from 'react'
import AppShell from '../components/AppShell'
import { apiFetch } from '../lib/api'
import styles from '../styles/Manage.module.css'

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
  const [needsInit, setNeedsInit] = useState(false)
  const [cash, setCash] = useState('')
  const [symbol, setSymbol] = useState('')
  const [quantity, setQuantity] = useState('')
  const [avgPrice, setAvgPrice] = useState('')
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState(null)

  const messageClass = useMemo(() => {
    if (!message) return styles.msg
    if (String(message).toLowerCase().startsWith('error')) return `${styles.msg} ${styles.msgError}`
    return `${styles.msg} ${styles.msgSuccess}`
  }, [message])

  async function loadPortfolio() {
    setLoading(true)
    setMessage(null)
    try {
      const data = await apiFetch('/api/portfolio')
      setPortfolio(data)
      setNeedsInit(false)
      setCash(String(data.cash_balance ?? ''))
    } catch (e) {
      const msg = String(e?.message || '')
      if (msg.toLowerCase().includes('not initialized')) {
        setPortfolio(null)
        setNeedsInit(true)
        setCash('')
      } else {
        setMessage(`Error: ${msg}`)
      }
    } finally {
      setLoading(false)
    }
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

  useEffect(() => {
    loadPortfolio()
  }, [])

  return (
    <AppShell title="Portfolio" subtitle="Simulated portfolio (cash + holdings)">
      <div className={styles.page}>
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
                  Saving the same symbol overwrites that position.
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
