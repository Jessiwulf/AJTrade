import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import styles from '../styles/InfoTip.module.css'

const VIEWPORT_MARGIN = 12
const GAP = 8

/**
 * Small "i" button that explains something on hover, keyboard focus or tap.
 *
 * The bubble is rendered into <body> with fixed positioning and only while open, so it never widens
 * the page or gets clipped by a card, and it is measured and clamped to stay fully on screen (it
 * flips above the button when there is no room below). On touch screens a tap pins it open; a tap
 * outside or Escape closes it.
 *
 * align="end" prefers anchoring the bubble's right edge to the button (for tips on the right side).
 */
export default function InfoTip({ label, children, align = 'start', wide = false, icon = 'i' }) {
  const id = useId()
  const triggerRef = useRef(null)
  const tipRef = useRef(null)
  const closeTimer = useRef(null)
  const [open, setOpen] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [position, setPosition] = useState(null)

  function show() {
    clearTimeout(closeTimer.current)
    setOpen(true)
  }

  function hideSoon() {
    if (pinned) return
    clearTimeout(closeTimer.current)
    // A short delay lets the pointer travel from the button into the bubble (e.g. to click a link).
    closeTimer.current = setTimeout(() => setOpen(false), 140)
  }

  function close() {
    clearTimeout(closeTimer.current)
    setPinned(false)
    setOpen(false)
  }

  useEffect(() => () => clearTimeout(closeTimer.current), [])

  useLayoutEffect(() => {
    if (!open) {
      setPosition(null)
      return undefined
    }
    function place() {
      const trigger = triggerRef.current?.getBoundingClientRect()
      const tip = tipRef.current
      if (!trigger || !tip) return
      const viewportWidth = document.documentElement.clientWidth
      const viewportHeight = window.innerHeight
      const width = Math.min(tip.offsetWidth, viewportWidth - VIEWPORT_MARGIN * 2)
      let left = align === 'end' ? trigger.right - width + 10 : trigger.left - 10
      left = Math.max(VIEWPORT_MARGIN, Math.min(left, viewportWidth - width - VIEWPORT_MARGIN))
      const height = tip.offsetHeight
      let top = trigger.bottom + GAP
      const fitsBelow = top + height <= viewportHeight - VIEWPORT_MARGIN
      const fitsAbove = trigger.top - GAP - height >= VIEWPORT_MARGIN
      if (!fitsBelow && fitsAbove) top = trigger.top - GAP - height
      setPosition({ left, top, width })
    }
    place()
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [open, align])

  useEffect(() => {
    if (!open) return undefined
    function onPointerDown(event) {
      if (triggerRef.current?.contains(event.target) || tipRef.current?.contains(event.target)) return
      close()
    }
    function onKeyDown(event) {
      if (event.key === 'Escape') close()
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  function onBlur(event) {
    // Keep it open while focus moves into the bubble (e.g. tabbing to a link inside it).
    if (tipRef.current?.contains(event.relatedTarget)) return
    if (!pinned) hideSoon()
  }

  return (
    <span className={styles.infoTip} ref={triggerRef} onMouseEnter={show} onMouseLeave={hideSoon}>
      <button
        type="button"
        className={styles.infoBtn}
        aria-label={`About ${label}`}
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onFocus={show}
        onBlur={onBlur}
        onClick={() => {
          if (pinned) {
            close()
          } else {
            setPinned(true)
            show()
          }
        }}
      >
        {icon}
      </button>
      {open && typeof document !== 'undefined'
        ? createPortal(
          <span
            role="tooltip"
            id={id}
            ref={tipRef}
            className={`${styles.tooltip} ${wide ? styles.wide : ''}`}
            style={position ? { left: position.left, top: position.top, width: position.width } : { left: 0, top: 0, visibility: 'hidden' }}
            onMouseEnter={show}
            onMouseLeave={hideSoon}
            onBlur={(event) => {
              if (!triggerRef.current?.contains(event.relatedTarget) && !tipRef.current?.contains(event.relatedTarget) && !pinned) hideSoon()
            }}
          >
            {children}
          </span>,
          document.body,
        )
        : null}
    </span>
  )
}
