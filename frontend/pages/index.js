import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import FeatureArt from '../components/FeatureArt'
import HeroStage from '../components/HeroStage'
import TopNav from '../components/TopNav'
import styles from '../styles/Landing.module.css'

function cx(...names) {
  return names.filter(Boolean).join(' ')
}

// Fades children up once they scroll into view. Falls back to visible when
// IntersectionObserver is unavailable; reduced-motion users skip the animation in CSS.
function Reveal({ as: Tag = 'div', delay = 0, className, children, ...rest }) {
  const ref = useRef(null)
  const [shown, setShown] = useState(false)

  useEffect(() => {
    const node = ref.current
    if (!node) return undefined
    if (typeof IntersectionObserver === 'undefined') {
      setShown(true)
      return undefined
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setShown(true)
          io.disconnect()
        }
      },
      { threshold: 0.15, rootMargin: '0px 0px -40px 0px' }
    )
    io.observe(node)
    return () => io.disconnect()
  }, [])

  return (
    <Tag
      ref={ref}
      className={cx(styles.reveal, shown && styles.revealIn, className)}
      style={{ '--reveal-delay': `${delay}ms` }}
      {...rest}
    >
      {children}
    </Tag>
  )
}

/* ------------------------------------------------------------------ */
/* Icons                                                               */
/* ------------------------------------------------------------------ */

