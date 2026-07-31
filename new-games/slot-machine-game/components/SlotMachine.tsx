'use client';

import { useState, useCallback, useEffect } from 'react';
import { motion } from 'framer-motion';
import { ReelContainer } from './ReelContainer';
import { HUD } from './HUD';
import { ControlPanel } from './ControlPanel';
import { WinDisplay } from './WinDisplay';
import { ParticleSystem } from './ParticleSystem';
import { JackpotDisplay } from './JackpotDisplay';
import { SoundToggle } from './SoundToggle';
import { Statistics } from './Statistics';
import { checkWins, WinResult } from '@/lib/paylines';
import { Symbol as SymbolType } from '@/lib/symbols';
import { getJackpotState, addToJackpot, checkJackpotWin, claimJackpot } from '@/lib/jackpot';
import { soundManager } from '@/lib/sounds';
import { recordSpin } from '@/lib/stats';

const INITIAL_BALANCE = 1000;

export function SlotMachine() {
  const [balance, setBalance] = useState(INITIAL_BALANCE);
  const [bet, setBet] = useState(10);
  const [isSpinning, setIsSpinning] = useState(false);
  const [lastWin, setLastWin] = useState(0);
  const [winResult, setWinResult] = useState<WinResult | null>(null);
  const [showWinDisplay, setShowWinDisplay] = useState(false);
  const [triggerParticles, setTriggerParticles] = useState(false);
  const [particleType, setParticleType] = useState<'win' | 'mega-win'>('win');
  const [jackpotAmount, setJackpotAmount] = useState(500);
  const [jackpotHits, setJackpotHits] = useState(0);
  const [showJackpotWin, setShowJackpotWin] = useState(false);

  // Initialize jackpot from storage
  useEffect(() => {
    const jackpot = getJackpotState();
    setJackpotAmount(jackpot.amount);
    setJackpotHits(jackpot.hits);
  }, []);

  const handleSpin = useCallback(() => {
    if (balance < bet || isSpinning) return;

    setBalance(prev => prev - bet);
    setIsSpinning(true);
    setLastWin(0);
    setWinResult(null);
    setShowWinDisplay(false);
    
    // Play spin sound
    soundManager.playSound('spin', 0.3);
    
    // Add to progressive jackpot
    const newJackpot = addToJackpot(bet);
    setJackpotAmount(newJackpot);
  }, [balance, bet, isSpinning]);

  const handleSpinComplete = useCallback(
    (reels: SymbolType[][]) => {
      setIsSpinning(false);

      // Check for wins after a short delay
      setTimeout(() => {
        const result = checkWins(reels);
        let totalWin = result.winAmount;

        // Check for progressive jackpot win
        if (checkJackpotWin()) {
          const jackpotWin = claimJackpot();
          totalWin += jackpotWin;
          setShowJackpotWin(true);
          
          // Play jackpot sound
          soundManager.playSound('jackpot', 0.7);
          
          // Simulate the massive explosion for jackpot
          setJackpotAmount(500); // Reset to base
          const jackpot = getJackpotState();
          setJackpotHits(jackpot.hits);
          
          setTimeout(() => {
            setShowJackpotWin(false);
          }, 5000);
        }

        // Record statistics
        recordSpin(bet, totalWin);

        if (totalWin > 0) {
          setWinResult({ ...result, winAmount: totalWin });
          setLastWin(totalWin);
          setBalance(prev => prev + totalWin);
          setShowWinDisplay(true);

          // Play win sound based on size
          if (totalWin >= bet * 50) {
            soundManager.playSound('jackpot', 0.7);
          } else if (totalWin >= bet * 10) {
            soundManager.playSound('win', 0.6);
          } else {
            soundManager.playSound('coin', 0.4);
          }

          // Determine particle type based on win size
          const particleType = totalWin >= bet * 50 ? 'mega-win' : totalWin >= bet * 10 ? 'mega-win' : 'win';
          setParticleType(particleType);
          setTriggerParticles(true);

          // Auto-hide win display after 3 seconds
          setTimeout(() => {
            setShowWinDisplay(false);
          }, 3000);
        }
      }, 200);
    },
    [bet]
  );

  const handleReset = useCallback(() => {
    setBalance(INITIAL_BALANCE);
    setBet(10);
    setLastWin(0);
    setWinResult(null);
    setShowWinDisplay(false);
  }, []);

  const handleBetChange = (newBet: number) => {
    if (!isSpinning) {
      setBet(Math.max(1, Math.min(newBet, Math.floor(balance / 10))));
    }
  };

  return (
    <>
      <SoundToggle />
      <Statistics />
      <motion.div
        className="w-full max-w-2xl mx-auto px-3 sm:px-4 py-4 sm:py-8 flex flex-col h-screen sm:h-auto"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.5 }}
      >
      {/* Title */}
      <motion.div
        className="text-center mb-4 sm:mb-12"
        initial={{ y: -20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ delay: 0.1 }}
      >
        <h1 className="text-3xl sm:text-5xl font-black text-transparent bg-clip-text bg-gradient-to-r from-yellow-400 via-yellow-300 to-yellow-400 mb-1 sm:mb-2 drop-shadow-lg">
          PREMIUM SLOTS
        </h1>
        <p className="text-slate-400 text-xs sm:text-sm uppercase tracking-widest">Experience the thrill of fortune</p>
      </motion.div>

      {/* HUD */}
      <HUD balance={balance} bet={bet} lastWin={lastWin} isSpinning={isSpinning} />

      {/* Jackpot Display */}
      <motion.div
        className="mb-3 sm:mb-6"
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.3 }}
      >
        <JackpotDisplay amount={jackpotAmount} hits={jackpotHits} isWinning={showJackpotWin} />
      </motion.div>

      {/* Main Game Area */}
      <motion.div
        className="bg-gradient-to-b from-slate-800 to-slate-900 border-3 border-yellow-500/30 rounded-2xl sm:rounded-3xl p-4 sm:p-8 mb-4 sm:mb-8 backdrop-blur-sm shadow-2xl flex-1 sm:flex-none"
        initial={{ scale: 0.95, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ delay: 0.2 }}
      >
        {/* Decorative top border */}
        <motion.div
          className="absolute -top-3 left-1/2 transform -translate-x-1/2 flex gap-2"
          animate={{ y: [0, -5, 0] }}
          transition={{ duration: 2, repeat: Infinity }}
        >
          {[...Array(5)].map((_, i) => (
            <div
              key={i}
              className="w-3 h-3 rounded-full bg-yellow-400"
              style={{
                boxShadow: '0 0 10px rgba(250, 204, 21, 0.8)',
              }}
            />
          ))}
        </motion.div>

        {/* Reel display area */}
        <div className="flex flex-col items-center gap-8 relative">
          {/* Payline indicator */}
          <div className="text-center">
            <p className="text-slate-300 text-xs uppercase tracking-widest">9 ACTIVE PAYLINES</p>
          </div>

          {/* Reels */}
          <motion.div
            className="my-4 sm:my-8 scale-90 sm:scale-100 origin-center"
          >
            <ReelContainer isSpinning={isSpinning} onSpinComplete={handleSpinComplete} />
          </motion.div>

          {/* Payline visualization */}
          <motion.div
            className="text-center text-xs sm:text-sm text-slate-400"
            animate={showWinDisplay ? { color: '#10B981' } : {}}
          >
            {winResult?.paylineIds.length ? (
              <p>Winning Paylines: {winResult.paylineIds.join(', ')}</p>
            ) : (
              <p>Ready to spin!</p>
            )}
          </motion.div>
        </div>

        {/* Decorative bottom border */}
        <motion.div
          className="absolute -bottom-3 left-1/2 transform -translate-x-1/2 flex gap-2"
          animate={{ y: [0, 5, 0] }}
          transition={{ duration: 2, repeat: Infinity }}
        >
          {[...Array(5)].map((_, i) => (
            <div
              key={i}
              className="w-3 h-3 rounded-full bg-yellow-400"
              style={{
                boxShadow: '0 0 10px rgba(250, 204, 21, 0.8)',
              }}
            />
          ))}
        </motion.div>
      </motion.div>

      {/* Control Panel */}
      <ControlPanel
        balance={balance}
        bet={bet}
        onBetChange={handleBetChange}
        onSpin={handleSpin}
        isSpinning={isSpinning}
        onReset={handleReset}
      />

      {/* Win Display Overlay */}
      <WinDisplay result={winResult} isVisible={showWinDisplay} bet={bet} />

      {/* Particle System */}
      <ParticleSystem trigger={triggerParticles} type={particleType} />

      {/* Game Info Footer */}
      <motion.div
        className="mt-4 sm:mt-12 text-center text-slate-500 text-xs space-y-1"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.5 }}
      >
        <p>Test your luck with this premium slot machine experience</p>
        <p className="hidden sm:block">Balance: 🎯 • Bet: 💰 • Win: 🎉</p>
      </motion.div>
      </motion.div>
    </>
  );
}
