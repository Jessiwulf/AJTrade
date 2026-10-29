import Head from 'next/head'
import Link from 'next/link'
import TopNav from '../components/TopNav'
import { NOT_ADVICE } from '../lib/outlook'
import styles from '../styles/Legal.module.css'

// Privacy notice for Thailand's Personal Data Protection Act B.E. 2562 (PDPA), sections 23 and 30-36:
// what is collected, why, who receives it, how long it is kept, and the member's rights.
const CONTACT = process.env.NEXT_PUBLIC_PRIVACY_CONTACT || ''
const UPDATED = '29 October 2026'

const DATA = [
  ['Account', 'Email address and password. Sign-in is handled by Supabase Auth; AJTrade does not store your password.'],
  ['Profile', 'Display name and profile picture, if you add them.'],
  ['Broker and API keys', 'Alpaca API keys, Settrade app ID, secret, account number and PIN, and a NewsAPI key, if you save them. They are encrypted (AES-256-GCM) before they are stored and are only decrypted to call that service for you.'],
  ['Trading data', 'Your watchlist, bot rules and logs, and the positions, orders, fills and account values AJTrade reads from your broker.'],
  ['Notifications', 'The email address alerts go to, your Discord webhook (encrypted) and a log of the alerts sent to you.'],
  ['AI usage', 'Questions you ask the AI assistant and model telemetry (timings, scores), used to show and improve AI performance.'],
]

const RECIPIENTS = [
  ['Supabase', 'hosts the database and sign-in.'],
  ['Alpaca and Settrade', 'your broker, only once you connect it: AJTrade reads your account and places the orders you or your bot rules ask for.'],
  ['Alpaca News and NewsAPI', 'receive ticker symbols only, to fetch news.'],
  ['Email provider and Discord', 'deliver the alerts you switch on.'],
]

export default function Privacy() {
  return (
    <div className={styles.page}>
      <Head>
        <title>Privacy Notice · AJTrade</title>
      </Head>
      <TopNav />
      <main className={styles.main}>
        <article className={styles.doc}>
          <p className={styles.eyebrow}>Last updated {UPDATED}</p>
          <h1>Privacy Notice</h1>
          <p className={styles.lead}>
            AJTrade is an academic thesis project at Chiang Mai University by Jirapat Sereerat and Atiwit Tin Intasarn,
            who decide how your personal data is used (the data controller). This notice follows Thailand&apos;s Personal
            Data Protection Act B.E. 2562 (PDPA).
          </p>

          <h2>What we collect</h2>
          <dl className={styles.list}>
            {DATA.map(([term, text]) => (
              <div key={term}>
                <dt>{term}</dt>
                <dd>{text}</dd>
              </div>
            ))}
          </dl>

          <h2>Why we use it</h2>
          <ul>
            <li>To run the service you sign up for: your account, watchlist, AI insights, analytics, trading bot and alerts (PDPA section 24(3), performance of a contract).</li>
            <li>To keep the service secure and working, and to measure model performance for the thesis (section 24(5), legitimate interest). Research results are reported only in aggregate, never with your name or account.</li>
          </ul>
          <p>We do not sell your data, use it for advertising, or collect sensitive data such as ID numbers, health or biometric data.</p>

          <h2>Who receives it</h2>
          <ul>
            {RECIPIENTS.map(([who, why]) => (
              <li key={who}><strong>{who}</strong> {why}</li>
            ))}
          </ul>
          <p>
            The AI models (FinBERT, LightGBM and the chat assistant) run on AJTrade&apos;s own server, so your questions are not
            sent to outside AI services. Some of the services above store data outside Thailand; they protect it under
            their own security and privacy terms.
          </p>

          <h2>How long we keep it</h2>
          <p>
            Your data is kept while your account exists. When you delete your account, everything AJTrade holds about you
            is deleted straight away. Public market news (not personal data) is kept for 45 days.
          </p>

          <h2>Your rights</h2>
          <p>
            Under the PDPA you can ask to see or get a copy of your data, correct it, object to or restrict its use, move it
            to another service, or have it deleted. You can also complain to the Personal Data Protection Committee (PDPC).
          </p>
          <ul>
            <li><strong>Delete everything yourself</strong>: <Link href="/profile">Profile</Link> → Delete account. This does not close your Alpaca or Settrade account.</li>
            <li><strong>Remove keys or alerts</strong> any time on <Link href="/api-keys">API Management</Link> and <Link href="/notifications">Notifications</Link>.</li>
            <li>
              <strong>Other requests</strong>:{' '}
              {CONTACT ? <a href={`mailto:${CONTACT}`}>{CONTACT}</a> : 'contact the AJTrade project team'}. We reply within 30 days.
            </li>
          </ul>

          <h2>Not investment advice</h2>
          <p>
            {NOT_ADVICE} AJTrade is not a licensed investment advisor or broker. Any trade, including one placed by a bot
            you set up, is your own decision and risk.
          </p>
        </article>
      </main>
    </div>
  )
}