function Icon({ kind, size = 22 }) {
  const common = { width: size, height: size, viewBox: '0 0 24 24', 'aria-hidden': true, fill: 'none' }
  const navy = { stroke: 'var(--aj-indigo)', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' }
  const amber = { stroke: 'var(--aj-cta-to)', strokeWidth: 2.4, strokeLinecap: 'round', strokeLinejoin: 'round' }

  switch (kind) {
    case 'news':
      return (
        <svg {...common}>
          <rect x="3" y="4" width="15" height="16" rx="2.5" {...navy} />
          <path d="M7 9h7M7 13h7M7 17h4" {...navy} />
          <path d="M18 8h2.5v9.5A2.5 2.5 0 0 1 18 20" {...amber} />
        </svg>
      )
    case 'forecast':
      return (
        <svg {...common}>
          <path d="M4 19V5m0 14h16" {...navy} />
          <path d="M7 15l3-3 3 2 3-4" {...navy} />
          <path d="M16 10l4-4" {...amber} strokeDasharray="2 2.5" />
        </svg>
      )
    case 'bot':
      return (
        <svg {...common}>
          <rect x="4" y="8" width="16" height="11" rx="3" {...navy} />
          <path d="M12 4v4M9 13h.01M15 13h.01" {...navy} />
          <path d="M9 16.5h6" {...amber} />
        </svg>
      )
    case 'shap':
      return (
        <svg {...common}>
          <path d="M4 6h9M4 12h13M4 18h6" {...navy} />
          <path d="M13 6h5M17 12h3" {...amber} />
        </svg>
      )
    case 'chat':
      return (
        <svg {...common}>
          <path d="M5 5h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H10l-4 3v-3H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z" {...navy} />
          <path d="M8 10h8M8 13h5" {...amber} />
        </svg>
      )
    case 'broker':
      return (
        <svg {...common}>
          <path d="M3 10l9-6 9 6M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18" {...navy} />
          <path d="M12 7.2h.01" {...amber} />
        </svg>
      )
    case 'shield':
      return (
        <svg {...common}>
          <path d="M12 2.5l8 3.5v6c0 5-3.4 9-8 10-4.6-1-8-5-8-10V6l8-3.5z" {...navy} />
          <path d="M8.5 12l2.5 2.5 4.5-5" {...amber} />
        </svg>
      )
    case 'check':
      return (
        <svg {...common}>
          <path d="M5 12.5l4.5 4.5L19 7.5" {...amber} />
        </svg>
      )
    case 'x':
      return (
        <svg {...common}>
          <path d="M7 7l10 10M17 7L7 17" stroke="var(--aj-negative)" strokeWidth="2.2" strokeLinecap="round" />
        </svg>
      )
    case 'arrow':
      return (
        <svg {...common}>
          <path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )
    case 'play':
      return (
        <svg {...common}>
          <path d="M8 5.5v13l11-6.5-11-6.5z" fill="currentColor" />
        </svg>
      )
    default:
      return null
  }
}

/* ------------------------------------------------------------------ */
/* Content                                                             */
/* ------------------------------------------------------------------ */

const STATS = [
  { value: '5-stage', label: 'AI pipeline, from headline to order' },
  { value: 'US + SET', label: 'Markets via Alpaca & Settrade' },
  { value: '100%', label: 'Of signals explained with SHAP' },
  { value: 'AES-256', label: 'Encrypted broker API key vault' },
]

const PIPELINE = [
  { icon: 'news', title: 'Ingest', text: 'Financial headlines stream in from NewsAPI for every asset you watch.' },
  { icon: 'news', title: 'Score', text: 'FinBERT turns each headline into a Positive / Neutral / Negative score.' },
  { icon: 'forecast', title: 'Forecast', text: 'LightGBM blends sentiment with OHLCV history into a directional signal.' },
  { icon: 'shap', title: 'Explain', text: 'SHAP attributes the signal to the exact keywords and features behind it.' },
  { icon: 'bot', title: 'Execute', text: 'The bot validates your risk rules, then routes the order to your broker.' },
]

// Each feature is illustrated by an animated drawing (components/FeatureArt.js, keyed by `id`). To use an exported
// Canva GIF instead, add `gif: '/features/<name>.gif'` (file in public/features/).
const FEATURES = [
  {
    id: 'sentiment',
    icon: 'news',
    eyebrow: 'AI News Sentiment',
    title: 'Read the market’s mood before the crowd does.',
    text:
      'AJTrade runs every headline through FinBERT, a language model fine-tuned on financial text, and converts unstructured news into a quantified sentiment score you can trade on.',
    bullets: [
      'Positive, neutral, and negative scores with model confidence',
      'News tracked per asset across your watchlist',
      'Scored in batches and logged for audit and backtesting',
    ],
    tag: 'FinBERT · NewsAPI',
  },
  {
    id: 'forecast',
    icon: 'forecast',
    eyebrow: 'Price Forecasting',
    title: 'Short-term forecasts built on news and price action.',
    text:
      'A LightGBM model combines sentiment features with historical OHLCV data to forecast short-term trend direction. It is fast and light on memory, so signals stay fresh.',
    bullets: [
      'Sentiment + technical features in one model',
      'Directional signals with a confidence level',
      'Performance tracked on the AI Performance page',
    ],
    tag: 'LightGBM',
  },
  {
    id: 'bot',
    icon: 'bot',
    eyebrow: 'Automated Trading Bot',
    title: 'Rules execute. Emotions don’t.',
    text:
      'A strict, rule-based engine intercepts every model signal and checks it against your stop-loss, take-profit, and position-size limits before a single order reaches the broker.',
    bullets: [
      'Paper trading and live execution modes',
      'Per-asset stop-loss and take-profit guardrails',
      'Broker keys encrypted with AES-256-GCM',
    ],
    tag: 'Alpaca · Settrade',
  },
  {
    id: 'xai',
    icon: 'shap',
    eyebrow: 'Explainable AI',
    title: 'Every signal comes with its reasons.',
    text:
      'SHAP breaks each prediction into the exact influence of every keyword and feature, such as “earnings beat: +15.2%”, so you can see why the model decided and not only what it decided.',
    bullets: [
      'Per-feature attribution in percentages',
      'Signals traced back to the headline keywords',
      'Inputs and explanations stored for review',
    ],
    tag: 'SHAP',
  },
  {
    id: 'chat',
    icon: 'chat',
    eyebrow: 'AI Chat Assistant',
    title: 'Ask “why?” and get a plain-language answer.',
    text:
      'A conversational assistant explains signals, sentiment, and SHAP results in everyday language. It is a presentation layer only: it can explain decisions but never place trades.',
    bullets: [
      'Explains any signal on demand',
      'Runs on a local open-source LLM via Ollama',
      'Kept separate from order execution by design',
    ],
    tag: 'LLM · Ollama',
  },
]

// Product tour screenshots (public/showcase/*.webp).
const SCREENS = [
  { id: 'dashboard', label: 'Dashboard', path: '/dashboard', text: 'Portfolio overview, watchlist movers, live charts and the AI assistant at a glance.' },
  { id: 'markets-news', label: 'Markets News', path: '/markets', text: 'Latest news for every watched asset, each headline scored by FinBERT.' },
  { id: 'ai-insights', label: 'AI Insights', path: '/insights', text: 'LightGBM signals with confidence, SHAP drivers, rationale and one-click Automate.' },
  { id: 'automated-trading', label: 'Auto Trade', path: '/automated', text: 'Per-asset bot rules: strategy, stop-loss, trailing stop, take-profit and capital limits.' },
  { id: 'watchlist', label: 'Watchlist', path: '/watchlist', text: 'The stocks and crypto pairs AJTrade analyses for you.' },
  { id: 'portfolio', label: 'Portfolio', path: '/portfolio', text: 'Holdings, allocation and trades synced from your Alpaca paper account.' },
  { id: 'analytics', label: 'Analytics', path: '/analytics', text: 'Portfolio growth, realized and unrealized P/L, win rate and a news-sentiment heatmap.' },
  { id: 'ai-performance', label: 'AI Performance', path: '/ai-performance', text: 'Model health: forecaster accuracy, sentiment mix, news usage and assistant metrics.' },
  { id: 'api-management', label: 'API Management', path: '/api-keys', text: 'Broker and news API keys, encrypted with AES-256-GCM.' },
]

const STACK = [
  {
    group: 'Markets & Data',
    items: [
      { name: 'Alpaca', role: 'US brokerage API (paper trading)', logos: ['/logos/alpaca.png'] },
      { name: 'Settrade', role: 'Thai SET brokerage API', logos: ['/logos/settrade.svg'], wide: true },
      { name: 'NewsAPI', role: 'Real-time financial news', logos: ['/logos/newsapi.png'] },
    ],
  },
  {
    group: 'AI & Machine Learning',
    items: [
      { name: 'FinBERT', role: 'Financial sentiment NLP (Hugging Face)', logos: ['/logos/huggingface.svg'] },
      { name: 'LightGBM', role: 'Gradient-boosted forecaster', logos: ['/logos/lightgbm.svg'], wide: true },
      { name: 'SHAP', role: 'Explainable AI attributions', logos: ['/logos/shap.png'] },
    ],
  },
  {
    group: 'Platform & Infrastructure',
    items: [
      { name: 'Supabase', role: 'Postgres & authentication', logos: ['/logos/supabase.svg'] },
      { name: 'Vercel', role: 'Frontend hosting & edge', logos: ['/logos/vercel.svg'] },
      { name: 'Next.js · FastAPI', role: 'Web app & Python API', logos: ['/logos/nextdotjs.svg', '/logos/fastapi.svg'] },
    ],
  },
]

/* ------------------------------------------------------------------ */
/* Feature illustrations                                               */
/* ------------------------------------------------------------------ */

function FeatureMedia({ feature }) {
  return (
    <div className={styles.media}>
      {feature.gif ? (
        <img src={feature.gif} alt={`${feature.eyebrow} animation`} className={styles.mediaGif} loading="lazy" />
      ) : (
        <FeatureArt kind={feature.id} label={`${feature.eyebrow} animated illustration`} />
      )}
      <div className={styles.mediaFooter}>
        <span className={styles.mediaTag}>
          <Icon kind={feature.icon} size={14} /> {feature.tag}
        </span>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Project demo video                                                  */
/* ------------------------------------------------------------------ */

const DEMO_CHAPTERS = [
  { t: 0, label: 'The problem' },
  { t: 15, label: 'Dashboard' },
  { t: 21, label: 'Portfolio & Watchlist' },
  { t: 26, label: 'News & AI Insights' },
  { t: 30, label: 'Trading bot' },
  { t: 37, label: 'AI Performance' },
  { t: 41, label: 'Analytics' },
]

function formatClock(seconds) {
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
}

function DemoVideo() {
  const ref = useRef(null)
  const [time, setTime] = useState(0)
  const current = DEMO_CHAPTERS.reduce((acc, chapter, i) => (time >= chapter.t ? i : acc), 0)

  function jump(t) {
    const video = ref.current
    if (!video) return
    video.currentTime = t
    video.play().catch(() => {})
  }

  return (
    <div className={styles.demo}>
      <div className={styles.demoFrame}>
        <video
          ref={ref}
          className={styles.demoVideo}
          src="/demo/ajtrade-demo.mp4"
          poster="/demo/ajtrade-demo-poster.jpg"
          controls
          playsInline
          preload="metadata"
          onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
        />
      </div>
      <ol className={styles.chapters} aria-label="Jump to a part of the demo">
        {DEMO_CHAPTERS.map((chapter, i) => (
          <li key={chapter.t}>
            <button
              type="button"
              className={cx(styles.chapter, i === current && time > 0 && styles.chapterOn)}
              onClick={() => jump(chapter.t)}
            >
              <span className={styles.chapterTime}>{formatClock(chapter.t)}</span>
              {chapter.label}
            </button>
          </li>
        ))}
      </ol>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* UI showcase                                                         */
/* ------------------------------------------------------------------ */

function Showcase() {
  const [active, setActive] = useState(0)
  const [paused, setPaused] = useState(false)
  const total = SCREENS.length
  const screen = SCREENS[active]

  useEffect(() => {
    const reduce = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (paused || reduce) return undefined
    const id = setInterval(() => setActive((a) => (a + 1) % total), 6000)
    return () => clearInterval(id)
  }, [paused, total])

  const go = (delta) => setActive((a) => (a + delta + total) % total)

  return (
    <div
      className={styles.showcase}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <div className={styles.tabs} role="tablist" aria-label="Application pages">
        {SCREENS.map((s, i) => (
          <button
            key={s.id}
            type="button"
            role="tab"
            id={`showcase-tab-${s.id}`}
            aria-selected={i === active}
            aria-controls="showcase-panel"
            className={cx(styles.tab, i === active && styles.tabActive)}
            onClick={() => setActive(i)}
          >
            {s.label}
            {i === active && !paused ? <span key={active} className={styles.tabProgress} /> : null}
          </button>
        ))}
      </div>

      <div className={styles.stageWrap}>
        <button type="button" className={cx(styles.navBtn, styles.navPrev)} onClick={() => go(-1)} aria-label="Previous page">
          <Icon kind="arrow" size={18} />
        </button>

        <div className={styles.browser} id="showcase-panel" role="tabpanel" aria-labelledby={`showcase-tab-${screen.id}`}>
          <div className={styles.browserBar}>
            <span className={styles.dots}>
              <i />
              <i />
              <i />
            </span>
            <span className={styles.url}>ajtrade.vercel.app{screen.path}</span>
          </div>
          <div key={screen.id} className={styles.browserBody}>
            <img
              src={`/showcase/${screen.id}.webp`}
              alt={`${screen.label} page`}
              className={styles.screenshot}
              width={1600}
              height={867}
              loading="lazy"
            />
          </div>
        </div>

        <button type="button" className={cx(styles.navBtn, styles.navNext)} onClick={() => go(1)} aria-label="Next page">
          <Icon kind="arrow" size={18} />
        </button>
      </div>

      <p className={styles.showcaseCaption} aria-live="polite">
        <strong>{screen.label}.</strong> {screen.text}
      </p>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export default function Home() {
  return (
    <div className={styles.page}>
      <TopNav />

      {/* Hero */}
      <header className={styles.hero}>
        <div className={styles.heroBg} aria-hidden="true" />
        <div className={cx(styles.container, styles.heroGrid)}>
          <div className={styles.heroCopy}>
            {/* <span className={cx(styles.eyebrowPill, styles.heroIn)} style={{ '--i': 0 }}>
              <span className={styles.liveDot} /> AI Asset Analysis & Automated Trading
            </span> */}
            <h1 className={cx(styles.headline, styles.heroIn)} style={{ '--i': 1 }}>
              Trade at the speed of news, <span className={styles.highlight}>protected by intelligence.</span>
            </h1>
            <p className={cx(styles.lead, styles.heroIn)} style={{ '--i': 2 }}>
              AJTrade reads financial news with FinBERT, forecasts price direction with LightGBM, explains every signal with
              SHAP, and executes under your risk rules. No fear, no FOMO.
            </p>
            <div className={cx(styles.ctaRow, styles.heroIn)} style={{ '--i': 3 }}>
              <Link href="/signup" className={styles.cta}>
                Get Started <Icon kind="arrow" size={18} />
              </Link>
              <a href="#how-it-works" className={styles.ctaGhost}>
                See how it works
              </a>
            </div>
            <ul className={cx(styles.heroChecks, styles.heroIn)} style={{ '--i': 4 }}>
              <li>
                <Icon kind="check" size={16} /> Paper trading built in
              </li>
              <li>
                <Icon kind="check" size={16} /> US & Thai markets
              </li>
              <li>
                <Icon kind="check" size={16} /> Fully explainable
              </li>
            </ul>
          </div>
          <HeroStage />
        </div>
      </header>

      {/* Trust strip */}
      <section className={styles.statsSection} aria-label="Platform at a glance">
        <div className={cx(styles.container, styles.stats)}>
          {STATS.map((s, i) => (
            <Reveal key={s.label} className={styles.stat} delay={i * 80}>
              <span className={styles.statValue}>{s.value}</span>
              <span className={styles.statLabel}>{s.label}</span>
            </Reveal>
          ))}
        </div>
      </section>

      <main>
        {/* Project demo */}
        <section id="demo" className={cx(styles.section, styles.demoSection)}>
          <div className={styles.container}>
            <Reveal className={styles.sectionHead}>
              <img src="/brand/ajtrade-logo.png" alt="AJTrade" className={styles.demoLogo} width={640} height={598} />
              <span className={styles.eyebrow}>Project demo</span>
              <h2 className={styles.sectionTitle}>See AJTrade in 45 seconds.</h2>
              <p className={styles.sectionLead}>
                From the problem it solves to the dashboard, AI insights, the trading bot and analytics. Pick a chapter to
                jump straight to it.
              </p>
            </Reveal>
            <Reveal delay={100}>
              <DemoVideo />
            </Reveal>
          </div>
        </section>

        {/* Overview */}
        <section id="overview" className={styles.section}>
          <div className={cx(styles.container, styles.overviewGrid)}>
            <Reveal>
              <span className={styles.eyebrow}>Project overview</span>
              <h2 className={styles.sectionTitle}>Built to take emotion out of trading.</h2>
              <p className={styles.sectionLead}>
                Markets overreact. People panic-sell on bad headlines and chase rallies out of FOMO. AJTrade treats that
                irrationality as a signal: it quantifies sentiment from the news, acts on short-term momentum before the
                crowd reacts, and does it all under rules you set in advance.
              </p>
              <p className={styles.sectionLead}>
                Instead of one black-box model, AJTrade uses a strictly separated multi-model pipeline, which keeps it
                fast, safe, and <strong>transparent at every step</strong>.
              </p>
            </Reveal>

            <Reveal className={styles.compare} delay={120}>
              <div className={styles.compareCol}>
                <p className={styles.compareHead}>Emotional trading</p>
                {['Panic-sells on scary headlines', 'Chases rallies out of FOMO', 'Moves stop-losses “just this once”', 'Can’t explain why a trade was made'].map((t) => (
                  <p key={t} className={styles.compareItem}>
                    <Icon kind="x" size={16} /> {t}
                  </p>
                ))}
              </div>
              <div className={cx(styles.compareCol, styles.compareBrand)}>
                <p className={styles.compareHead}>With AJTrade</p>
                {['Sentiment measured, not felt', 'Signals from data, not hype', 'Risk limits enforced on every order', 'Every decision explained by SHAP'].map((t) => (
                  <p key={t} className={styles.compareItem}>
                    <Icon kind="check" size={16} /> {t}
                  </p>
                ))}
              </div>
            </Reveal>
          </div>
        </section>

        {/* How it works */}
        <section id="how-it-works" className={cx(styles.section, styles.sectionTint)}>
          <div className={styles.container}>
            <Reveal className={styles.sectionHead}>
              <span className={styles.eyebrow}>How it works</span>
              <h2 className={styles.sectionTitle}>From headline to order in five steps.</h2>
              <p className={styles.sectionLead}>
                Each stage is its own model with one job. You can inspect, audit, and trust every step.
              </p>
            </Reveal>
            <ol className={styles.pipeline}>
              {PIPELINE.map((p, i) => (
                <Reveal as="li" key={p.title} className={styles.stepItem} delay={i * 110}>
                  <div className={styles.step}>
                    <span className={styles.stepNum}>{String(i + 1).padStart(2, '0')}</span>
                    <span className={styles.stepIcon}>
                      <Icon kind={p.icon} />
                    </span>
                    <h3 className={styles.stepTitle}>{p.title}</h3>
                    <p className={styles.stepText}>{p.text}</p>
                  </div>
                </Reveal>
              ))}
            </ol>
          </div>
        </section>

        {/* Features */}
        <section id="features" className={styles.section}>
          <div className={styles.container}>
            <Reveal className={styles.sectionHead}>
              <span className={styles.eyebrow}>Core features</span>
              <h2 className={styles.sectionTitle}>Five AI components, one disciplined trader.</h2>
            </Reveal>

            <div className={styles.featureList}>
              {FEATURES.map((f, i) => (
                <article key={f.id} id={`feature-${f.id}`} className={cx(styles.featureRow, i % 2 === 1 && styles.featureRowFlip)}>
                  <Reveal className={styles.featureCopy}>
                    <span className={styles.featureBadge}>
                      <Icon kind={f.icon} size={18} /> {f.eyebrow}
                    </span>
                    <h3 className={styles.featureTitle}>{f.title}</h3>
                    <p className={styles.featureText}>{f.text}</p>
                    <ul className={styles.featureBullets}>
                      {f.bullets.map((b) => (
                        <li key={b}>
                          <Icon kind="check" size={16} /> {b}
                        </li>
                      ))}
                    </ul>
                  </Reveal>
                  <Reveal delay={140}>
                    <FeatureMedia feature={f} />
                  </Reveal>
                </article>
              ))}
            </div>
          </div>
        </section>

        {/* UI showcase */}
        <section id="showcase" className={cx(styles.section, styles.sectionTint)}>
          <div className={styles.container}>
            <Reveal className={styles.sectionHead}>
              <span className={styles.eyebrow}>Product tour</span>
              <h2 className={styles.sectionTitle}>A calm, clear workspace for every decision.</h2>
              <p className={styles.sectionLead}>
                From the dashboard to the bot controls, every page stays light, minimal, and focused on what matters.
              </p>
            </Reveal>
            <Reveal delay={100}>
              <Showcase />
            </Reveal>
          </div>
        </section>

        {/* Tech stack */}
        <section id="stack" className={styles.section}>
          <div className={styles.container}>
            <Reveal className={styles.sectionHead}>
              <span className={styles.eyebrow}>APIs & tech stack</span>
              <h2 className={styles.sectionTitle}>Built on proven, production-grade technology.</h2>
              <p className={styles.sectionLead}>
                Trusted brokerage APIs, research-grade AI models, and a modern cloud platform.
              </p>
            </Reveal>
            <div className={styles.stackGrid}>
              {STACK.map((g, gi) => (
                <Reveal key={g.group} className={styles.stackGroup} delay={gi * 120}>
                  <p className={styles.stackGroupTitle}>{g.group}</p>
                  <div className={styles.stackItems}>
                    {g.items.map((t) => (
                      <div key={t.name} className={styles.stackItem}>
                        <span className={cx(styles.stackLogo, t.wide && styles.stackLogoWide)}>
                          {t.logos.map((src) => (
                            <img key={src} src={src} alt="" loading="lazy" />
                          ))}
                        </span>
                        <span>
                          <span className={styles.stackName}>{t.name}</span>
                          <span className={styles.stackRole}>{t.role}</span>
                        </span>
                      </div>
                    ))}
                  </div>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        {/* Final CTA */}
        <section className={styles.ctaSection}>
          <Reveal className={cx(styles.container, styles.ctaBand)}>
            <div>
              <h2 className={styles.ctaTitle}>Let the data trade. Keep your peace of mind.</h2>
              <p className={styles.ctaText}>
                Start in paper mode, watch the AI explain itself, then go live when you are ready.
              </p>
            </div>
            <Link href="/signup" className={styles.cta}>
              Get Started <Icon kind="arrow" size={18} />
            </Link>
          </Reveal>
        </section>
      </main>

      <footer className={styles.footer}>
        <div className={cx(styles.container, styles.footerInner)}>
          <img src="/brand/ajtrade-logo.png" alt="AJTrade" className={styles.footerLogo} width={640} height={598} loading="lazy" />
          <div>
            <p>
              Created by Jirapat Sereerat &amp; Atiwit Tin Intasarn | Advisor: Asst.Prof. Tisinee Surapunt
            </p>
            <p className={styles.disclaimer}>
              AJTrade is an academic thesis project. Nothing on this site is financial advice. Trading involves risk,
              including possible loss of principal.
            </p>
          </div>
        </div>
      </footer>
    </div>
  )
}
