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

  buildGrid() {
    const { reels, rows } = this.config;
    this.grid.style.setProperty('--slot-reels', reels);
    this.grid.innerHTML = '';
    this.cells = [];
    for (let reel = 0; reel < reels; reel += 1) {
      const column = document.createElement('div');
      column.className = 'slot-reel';
      const columnCells = [];
      for (let row = 0; row < rows; row += 1) {
        const cell = document.createElement('span');
        cell.className = 'slot-cell';
        cell.textContent = '❔';
        column.appendChild(cell);
        columnCells.push(cell);
      }
      this.grid.appendChild(column);
      this.cells.push(columnCells);
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
    this.cells.forEach(column => column.forEach(cell => { cell.textContent = this.randomSymbol(); }));
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

  startCycling() {
    // Plain interval rather than requestAnimationFrame: this still ticks when
    // the tab is throttled, so a spin can never appear to hang mid-flight.
    clearInterval(this.cycler);
    this.cells.forEach(column => column.forEach(cell => cell.classList.add('is-spinning')));
    this.cycler = setInterval(() => {
      this.cells.forEach((column, reel) => {
        if (this.settled > reel) return;
        column.forEach(cell => { cell.textContent = this.randomSymbol(); });
      });
    }, 70);
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

    let result;
    try {
      result = await this.api(`/api/games/${this.config.game}/spin`, 'POST', { amount: this.amount });
    } catch (error) {
      clearInterval(this.cycler);
      this.cells.forEach(column => column.forEach(cell => cell.classList.remove('is-spinning')));
      this.busy = false;
      this.render();
      return this.toast(error.message || 'Spin failed.', 'error');
    }

    await this.settleReels(result);
    clearInterval(this.cycler);
    this.busy = false;

    // The server's balance is authoritative -- take it rather than doing the
    // arithmetic again here, so a dropped response can never leave the two
    // numbers disagreeing.
    this.setBalance(result.balance);
    this.showResult(result);
    this.render();
  }

  settleReels(result) {
    // Reels stop left to right, 260ms apart, the way a physical machine does.
    return new Promise(resolve => {
      const stop = reel => {
        if (reel >= this.cells.length) return resolve();
        result.reels[reel].forEach((symbol, row) => {
          const cell = this.cells[reel][row];
          cell.textContent = this.emoji?.[symbol] || symbol;
          cell.classList.remove('is-spinning');
          cell.classList.add('is-landing');
          setTimeout(() => cell.classList.remove('is-landing'), 320);
        });
        this.settled = reel + 1;
        setTimeout(() => stop(reel + 1), 260);
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
