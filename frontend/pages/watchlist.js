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

export default function WatchlistPage() {
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
      await apiFetch('/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: s, notes: notes.trim() || null }),
      })
      setSymbol('')
      setNotes('')
      await refresh()
      setMessage('Added')
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

  return (
    <AppShell title="Watchlist" subtitle="Manage tracked assets">
      <div className={styles.page}>
        <section className={styles.card} aria-label="Add watchlist item">
          <p className={styles.cardTitle}>Add Asset</p>
          <div className={styles.field}>
            <div className={styles.label}>Ticker symbol</div>
            <div className={styles.rowWrap}>
              <input
                className={styles.input}
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                placeholder="AAPL"
                autoCapitalize="characters"
                aria-label="Ticker symbol"
              />
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
          <p className={styles.cardTitle}>Your Watchlist</p>
          <div className={styles.list}>
            {items.length ? (
              items.map((item) => (
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
              <p className={styles.msg}>No assets yet. Add one above.</p>
            )}
          </div>
        </section>
      </div>
    </AppShell>
  )
}
