'use client';

import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Symbol as SymbolType, getSymbolEmoji, SYMBOL_COLORS } from '@/lib/symbols';

interface SymbolProps {
  symbol: SymbolType;
  isWinning?: boolean;
  size?: number;
}

export function Symbol({ symbol, isWinning = false, size = 80 }: SymbolProps) {
  const [mounted, setMounted] = useState(false);
  
  useEffect(() => {
    setMounted(true);
  }, []);

  const colors = SYMBOL_COLORS[symbol];
  const emoji = getSymbolEmoji(symbol);

  const baseStyle = {
    width: size,
    height: size,
    background: `linear-gradient(135deg, ${colors.bg} 0%, ${colors.glow} 100%)`,
    boxShadow: isWinning
      ? `0 0 20px ${colors.glow}, 0 0 40px ${colors.glow}, inset 0 0 10px rgba(255,255,255,0.3)`
      : `0 0 10px ${colors.glow}, inset 0 0 5px rgba(255,255,255,0.2)`,
  };

  if (!mounted) {
    return (
      <div className="flex items-center justify-center rounded-lg relative" style={baseStyle}>
        <span className="text-4xl">{emoji}</span>
      </div>
    );
  }

  return (
    <motion.div
      className="flex items-center justify-center rounded-lg relative"
      style={baseStyle}
      animate={
        isWinning
          ? {
              scale: [1, 1.1, 1],
              boxShadow: [
                `0 0 20px ${colors.glow}`,
                `0 0 40px ${colors.glow}, 0 0 60px ${colors.glow}`,
                `0 0 20px ${colors.glow}`,
              ],
            }
          : {}
      }
      transition={
        isWinning
          ? {
              duration: 0.6,
              repeat: Infinity,
              repeatType: 'loop',
            }
          : {}
      }
    >
      <span className="text-4xl">{emoji}</span>
      
      {isWinning && (
        <motion.div
          className="absolute inset-0 rounded-lg border-2"
          style={{ borderColor: colors.glow }}
          animate={{
            boxShadow: [
              `inset 0 0 10px ${colors.glow}`,
              `inset 0 0 20px ${colors.glow}`,
              `inset 0 0 10px ${colors.glow}`,
            ],
          }}
          transition={{
            duration: 0.6,
            repeat: Infinity,
          }}
        />
      )}
    </motion.div>
  );
}
