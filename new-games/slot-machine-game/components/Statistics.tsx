'use client';

import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { getStats, getROI, getWinRate, getSessionDuration } from '@/lib/stats';
import { GameStats } from '@/lib/stats';

export function Statistics() {
  const [showStats, setShowStats] = useState(false);
  const [stats, setStats] = useState<GameStats | null>(null);

  useEffect(() => {
    const loadStats = () => {
      setStats(getStats());
    };
    loadStats();
    const interval = setInterval(loadStats, 1000); // Update every second
    return () => clearInterval(interval);
  }, []);

  if (!stats) return null;

  const roi = getROI();
  const winRate = getWinRate();
  const sessionDuration = getSessionDuration();
  const netProfit = stats.totalWon - stats.totalLost;

  return (
    <>
      {/* Stats Toggle Button */}
      <motion.button
        onClick={() => setShowStats(!showStats)}
        className="fixed bottom-4 right-4 sm:bottom-6 sm:right-6 z-50 p-3 rounded-full bg-gradient-to-r from-purple-500/20 to-purple-600/20 border-2 border-purple-500/60 hover:border-purple-400 transition-all hover:scale-110 active:scale-95"
        whileHover={{ scale: 1.1 }}
        whileTap={{ scale: 0.95 }}
      >
        <div className="text-2xl">📊</div>
      </motion.button>

      {/* Statistics Panel */}
      <AnimatePresence>
        {showStats && (
          <motion.div
            className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm flex items-center justify-center p-4 sm:p-0"
            onClick={() => setShowStats(false)}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <motion.div
              onClick={e => e.stopPropagation()}
              className="bg-gradient-to-br from-slate-900 to-slate-950 border-2 border-purple-500/50 rounded-2xl p-6 max-w-md w-full max-h-96 overflow-y-auto"
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.9, opacity: 0 }}
            >
              <div className="flex justify-between items-center mb-6">
                <h2 className="text-2xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-purple-400 to-purple-300">
                  Game Statistics
                </h2>
                <button
                  onClick={() => setShowStats(false)}
                  className="text-slate-400 hover:text-slate-200 text-xl"
                >
                  ✕
                </button>
              </div>

              <div className="space-y-4">
                {/* Session Info */}
                <div className="bg-slate-800/50 border border-purple-500/30 rounded-lg p-4">
                  <p className="text-slate-400 text-xs uppercase tracking-wider mb-2">Session Time</p>
                  <p className="text-lg font-bold text-purple-300">{sessionDuration}m</p>
                </div>

                {/* Spins */}
                <div className="grid grid-cols-2 gap-4">
                  <div className="bg-slate-800/50 border border-blue-500/30 rounded-lg p-4">
                    <p className="text-slate-400 text-xs uppercase tracking-wider mb-1">Total Spins</p>
                    <p className="text-2xl font-bold text-blue-300">{stats.totalSpins}</p>
                  </div>
                  <div className="bg-slate-800/50 border border-emerald-500/30 rounded-lg p-4">
                    <p className="text-slate-400 text-xs uppercase tracking-wider mb-1">Win Rate</p>
                    <p className="text-2xl font-bold text-emerald-300">{winRate.toFixed(1)}%</p>
                  </div>
                </div>

                {/* Money Stats */}
                <div className="grid grid-cols-2 gap-4">
                  <div className="bg-slate-800/50 border border-yellow-500/30 rounded-lg p-4">
                    <p className="text-slate-400 text-xs uppercase tracking-wider mb-1">Total Bet</p>
                    <p className="text-lg font-bold text-yellow-300">${stats.totalBet.toLocaleString()}</p>
                  </div>
                  <div className="bg-slate-800/50 border border-emerald-500/30 rounded-lg p-4">
                    <p className="text-slate-400 text-xs uppercase tracking-wider mb-1">Total Won</p>
                    <p className="text-lg font-bold text-emerald-300">${stats.totalWon.toLocaleString()}</p>
                  </div>
                </div>

                {/* Net Result */}
                <div className={`bg-slate-800/50 border rounded-lg p-4 ${netProfit >= 0 ? 'border-emerald-500/30' : 'border-red-500/30'}`}>
                  <p className="text-slate-400 text-xs uppercase tracking-wider mb-2">Net Result</p>
                  <p className={`text-2xl font-bold ${netProfit >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                    {netProfit >= 0 ? '+' : ''} ${netProfit.toLocaleString()}
                  </p>
                </div>

                {/* ROI */}
                <div className={`bg-slate-800/50 border rounded-lg p-4 ${roi >= 0 ? 'border-purple-500/30' : 'border-red-500/30'}`}>
                  <p className="text-slate-400 text-xs uppercase tracking-wider mb-2">Return on Investment</p>
                  <p className={`text-2xl font-bold ${roi >= 0 ? 'text-purple-300' : 'text-red-300'}`}>
                    {roi >= 0 ? '+' : ''} {roi.toFixed(1)}%
                  </p>
                </div>

                {/* Wins vs Losses */}
                <div className="grid grid-cols-2 gap-4">
                  <div className="bg-slate-800/50 border border-green-500/30 rounded-lg p-4">
                    <p className="text-slate-400 text-xs uppercase tracking-wider mb-1">Wins</p>
                    <p className="text-2xl font-bold text-green-300">{stats.wins}</p>
                  </div>
                  <div className="bg-slate-800/50 border border-red-500/30 rounded-lg p-4">
                    <p className="text-slate-400 text-xs uppercase tracking-wider mb-1">Losses</p>
                    <p className="text-2xl font-bold text-red-300">{stats.losses}</p>
                  </div>
                </div>

                {/* Largest Win */}
                {stats.largestWin > 0 && (
                  <div className="bg-gradient-to-r from-yellow-500/20 to-yellow-600/20 border border-yellow-500/50 rounded-lg p-4">
                    <p className="text-slate-400 text-xs uppercase tracking-wider mb-2">Largest Win</p>
                    <p className="text-2xl font-bold text-yellow-300">${stats.largestWin.toLocaleString()}</p>
                  </div>
                )}
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
