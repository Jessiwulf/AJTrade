import Link from 'next/link'
import TopNav from '../components/TopNav'
import styles from '../styles/Landing.module.css'

function TrendMiniChart() {
  return (
    <svg width="100%" height="120" viewBox="0 0 320 120" aria-hidden="true">
      <defs>
        <linearGradient id="ajTrendFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--aj-cta-to)" stopOpacity="0.35" />
          <stop offset="1" stopColor="var(--aj-cta-to)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path
        d="M10 86 C 52 58, 86 72, 120 52 C 154 32, 196 46, 230 30 C 264 14, 290 26, 310 18"
        fill="none"
        stroke="var(--aj-cta-to)"
        strokeWidth="4"
        strokeLinecap="round"
      />
      <path
        d="M10 86 C 52 58, 86 72, 120 52 C 154 32, 196 46, 230 30 C 264 14, 290 26, 310 18 L 310 112 L 10 112 Z"
        fill="url(#ajTrendFill)"
      />
      <circle cx="120" cy="52" r="4.5" fill="var(--aj-cta-from)" />
      <circle cx="230" cy="30" r="4.5" fill="var(--aj-cta-from)" />
    </svg>
  )
}

function CandleMiniChart() {
  const bars = [
    { x: 24, hi: 18, lo: 78, o: 64, c: 40 },
    { x: 58, hi: 26, lo: 84, o: 52, c: 68 },
    { x: 92, hi: 22, lo: 74, o: 58, c: 36 },
    { x: 126, hi: 14, lo: 70, o: 40, c: 58 },
    { x: 160, hi: 18, lo: 80, o: 70, c: 44 },
    { x: 194, hi: 10, lo: 66, o: 34, c: 50 },
    { x: 228, hi: 16, lo: 76, o: 56, c: 30 },
    { x: 262, hi: 8, lo: 62, o: 28, c: 42 },
  ]
  return (
    <svg width="100%" height="120" viewBox="0 0 300 120" aria-hidden="true">
      {bars.map((b) => {
        const up = b.c < b.o
        const y1 = Math.min(b.o, b.c)
        const y2 = Math.max(b.o, b.c)
        return (
          <g key={b.x}>
            <line
              x1={b.x}
              x2={b.x}
              y1={b.hi}
              y2={b.lo}
              stroke="var(--aj-border-strong)"
              strokeWidth="2"
            />
            <rect
              x={b.x - 7}
              y={y1}
              width="14"
              height={Math.max(10, y2 - y1)}
              rx="6"
              fill={up ? 'var(--aj-cta-to)' : 'var(--aj-indigo-soft)'}
            />
          </g>
        )
      })}
    </svg>
  )
}

function FeatureIcon({ kind }) {
  const common = {
    width: 22,
    height: 22,
    viewBox: '0 0 24 24',
    'aria-hidden': true,
  }
  if (kind === 'market') {
    return (
      <svg {...common}>
        <path
          d="M4 18V6m0 12h16"
          fill="none"
          stroke="var(--aj-indigo)"
          strokeWidth="2"
          strokeLinecap="round"
        />
        <path
          d="M7 15l3-4 4 3 5-7"
          fill="none"
          stroke="var(--aj-cta-to)"
          strokeWidth="2.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    )
  }
  if (kind === 'auto') {
    return (
      <svg {...common}>
        <path
          d="M7 7h10M7 12h6M7 17h10"
          fill="none"
          stroke="var(--aj-indigo)"
          strokeWidth="2"
          strokeLinecap="round"
        />
        <path
          d="M17 13l2 2 4-4"
          fill="none"
          stroke="var(--aj-cta-to)"
          strokeWidth="2.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    )
  }
  return (
    <svg {...common}>
      <path
        d="M12 2l8 4v6c0 5-3.4 9.3-8 10-4.6-.7-8-5-8-10V6l8-4z"
        fill="none"
        stroke="var(--aj-indigo)"
        strokeWidth="2"
      />
      <path
        d="M8 12l2.5 2.5L16 9"
        fill="none"
        stroke="var(--aj-cta-to)"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export default function Home() {
  return (
    <div className={styles.page}>
      <TopNav />

      <main className={styles.hero}>
        <div className={styles.heroGrid}>
          <div>
            <h1 className={styles.headline}>AJTrade: AI Asset Analysis and Automated Trading</h1>
            <p className={styles.tagline}>
              Trade at the Speed of News, Protected by Intelligence.
            </p>
            <div className={styles.ctaRow}>
              <Link href="/signup" className={styles.cta}>
                GET STARTED
              </Link>
              <span className={styles.subNote}>
                Light mode, minimalist, and accessible.
              </span>
            </div>
          </div>

          <div className={styles.visual} aria-hidden="true">
            <div className={`${styles.floatCard} ${styles.cardA}`}>
              <p className={styles.cardTitle}>Candle snapshot</p>
              <CandleMiniChart />
            </div>
            <div className={`${styles.floatCard} ${styles.cardB}`}>
              <p className={styles.cardTitle}>AI trend line</p>
              <TrendMiniChart />
            </div>
            <div className={`${styles.floatCard} ${styles.cardC}`}>
              <p className={styles.cardTitle}>Signals</p>
              <div style={{ display: 'grid', gap: 10 }}>
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    padding: 10,
                    borderRadius: 14,
                    background: 'var(--aj-surface)',
                    border: '1px solid var(--aj-border)',
                  }}
                >
                  <span style={{ fontWeight: 700, color: 'var(--aj-indigo)' }}>AAPL</span>
                  <span style={{ fontWeight: 800, color: 'var(--aj-positive)' }}>BUY</span>
                </div>
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    padding: 10,
                    borderRadius: 14,
                    background: 'var(--aj-surface)',
                    border: '1px solid var(--aj-border)',
                  }}
                >
                  <span style={{ fontWeight: 700, color: 'var(--aj-indigo)' }}>TSLA</span>
                  <span style={{ fontWeight: 800, color: 'var(--aj-negative)' }}>HOLD</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </main>

      <section className={styles.features} aria-label="Highlights">
        <div className={styles.feature}>
          <div className={styles.featureHead}>
            <FeatureIcon kind="market" />
            <h3 className={styles.featureTitle}>Market Intelligence</h3>
          </div>
          <p className={styles.featureText}>
            Real-time market insights powered by adaptive AI models.
          </p>
        </div>
        <div className={styles.feature}>
          <div className={styles.featureHead}>
            <FeatureIcon kind="auto" />
            <h3 className={styles.featureTitle}>Automated Trading</h3>
          </div>
          <p className={styles.featureText}>
            Hassle-free automation from signal selection to execution.
          </p>
        </div>
        <div className={styles.feature}>
          <div className={styles.featureHead}>
            <FeatureIcon kind="risk" />
            <h3 className={styles.featureTitle}>Risk Management</h3>
          </div>
          <p className={styles.featureText}>
            Sophisticated controls to optimize exposure and manage risk.
          </p>
        </div>
      </section>

      <footer className={styles.footer}>
        Created by Jirapat Sereerat &amp; Atiwit Tin Intasarn | Advisor: Asst.Prof.
        Tisinee Surapunt
      </footer>
    </div>
  )
}
