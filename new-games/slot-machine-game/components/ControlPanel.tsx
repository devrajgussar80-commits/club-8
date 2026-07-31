'use client';

import { motion } from 'framer-motion';

interface ControlPanelProps {
  balance: number;
  bet: number;
  onBetChange: (newBet: number) => void;
  onSpin: () => void;
  isSpinning: boolean;
  onReset: () => void;
}

export function ControlPanel({ balance, bet, onBetChange, onSpin, isSpinning, onReset }: ControlPanelProps) {
  const maxBet = Math.floor(balance / 10);
  const canSpin = !isSpinning && balance >= bet;
  const canIncreaseBet = bet < maxBet;
  const canDecreaseBet = bet > 1;

  const BetButton = ({ onClick, disabled, children, label }: any) => (
    <motion.button
      onClick={onClick}
      disabled={disabled}
      className={`px-4 py-2 rounded-lg font-semibold uppercase text-sm transition-all ${
        disabled
          ? 'bg-slate-700 text-slate-500 cursor-not-allowed'
          : 'bg-gradient-to-r from-yellow-500 to-yellow-600 text-slate-900 hover:shadow-lg hover:shadow-yellow-500/50 hover:scale-105 active:scale-95'
      }`}
      whileHover={disabled ? {} : { scale: 1.05 }}
      whileTap={disabled ? {} : { scale: 0.95 }}
    >
      {children}
    </motion.button>
  );

  return (
    <motion.div
      className="bg-gradient-to-b from-slate-800 to-slate-900 border-2 border-yellow-500/30 rounded-2xl p-3 sm:p-6 backdrop-blur-sm space-y-3 sm:space-y-4"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.2 }}
    >
      {/* Bet Controls */}
      <div className="space-y-2">
        <label className="block text-yellow-400 font-semibold text-xs sm:text-sm uppercase tracking-wider">Adjust Bet</label>
        <div className="flex gap-1 sm:gap-2">
          <BetButton
            onClick={() => onBetChange(Math.max(1, bet - 10))}
            disabled={!canDecreaseBet}
            label="Decrease"
          >
            <span className="hidden sm:inline">− $10</span>
            <span className="sm:hidden">−</span>
          </BetButton>

          <motion.div
            className="flex-1 bg-slate-950 border-2 border-yellow-500/30 rounded-lg flex items-center justify-center"
            animate={isSpinning ? { borderColor: ['rgba(234, 179, 8, 0.3)', 'rgba(234, 179, 8, 0.8)', 'rgba(234, 179, 8, 0.3)'] } : {}}
            transition={{ duration: 1, repeat: Infinity }}
          >
            <p className="text-yellow-400 font-bold text-sm sm:text-xl">${bet.toLocaleString()}</p>
          </motion.div>

          <BetButton
            onClick={() => onBetChange(Math.min(maxBet, bet + 10))}
            disabled={!canIncreaseBet}
            label="Increase"
          >
            <span className="hidden sm:inline">+ $10</span>
            <span className="sm:hidden">+</span>
          </BetButton>
        </div>
        <p className="text-slate-400 text-xs text-center">Max: ${maxBet.toLocaleString()}</p>
      </div>

      {/* Spin Button */}
      <motion.button
        onClick={onSpin}
        disabled={!canSpin}
        className={`w-full py-3 sm:py-4 rounded-lg font-bold text-base sm:text-lg uppercase tracking-widest transition-all relative overflow-hidden group ${
          canSpin
            ? 'bg-gradient-to-r from-red-600 via-red-500 to-red-600 text-white hover:shadow-2xl hover:shadow-red-500/50 text-shadow'
            : 'bg-slate-700 text-slate-500 cursor-not-allowed'
        }`}
        whileHover={canSpin ? { scale: 1.05 } : {}}
        whileTap={canSpin ? { scale: 0.95 } : {}}
        animate={
          isSpinning
            ? {
                boxShadow: [
                  '0 0 20px rgba(220, 38, 38, 0.5)',
                  '0 0 40px rgba(220, 38, 38, 0.8)',
                  '0 0 20px rgba(220, 38, 38, 0.5)',
                ],
              }
            : {}
        }
        transition={{ duration: 0.6, repeat: isSpinning ? Infinity : 0 }}
      >
        {isSpinning ? 'SPINNING...' : 'SPIN'}
      </motion.button>

      {/* Reset Button */}
      <motion.button
        onClick={onReset}
        disabled={isSpinning}
        className={`w-full py-2 rounded-lg font-semibold text-xs sm:text-sm uppercase transition-all ${
          isSpinning
            ? 'bg-slate-700 text-slate-500 cursor-not-allowed'
            : 'bg-slate-700 text-slate-300 hover:bg-slate-600 hover:text-slate-100'
        }`}
        whileHover={isSpinning ? {} : { scale: 1.02 }}
        whileTap={isSpinning ? {} : { scale: 0.98 }}
      >
        Reset Game
      </motion.button>
    </motion.div>
  );
}
