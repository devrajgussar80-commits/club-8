/**
 * Deterministic WinGo room clock.
 *
 * Keep period generation in one place so the frontend and backend contract is
 * easy to audit. Period format: YYYYMMDD + room code + 8-digit epoch slot.
 */

export const ROOM_CONFIG = Object.freeze({
  parity: Object.freeze({ duration: 30, code: '30', label: '30 sec' }),
  sapre: Object.freeze({ duration: 60, code: '01', label: '1 Min' }),
  bcone: Object.freeze({ duration: 180, code: '03', label: '3 Min' }),
  emerd: Object.freeze({ duration: 300, code: '05', label: '5 Min' })
});

export function getRoomClock(room, nowMs = Date.now()) {
  const config = ROOM_CONFIG[room] || ROOM_CONFIG.parity;
  const now = new Date(nowMs);
  const datePart = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, '0'),
    String(now.getDate()).padStart(2, '0')
  ].join('');
  const epochSeconds = Math.floor(nowMs / 1000);
  const slot = Math.floor(epochSeconds / config.duration);
  const slotPart = String(slot % 100000000).padStart(8, '0');

  return {
    duration: config.duration,
    period: `${datePart}${config.code}${slotPart}`,
    timeRemaining: config.duration - (epochSeconds % config.duration)
  };
}
