/**
 * Tap ripple.
 *
 * One delegated listener on the document rather than a listener per button:
 * the app builds most of its buttons at runtime (game boards, admin tables,
 * lottery tiles), so anything attached at load would miss them, and
 * re-attaching after every render leaks handlers.
 *
 * The JS only records where the tap landed and toggles a class — the growth
 * and fade are a CSS animation, so they run on the compositor and stay smooth
 * while the main thread is busy waiting on a bet.
 */

const RIPPLE_MS = 560;
// Elements whose own animation would fight a ripple, or where it would just
// be noise.
const SKIP = '.lot-tile, .dice-face, .slot-cell, input, select, textarea';

function ripple(event) {
  const target = event.target.closest(
    'button, [role="button"], .lobby-mini-card, .nav-item, .nd-tab, .nd-seg-btn'
  );
  if (!target || target.disabled || target.matches(SKIP)) return;

  const box = target.getBoundingClientRect();
  // Fall back to the centre for keyboard-triggered clicks, which report 0,0.
  const x = event.clientX ? event.clientX - box.left : box.width / 2;
  const y = event.clientY ? event.clientY - box.top : box.height / 2;

  target.style.setProperty('--rx', `${x}px`);
  target.style.setProperty('--ry', `${y}px`);
  target.classList.add('has-ripple');

  // Restart the animation even when the same button is tapped repeatedly.
  target.classList.remove('is-rippling');
  void target.offsetWidth;
  target.classList.add('is-rippling');

  clearTimeout(target._rippleTimer);
  target._rippleTimer = setTimeout(() => target.classList.remove('is-rippling'), RIPPLE_MS);
}

export function initInteractions() {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  document.addEventListener('pointerdown', ripple, { passive: true });
}
