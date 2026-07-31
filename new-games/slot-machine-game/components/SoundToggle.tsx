'use client';

import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { soundManager } from '@/lib/sounds';

export function SoundToggle() {
  const [isMuted, setIsMuted] = useState(false);

  useEffect(() => {
    setIsMuted(soundManager.isMuted());
  }, []);

  const handleToggle = () => {
    soundManager.toggleMute();
    setIsMuted(!isMuted);
  };

  return (
    <motion.button
      onClick={handleToggle}
      className="fixed top-4 right-4 z-50 p-3 rounded-full bg-gradient-to-r from-yellow-500/20 to-yellow-600/20 border-2 border-yellow-500/60 hover:border-yellow-400 transition-all hover:scale-110 active:scale-95"
      whileHover={{ scale: 1.1 }}
      whileTap={{ scale: 0.95 }}
    >
      <div className="text-2xl">
        {isMuted ? '🔇' : '🔊'}
      </div>
    </motion.button>
  );
}
