/**
 * Reel-slot engine, driven entirely by the server.
 *
 * Both slot titles share this class -- Lucky Reels is 3x3 with one payout per
 * symbol, Mega Slots is 5x3 with runs of 3/4/5 -- because the only real
 * difference is the grid shape and the endpoint. The symbol set, the paylines
 * and the payouts are all fetched from that game's /paytable route, so the
 * browser never holds a second copy of the maths that could drift.
 *
 * IMPORTANT: the reels you see spinning are decoration. The outcome arrives
 * from POST /spin already decided, and the cells are then set to it. Nothing
 * the player does to this file can change what a spin pays -- the wallet is
 * debited and credited in one locked transaction on the server.
 */

const money = value => Number(value || 0).toFixed(2);

// Symbols per reel strip. Long enough that a spin travels a convincing
// distance before the result rolls into the window.
const STRIP_LEN = 14;
// Reels stop left to right; each one runs this much longer than the last.
const REEL_STAGGER_MS = 260;
const SPIN_MS = 900;

export class SlotEngine {
  /**
   * @param {object} options       shared bridge (toast, canPlay, api, ...)
   * @param {object} config        { game, prefix, reels, rows }
   *   `prefix` is the id prefix of this game's markup, e.g. "slots" ->
   *   #slots-grid, #slots-spin. `game` is the API path segment.
   */
  constructor(options, config) {
    Object.assign(this, options);
    this.config = config;
    this.amount = 10;
    this.busy = false;
    this.paytable = null;
    this.cells = [];
    this.cycler = 0;
  }

  el(name) {
    return document.getElementById(`${this.config.prefix}-${name}`);
  }

  init() {
    this.grid = this.el('grid');
    this.linesEl = this.el('lines');
    this.amountEl = this.el('amount');
    this.spinBtn = this.el('spin');
    this.winEl = this.el('win');
    this.walletEl = this.el('wallet');
    this.paytableEl = this.el('paytable');
    if (!this.grid) return;

    this.el('minus')?.addEventListener('click', () => this.setAmount(this.amount - 10));
    this.el('plus')?.addEventListener('click', () => this.setAmount(this.amount + 10));
    this.grid.closest('.slot-page')
      ?.querySelectorAll('[data-slot-multiplier]')
      .forEach(button => button.addEventListener('click',
        () => this.setAmount(this.amount * Number(button.dataset.slotMultiplier))));
    this.spinBtn?.addEventListener('click', () => this.spin());

    this.buildGrid();
    this.loadPaytable();
    this.render();
  }

  /**
   * A real reel, not a grid of cells that swap glyphs.
   *
   * Each reel is a window (`.slot-reel`) with a tall strip inside it. The
   * strip holds STRIP_LEN symbols; sliding it up by whole symbol-heights and
   * then snapping back to the top is what makes a continuous spin. The three
   * visible symbols are the LAST `rows` on the strip, so when the strip
   * finishes its travel the result is already sitting in the window.
   */
  buildGrid() {
    const { reels, rows } = this.config;
    this.grid.style.setProperty('--slot-reels', reels);
    this.grid.style.setProperty('--slot-rows', rows);
    this.grid.innerHTML = '';
    this.cells = [];
    this.strips = [];

    for (let reel = 0; reel < reels; reel += 1) {
      const window_ = document.createElement('div');
      window_.className = 'slot-reel';
      const strip = document.createElement('div');
      strip.className = 'slot-strip';

      for (let index = 0; index < STRIP_LEN; index += 1) {
        const cell = document.createElement('span');
        cell.className = 'slot-cell';
        cell.textContent = '❔';
        strip.appendChild(cell);
      }
      window_.appendChild(strip);
      this.grid.appendChild(window_);
      this.strips.push(strip);
      // The visible window is the tail of the strip.
      this.cells.push([...strip.children].slice(-rows));
    }
  }

