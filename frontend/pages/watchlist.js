import { useEffect, useMemo, useRef, useState } from 'react'
import AppShell from '../components/AppShell'
import InfoTip from '../components/InfoTip'
import Link from 'next/link'
import { apiFetch } from '../lib/api'
import { PROVIDER_META, symbolProvider, useBroker } from '../lib/broker'
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

const STOCK_DIRECTORY_URL = 'https://stockanalysis.com/stocks/'

// Search-as-you-type over Yahoo Finance: matches tickers and company names ("berkshire" -> BRK-B).
function SymbolSearch({ value, onChange, onSubmit, disabled }) {
  const [results, setResults] = useState([])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [searching, setSearching] = useState(false)
  const requestId = useRef(0)

  useEffect(() => {
    const query = value.trim()
    if (query.length < 1) {
      setResults([])
      setSearching(false)
      return undefined
    }
    const id = ++requestId.current
    setSearching(true)
    const timer = setTimeout(async () => {
      try {
        const data = await apiFetch(`/api/market/search?q=${encodeURIComponent(query)}`)
        if (id !== requestId.current) return
        setResults(Array.isArray(data?.results) ? data.results : [])
        setActive(-1)
      } catch {
        if (id === requestId.current) setResults([])
      } finally {
        if (id === requestId.current) setSearching(false)
      }
    }, 250)
    return () => clearTimeout(timer)
  }, [value])

  function choose(item) {
    onChange(item.symbol)
    setOpen(false)
    setResults([])
  }

  function onKeyDown(event) {
    const showing = open && results.length > 0
    if (event.key === 'ArrowDown' && showing) {
      event.preventDefault()
      setActive((i) => (i + 1) % results.length)
    } else if (event.key === 'ArrowUp' && showing) {
      event.preventDefault()
      setActive((i) => (i <= 0 ? results.length - 1 : i - 1))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      if (showing && active >= 0) choose(results[active])
      else onSubmit()
    } else if (event.key === 'Escape') {
      setOpen(false)
    }
  }

  const showList = open && value.trim() && (results.length > 0 || !searching)

  return (
    <div className={styles.search}>
      <input
        className={styles.input}
        value={value}
        onChange={(e) => {
          onChange(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
        placeholder="Search ticker or company, e.g. AAPL, Berkshire, PTT"
        autoCapitalize="characters"
        autoComplete="off"
        spellCheck={false}
        disabled={disabled}
        role="combobox"
        aria-label="Ticker symbol or company name"
        aria-expanded={Boolean(showList)}
        aria-controls="symbol-search-results"
        aria-activedescendant={active >= 0 ? `symbol-option-${active}` : undefined}
      />
      {showList ? (
        <ul className={styles.searchList} id="symbol-search-results" role="listbox">
          {results.length ? results.map((item, index) => (
            <li
              key={item.symbol}
              id={`symbol-option-${index}`}
              role="option"
              aria-selected={index === active}
              className={`${styles.searchItem} ${index === active ? styles.searchItemActive : ''}`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => choose(item)}
              onMouseEnter={() => setActive(index)}
            >
              <span className={styles.searchSymbol}>{item.symbol}</span>
              <span className={styles.searchName}>{item.name}</span>
              <span className={styles.searchMeta}>{[item.exchange, item.type].filter(Boolean).join(' · ')}</span>
            </li>
          )) : (
            <li className={styles.searchEmpty}>
              No matches. Check the ticker on{' '}
              <a href={STOCK_DIRECTORY_URL} target="_blank" rel="noopener noreferrer" onMouseDown={(e) => e.preventDefault()}>
                stockanalysis.com
              </a>
            </li>
          )}
        </ul>
      ) : null}
    </div>
  )
}

// Stays open while the pointer is inside it (and pins open on tap), so the link can be clicked.
function TickerGuideTip() {
  return (
    <InfoTip label="Not sure of the ticker?" icon="?">
        <span className={styles.guideTitle}>Not sure of the ticker?</span>
        <span className={styles.guideText}>
          Type a company name in the box, or browse every US-listed stock (NYSE, NASDAQ, NYSE American) on{' '}
          <a href={STOCK_DIRECTORY_URL} target="_blank" rel="noopener noreferrer" className={styles.guideLink}>
            stockanalysis.com/stocks ↗
          </a>{' '}
          and enter the symbol shown there.
        </span>
        <span className={styles.guideFormats}>
          <span><code>AAPL</code> US stocks</span>
          <span><code>BRK.B</code> share classes</span>
          <span><code>PTT.BK</code> Thai SET</span>
          <span><code>BTC-USD</code> crypto</span>
          <span><code>HKDTHB=X</code> currencies</span>
        </span>
    </InfoTip>
  )
}

export default function WatchlistPage() {
  const { provider, meta } = useBroker()
  const [items, setItems] = useState([])
  const [symbol, setSymbol] = useState('')
  const [notes, setNotes] = useState('')
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState(null)
  const [editingId, setEditingId] = useState(null)
  const [editSymbol, setEditSymbol] = useState('')
  const [editNotes, setEditNotes] = useState('')

  const messageClass = useMemo(() => {
    if (!message) return styles.msg
    if (String(message).toLowerCase().startsWith('error')) return `${styles.msg} ${styles.msgError}`
    return `${styles.msg} ${styles.msgSuccess}`
  }, [message])

  async function refresh() {
    setLoading(true)
    setMessage(null)
    try {
      const data = await apiFetch('/api/watchlist')
      setItems(Array.isArray(data) ? data : [])
    } catch (e) {
      setMessage(`Error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function addItem() {
    const s = symbol.trim()
    if (!s) return
    setLoading(true)
    setMessage(null)
    try {
      const added = await apiFetch('/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: s, notes: notes.trim() || null }),
      })
      setSymbol('')
      setNotes('')
      await refresh()
      const addedProvider = symbolProvider(added?.symbol || s)
      setMessage(
        addedProvider === provider
          ? `Added ${added?.symbol || s}`
          : `Added ${added?.symbol || s}. It trades on ${PROVIDER_META[addedProvider].label}, so it appears when you switch to ${PROVIDER_META[addedProvider].label} in Profile & Settings.`,
      )
    } catch (e) {
      setMessage(`Error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  function startEdit(item) {
    setEditingId(item.id)
    setEditSymbol(item.symbol || '')
    setEditNotes(item.notes || '')
    setMessage(null)
  }

  function cancelEdit() {
    setEditingId(null)
    setEditSymbol('')
    setEditNotes('')
  }

  async function saveEdit() {
    if (!editingId) return
    const s = editSymbol.trim()
    if (!s) return
    setLoading(true)
    setMessage(null)
    try {
      await apiFetch(`/api/watchlist/${editingId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: s, notes: editNotes.trim() || null }),
      })
      cancelEdit()
      await refresh()
      setMessage('Saved')
    } catch (e) {
      setMessage(`Error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function removeItem(id) {
    if (!id) return
    setLoading(true)
    setMessage(null)
    try {
      await apiFetch(`/api/watchlist/${id}`, { method: 'DELETE' })
      await refresh()
      setMessage('Removed')
    } catch (e) {
      setMessage(`Error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  const visibleItems = items.filter((item) => symbolProvider(item.symbol, item.provider) === provider)
  const hiddenCount = items.length - visibleItems.length

  return (
    <AppShell title="Watchlist" subtitle="Manage tracked assets">
      <div className={styles.page}>
        <section className={styles.card} aria-label="Add watchlist item">
          <p className={styles.cardTitle}>Add Asset</p>
          <div className={styles.field}>
            <div className={styles.labelRow}>
              <div className={styles.label}>Ticker symbol or company name</div>
              <TickerGuideTip />
            </div>
            <div className={styles.rowWrap}>
              <SymbolSearch value={symbol} onChange={setSymbol} onSubmit={addItem} disabled={loading} />
              <button
                type="button"
                className={styles.primary}
                onClick={addItem}
                disabled={loading || !symbol.trim()}
              >
                Add
              </button>
              <button
                type="button"
                className={styles.secondary}
                onClick={refresh}
                disabled={loading}
              >
                Refresh
              </button>
            </div>
          </div>
          <div className={styles.field}>
            <div className={styles.label}>Notes (optional)</div>
            <input
              className={styles.input}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Optional notes"
              aria-label="Notes"
            />
          </div>
          {message ? <p className={messageClass}>{message}</p> : null}
        </section>

        <section className={styles.card} aria-label="Your watchlist">
          <p className={styles.cardTitle}>Your Watchlist · {meta.label} ({meta.market})</p>
          {hiddenCount ? (
            <p className={styles.muted} style={{ margin: '6px 0 0' }}>
              {hiddenCount} {hiddenCount === 1 ? 'asset' : 'assets'} from the other provider {hiddenCount === 1 ? 'is' : 'are'} hidden.{' '}
              <Link href="/profile" className={styles.guideLink}>Switch provider</Link>
            </p>
          ) : null}
          <div className={styles.list}>
            {visibleItems.length ? (
              visibleItems.map((item) => (
                <div key={item.id} className={styles.item}>
                  {editingId === item.id ? (
                    <div style={{ flex: 1, display: 'grid', gap: 10 }}>
                      <div className={styles.rowWrap}>
                        <input
                          className={styles.input}
                          value={editSymbol}
                          onChange={(e) => setEditSymbol(e.target.value)}
                          placeholder="AAPL"
                          autoCapitalize="characters"
                          aria-label="Edit symbol"
                        />
                        <button
                          type="button"
                          className={styles.primary}
                          onClick={saveEdit}
                          disabled={loading || !editSymbol.trim()}
                        >
                          Save
                        </button>
                        <button
                          type="button"
                          className={styles.secondary}
                          onClick={cancelEdit}
                          disabled={loading}
                        >
                          Cancel
                        </button>
                      </div>
                      <input
                        className={styles.input}
                        value={editNotes}
                        onChange={(e) => setEditNotes(e.target.value)}
                        placeholder="Notes"
                        aria-label="Edit notes"
                      />
                    </div>
                  ) : (
                    <>
                      <div className={styles.itemLeft}>
                        <div className={styles.itemTitle}>{item.symbol}</div>
                        <div className={styles.itemMeta}>
                          {item.notes ? item.notes : 'No notes'}
                          {item.created_at ? ` • Added ${formatDate(item.created_at)}` : ''}
                        </div>
                      </div>
                      <div className={styles.rowWrap}>
                        <button
                          type="button"
                          className={styles.secondary}
                          onClick={() => startEdit(item)}
                          disabled={loading}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className={styles.danger}
                          onClick={() => removeItem(item.id)}
                          disabled={loading}
                        >
                          Remove
                        </button>
                      </div>
                    </>
                  )}
                </div>
              ))
            ) : (
              <p className={styles.msg}>No {meta.market} in your watchlist yet. Add one above.</p>
            )}
          </div>
        </section>
      </div>
    </AppShell>
  )
}
