import { useMemo, useState } from 'react'
import AppShell from '../components/AppShell'
import styles from '../styles/Automated.module.css'

const STRATEGIES = ['Trend Following', 'Mean Reversion', 'News Momentum']

function riskLabel(maxDrawdownPercent) {
  const v = Number(maxDrawdownPercent) || 0
  if (v <= 10) return 'Low'
  if (v <= 25) return 'Medium'
  return 'High'
}

export default function Automated() {
  const [strategy, setStrategy] = useState('Trend Following')
  const [maxDrawdown, setMaxDrawdown] = useState(20)
  const [positionSize, setPositionSize] = useState(250)
  const [capital, setCapital] = useState('5000')
  const [active, setActive] = useState(false)

  const risk = useMemo(() => riskLabel(maxDrawdown), [maxDrawdown])

  return (
    <AppShell
      title="Automated Trading Setup"
      subtitle="Step-by-step strategy wizard"
    >
      <div className={styles.layout}>
        <div style={{ display: 'grid', gap: 16 }}>
          <section className={styles.stepCard} aria-label="Step 1 choose strategy">
            <div className={styles.row}>
              <span className={styles.stepNum}>1</span>
              <h2 className={styles.stepTitle}>Step 1: Choose Strategy</h2>
            </div>
            <div className={styles.field}>
              <div className={styles.label}>Strategy</div>
              <select
                className={styles.select}
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                aria-label="Choose strategy"
              >
                {STRATEGIES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
          </section>

          <section className={styles.stepCard} aria-label="Step 2 define risk parameters">
            <div className={styles.row}>
              <span className={styles.stepNum}>2</span>
              <h2 className={styles.stepTitle}>Step 2: Define Risk Parameters</h2>
            </div>

            <div className={styles.field}>
              <div className={styles.label}>Max Drawdown (%)</div>
              <div className={styles.sliderRow}>
                <input
                  className={styles.range}
                  type="range"
                  min={0}
                  max={50}
                  value={maxDrawdown}
                  onChange={(e) => setMaxDrawdown(Number(e.target.value))}
                  aria-label="Max drawdown"
                />
                <div className={styles.pill}>{maxDrawdown}%</div>
              </div>
            </div>

            <div className={styles.field}>
              <div className={styles.label}>Position Size</div>
              <div className={styles.sliderRow}>
                <input
                  className={styles.range}
                  type="range"
                  min={0}
                  max={1000}
                  step={25}
                  value={positionSize}
                  onChange={(e) => setPositionSize(Number(e.target.value))}
                  aria-label="Position size"
                />
                <div className={styles.pill}>{positionSize}</div>
              </div>
            </div>
          </section>

          <section className={styles.stepCard} aria-label="Step 3 allocate capital">
            <div className={styles.row}>
              <span className={styles.stepNum}>3</span>
              <h2 className={styles.stepTitle}>Step 3: Allocate Capital</h2>
            </div>
            <div className={styles.field}>
              <div className={styles.label}>Allocate Capital</div>
              <input
                className={styles.input}
                value={capital}
                onChange={(e) => setCapital(e.target.value)}
                inputMode="numeric"
                aria-label="Allocate capital"
              />
            </div>
          </section>
        </div>

        <aside className={styles.summary} aria-label="Summary">
          <p className={styles.summaryTitle}>Summary</p>
          <div className={styles.summaryGrid}>
            <div className={styles.kv}>
              <div className={styles.k}>Strategy</div>
              <div className={styles.v}>{strategy}</div>
            </div>
            <div className={styles.kv}>
              <div className={styles.k}>Risk Level</div>
              <div className={styles.v}>{risk}</div>
            </div>
            <div className={styles.kv}>
              <div className={styles.k}>Capital</div>
              <div className={styles.v}>${capital || '0'}</div>
            </div>
          </div>

          <div className={styles.toggleRow}>
            <div>
              <div className={styles.k}>Activate Strategy</div>
              <div className={styles.v}>{active ? 'Enabled' : 'Disabled'}</div>
            </div>
            <button
              type="button"
              className={`${styles.switch} ${active ? styles.switchOn : ''}`}
              onClick={() => setActive((v) => !v)}
              aria-label="Activate strategy"
              aria-pressed={active}
            >
              <span className={styles.knob} />
            </button>
          </div>
        </aside>
      </div>
    </AppShell>
  )
}
