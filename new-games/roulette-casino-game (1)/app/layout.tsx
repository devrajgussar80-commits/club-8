import { Analytics } from '@vercel/analytics/next'
import { Cormorant_Garamond, Geist, Geist_Mono } from 'next/font/google'
import type { Metadata, Viewport } from 'next'
import './globals.css'

const sans = Geist({ subsets: ['latin'], variable: '--font-geist' })
const mono = Geist_Mono({ subsets: ['latin'], variable: '--font-geist-mono' })
const serif = Cormorant_Garamond({ subsets: ['latin'], variable: '--font-cormorant', weight: ['500', '600', '700'] })

export const metadata: Metadata = {
  title: 'Aurelia Roulette — Premium Demo',
  description: 'A premium frontend-only European roulette game experience.',
  generator: 'v0.app',
}

export const viewport: Viewport = {
  colorScheme: 'dark',
  themeColor: '#090a09',
  userScalable: false,
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" className="dark bg-background">
      <body className={`${sans.variable} ${mono.variable} ${serif.variable} font-sans antialiased`}>
        {children}
        {process.env.NODE_ENV === 'production' && <Analytics />}
      </body>
    </html>
  )
}
