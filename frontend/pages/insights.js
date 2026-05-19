import AppShell from '../components/AppShell'
import styles from '../styles/Insights.module.css'

const assets = [
  {
    symbol: 'MSFT',
    recommendation: 'Strong Buy',
    confidence: 92,
    rationale: [
      'Positive earnings sentiment and strong fundamentals',
      'Momentum aligns with trend-following signals',
      'News flow suggests improving risk-adjusted outlook',
    ],
  },
  {
    symbol: 'TSLA',
    recommendation: 'Strong Buy',
    confidence: 92,
    rationale: [
      'Strong sentiment pickup across recent coverage',
      'High volatility balanced by tight risk controls',
      'Technical structure supports continuation bias',
    ],
  },
]

export default function Insights() {
  return (
    <AppShell
      title="AI Insights"
      subtitle="Sentiment & recommendations"
    >
      <div className={styles.list}>
        {assets.map((a) => (
          <section key={a.symbol} className={styles.card} aria-label={`${a.symbol} insight`}>
            <div>
              <h2 className={styles.asset}>{a.symbol}</h2>

              <div className={styles.meta}>
                <div>
                  <p className={styles.label}>AI Recommendation</p>
                  <p className={`${styles.reco} ${styles.recoStrongBuy}`}>{a.recommendation}</p>
                </div>

                <div>
                  <p className={styles.label}>Rationale</p>
                  <ul className={styles.rationale}>
                    {a.rationale.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>

            <aside className={styles.side} aria-label="Automation">
              <div>
                <p className={styles.label}>Confidence Score</p>
                <p className={styles.confidence}>{a.confidence}%</p>
              </div>
              <button type="button" className={styles.primary}>
                AUTOMATE
              </button>
            </aside>
          </section>
        ))}
      </div>
    </AppShell>
  )
}
