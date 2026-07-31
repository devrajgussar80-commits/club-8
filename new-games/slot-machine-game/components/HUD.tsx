'use client';

import { motion } from 'framer-motion';

interface HUDProps {
  balance: number;
  bet: number;
  lastWin: number;
  isSpinning: boolean;
}

export function HUD({ balance, bet, lastWin, isSpinning }: HUDProps) {
  return (
    <div className="grid grid-cols-3 gap-2 sm:gap-4 mb-4 sm:mb-8">
      {/* Balance */}
      <motion.div
        className="bg-gradient-to-br from-slate-800 to-slate-900 border-2 border-emerald-500/50 rounded-lg p-2 sm:p-4 text-center backdrop-blur-sm"
        animate={
          lastWin > 0
            ? {
                boxShadow: [
                  '0 0 10px rgba(34, 197, 94, 0.3)',
                  '0 0 30px rgba(34, 197, 94, 0.6)',
                  '0 0 10px rgba(34, 197, 94, 0.3)',
                ],
              }
            : {}
        }
        transition={{ duration: 0.6 }}
      >
        <p className="text-emerald-400 text-xs font-semibold mb-1 uppercase tracking-wider">Balance</p>
        <motion.p
          className="text-emerald-300 text-lg sm:text-3xl font-bold"
          key={balance}
          animate={{ scale: [1, 1.1, 1] }}
          transition={{ duration: 0.3 }}
        >
          ${balance.toLocaleString()}
        </motion.p>
      </motion.div>

      {/* Current Bet */}
      <motion.div
        className="bg-gradient-to-br from-slate-800 to-slate-900 border-2 border-yellow-500/50 rounded-lg p-2 sm:p-4 text-center backdrop-blur-sm"
        animate={isSpinning ? { scale: [1, 0.95, 1] } : {}}
        transition={{ duration: 0.2 }}
      >
        <p className="text-yellow-400 text-xs font-semibold mb-1 uppercase tracking-wider">Bet</p>
        <p className="text-yellow-300 text-lg sm:text-3xl font-bold">${bet.toLocaleString()}</p>
      </motion.div>

      {/* Last Win */}
      <motion.div
        className={`bg-gradient-to-br from-slate-800 to-slate-900 border-2 rounded-lg p-2 sm:p-4 text-center backdrop-blur-sm ${
          lastWin > 0 ? 'border-purple-500/50' : 'border-slate-700/50'
        }`}
        animate={
          lastWin > 0
            ? {
                scale: [1, 1.05, 1],
                boxShadow: [
                  '0 0 10px rgba(168, 85, 247, 0.3)',
                  '0 0 30px rgba(168, 85, 247, 0.6)',
                  '0 0 10px rgba(168, 85, 247, 0.3)',
                ],
              }
            : {}
        }
        transition={{ duration: 0.6 }}
      >
        <p className={`text-xs font-semibold mb-1 uppercase tracking-wider ${lastWin > 0 ? 'text-purple-400' : 'text-slate-400'}`}>
          Last Win
        </p>
        <motion.p
          className={`text-lg sm:text-3xl font-bold ${lastWin > 0 ? 'text-purple-300' : 'text-slate-500'}`}
          key={lastWin}
          animate={lastWin > 0 ? { scale: [1, 1.2, 1] } : {}}
          transition={{ duration: 0.3 }}
        >
          ${lastWin.toLocaleString()}
        </motion.p>
      </motion.div>
    </div>
  );
}
