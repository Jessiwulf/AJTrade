import { useMemo, useState } from 'react'
import styles from '../styles/Analytics.module.css'

const RANGES = [7, 14, 30]
// Daily averages rarely pass +/-0.6, so the colour ramp reaches full strength there.
const FULL_SCALE = 0.6
const NEUTRAL = [236, 239, 243]
const BULLISH = [21, 128, 61]
const BEARISH = [185, 28, 28]

function dayKey(date) {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

function lastDays(count) {
  const days = []
  const today = new Date()
  for (let i = count - 1; i >= 0; i -= 1) {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - i)
    days.push({ key: dayKey(d), date: d })
  }
  return days
}

// Diverging scale: bearish red <- neutral grey -> bullish green.
function cellColor(avg) {
  const t = Math.min(Math.abs(avg) / FULL_SCALE, 1)
  const target = avg >= 0 ? BULLISH : BEARISH
  const rgb = NEUTRAL.map((n, i) => Math.round(n + (target[i] - n) * t))
  return { background: `rgb(${rgb.join(',')})`, color: t > 0.55 ? '#fff' : 'var(--aj-text)' }
}

function formatScore(avg) {
  return `${avg > 0 ? '+' : avg < 0 ? '−' : ''}${Math.abs(avg).toFixed(2)}`
}

function sentimentWord(avg) {
  if (avg >= 0.5) return 'Very Bullish'
  if (avg >= 0.1) return 'Bullish'
  if (avg > -0.1) return 'Neutral'
  if (avg >= -0.5) return 'Bearish'
  return 'Very Bearish'
}

