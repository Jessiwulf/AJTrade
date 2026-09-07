import '../styles/globals.css'
import { Inter } from 'next/font/google'
import { SWRConfig } from 'swr'
import { apiFetch } from '../lib/api'

const inter = Inter({
  subsets: ['latin'],
  display: 'swap',
})

export default function App({ Component, pageProps }) {
  return (
    <div className={inter.className}>
      <SWRConfig
        value={{
          fetcher: (url) => apiFetch(url),
          dedupingInterval: 5 * 60 * 1000,
          revalidateOnFocus: false,
          revalidateOnReconnect: false,
          revalidateIfStale: false,
          shouldRetryOnError: false,
          keepPreviousData: true,
        }}
      >
        <Component {...pageProps} />
      </SWRConfig>
    </div>
  )
}