  /**
   * Distance from one symbol to the next, in px.
   *
   * Measured between two real cells rather than computed from a height, so
   * the 6px CSS gap is included. Using the bare cell height here drifted the
   * reel by one gap per symbol and the result landed misaligned.
   */
  pitch() {
    const cells = this.strips[0]?.children;
    if (!cells || cells.length < 2) return 0;
    return cells[1].getBoundingClientRect().top - cells[0].getBoundingClientRect().top;
  }

  /** Total travel from the top of the strip to its resting position. */
  travel() {
    return (STRIP_LEN - this.config.rows) * this.pitch();
  }

  /** At rest the strip sits at translateY(0); CSS bottom-anchors it. */
  resetStrip(strip) {
    strip.style.transition = 'none';
    strip.style.transform = 'translateY(0px)';
  }

  /** Fill everything above the visible window with fresh random symbols. */
  reseedStrip(reel) {
    const cells = [...this.strips[reel].children];
    for (let index = 0; index < cells.length - this.config.rows; index += 1) {
      cells[index].textContent = this.randomSymbol();
    }
  }

  async loadPaytable() {
    try {
      this.paytable = await this.api(`/api/games/${this.config.game}/paytable`);
      this.emoji = Object.fromEntries(this.paytable.symbols.map(s => [s.id, s.emoji]));
      this.renderPaytable();
      this.fillRandom();
    } catch (error) {
      // Paytable is cosmetic; a spin still works without it.
      console.warn('paytable unavailable', error.message);
    }
  }

  renderPaytable() {
    if (!this.paytableEl || !this.paytable) return;
    this.paytableEl.innerHTML = this.paytable.symbols
      .slice()
      .reverse()
      .map(symbol => {
        const pays = typeof symbol.payout === 'number'
          ? `${symbol.payout}x`
          : Object.entries(symbol.pays).map(([count, value]) => `${count}→${value}`).join(' · ');
        return `<li><span>${symbol.emoji}</span><b>${pays}</b></li>`;
      })
      .join('');
  }

  randomSymbol() {
    const list = this.paytable?.symbols || [];
    if (!list.length) return '❔';
    return list[Math.floor(Math.random() * list.length)].emoji;
  }

  fillRandom() {
    this.strips.forEach(strip => {
      [...strip.children].forEach(cell => { cell.textContent = this.randomSymbol(); });
      this.resetStrip(strip);
    });
  }

  setAmount(value) {
    if (this.busy) return;
    this.amount = Math.max(10, Math.min(100000, Math.round(Number(value) || 10)));
    this.render();
  }

  render() {
    if (this.amountEl) this.amountEl.textContent = money(this.amount);
    if (this.walletEl) this.walletEl.textContent = money(this.getBalance());
    if (this.spinBtn) {
      this.spinBtn.disabled = this.busy;
      this.spinBtn.textContent = this.busy ? 'SPINNING…' : 'SPIN';
    }
  }

  clearHighlights() {
    this.cells.forEach(column => column.forEach(cell => cell.classList.remove('is-win')));
    if (this.linesEl) this.linesEl.innerHTML = '';
    if (this.winEl) this.winEl.textContent = '';
  }

  /**
   * Free-run the reels while the server round is in flight.
   *
   * The strip slides up by one symbol every tick and snaps back to the top
   * without a transition, so the motion never stops or shows a seam. A CSS
   * animation could not be used here because the spin has to keep going for
   * however long the network takes, which is not known up front.
   */
  startCycling() {
    clearInterval(this.cycler);
    const pitch = this.pitch();
    const limit = STRIP_LEN - this.config.rows;
    this.offsets = this.strips.map(() => 0);
    this.grid.classList.add('is-spinning');

    this.cycler = setInterval(() => {
      this.strips.forEach((strip, reel) => {
        if (this.settled > reel) return;
        this.offsets[reel] += 1;
        if (this.offsets[reel] > limit) {
          // Snap back to rest with fresh symbols above, no transition, so the
          // wrap is invisible and the reel reads as endless.
          this.offsets[reel] = 0;
          this.reseedStrip(reel);
          strip.style.transition = 'none';
        } else {
          strip.style.transition = 'transform 70ms linear';
        }
        strip.style.transform = `translateY(${this.offsets[reel] * pitch}px)`;
      });
    }, 70);
  }

