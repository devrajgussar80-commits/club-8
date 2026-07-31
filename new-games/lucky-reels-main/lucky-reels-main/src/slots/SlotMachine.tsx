import { useCallback, useEffect, useRef, useState } from 'react';
import { Coins, Plus, RotateCcw, Trophy, Volume2, VolumeX, Zap } from 'lucide-react';
import Reel from './Reel';
import {
  evaluateSpin,
  randomReel,
  randomSymbol,
  type SpinResult,
  type Symbol,
} from './symbols';

const BET_OPTIONS = [1, 5, 10, 25, 50, 100];
const STARTING_BALANCE = 1000;

type HistoryEntry = {
  id: number;
  reels: Symbol[][];
  bet: number;
  payout: number;
};

export default function SlotMachine() {
  const [balance, setBalance] = useState(STARTING_BALANCE);
  const [bet, setBet] = useState(5);
  const [reels, setReels] = useState<Symbol[][]>(() => [
    randomReel(),
    randomReel(),
    randomReel(),
  ]);
  const [spinning, setSpinning] = useState(false);
  const [stoppedCount, setStoppedCount] = useState(0);
  const [lastResult, setLastResult] = useState<SpinResult | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [bigWin, setBigWin] = useState(false);
  const [muted, setMuted] = useState(false);
  const [autoSpin, setAutoSpin] = useState(false);
  const [shake, setShake] = useState(false);
  const audioCtx = useRef<AudioContext | null>(null);
  const historyId = useRef(0);

  // --- Audio (Web Audio API, no assets) ---
  const playTone = useCallback(
    (freq: number, duration: number, type: OscillatorType = 'sine', vol = 0.08) => {
      if (muted) return;
      try {
        if (!audioCtx.current) {
          audioCtx.current = new (window.AudioContext ||
            (window as any).webkitAudioContext)();
        }
        const ctx = audioCtx.current;
        if (ctx.state === 'suspended') ctx.resume();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = type;
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(vol, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + duration);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start();
        osc.stop(ctx.currentTime + duration);
      } catch {
        /* ignore */
      }
    },
    [muted]
  );

  const playWinFanfare = useCallback(() => {
    if (muted) return;
    const notes = [523, 659, 784, 1047];
    notes.forEach((f, i) => {
      setTimeout(() => playTone(f, 0.18, 'triangle', 0.1), i * 90);
    });
  }, [muted, playTone]);

  const spin = useCallback(() => {
    if (spinning) return;
    if (balance < bet) {
      setShake(true);
      playTone(180, 0.12, 'square', 0.06);
      setTimeout(() => setShake(false), 400);
      return;
    }

    setBalance((b) => b - bet);
    setLastResult(null);
    setBigWin(false);
    setSpinning(true);
    setStoppedCount(0);
    playTone(440, 0.05, 'square', 0.05);

    // Precompute final result
    const finalReels = [randomReel(), randomReel(), randomReel()];
    const result = evaluateSpin(finalReels, bet);
    setReels(finalReels);
    // store result to apply when reels finish
    (spin as any)._pendingResult = result;
  }, [spinning, balance, bet, playTone]);

  const handleReelStopped = useCallback(() => {
    setStoppedCount((c) => {
      const next = c + 1;
      playTone(300 + next * 80, 0.06, 'square', 0.05);
      if (next === 3) {
        // all reels stopped
        const result: SpinResult = (spin as any)._pendingResult;
        setSpinning(false);
        if (result) {
          setLastResult(result);
          if (result.win > 0) {
            setBalance((b) => b + result.win);
            historyId.current += 1;
            setHistory((h) =>
              [
                {
                  id: historyId.current,
                  reels: result.reels,
                  bet,
                  payout: result.win,
                },
                ...h,
              ].slice(0, 20)
            );
            if (result.win >= bet * 20) {
              setBigWin(true);
              playWinFanfare();
            } else {
              playTone(660, 0.12, 'triangle', 0.08);
              setTimeout(() => playTone(880, 0.12, 'triangle', 0.08), 100);
            }
          } else {
            historyId.current += 1;
            setHistory((h) =>
              [
                {
                  id: historyId.current,
                  reels: result.reels,
                  bet,
                  payout: 0,
                },
                ...h,
              ].slice(0, 20)
            );
          }
        }
      }
      return next;
    });
  }, [bet, playTone, playWinFanfare, spin]);

  // Auto-spin loop
  useEffect(() => {
    if (!autoSpin || spinning) return;
    if (balance < bet) {
      setAutoSpin(false);
      return;
    }
    const t = setTimeout(() => spin(), 700);
    return () => clearTimeout(t);
  }, [autoSpin, spinning, balance, bet, spin]);

  const addCoins = () => {
    setBalance((b) => b + 500);
    playTone(880, 0.08, 'sine', 0.06);
    setTimeout(() => playTone(1320, 0.1, 'sine', 0.06), 80);
  };

  const resetGame = () => {
    setBalance(STARTING_BALANCE);
    setHistory([]);
    setLastResult(null);
    setBigWin(false);
    setAutoSpin(false);
  };

  const winLines = lastResult?.lines ?? [];
  const lastWin = lastResult?.win ?? 0;

  return (
    <div className="mx-auto w-full max-w-md px-4 pb-10">
      {/* Header */}
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-amber-400 to-yellow-600 shadow-lg shadow-amber-500/30">
            <Trophy className="h-5 w-5 text-slate-900" />
          </div>
          <div>
            <h1 className="text-lg font-extrabold tracking-tight text-amber-300">
              LUCKY REELS
            </h1>
            <p className="text-[10px] uppercase tracking-widest text-amber-200/50">
              Casino Slots
            </p>
          </div>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setMuted((m) => !m)}
            className="grid h-9 w-9 place-items-center rounded-lg bg-slate-800/80 text-slate-300 transition hover:bg-slate-700 active:scale-95"
            aria-label="Toggle sound"
          >
            {muted ? (
              <VolumeX className="h-4 w-4" />
            ) : (
              <Volume2 className="h-4 w-4" />
            )}
          </button>
          <button
            onClick={resetGame}
            className="grid h-9 w-9 place-items-center rounded-lg bg-slate-800/80 text-slate-300 transition hover:bg-slate-700 active:scale-95"
            aria-label="Reset game"
          >
            <RotateCcw className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Balance */}
      <div className="mb-3 flex items-center justify-between rounded-2xl border border-amber-500/20 bg-gradient-to-r from-slate-900 to-slate-800 px-4 py-3 shadow-lg">
        <div className="flex items-center gap-2">
          <Coins className="h-5 w-5 text-amber-400" />
          <span className="text-xs uppercase tracking-wider text-amber-200/60">
            Balance
          </span>
        </div>
        <span className="text-2xl font-black tabular-nums text-amber-300">
          {balance.toLocaleString()}
        </span>
      </div>

      {/* Machine */}
      <div
        className={`relative overflow-hidden rounded-3xl border border-amber-500/30 bg-gradient-to-b from-slate-800 to-slate-950 p-4 shadow-2xl shadow-amber-900/20 ${
          shake ? 'animate-[shake_0.4s]' : ''
        }`}
      >
        {/* decorative bulbs */}
        <div className="pointer-events-none absolute inset-x-0 top-0 flex justify-between px-3 py-1">
          {Array.from({ length: 9 }).map((_, i) => (
            <span
              key={i}
              className="h-1.5 w-1.5 rounded-full bg-amber-400/80 shadow-[0_0_6px_rgba(251,191,36,0.8)]"
              style={{ animation: `bulb 1.2s ${i * 0.12}s infinite` }}
            />
          ))}
        </div>

        {/* Reels window */}
        <div className="relative mt-4 grid grid-cols-3 gap-2 rounded-2xl border border-slate-700/60 bg-black/40 p-2">
          {reels.map((reel, i) => (
            <Reel
              key={i}
              finalSymbols={reel}
              spinning={spinning}
              delay={900 + i * 350}
              onStopped={handleReelStopped}
            />
          ))}
          {/* payline indicator */}
          <div className="pointer-events-none absolute inset-y-0 left-0 right-0 top-1/3 z-20 h-1/3 border-y-2 border-amber-400/0" />
        </div>

        {/* Win banner */}
        <div className="mt-3 h-10">
          {bigWin ? (
            <div className="flex items-center justify-center">
              <span
                className="bg-gradient-to-r from-amber-300 via-yellow-200 to-amber-400 bg-clip-text text-xl font-black text-transparent"
                style={{ animation: 'pulse 0.6s infinite' }}
              >
                ★ BIG WIN! +{lastWin.toLocaleString()} ★
              </span>
            </div>
          ) : lastWin > 0 ? (
            <div className="flex items-center justify-center">
              <span className="text-base font-bold text-emerald-400">
                WIN! +{lastWin.toLocaleString()} coins
              </span>
            </div>
          ) : lastResult ? (
            <div className="flex items-center justify-center">
              <span className="text-sm text-slate-500">No win — spin again!</span>
            </div>
          ) : (
            <div className="flex items-center justify-center">
              <span className="text-sm text-slate-500">
                Place your bet and pull the lever
              </span>
            </div>
          )}
        </div>

        {/* Winning lines chips */}
        {winLines.length > 0 && !spinning && (
          <div className="mt-1 flex flex-wrap justify-center gap-1.5">
            {winLines.map((l, i) => (
              <span
                key={i}
                className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-300"
              >
                {l.type} · {l.symbols[0].emoji} ×3 · +{l.payout}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Bet selector */}
      <div className="mt-4">
        <div className="mb-1.5 flex items-center justify-between px-1">
          <span className="text-xs uppercase tracking-wider text-slate-400">
            Bet
          </span>
          <span className="text-xs font-semibold text-amber-300">
            {bet} coins
          </span>
        </div>
        <div className="grid grid-cols-6 gap-1.5">
          {BET_OPTIONS.map((b) => (
            <button
              key={b}
              disabled={spinning}
              onClick={() => setBet(b)}
              className={`rounded-lg py-2 text-sm font-bold transition active:scale-95 disabled:opacity-50 ${
                bet === b
                  ? 'bg-gradient-to-b from-amber-400 to-amber-600 text-slate-900 shadow-md shadow-amber-500/30'
                  : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
              }`}
            >
              {b}
            </button>
          ))}
        </div>
      </div>

      {/* Controls */}
      <div className="mt-4 flex gap-2">
        <button
          onClick={spin}
          disabled={spinning || balance < bet}
          className="group relative flex-1 overflow-hidden rounded-2xl bg-gradient-to-b from-rose-500 to-red-700 py-4 text-lg font-black uppercase tracking-wider text-white shadow-lg shadow-red-900/40 transition active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-50"
        >
          <span className="relative z-10 flex items-center justify-center gap-2">
            <Zap className="h-5 w-5" />
            {spinning ? 'Spinning…' : 'SPIN'}
          </span>
          <span className="absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-white/30 to-transparent transition-transform duration-700 group-hover:translate-x-full" />
        </button>
        <button
          onClick={() => setAutoSpin((a) => !a)}
          className={`rounded-2xl px-4 py-4 text-sm font-bold uppercase tracking-wider transition active:scale-95 ${
            autoSpin
              ? 'bg-emerald-500 text-white shadow-lg shadow-emerald-900/40'
              : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
          }`}
        >
          Auto
        </button>
      </div>

      {/* Add coins when low */}
      {balance < bet && (
        <button
          onClick={addCoins}
          className="mt-3 flex w-full items-center justify-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 py-2.5 text-sm font-semibold text-amber-300 transition hover:bg-amber-500/20 active:scale-[0.98]"
        >
          <Plus className="h-4 w-4" /> Add 500 coins
        </button>
      )}

      {/* History */}
      {history.length > 0 && (
        <div className="mt-6">
          <h2 className="mb-2 px-1 text-xs uppercase tracking-wider text-slate-400">
            Recent Spins
          </h2>
          <div className="space-y-1.5">
            {history.map((h) => (
              <div
                key={h.id}
                className="flex items-center justify-between rounded-xl bg-slate-800/60 px-3 py-2"
              >
                <div className="flex items-center gap-1.5">
                  {h.reels.map((reel, ri) => (
                    <span key={ri} className="text-lg">
                      {reel[1].emoji}
                    </span>
                  ))}
                </div>
                <div className="flex items-center gap-3 text-xs">
                  <span className="text-slate-500">bet {h.bet}</span>
                  <span
                    className={`font-bold ${
                      h.payout > 0 ? 'text-emerald-400' : 'text-slate-600'
                    }`}
                  >
                    {h.payout > 0 ? `+${h.payout}` : '—'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Paytable */}
      <details className="mt-6 rounded-xl bg-slate-800/40 px-3 py-2 text-sm text-slate-300">
        <summary className="cursor-pointer text-xs uppercase tracking-wider text-slate-400">
          Paytable (× bet)
        </summary>
        <div className="mt-2 grid grid-cols-2 gap-1.5">
          {[
            ['🍒', 2],
            ['🍋', 3],
            ['🍉', 5],
            ['🔔', 10],
            ['⭐', 20],
            ['💎', 50],
            ['7️⃣', 100],
          ].map(([emoji, mult]) => (
            <div
              key={emoji}
              className="flex items-center justify-between rounded-lg bg-slate-900/60 px-2 py-1"
            >
              <span className="text-base">{emoji} ×3</span>
              <span className="font-semibold text-amber-300">×{mult}</span>
            </div>
          ))}
        </div>
        <p className="mt-2 text-[11px] text-slate-500">
          Wins counted on top, center, bottom rows and both diagonals.
        </p>
      </details>
    </div>
  );
}
