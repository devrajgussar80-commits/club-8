// Game statistics tracking

const STATS_STORAGE_KEY = 'slotmachine_stats';

export interface GameStats {
  totalSpins: number;
  totalBet: number;
  totalWon: number;
  totalLost: number;
  wins: number;
  losses: number;
  largestWin: number;
  session: {
    startTime: number;
    duration: number; // in minutes
    balance: number;
  };
}

const DEFAULT_STATS: GameStats = {
  totalSpins: 0,
  totalBet: 0,
  totalWon: 0,
  totalLost: 0,
  wins: 0,
  losses: 0,
  largestWin: 0,
  session: {
    startTime: Date.now(),
    duration: 0,
    balance: 1000,
  },
};

export const getStats = (): GameStats => {
  if (typeof window === 'undefined') {
    return DEFAULT_STATS;
  }

  const stored = localStorage.getItem(STATS_STORAGE_KEY);
  return stored ? JSON.parse(stored) : { ...DEFAULT_STATS, session: { ...DEFAULT_STATS.session, startTime: Date.now() } };
};

export const saveStats = (stats: GameStats): void => {
  if (typeof window === 'undefined') return;
  localStorage.setItem(STATS_STORAGE_KEY, JSON.stringify(stats));
};

export const recordSpin = (bet: number, winAmount: number): void => {
  const stats = getStats();
  stats.totalSpins++;
  stats.totalBet += bet;

  if (winAmount > 0) {
    stats.totalWon += winAmount;
    stats.wins++;
    stats.largestWin = Math.max(stats.largestWin, winAmount);
  } else {
    stats.totalLost += bet;
    stats.losses++;
  }

  saveStats(stats);
};

export const getROI = (): number => {
  const stats = getStats();
  if (stats.totalBet === 0) return 0;
  return ((stats.totalWon - stats.totalLost) / stats.totalBet) * 100;
};

export const getWinRate = (): number => {
  const stats = getStats();
  if (stats.totalSpins === 0) return 0;
  return (stats.wins / stats.totalSpins) * 100;
};

export const getSessionDuration = (): number => {
  const stats = getStats();
  return Math.floor((Date.now() - stats.session.startTime) / 60000); // minutes
};

export const resetStats = (): void => {
  if (typeof window === 'undefined') return;
  localStorage.removeItem(STATS_STORAGE_KEY);
};
