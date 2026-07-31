'use client'

import { cn } from '@/lib/utils'
import { redNumbers } from '@/components/roulette-wheel'

const rows = [[3,6,9,12,15,18,21,24,27,30,33,36],[2,5,8,11,14,17,20,23,26,29,32,35],[1,4,7,10,13,16,19,22,25,28,31,34]]

export function BettingTable({ selected, disabled, onToggle }: { selected: number[]; disabled: boolean; onToggle: (number: number) => void }) {
  const numberButton = (number: number) => {
    const active = selected.includes(number)
    return <button key={number} type="button" disabled={disabled} aria-pressed={active} onClick={() => onToggle(number)} className={cn('number-cell relative flex min-h-10 min-w-9 items-center justify-center border border-border px-2 font-mono text-xs font-semibold transition-transform hover:-translate-y-0.5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-50', number === 0 ? 'bg-accent text-accent-foreground' : redNumbers.has(number) ? 'bg-destructive text-foreground' : 'bg-secondary text-foreground', active && 'selected-cell text-primary')}><span>{number}</span>{active && <span className="absolute right-1 top-1 size-1 rounded-full bg-primary" />}</button>
  }

  return (
    <section aria-labelledby="betting-heading" className="flex flex-col gap-3">
      <div className="flex items-end justify-between gap-4"><div><p className="text-[9px] font-medium uppercase tracking-[0.24em] text-primary">Control deck</p><h2 id="betting-heading" className="text-lg font-semibold tracking-tight">Place your bets</h2></div><p className="text-[10px] text-muted-foreground">Straight-up bets</p></div>
      <div className="betting-deck overflow-x-auto rounded-2xl border border-border p-2">
        <div className="grid min-w-[570px] grid-cols-[44px_1fr] overflow-hidden rounded-xl">
          <div className="row-span-3 flex">{numberButton(0)}</div>
          {rows.map((row, index) => <div key={index} className="grid grid-cols-12">{row.map(numberButton)}</div>)}
          <div className="col-start-2 grid grid-cols-3">{['1st 12','2nd 12','3rd 12'].map(label => <button type="button" disabled={disabled} key={label} className="min-h-9 border border-border bg-card text-[10px] font-medium text-muted-foreground hover:text-primary">{label}</button>)}</div>
          <div className="col-start-2 grid grid-cols-6">{['1–18','EVEN','RED','BLACK','ODD','19–36'].map((label,index) => <button type="button" disabled={disabled} key={label} className={cn('min-h-9 border border-border text-[10px] font-bold', index === 2 ? 'bg-destructive' : index === 3 ? 'bg-secondary' : 'bg-card text-muted-foreground')}>{label}</button>)}</div>
        </div>
      </div>
    </section>
  )
}
