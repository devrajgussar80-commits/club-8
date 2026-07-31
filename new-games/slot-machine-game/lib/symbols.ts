export type Symbol = 'cherry' | 'lemon' | 'orange' | 'plum' | 'grape' | 'bell' | 'melon' | 'diamond' | 'lucky7' | 'gold' | 'crown' | 'star';

export const SYMBOLS: Symbol[] = [
  'cherry',
  'lemon',
  'orange',
  'plum',
  'grape',
  'bell',
  'melon',
  'diamond',
  'lucky7',
  'gold',
  'crown',
  'star',
];

export const SYMBOL_WEIGHTS: Record<Symbol, number> = {
  cherry: 15,
  lemon: 15,
  orange: 12,
  plum: 12,
  grape: 10,
  bell: 10,
  melon: 8,
  diamond: 7,
  lucky7: 5,
  gold: 3,
  crown: 2,
  star: 1,
};

export const SYMBOL_COLORS: Record<Symbol, { bg: string; glow: string }> = {
  cherry: { bg: '#DC2626', glow: '#FCA5A5' },
  lemon: { bg: '#FBBF24', glow: '#FCD34D' },
  orange: { bg: '#F97316', glow: '#FDBA74' },
  plum: { bg: '#A855F7', glow: '#D8B4FE' },
  grape: { bg: '#7C3AED', glow: '#C4B5FD' },
  bell: { bg: '#EC4899', glow: '#F472B6' },
  melon: { bg: '#06B6D4', glow: '#67E8F9' },
  diamond: { bg: '#06B6D4', glow: '#A5F3FC' },
  lucky7: { bg: '#FBBF24', glow: '#FDE047' },
  gold: { bg: '#CA8A04', glow: '#FACC15' },
  crown: { bg: '#D97706', glow: '#FCD34D' },
  star: { bg: '#8B5CF6', glow: '#E9D5FF' },
};

export const getRandomSymbol = (): Symbol => {
  const totalWeight = Object.values(SYMBOL_WEIGHTS).reduce((a, b) => a + b, 0);
  let random = Math.random() * totalWeight;
  
  for (const symbol of SYMBOLS) {
    random -= SYMBOL_WEIGHTS[symbol];
    if (random <= 0) return symbol;
  }
  
  return SYMBOLS[0];
};

export const getSymbolEmoji = (symbol: Symbol): string => {
  const emojis: Record<Symbol, string> = {
    cherry: '🍒',
    lemon: '🍋',
    orange: '🍊',
    plum: '🍑',
    grape: '🍇',
    bell: '🔔',
    melon: '🍉',
    diamond: '💎',
    lucky7: '7️⃣',
    gold: '🏆',
    crown: '👑',
    star: '⭐',
  };
  return emojis[symbol];
};
