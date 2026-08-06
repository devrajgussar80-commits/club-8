/**
 * The thin bar across the top of the app while it is loading something.
 *
 * Two things make it feel like progress rather than a spinner:
 *
 * 1. It eases towards 90% over a five second budget and stops there. It never
 *    reaches the end on its own, because it does not know how long the request
 *    will take -- what it does know is that the bar must not sit still, which
 *    is what makes a slow screen read as a frozen one.
 * 2. When the work finishes it runs to 100% and fades, so the end is always
 *    the request completing and never a timer expiring.
 *
 * Requests are counted, not tracked individually: a screen that loads four
 * things shows one bar that ends when the last of them lands.
 */

// How long the crawl takes to reach its ceiling. Past this the bar holds at
// STALL_AT until the work actually finishes.
const BUDGET_MS = 5000;
const STALL_AT = 0.9;
const FADE_MS = 320;

class ProgressBar {
  constructor() {
    this.pending = 0;
    this.frame = null;
    this.startedAt = 0;
    this.el = null;
    this.fill = null;
  }

  mount() {
    if (this.el) return;
    this.el = document.createElement('div');
    this.el.className = 'app-progress';
    this.el.setAttribute('role', 'progressbar');
    this.el.setAttribute('aria-hidden', 'true');
    this.fill = document.createElement('span');
    this.el.appendChild(this.fill);
    document.body.appendChild(this.el);
  }

  /** Begin, or join, the current load. */
  start() {
    this.mount();
    this.pending += 1;
    if (this.pending > 1) return;

    clearTimeout(this.hideTimer);
    this.startedAt = performance.now();
    this.el.classList.add('is-active');
    this.el.setAttribute('aria-hidden', 'false');
    this.set(0.08);  // Visible immediately, so a fast load still reads as one.
    this.tick();
  }

  /** One in-flight request finished. The bar ends when the last one does. */
  done() {
    if (this.pending === 0) return;
    this.pending -= 1;
    if (this.pending > 0) return;

    cancelAnimationFrame(this.frame);
    this.frame = null;
    this.set(1);
    this.hideTimer = setTimeout(() => {
      this.el.classList.remove('is-active');
      this.el.setAttribute('aria-hidden', 'true');
      // Reset only once it is invisible, or the bar is seen sliding back.
      this.set(0);
    }, FADE_MS);
  }

  tick() {
    this.frame = requestAnimationFrame(() => {
      const elapsed = (performance.now() - this.startedAt) / BUDGET_MS;
      // Ease out: quick at first, then slower, so the last stretch of a long
      // wait still moves without ever looking like it is about to finish.
      const eased = 1 - Math.pow(1 - Math.min(1, elapsed), 3);
      this.set(0.08 + eased * (STALL_AT - 0.08));
      if (this.pending > 0) this.tick();
    });
  }

  set(value) {
    if (!this.fill) return;
    this.fill.style.transform = `scaleX(${value})`;
    this.el.setAttribute('aria-valuenow', String(Math.round(value * 100)));
  }
}

export const progress = new ProgressBar();

/**
 * Wrap a promise so the bar covers it.
 *
 * Anything polled on a timer is deliberately left out by the caller: a bar
 * that reappeared every couple of seconds for the round clock would be noise,
 * not feedback.
 */
export async function withProgress(promise) {
  progress.start();
  try {
    return await promise;
  } finally {
    progress.done();
  }
}
