import { Symbol } from './symbols';

export type PaylineType = 'horizontal' | 'diagonal' | 'v-shape' | 'z-shape';

export interface Payline {
  id: number;
  type: PaylineType;
  positions: [number, number][]; // [reel, row]
  multiplier: number;
}

export const PAYLINES: Payline[] = [
  // 5 horizontal paylines
  { id: 1, type: 'horizontal', positions: [[0, 0], [1, 0], [2, 0], [3, 0], [4, 0]], multiplier: 1 },
  { id: 2, type: 'horizontal', positions: [[0, 1], [1, 1], [2, 1], [3, 1], [4, 1]], multiplier: 1 },
  { id: 3, type: 'horizontal', positions: [[0, 2], [1, 2], [2, 2], [3, 2], [4, 2]], multiplier: 1 },
  
  // 2 diagonal paylines
  { id: 4, type: 'diagonal', positions: [[0, 0], [1, 1], [2, 2], [3, 1], [4, 0]], multiplier: 1.5 },
  { id: 5, type: 'diagonal', positions: [[0, 2], [1, 1], [2, 0], [3, 1], [4, 2]], multiplier: 1.5 },
  
  // 2 V-shape paylines
  { id: 6, type: 'v-shape', positions: [[0, 2], [1, 1], [2, 0], [3, 1], [4, 2]], multiplier: 1.2 },
  { id: 7, type: 'v-shape', positions: [[0, 0], [1, 1], [2, 2], [3, 1], [4, 0]], multiplier: 1.2 },
  
  // 2 Z-shape paylines
  { id: 8, type: 'z-shape', positions: [[0, 0], [1, 0], [2, 1], [3, 2], [4, 2]], multiplier: 1.3 },
  { id: 9, type: 'z-shape', positions: [[0, 2], [1, 2], [2, 1], [3, 0], [4, 0]], multiplier: 1.3 },
];

export interface WinResult {
  paylineIds: number[];
  winAmount: number;
  matches: {
    symbol: Symbol;
    paylineId: number;
    count: number;
    multiplier: number;
  }[];
}

export const PAYOUT_TABLE: Record<Symbol, Record<number, number>> = {
  cherry: { 3: 10, 4: 25, 5: 50 },
  lemon: { 3: 15, 4: 40, 5: 100 },
  orange: { 3: 15, 4: 40, 5: 100 },
  plum: { 3: 20, 4: 50, 5: 150 },
  grape: { 3: 20, 4: 50, 5: 150 },
  bell: { 3: 25, 4: 75, 5: 250 },
  melon: { 3: 30, 4: 100, 5: 400 },
  diamond: { 3: 50, 4: 250, 5: 1000 },
  lucky7: { 3: 100, 4: 500, 5: 2500 },
  gold: { 3: 150, 4: 750, 5: 5000 },
  crown: { 3: 200, 4: 1000, 5: 7500 },
  star: { 3: 500, 4: 2500, 5: 10000 },
};

export const checkWins = (reels: Symbol[][]): WinResult => {
  const matches: WinResult['matches'] = [];
  const winningPaylines: number[] = [];
  let totalWinAmount = 0;

  for (const payline of PAYLINES) {
    const symbolsOnPayline = payline.positions.map(([reel, row]) => reels[reel][row]);
    const firstSymbol = symbolsOnPayline[0];

    // Check if all 5 are matching
    if (symbolsOnPayline.every(s => s === firstSymbol)) {
      const payout = PAYOUT_TABLE[firstSymbol][5] || 0;
      const winAmount = Math.floor(payout * payline.multiplier);
      totalWinAmount += winAmount;
      winningPaylines.push(payline.id);
      matches.push({
        symbol: firstSymbol,
        paylineId: payline.id,
        count: 5,
        multiplier: payline.multiplier,
      });
      continue;
    }

    // Check for 4 matching (middle 4 or any consecutive 4)
    for (let i = 0; i <= 1; i++) {
      const fourSymbols = symbolsOnPayline.slice(i, i + 4);
      if (fourSymbols.every(s => s === fourSymbols[0])) {
        const payout = PAYOUT_TABLE[firstSymbol][4] || 0;
        const winAmount = Math.floor(payout * payline.multiplier);
        totalWinAmount += winAmount;
        if (!winningPaylines.includes(payline.id)) {
          winningPaylines.push(payline.id);
        }
        matches.push({
          symbol: fourSymbols[0],
          paylineId: payline.id,
          count: 4,
          multiplier: payline.multiplier,
        });
        break;
      }
    }

    // Check for 3 matching in a row
    for (let i = 0; i <= 2; i++) {
      const threeSymbols = symbolsOnPayline.slice(i, i + 3);
      if (threeSymbols.every(s => s === threeSymbols[0])) {
        const payout = PAYOUT_TABLE[threeSymbols[0]][3] || 0;
        const winAmount = Math.floor(payout * payline.multiplier);
        totalWinAmount += winAmount;
        if (!winningPaylines.includes(payline.id)) {
          winningPaylines.push(payline.id);
        }
        matches.push({
          symbol: threeSymbols[0],
          paylineId: payline.id,
          count: 3,
          multiplier: payline.multiplier,
        });
        break;
      }
    }
  }

  return {
    paylineIds: winningPaylines,
    winAmount: totalWinAmount,
    matches,
  };
};
