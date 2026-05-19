export default function AJLogo({ size = 22, title = 'AJTrade' }) {
  const s = Number(size) || 22
  return (
    <svg
      width={s}
      height={s}
      viewBox="0 0 24 24"
      role="img"
      aria-label={title}
      focusable="false"
    >
      <defs>
        <linearGradient id="ajtradeMark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--aj-cta-from)" />
          <stop offset="1" stopColor="var(--aj-cta-to)" />
        </linearGradient>
      </defs>
      <path
        d="M12 2.4l9.4 18.2a1.2 1.2 0 0 1-1.07 1.75H3.67A1.2 1.2 0 0 1 2.6 20.6L12 2.4z"
        fill="url(#ajtradeMark)"
      />
      <path
        d="M12 6.8l6.5 12.6H5.5L12 6.8z"
        fill="var(--aj-overlay-white)"
      />
    </svg>
  )
}
