'use client';

import { useEffect, useRef, useState, useMemo, useCallback } from 'react';
import { Symbol as SymbolType, getRandomSymbol } from '@/lib/symbols';
import { Symbol } from './Symbol';

interface ReelProps {
  symbols: SymbolType[];
  isSpinning: boolean;
  stopDelay: number;
  onSpinComplete: () => void;
  winningRow?: number;
}

export function Reel({ symbols, isSpinning, stopDelay, onSpinComplete, winningRow }: ReelProps) {
  const [yOffset, setYOffset] = useState(0);
  const animationRef = useRef<number>();
  const startTimeRef = useRef<number>();
  const hasCompletedRef = useRef(false);

  const SYMBOL_HEIGHT = 100;
  const TOTAL_HEIGHT = SYMBOL_HEIGHT * 3;

  // Ensure symbols are always available
  const displaySymbols = useMemo(() => {
    return symbols && symbols.length > 0 ? symbols : [getRandomSymbol(), getRandomSymbol(), getRandomSymbol()];
  }, [symbols]);

  // Create an extended list for continuous looping
  const extendedSymbols = useMemo(() => {
    return [...displaySymbols, ...displaySymbols, ...displaySymbols, ...displaySymbols, ...displaySymbols];
  }, [displaySymbols]);

  useEffect(() => {
    if (!isSpinning) {
      setYOffset(SYMBOL_HEIGHT);
      hasCompletedRef.current = false;
      return;
    }

    startTimeRef.current = Date.now();
    hasCompletedRef.current = false;
    let currentY = 0;

    const spin = () => {
      const elapsed = Date.now() - (startTimeRef.current || 0);

      if (elapsed < stopDelay) {
        // Spinning phase - continuous smooth rotation
        const spinDuration = stopDelay;
        const spinDistance = 8 * TOTAL_HEIGHT; // 8 full rotations
        const easeProgress = Math.min(elapsed / spinDuration, 1);

        // Cubic easeOut for deceleration effect
        const easedProgress = 1 - Math.pow(1 - easeProgress, 3);
        currentY = spinDistance * easedProgress;

        setYOffset(currentY % TOTAL_HEIGHT);
        animationRef.current = requestAnimationFrame(spin);
      } else if (!hasCompletedRef.current) {
        // Stop phase - snap to center symbol (only once)
        hasCompletedRef.current = true;
        setYOffset(SYMBOL_HEIGHT);
        onSpinComplete();
      }
    };

    animationRef.current = requestAnimationFrame(spin);

    return () => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [isSpinning, stopDelay, SYMBOL_HEIGHT, TOTAL_HEIGHT, onSpinComplete]);

  return (
    <div className="relative w-20 h-20 sm:w-24 sm:h-24 overflow-hidden rounded-lg sm:rounded-xl border-2 border-yellow-500/60 bg-gradient-to-b from-slate-800 via-slate-900 to-slate-950 shadow-xl flex-shrink-0">
      {/* Reel mask with gradient */}
      <div className="absolute inset-0 bg-gradient-to-t from-slate-950 via-transparent to-slate-950 pointer-events-none z-10 opacity-50" />

      {/* Symbols container */}
      <div
        className="w-full transition-none"
        style={{
          transform: `translateY(-${yOffset}px)`,
        }}
      >
        {extendedSymbols.map((symbol, idx) => (
          <div
            key={idx}
            className="w-20 h-20 sm:w-24 sm:h-24 flex items-center justify-center flex-shrink-0"
          >
            <Symbol
              symbol={symbol}
              isWinning={!isSpinning && idx === displaySymbols.length + 1 && winningRow !== undefined}
              size={64}
            />
          </div>
        ))}
      </div>

      {/* Center indicator line */}
      <div className="absolute top-1/2 left-1 right-1 h-0.5 bg-yellow-400 transform -translate-y-1/2 pointer-events-none z-20 opacity-60" />
    </div>
  );
}
