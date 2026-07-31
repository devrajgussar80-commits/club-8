/**
 * App State Management Module with LocalStorage Persistence
 */

const STORAGE_KEY = 'COLOR_PREDICTION_APP_STATE_V1';

// Initial default seed data
const DEFAULT_STATE = {
  viewMode: 'user', // 'user' or 'admin'
  soundEnabled: true,
  
  // User Account & Wallet
  user: {
    id: 'USR9842',
    name: 'Lucky Player',
    phone: '+91 98765 43210',
    balance: 1000.00,
    upiId: 'player98@upi'
  },

  // Active Game State
  activeRoom: 'parity',
  
  // Rounds State per room
  rounds: {
    parity: {
      currentPeriod: '20260724101',
      timeRemaining: 30,
      isFrozen: false,
      activeBets: [] // bets placed in current period
    },
    sapre: {
      currentPeriod: '',
      timeRemaining: 60,
      isFrozen: false,
      activeBets: []
    },
    bcone: {
      currentPeriod: '',
      timeRemaining: 180,
      isFrozen: false,
      activeBets: []
    },
    emerd: {
      currentPeriod: '',
      timeRemaining: 300,
      isFrozen: false,
      activeBets: []
    }
  },

  // History of completed period results (seed history for realistic charts)
  history: [
    { period: '20260724100', number: 7, color: 'green', size: 'Big', room: 'parity', timestamp: '21:40' },
    { period: '20260724099', number: 2, color: 'red', size: 'Small', room: 'parity', timestamp: '21:39' },
    { period: '20260724098', number: 0, color: 'violet', size: 'Small', room: 'parity', timestamp: '21:38' }, // 0 is Red+Violet
    { period: '20260724097', number: 3, color: 'green', size: 'Small', room: 'parity', timestamp: '21:37' },
    { period: '20260724096', number: 8, color: 'red', size: 'Big', room: 'parity', timestamp: '21:36' },
    { period: '20260724095', number: 5, color: 'violet', size: 'Big', room: 'parity', timestamp: '21:35' }, // 5 is Green+Violet
    { period: '20260724094', number: 9, color: 'green', size: 'Big', room: 'parity', timestamp: '21:34' },
    { period: '20260724093', number: 4, color: 'red', size: 'Small', room: 'parity', timestamp: '21:33' },
    { period: '20260724092', number: 1, color: 'green', size: 'Small', room: 'parity', timestamp: '21:32' },
    { period: '20260724091', number: 6, color: 'red', size: 'Big', room: 'parity', timestamp: '21:31' }
  ],

  // User Bet Orders History
  userBets: [
    { id: 'BET-901', period: '20260724100', selectType: 'color', selection: 'green', amount: 100, multiplier: 1, totalStake: 100, status: 'win', payout: 196, room: 'parity' },
    { id: 'BET-900', period: '20260724099', selectType: 'number', selection: '2', amount: 50, multiplier: 1, totalStake: 50, status: 'win', payout: 441, room: 'parity' }
  ],

  // Admin Prediction Control Engine
  admin: {
    upiId: 'adminpay@upi',
    qrUrl: 'https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=upi://pay?pa=adminpay@upi&pn=ColorPredictionGame',
    
    // Prediction Control Mode: 'auto_least' | 'manual' | 'random'
    predictionMode: 'auto_least', 
    
    // Forced result selection when in 'manual' mode
    forcedResult: {
      number: 7,
      color: 'green'
    },

    // Simulated bot bettor toggle
    botSimulatorEnabled: true
  },

  // UPI Transactions Ledger
  upiDeposits: [
    { id: 'DEP-8801', userId: 'USR9842', userName: 'Lucky Player', amount: 500, utr: '420918739102', timestamp: '20:15', status: 'approved' }
  ],
  
  upiWithdrawals: [
    { id: 'WTH-3301', userId: 'USR9842', userName: 'Lucky Player', amount: 200, upiId: 'player98@upi', timestamp: '19:40', status: 'paid' }
  ]
};

class StateManager {
  constructor() {
    this.state = this.loadState();
    this.listeners = [];
  }

  loadState() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        return JSON.parse(saved);
      }
    } catch (e) {
      console.error('Failed to load state from localStorage', e);
    }
    return JSON.parse(JSON.stringify(DEFAULT_STATE));
  }

  saveState() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this.state));
      this.notify();
    } catch (e) {
      console.error('Failed to save state', e);
    }
  }

  getState() {
    return this.state;
  }

  subscribe(listener) {
    this.listeners.push(listener);
    return () => {
      this.listeners = this.listeners.filter(l => l !== listener);
    };
  }

  notify() {
    this.listeners.forEach(l => l(this.state));
  }

  resetToDefault() {
    this.state = JSON.parse(JSON.stringify(DEFAULT_STATE));
    this.saveState();
  }
}

export const appState = new StateManager();
