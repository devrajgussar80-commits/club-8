import { useEffect, useRef, useState } from 'react';
import type { Symbol } from './symbols';
import { randomSymbol } from './symbols';

type Props = {
  finalSymbols: Symbol[]; // [top, center, bottom]
  spinning: boolean;
  delay: number; // ms before this reel stops
  onStopped: () => void;
};

const CELL_HEIGHT = 96; // px — fixed so the window never grows
const VISIBLE_ROWS = 3;
const STRIP_LENGTH = 24; // random symbols prepended before the final 3 land

export default function Reel({ finalSymbols, spinning, delay, onStopped }: Props) {
  const [strip, setStrip] = useState<Symbol[]>(() => finalSymbols);
  const [offset, setOffset] = useState(0); // px
  const [transition, setTransition] = useState('none');
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (!spinning) return;

    // Build a long strip of random symbols ending with the final 3.
    const body: Symbol[] = Array.from({ length: STRIP_LENGTH }, () => randomSymbol());
    const full = [...body, ...finalSymbols];
    setStrip(full);
    setTransition('none');
    setOffset(0);

    // Two rAFs: first lets React commit + paint the reset state (offset 0,
    // transition none), second kicks off the actual animated transition.
    // Without this the browser never records a "from" state and nothing animates.
    let raf2 = 0;
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => {
        const targetPx = (full.length - VISIBLE_ROWS) * CELL_HEIGHT;
        setTransition(`transform ${delay}ms cubic-bezier(0.18, 0.67, 0.3, 1)`);
        setOffset(targetPx);
      });
    });

    timer.current = window.setTimeout(() => {
      onStopped();
    }, delay + 60);

    return () => {
      cancelAnimationFrame(raf1);
      cancelAnimationFrame(raf2);
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spinning]);

  return (
    <div
      className="relative w-full overflow-hidden rounded-xl bg-gradient-to-b from-slate-900 via-slate-950 to-slate-900 shadow-inner"
      style={{ height: VISIBLE_ROWS * CELL_HEIGHT }}
    >
      {/* top & bottom fade */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-10 h-10 bg-gradient-to-b from-slate-950/90 to-transparent" />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-10 bg-gradient-to-t from-slate-950/90 to-transparent" />

      <div
        className="will-change-transform"
        style={{
          transform: `translateY(-${offset}px)`,
          transition,
        }}
      >
        {strip.map((s, i) => (
          <div
            key={i}
            className="flex w-full items-center justify-center"
            style={{ height: CELL_HEIGHT }}
          >
            <span
              className={`text-5xl drop-shadow-[0_0_12px_rgba(255,255,255,0.25)] sm:text-6xl ${s.color}`}
            >
              {s.emoji}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
