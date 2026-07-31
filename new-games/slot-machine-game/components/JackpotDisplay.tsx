'use client';

import { motion } from 'framer-motion';

interface JackpotDisplayProps {
  amount: number;
  hits: number;
  isWinning?: boolean;
}

export function JackpotDisplay({ amount, hits, isWinning = false }: JackpotDisplayProps) {
  return (
    <motion.div
      className="bg-gradient-to-r from-yellow-900/40 to-yellow-800/40 border-2 border-yellow-500/60 rounded-xl p-4 backdrop-blur-sm"
      animate={isWinning ? { 
        boxShadow: [
          '0 0 0px rgba(250, 204, 21, 0)',
          '0 0 30px rgba(250, 204, 21, 1)',
          '0 0 0px rgba(250, 204, 21, 0)',
        ]
      } : {}}
      transition={isWinning ? { duration: 0.8, repeat: Infinity } : {}}
    >
      <div className="flex items-center justify-between">
        <div>
          <p className="text-yellow-400/80 text-xs uppercase tracking-widest mb-1">Progressive Jackpot</p>
          <motion.p
            className="text-3xl font-black text-transparent bg-clip-text bg-gradient-to-r from-yellow-400 to-yellow-300"
            animate={isWinning ? { scale: [1, 1.2, 1] } : {}}
            transition={isWinning ? { duration: 0.6, repeat: Infinity } : {}}
          >
            ${amount.toFixed(2)}
          </motion.p>
        </div>
        <div className="text-right">
          <p className="text-slate-400 text-xs uppercase tracking-widest">Times Won</p>
          <p className="text-2xl font-bold text-yellow-300">{hits}</p>
        </div>
      </div>
    </motion.div>
  );
}
