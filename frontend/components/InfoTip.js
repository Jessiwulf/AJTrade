import { useId } from 'react'
import styles from '../styles/InfoTip.module.css'

/**
 * Small "i" button that shows an explanation on hover or keyboard focus.
 * align="end" anchors the bubble to the right edge (for tips near the right side of the screen).
 */
export default function InfoTip({ label, children, align = 'start' }) {
  const id = useId()
  return (
    <span className={styles.infoTip}>
      <button type="button" className={styles.infoBtn} aria-label={`About ${label}`} aria-describedby={id}>
        i
      </button>
      <span role="tooltip" id={id} className={`${styles.tooltip} ${align === 'end' ? styles.alignEnd : ''}`}>
        {children}
      </span>
    </span>
  )
}
