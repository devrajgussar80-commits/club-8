'use client'

import { useState } from 'react'
import { CircleDollarSign, RotateCcw, Sparkles } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { Button } from '@/components/ui/button'
import { BettingTable } from '@/components/betting-table'
import { RouletteWheel, redNumbers, wheelOrder } from '@/components/roulette-wheel'

export function RouletteGame() {
  const [selected, setSelected] = useState<number[]>([7, 17])
  const [winner, setWinner] = useState<number | null>(null)
  const [lastWinner, setLastWinner] = useState<number | null>(32)
  const [isSpinning, setIsSpinning] = useState(false)
  const [wheelRotation, setWheelRotation] = useState(0)
  const [ballRotation, setBallRotation] = useState(0)
  const [balance, setBalance] = useState(2500)
  const [history, setHistory] = useState<number[]>([32, 11, 0, 19, 7])

  function toggleNumber(number: number) {
    setSelected(current => current.includes(number) ? current.filter(item => item !== number) : [...current, number])
  }

  function spin() {
    if (isSpinning) return
    const nextWinner = Math.floor(Math.random() * 37)
    const pocket = wheelOrder.indexOf(nextWinner)
    setWinner(null)
    setIsSpinning(true)
    setBalance(value => Math.max(0, value - Math.max(10, selected.length * 10)))
    setWheelRotation(value => value + 1440 + 360 - pocket * (360 / 37))
    setBallRotation(value => value - 1800 - (360 - pocket * (360 / 37)))
    window.setTimeout(() => {
      setWinner(nextWinner)
      setLastWinner(nextWinner)
      setHistory(current => [nextWinner, ...current].slice(0, 6))
      setIsSpinning(false)
      if (selected.includes(nextWinner)) setBalance(value => value + 350)
    }, 6600)
  }

  const shownWinner = winner ?? lastWinner

  return (
    <main className="min-h-screen overflow-hidden bg-background text-foreground">
      <div className="mx-auto flex min-h-screen w-full max-w-7xl flex-col gap-5 px-4 py-4 sm:px-6 lg:py-6">
        <header className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="brand-mark flex size-10 items-center justify-center rounded-xl"><Sparkles aria-hidden="true" /></div>
            <div><p className="text-base font-semibold tracking-tight">Aurelia <span className="text-primary">3D</span></p><p className="text-[10px] uppercase tracking-[0.24em] text-muted-foreground">European roulette</p></div>
          </div>
          <div className="surface-panel rounded-xl px-4 py-2 text-right"><p className="text-[9px] uppercase tracking-[0.18em] text-muted-foreground">Demo credit</p><p className="font-mono text-base font-semibold text-primary">${balance.toLocaleString()}</p></div>
        </header>

        <div className="grid min-h-0 flex-1 items-center gap-5 lg:grid-cols-[minmax(360px,0.9fr)_minmax(440px,1.1fr)]">
          <section className="flex min-w-0 flex-col gap-3">
            <div className="surface-panel flex items-center justify-between gap-3 rounded-xl px-3 py-2">
              <div className="flex items-center gap-2"><span className={`size-2 rounded-full ${isSpinning ? 'bg-destructive animate-pulse' : 'bg-accent'}`} /><p className="text-xs font-medium">{isSpinning ? 'Ball in motion' : winner !== null ? 'Result confirmed' : 'Bets are open'}</p></div>
              <div className="flex gap-1.5" aria-label="Recent results">{history.map((number, index) => <span key={`${number}-${index}`} className={`flex size-7 items-center justify-center rounded-lg border border-border text-[10px] font-bold ${number === 0 ? 'bg-accent text-accent-foreground' : redNumbers.has(number) ? 'bg-destructive' : 'bg-secondary'}`}>{number}</span>)}</div>
            </div>
            <RouletteWheel rotation={wheelRotation} ballRotation={ballRotation} isSpinning={isSpinning} winner={winner} />
            <AnimatePresence mode="wait">
              <motion.div key={isSpinning ? 'spin' : shownWinner} className="text-center" initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -5 }}>
                <p className="text-[9px] uppercase tracking-[0.28em] text-muted-foreground">{isSpinning ? 'No more bets' : winner !== null ? 'Winning number' : 'Last result'}</p>
                <p className="font-mono text-2xl font-semibold text-primary">{isSpinning ? '—' : shownWinner}</p>
              </motion.div>
            </AnimatePresence>
          </section>

          <section className="flex min-w-0 flex-col gap-4">
            <BettingTable selected={selected} disabled={isSpinning} onToggle={toggleNumber} />
            <div className="surface-panel flex items-center justify-between gap-4 rounded-xl px-4 py-3">
              <div className="min-w-0"><p className="text-[9px] uppercase tracking-[0.2em] text-muted-foreground">Active numbers</p><p className="truncate font-mono text-sm">{selected.length ? selected.join('  /  ') : 'Choose a number'}</p></div>
              {selected.length > 0 && <Button variant="ghost" size="icon" disabled={isSpinning} onClick={() => setSelected([])} aria-label="Clear selected numbers"><RotateCcw /></Button>}
            </div>
            <Button className="h-14 rounded-xl text-sm font-bold tracking-[0.22em]" size="lg" disabled={isSpinning || selected.length === 0} onClick={spin}><CircleDollarSign data-icon="inline-start" />{isSpinning ? 'SPINNING…' : 'SPIN WHEEL'}</Button>
            <p className="text-center text-[10px] leading-relaxed text-muted-foreground">Interactive demo only. No real money, payments, or prizes.</p>
          </section>
        </div>
      </div>
    </main>
  )
}
