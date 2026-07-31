// Progressive Jackpot System

const JACKPOT_STORAGE_KEY = 'slotmachine_jackpot';
const JACKPOT_HITS_STORAGE_KEY = 'slotmachine_jackpot_hits';
const INITIAL_JACKPOT = 500;
const JACKPOT_INCREMENT = 0.5; // 50% of each bet goes to jackpot
const JACKPOT_WIN_THRESHOLD = 0.001; // 0.1% chance per spin

export interface JackpotState {
  amount: number;
  hits: number;
  lastHitDate?: string;
}

export const getJackpotState = (): JackpotState => {
  if (typeof window === 'undefined') {
    return { amount: INITIAL_JACKPOT, hits: 0 };
  }

  const stored = localStorage.getItem(JACKPOT_STORAGE_KEY);
  const storedHits = localStorage.getItem(JACKPOT_HITS_STORAGE_KEY);
  
  return {
    amount: stored ? parseFloat(stored) : INITIAL_JACKPOT,
    hits: storedHits ? parseInt(storedHits) : 0,
  };
};

export const saveJackpotState = (state: JackpotState): void => {
  if (typeof window === 'undefined') return;
  
  localStorage.setItem(JACKPOT_STORAGE_KEY, state.amount.toString());
  localStorage.setItem(JACKPOT_HITS_STORAGE_KEY, state.hits.toString());
};

export const addToJackpot = (bet: number): number => {
  const state = getJackpotState();
  const increment = bet * JACKPOT_INCREMENT;
  state.amount += increment;
  saveJackpotState(state);
  return state.amount;
};

export const checkJackpotWin = (): boolean => {
  // Random chance of winning jackpot
  return Math.random() < JACKPOT_WIN_THRESHOLD;
};

export const claimJackpot = (): number => {
  const state = getJackpotState();
  const amount = state.amount;
  
  // Reset jackpot after claim
  state.amount = INITIAL_JACKPOT;
  state.hits += 1;
  state.lastHitDate = new Date().toISOString();
  
  saveJackpotState(state);
  
  return amount;
};

export const resetJackpot = (): void => {
  const state: JackpotState = { amount: INITIAL_JACKPOT, hits: 0 };
  saveJackpotState(state);
};