  stopCycling() {
    clearInterval(this.cycler);
    this.grid.classList.remove('is-spinning');
  }

  async spin() {
    if (this.busy) return;
    if (!this.canPlay()) return this.denyPlay();
    if (this.amount > this.getBalance()) return this.toast('Wallet balance kam hai.', 'error');

    this.busy = true;
    this.settled = 0;
    this.clearHighlights();
    this.render();
    this.startCycling();

    // The reels always spin for at least this long, even on a fast reply --
    // a spin that resolves in 80ms reads as a broken machine, not a fast one.
    const floor = new Promise(resolve => setTimeout(resolve, SPIN_MS));

    let result;
    try {
      result = await this.api(`/api/games/${this.config.game}/spin`, 'POST', { amount: this.amount });
    } catch (error) {
      this.stopCycling();
      this.fillRandom();
      this.busy = false;
      this.render();
      return this.toast(error.message || 'Spin failed.', 'error');
    }
    await floor;

    await this.settleReels(result);
    this.stopCycling();
    this.busy = false;

    // The server's balance is authoritative -- take it rather than doing the
    // arithmetic again here, so a dropped response can never leave the two
    // numbers disagreeing.
    this.setBalance(result.balance);
    this.showResult(result);
    this.render();
  }

  /**
   * Bring the reels to rest on the result, left to right.
   *
   * The winning symbols are written into the tail of the strip *before* the
   * final slide, so the reel decelerates into them rather than snapping.
   */
  settleReels(result) {
    const travel = this.travel();

    return new Promise(resolve => {
      const stop = reel => {
        if (reel >= this.strips.length) return setTimeout(resolve, 220);

        const strip = this.strips[reel];
        const cells = [...strip.children];

        // Land the result in the window and fill the run-up above it.
        result.reels[reel].forEach((symbol, row) => {
          cells[cells.length - this.config.rows + row].textContent =
            this.emoji?.[symbol] || symbol;
        });
        for (let index = 0; index < cells.length - this.config.rows; index += 1) {
          cells[index].textContent = this.randomSymbol();
        }

        // Jump to the top of the strip, then ease the whole way back down to
        // rest: the long travel plus the slight overshoot in the easing is
        // what gives the reel its weight.
        strip.style.transition = 'none';
        strip.style.transform = `translateY(${travel}px)`;
        void strip.offsetHeight;  // force the jump to land before the ease
        strip.style.transition = 'transform 620ms cubic-bezier(.18,.9,.24,1.06)';
        strip.style.transform = 'translateY(0px)';

        this.settled = reel + 1;
        strip.parentElement.classList.add('is-landing');
        setTimeout(() => strip.parentElement.classList.remove('is-landing'), 700);

        setTimeout(() => stop(reel + 1), REEL_STAGGER_MS);
      };
      stop(0);
    });
  }

  showResult(result) {
    const wins = result.wins || result.lines || [];
    wins.forEach(win => {
      // 3x3 sends explicit [reel, row] pairs; 5x3 sends a row per reel.
      const positions = win.positions
        || win.rows.slice(0, win.count).map((row, reel) => [reel, row]);
      positions.forEach(([reel, row]) => this.cells[reel]?.[row]?.classList.add('is-win'));
    });

    if (this.linesEl) {
      this.linesEl.innerHTML = wins
        .map(win => `<li><b>${win.line}</b><span>${this.emoji?.[win.symbol] || win.symbol}</span>
                     <em>+₹${money(win.payout)}</em></li>`)
        .join('');
    }
    if (this.winEl) {
      this.winEl.textContent = result.payout > 0
        ? `WIN ₹${money(result.payout)}`
        : 'No win — spin again';
      this.winEl.classList.toggle('is-win', result.payout > 0);
    }
    if (result.payout > 0) this.toast(`Jeet gaye ₹${money(result.payout)}!`, 'success');
  }
}
