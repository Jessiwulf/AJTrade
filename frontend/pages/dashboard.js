import { useMemo, useState } from 'react'
import AppShell from '../components/AppShell'
import styles from '../styles/Dashboard.module.css'

function DonutChart({ percent = 0.62 }) {
  const p = Math.max(0, Math.min(1, Number(percent) || 0))
  const r = 34
  const c = 2 * Math.PI * r
  const dash = c * p
  const gap = c - dash

  return (
    <svg width="90" height="90" viewBox="0 0 90 90" aria-hidden="true">
      <defs>
        <linearGradient id="ajDonut" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--aj-cta-from)" />
          <stop offset="1" stopColor="var(--aj-cta-to)" />
        </linearGradient>
      </defs>
      <circle cx="45" cy="45" r={r} fill="none" stroke="var(--aj-border)" strokeWidth="10" />
      <circle
        cx="45"
        cy="45"
        r={r}
        fill="none"
        stroke="url(#ajDonut)"
        strokeWidth="10"
        strokeLinecap="round"
        strokeDasharray={`${dash} ${gap}`}
        transform="rotate(-90 45 45)"
      />
    </svg>
  )
}

function AreaChart() {
  return (
    <svg width="100%" height="260" viewBox="0 0 700 260" aria-hidden="true">
      <defs>
        <linearGradient id="ajArea" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--aj-cta-to)" stopOpacity="0.32" />
          <stop offset="1" stopColor="var(--aj-cta-to)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path
        d="M18 188 C 86 132, 128 154, 184 122 C 236 92, 286 112, 334 78 C 382 44, 430 70, 486 42 C 542 14, 612 54, 682 26"
        fill="none"
        stroke="var(--aj-cta-to)"
        strokeWidth="4"
        strokeLinecap="round"
      />
      <path
        d="M18 188 C 86 132, 128 154, 184 122 C 236 92, 286 112, 334 78 C 382 44, 430 70, 486 42 C 542 14, 612 54, 682 26 L 682 242 L 18 242 Z"
        fill="url(#ajArea)"
      />
      <g stroke="var(--aj-border-soft)" strokeWidth="1">
        <line x1="18" y1="242" x2="682" y2="242" />
        <line x1="18" y1="60" x2="682" y2="60" />
        <line x1="18" y1="120" x2="682" y2="120" />
        <line x1="18" y1="180" x2="682" y2="180" />
      </g>
    </svg>
  )
}

export default function Dashboard() {
  const [asset, setAsset] = useState('AAPL')
  const [side, setSide] = useState('buy')
  const [amount, setAmount] = useState('1000')
  const [range, setRange] = useState('ALL')

  const watchlist = useMemo(
    () => [
      { symbol: 'AAPL', change: 0.35 },
      { symbol: 'TSLA', change: -0.25 },
      { symbol: 'MSFT', change: 1.65 },
    ],
    []
  )

  return (
    <AppShell
      title="Trade Dashboard"
      subtitle="Portfolio monitoring"
    >
      <div className={styles.grid}>
        <div className={styles.columns}>
          <section className={styles.card} aria-label="Portfolio overview">
            <p className={styles.cardTitle}>Portfolio Overview</p>
            <p className={styles.bigValue}>$1,221,223.00</p>
            <div className={styles.subRow}>
              <span>Total value</span>
              <span className={styles.pill}>▲ 26.25%</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 12 }}>
              <DonutChart percent={0.62} />
              <div style={{ display: 'grid', gap: 8 }}>
                <div style={{ color: 'var(--aj-text-muted)', fontSize: 13 }}>
                  Balance
                </div>
                <div style={{ fontWeight: 900, letterSpacing: '-0.02em' }}>$109,735.00</div>
                <div style={{ color: 'var(--aj-text-muted)', fontSize: 13 }}>
                  Performance
                </div>
                <div style={{ fontWeight: 900, color: 'var(--aj-positive)' }}>+26.25%</div>
              </div>
            </div>
          </section>

          <section className={styles.card} aria-label="Watchlist">
            <p className={styles.cardTitle}>Watchlist</p>
            <div className={styles.watchlist}>
              {watchlist.map((w) => (
                <div key={w.symbol} className={styles.watchItem}>
                  <span className={styles.symbol}>{w.symbol}</span>
                  <span className={w.change >= 0 ? styles.changePos : styles.changeNeg}>
                    {w.change >= 0 ? '+' : ''}
                    {w.change.toFixed(2)}%
                  </span>
                </div>
              ))}
            </div>
          </section>
        </div>

        <section className={styles.card} aria-label="Selected asset chart">
          <div className={styles.centerHeader}>
            <div>
              <p className={styles.cardTitle} style={{ marginBottom: 6 }}>
                {asset}
              </p>
              <h2 className={styles.assetName}>{asset}</h2>
            </div>
            <select
              className={styles.miniSelect}
              value={range}
              onChange={(e) => setRange(e.target.value)}
              aria-label="Chart range"
            >
              <option value="AN">AN</option>
              <option value="ALL">ALL</option>
            </select>
          </div>
          <AreaChart />
        </section>

        <section className={styles.card} aria-label="Trade now">
          <p className={styles.cardTitle}>Trade Now</p>
          <div className={styles.tradeGrid}>
            <div>
              <div className={styles.label}>Asset</div>
              <select
                className={styles.select}
                value={asset}
                onChange={(e) => setAsset(e.target.value)}
                aria-label="Select asset"
              >
                <option value="AAPL">AAPL</option>
                <option value="TSLA">TSLA</option>
                <option value="MSFT">MSFT</option>
              </select>
            </div>

            <div>
              <div className={styles.label}>Type</div>
              <div className={styles.toggleRow} role="group" aria-label="Buy or sell">
                <button
                  type="button"
                  className={`${styles.toggle} ${side === 'buy' ? styles.toggleActive : ''}`}
                  onClick={() => setSide('buy')}
                >
                  Buy
                </button>
                <button
                  type="button"
                  className={`${styles.toggle} ${side === 'sell' ? styles.toggleActive : ''}`}
                  onClick={() => setSide('sell')}
                >
                  Sell
                </button>
              </div>
            </div>

            <div>
              <div className={styles.label}>Amount</div>
              <input
                className={styles.input}
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                inputMode="numeric"
                aria-label="Amount"
              />
            </div>

            <button type="button" className={styles.primary}>
              PLACE ORDER
            </button>
          </div>
        </section>
      </div>
    </AppShell>
  )
}
