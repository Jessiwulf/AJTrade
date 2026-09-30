import { useMemo, useState } from 'react'
import styles from '../styles/Portfolio.module.css'

// Validated categorical order (fixed, never cycled). Holdings past the 7th fold into "Other" so the bar
// and legend stay readable with any number of holdings; cash and "Other" use neutrals so they never
// look like a holding.
const SERIES_CLASSES = ['s1', 's2', 's3', 's4', 's5', 's6', 's7']

function formatMoney(value) {
  const n = Number(value) || 0
  return n.toLocaleString(undefined, { style: 'currency', currency: 'USD' })
}

/** holdings: [{ symbol, value }] sorted largest first. */
export default function PortfolioAllocation({ holdings, cash, total }) {
  const [hover, setHover] = useState(null)
  const segments = useMemo(() => {
    const top = holdings.slice(0, SERIES_CLASSES.length)
    const rest = holdings.slice(SERIES_CLASSES.length)
    const list = top.map((h, index) => ({ key: h.symbol, label: h.symbol, value: h.value, cls: styles[SERIES_CLASSES[index]] }))
    const otherValue = rest.reduce((sum, h) => sum + h.value, 0)
    if (otherValue > 0) {
      list.push({
        key: '__other',
        label: `Other (${rest.length})`,
        title: rest.map((h) => h.symbol).join(', '),
        value: otherValue,
        cls: styles.sOther,
      })
    }
    if (cash > 0) list.push({ key: '__cash', label: 'Cash', value: cash, cls: styles.sCash })
    return list.filter((s) => s.value > 0).map((s) => ({ ...s, pct: total > 0 ? (s.value / total) * 100 : 0 }))
  }, [holdings, cash, total])

  if (!segments.length) return <p className={styles.muted}>Nothing to allocate yet.</p>

  return (
    <div className={styles.alloc}>
      <div className={styles.allocBar} role="img" aria-label={segments.map((s) => `${s.label} ${s.pct.toFixed(1)}%`).join(', ')}>
        {segments.map((s) => (
          <span
            key={s.key}
            className={`${styles.allocSeg} ${s.cls} ${hover && hover !== s.key ? styles.allocDim : ''}`}
            style={{ flexGrow: s.value }}
            onMouseEnter={() => setHover(s.key)}
            onMouseLeave={() => setHover(null)}
            title={`${s.label}: ${formatMoney(s.value)} (${s.pct.toFixed(1)}%)`}
          />
        ))}
      </div>
      <ul className={styles.allocLegend}>
        {segments.map((s) => (
          <li
            key={s.key}
            className={hover && hover !== s.key ? styles.allocDim : ''}
            onMouseEnter={() => setHover(s.key)}
            onMouseLeave={() => setHover(null)}
            title={s.title || undefined}
          >
            <i className={`${styles.swatch} ${s.cls}`} aria-hidden="true" />
            <span className={styles.allocName}>{s.label}</span>
            <span className={styles.allocPct}>{s.pct.toFixed(1)}%</span>
            <span className={styles.allocValue}>{formatMoney(s.value)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
