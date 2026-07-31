'use client';

import { motion, AnimatePresence } from 'framer-motion';
import { WinResult } from '@/lib/paylines';

interface WinDisplayProps {
  result: WinResult | null;
  isVisible: boolean;
  bet: number;
}

export function WinDisplay({ result, isVisible, bet }: WinDisplayProps) {
  if (!result || result.winAmount === 0) {
    return null;
  }

  const isMegaWin = result.winAmount >= bet * 10;
  const isJackpot = result.winAmount >= bet * 50;

  return (
    <AnimatePresence>
      {isVisible && (
        <motion.div
          className="fixed inset-0 flex items-center justify-center pointer-events-none z-40"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        >
          {/* Background flash */}
          <motion.div
            className="absolute inset-0 bg-gradient-to-br from-yellow-400/20 to-purple-400/20"
            initial={{ opacity: 0 }}
            animate={{ opacity: [0, 0.5, 0] }}
            transition={{ duration: 1.5 }}
          />

          {/* Win text container */}
          <motion.div
            className="relative text-center"
            initial={{ scale: 0, rotate: -180 }}
            animate={{ scale: 1, rotate: 0 }}
            exit={{ scale: 0, rotate: 180 }}
            transition={{ type: 'spring', stiffness: 100, damping: 15 }}
          >
            {/* Main win display */}
            <motion.div
              className={`px-8 py-6 rounded-2xl backdrop-blur-md border-2 ${
                isJackpot
                  ? 'bg-gradient-to-br from-yellow-500/20 to-red-500/20 border-yellow-400'
                  : isMegaWin
                    ? 'bg-gradient-to-br from-purple-500/20 to-pink-500/20 border-purple-400'
                    : 'bg-gradient-to-br from-emerald-500/20 to-cyan-500/20 border-emerald-400'
              }`}
              animate={{
                scale: [1, 1.05, 1],
                boxShadow: [
                  `0 0 20px ${isJackpot ? 'rgba(250, 204, 21, 0.5)' : isMegaWin ? 'rgba(168, 85, 247, 0.5)' : 'rgba(16, 185, 129, 0.5)'}`,
                  `0 0 40px ${isJackpot ? 'rgba(250, 204, 21, 0.8)' : isMegaWin ? 'rgba(168, 85, 247, 0.8)' : 'rgba(16, 185, 129, 0.8)'}`,
                  `0 0 20px ${isJackpot ? 'rgba(250, 204, 21, 0.5)' : isMegaWin ? 'rgba(168, 85, 247, 0.5)' : 'rgba(16, 185, 129, 0.5)'}`,
                ],
              }}
              transition={{ duration: 0.8, repeat: Infinity }}
            >
              {/* Win label */}
              <motion.p
                className={`text-sm font-bold uppercase tracking-widest mb-2 ${
                  isJackpot
                    ? 'text-yellow-400'
                    : isMegaWin
                      ? 'text-purple-400'
                      : 'text-emerald-400'
                }`}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.2 }}
              >
                {isJackpot ? '🎰 JACKPOT! 🎰' : isMegaWin ? '💥 MEGA WIN! 💥' : '🎉 YOU WIN! 🎉'}
              </motion.p>

              {/* Win amount */}
              <motion.p
                className={`text-5xl font-black mb-2 ${
                  isJackpot
                    ? 'text-yellow-300'
                    : isMegaWin
                      ? 'text-purple-300'
                      : 'text-emerald-300'
                }`}
                initial={{ scale: 0 }}
                animate={{ scale: 1 }}
                transition={{ delay: 0.3, type: 'spring', stiffness: 150 }}
              >
                ${result.winAmount.toLocaleString()}
              </motion.p>

              {/* Payline info */}
              <motion.p
                className="text-sm text-slate-300 mt-2"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.4 }}
              >
                {result.paylineIds.length} payline{result.paylineIds.length !== 1 ? 's' : ''} hit
              </motion.p>
            </motion.div>

            {/* Rotating circles background */}
            <motion.div
              className="absolute -inset-12 border-2 border-yellow-400/30 rounded-full"
              animate={{ rotate: 360 }}
              transition={{ duration: 4, repeat: Infinity, linear: true }}
            />
            <motion.div
              className="absolute -inset-16 border-2 border-purple-400/20 rounded-full"
              animate={{ rotate: -360 }}
              transition={{ duration: 6, repeat: Infinity, linear: true }}
            />
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
