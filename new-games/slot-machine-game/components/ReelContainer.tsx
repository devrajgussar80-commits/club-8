'use client';

import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { motion } from 'framer-motion';
import { Reel } from './Reel';
import { Symbol as SymbolType, getRandomSymbol } from '@/lib/symbols';

interface ReelContainerProps {
  isSpinning: boolean;
  onSpinComplete: (results: SymbolType[][]) => void;
}

export function ReelContainer({ isSpinning, onSpinComplete }: ReelContainerProps) {
  const [reels, setReels] = useState<SymbolType[][]>([]);
  const [completedReels, setCompletedReels] = useState<Set<number>>(new Set());
  const reelsRef = useRef<SymbolType[][]>([]);
  const onSpinCompleteRef = useRef(onSpinComplete);
  
  const STOP_DELAYS = [500, 800, 1100, 1300, 1500];

  // Update ref when onSpinComplete changes
  useEffect(() => {
    onSpinCompleteRef.current = onSpinComplete;
  }, [onSpinComplete]);

  // Initialize reels when spinning starts
  useEffect(() => {
    if (isSpinning) {
      const newReels = Array(5)
        .fill(null)
        .map(() => [getRandomSymbol(), getRandomSymbol(), getRandomSymbol()]);
      
      reelsRef.current = newReels;
      setReels(newReels);
      setCompletedReels(new Set());
    }
  }, [isSpinning]);

  const handleReelComplete = useCallback((reelIndex: number) => {
    setCompletedReels(prev => {
      const newSet = new Set(prev);
      newSet.add(reelIndex);
      
      // When all 5 reels are complete, call onSpinComplete
      if (newSet.size === 5) {
        onSpinCompleteRef.current(reelsRef.current);
      }
      
      return newSet;
    });
  }, []);

  // Ensure reels always have content
  const displayReels = useMemo(() => {
    if (reels.length === 0) {
      return Array(5).fill([getRandomSymbol(), getRandomSymbol(), getRandomSymbol()]);
    }
    return reels;
  }, [reels]);

  // Create stable callbacks for each reel
  const reelCallbacks = useMemo(() => {
    return [0, 1, 2, 3, 4].map(idx => () => handleReelComplete(idx));
  }, [handleReelComplete]);

  return (
    <motion.div
      className="flex gap-4 justify-center items-center px-4"
      initial={{ scale: 0.95, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ duration: 0.3 }}
    >
      {displayReels.map((symbols, idx) => (
        <Reel
          key={idx}
          symbols={symbols}
          isSpinning={isSpinning}
          stopDelay={STOP_DELAYS[idx]}
          onSpinComplete={reelCallbacks[idx]}
        />
      ))}
    </motion.div>
  );
}
