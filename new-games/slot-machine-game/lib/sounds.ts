// Audio utilities for sound effects using Web Audio API

export type SoundEffect = 'spin' | 'win' | 'jackpot' | 'click' | 'coin';

export interface AudioContextManager {
  playSound: (effect: SoundEffect, volume?: number) => void;
  toggleMute: () => void;
  isMuted: () => boolean;
}

let audioContext: AudioContext | null = null;
let isMutedState = false;

const getAudioContext = (): AudioContext => {
  if (!audioContext && typeof window !== 'undefined') {
    const AudioContextClass = (window as any).AudioContext || (window as any).webkitAudioContext;
    audioContext = new AudioContextClass();
  }
  return audioContext!;
};

const playTone = (frequency: number, duration: number, volume: number = 0.5) => {
  if (isMutedState || !audioContext) return;

  try {
    const now = audioContext.currentTime;
    const osc = audioContext.createOscillator();
    const gain = audioContext.createGain();

    osc.connect(gain);
    gain.connect(audioContext.destination);

    osc.frequency.value = frequency;
    gain.gain.setValueAtTime(volume, now);
    gain.gain.exponentialRampToValueAtTime(0.01, now + duration);

    osc.start(now);
    osc.stop(now + duration);
  } catch (e) {
    console.log('[v0] Audio error:', e);
  }
};

const playSpinSound = (volume: number = 0.3) => {
  if (isMutedState || !audioContext) return;

  // Spinning wheel sound - descending frequencies
  const frequencies = [800, 700, 600, 500];
  frequencies.forEach((freq, idx) => {
    setTimeout(() => {
      playTone(freq, 0.15, volume);
    }, idx * 100);
  });
};

const playWinSound = (volume: number = 0.5) => {
  if (isMutedState || !audioContext) return;

  // Winning jingle - ascending tones
  const frequencies = [523, 659, 784]; // C5, E5, G5
  frequencies.forEach((freq, idx) => {
    setTimeout(() => {
      playTone(freq, 0.3, volume);
    }, idx * 150);
  });
};

const playJackpotSound = (volume: number = 0.7) => {
  if (isMutedState || !audioContext) return;

  // Jackpot sound - repeating winning pattern
  const pattern = [523, 659, 784, 1047]; // C5, E5, G5, C6
  for (let i = 0; i < 3; i++) {
    pattern.forEach((freq, idx) => {
      setTimeout(() => {
        playTone(freq, 0.2, volume);
      }, (i * 400) + idx * 100);
    });
  }
};

const playClickSound = (volume: number = 0.2) => {
  if (isMutedState || !audioContext) return;

  // Simple click sound
  playTone(400, 0.05, volume);
};

const playCoinSound = (volume: number = 0.4) => {
  if (isMutedState || !audioContext) return;

  // Coin dropping sound - descending tone
  playTone(800, 0.1, volume);
  setTimeout(() => {
    playTone(600, 0.1, volume);
  }, 50);
  setTimeout(() => {
    playTone(400, 0.1, volume);
  }, 100);
};

export const soundManager: AudioContextManager = {
  playSound: (effect: SoundEffect, volume: number = 0.5) => {
    // Ensure audio context is created on first user interaction
    if (!audioContext && typeof window !== 'undefined') {
      try {
        const ctx = getAudioContext();
        if (ctx.state === 'suspended') {
          ctx.resume();
        }
      } catch (e) {
        console.log('[v0] Audio context error:', e);
      }
    }

    switch (effect) {
      case 'spin':
        playSpinSound(volume);
        break;
      case 'win':
        playWinSound(volume);
        break;
      case 'jackpot':
        playJackpotSound(volume);
        break;
      case 'click':
        playClickSound(volume);
        break;
      case 'coin':
        playCoinSound(volume);
        break;
      default:
        break;
    }
  },

  toggleMute: () => {
    isMutedState = !isMutedState;
    if (typeof window !== 'undefined') {
      localStorage.setItem('slotmachine_muted', isMutedState.toString());
    }
  },

  isMuted: () => isMutedState,
};

// Initialize mute state from storage
if (typeof window !== 'undefined') {
  const saved = localStorage.getItem('slotmachine_muted');
  isMutedState = saved ? JSON.parse(saved) : false;
}