export default function SentimentHeatmapGrid({ rows }) {
  const [range, setRange] = useState(14)
  const [selected, setSelected] = useState(null)
  const days = useMemo(() => lastDays(range), [range])

  const grid = useMemo(
    () => (rows || []).map((row) => ({
      symbol: row.symbol,
      byDay: new Map((row.daily || []).map((d) => [d.date, d])),
    })),
    [rows],
  )
  const hasAnyDaily = grid.some((row) => days.some((day) => row.byDay.has(day.key)))
  const selectedDay = selected ? grid.find((r) => r.symbol === selected.symbol)?.byDay.get(selected.date) : null
  const showValues = range <= 14

  return (
    <details className={styles.heatDetails}>
      <summary className={styles.heatSummary}>
        <span className={styles.heatChevron} aria-hidden="true" />
        Daily sentiment heatmap
        <span className={styles.heatSummaryHint}>click to {`open / close`}</span>
      </summary>

      <div className={styles.heatBody}>
        <div className={styles.heatToolbar}>
          <div className={styles.heatLegend} aria-hidden="true">
            <span>Bearish</span>
            <i className={styles.heatLegendBar} />
            <span>Bullish</span>
            <i className={`${styles.heatSwatch} ${styles.heatEmpty}`} />
            <span>No news</span>
          </div>
          <div className={styles.heatRange} role="group" aria-label="Days shown">
            {RANGES.map((n) => (
              <button
                key={n}
                type="button"
                className={range === n ? styles.heatRangeOn : ''}
                onClick={() => setRange(n)}
                aria-pressed={range === n}
              >
                {n}d
              </button>
            ))}
          </div>
        </div>

        {!grid.length ? (
          <p className={styles.empty}>Add assets to your watchlist to see their daily news sentiment.</p>
        ) : (
          <div className={styles.heatScroll}>
            <div
              className={styles.heatTable}
              style={{ gridTemplateColumns: `72px repeat(${days.length}, minmax(${showValues ? 44 : 22}px, 1fr))` }}
              role="grid"
              aria-label="Daily news sentiment by asset"
            >
              <span className={styles.heatCorner} />
              {days.map((day, i) => (
                <span key={day.key} className={styles.heatDayLabel}>
                  {showValues || i % 3 === 0 || i === days.length - 1
                    ? day.date.toLocaleDateString(undefined, { month: 'numeric', day: 'numeric' })
                    : ''}
                </span>
              ))}

              {grid.map((row) => (
                <div key={row.symbol} className={styles.heatRow} role="row">
                  <span className={styles.heatSymbol} role="rowheader">{row.symbol}</span>
                  {days.map((day) => {
                    const entry = row.byDay.get(day.key)
                    const isSelected = selected?.symbol === row.symbol && selected?.date === day.key
                    const dateText = day.date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
                    if (!entry) {
                      return (
                        <span
                          key={day.key}
                          className={`${styles.heatCell} ${styles.heatEmpty}`}
                          role="gridcell"
                          aria-label={`${row.symbol} ${dateText}: no news`}
                          title={`${row.symbol} · ${dateText}: no news`}
                        />
                      )
                    }
                    return (
                      <button
                        key={day.key}
                        type="button"
                        role="gridcell"
                        className={`${styles.heatCell} ${isSelected ? styles.heatCellSelected : ''}`}
                        style={cellColor(entry.avg)}
                        onClick={() => setSelected(isSelected ? null : { symbol: row.symbol, date: day.key })}
                        aria-pressed={isSelected}
                        aria-label={`${row.symbol} ${dateText}: ${sentimentWord(entry.avg)} ${formatScore(entry.avg)} from ${entry.count} article${entry.count === 1 ? '' : 's'}`}
                        title={`${row.symbol} · ${dateText}: ${formatScore(entry.avg)} (${sentimentWord(entry.avg)}) · ${entry.count} article${entry.count === 1 ? '' : 's'}`}
                      >
                        {showValues ? formatScore(entry.avg) : ''}
                      </button>
                    )
                  })}
                </div>
              ))}
            </div>
          </div>
        )}

        {grid.length && !hasAnyDaily ? (
          <p className={styles.empty}>
            No daily sentiment in this range yet. It fills in as the AI insights refresh (every few minutes) and news with
            dates is scored.
          </p>
        ) : null}

        {selectedDay ? (
          <div className={styles.heatDetail} aria-live="polite">
            <div className={styles.heatDetailHead}>
              <strong>
                {selected.symbol} ·{' '}
                {new Date(`${selected.date}T00:00:00`).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })}
              </strong>
              <button type="button" className={styles.heatClose} onClick={() => setSelected(null)} aria-label="Close details">×</button>
            </div>
            <div className={styles.heatStats}>
              <span><b style={{ color: selectedDay.avg >= 0.1 ? 'var(--aj-positive)' : selectedDay.avg <= -0.1 ? 'var(--aj-negative)' : 'inherit' }}>{formatScore(selectedDay.avg)}</b> {sentimentWord(selectedDay.avg)}</span>
              <span>{selectedDay.count} article{selectedDay.count === 1 ? '' : 's'}</span>
              <span className={styles.pos}>{selectedDay.positive} positive</span>
              <span>{selectedDay.neutral} neutral</span>
              <span className={styles.neg}>{selectedDay.negative} negative</span>
            </div>
            {selectedDay.headlines?.length ? (
              <ul className={styles.heatHeadlines}>
                {selectedDay.headlines.map((h) => (
                  <li key={`${h.url || h.title}`}>
                    <span className={`${styles.heatScoreChip} ${h.score > 0.1 ? styles.pos : h.score < -0.1 ? styles.neg : ''}`}>{formatScore(h.score)}</span>
                    {h.url ? <a href={h.url} target="_blank" rel="noreferrer">{h.title}</a> : <span>{h.title}</span>}
                    {h.source ? <em>{h.source}</em> : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className={styles.empty}>Headlines were not stored for this day.</p>
            )}
          </div>
        ) : hasAnyDaily ? (
          <p className={styles.empty}>Click a coloured cell to see that day&apos;s articles and scores.</p>
        ) : null}
      </div>
    </details>
  )
}
