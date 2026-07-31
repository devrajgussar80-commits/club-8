export type Symbol = {
  id: string;
  emoji: string;
  payout: number; // multiplier of bet for 3-of-a-kind
  weight: number; // relative probability on a reel
  color: string; // tailwind text color class for glow
};

// Weighted symbol set. Higher payout = lower weight (rarer).
export const SYMBOLS: Symbol[] = [
  { id: 'cherry', emoji: '🍒', payout: 2, weight: 30, color: 'text-rose-400' },
  { id: 'lemon', emoji: '🍋', payout: 3, weight: 26, color: 'text-amber-300' },
  { id: 'watermelon', emoji: '🍉', payout: 5, weight: 20, color: 'text-emerald-400' },
  { id: 'bell', emoji: '🔔', payout: 10, weight: 12, color: 'text-yellow-300' },
  { id: 'star', emoji: '⭐', payout: 20, weight: 7, color: 'text-yellow-200' },
  { id: 'diamond', emoji: '💎', payout: 50, weight: 3, color: 'text-cyan-300' },
  { id: 'seven', emoji: '7️⃣', payout: 100, weight: 2, color: 'text-red-400' },
];

const TOTAL_WEIGHT = SYMBOLS.reduce((s, x) => s + x.weight, 0);

export function randomSymbol(): Symbol {
  let r = Math.random() * TOTAL_WEIGHT;
  for (const s of SYMBOLS) {
    r -= s.weight;
    if (r <= 0) return s;
  }
  return SYMBOLS[0];
}

export function randomReel(): Symbol[] {
  return [randomSymbol(), randomSymbol(), randomSymbol()];
}

// Evaluate a 3x3 grid (3 reels, 3 visible rows). Center row is the payline.
// Also checks diagonals for extra flavor.
export type SpinResult = {
  reels: Symbol[][]; // [reel][row]
  win: number; // total payout in coins
  lines: { type: string; symbols: Symbol[]; payout: number }[];
};

export function evaluateSpin(reels: Symbol[][], bet: number): SpinResult {
  const lines: { type: string; symbols: Symbol[]; payout: number }[] = [];

  // Center row payline
  const center = [reels[0][1], reels[1][1], reels[2][1]];
  if (center[0].id === center[1].id && center[1].id === center[2].id) {
    lines.push({
      type: 'CENTER',
      symbols: center,
      payout: center[0].payout * bet,
    });
  }

  // Top row
  const top = [reels[0][0], reels[1][0], reels[2][0]];
  if (top[0].id === top[1].id && top[1].id === top[2].id) {
    lines.push({ type: 'TOP', symbols: top, payout: top[0].payout * bet });
  }

  // Bottom row
  const bottom = [reels[0][2], reels[1][2], reels[2][2]];
  if (bottom[0].id === bottom[1].id && bottom[1].id === bottom[2].id) {
    lines.push({
      type: 'BOTTOM',
      symbols: bottom,
      payout: bottom[0].payout * bet,
    });
  }

  // Diagonal: top-left -> center -> bottom-right
  const diag1 = [reels[0][0], reels[1][1], reels[2][2]];
  if (diag1[0].id === diag1[1].id && diag1[1].id === diag1[2].id) {
    lines.push({
      type: 'DIAGONAL ↘',
      symbols: diag1,
      payout: diag1[0].payout * bet,
    });
  }

  // Diagonal: bottom-left -> center -> top-right
  const diag2 = [reels[0][2], reels[1][1], reels[2][0]];
  if (diag2[0].id === diag2[1].id && diag2[1].id === diag2[2].id) {
    lines.push({
      type: 'DIAGONAL ↗',
      symbols: diag2,
      payout: diag2[0].payout * bet,
    });
  }

  const win = lines.reduce((s, l) => s + l.payout, 0);
  return { reels, win, lines };
}
