/**
 * Dice Roll — multiplayer, one shared 30-second round (like WinGo).
 *
 * The server owns the clock and the roll. This engine polls /state, lets the
 * player build a bet slip and place it during the window, and when a round
 * settles it tumbles the CSS cube to the face the server rolled and shows the
 * player their win or loss. Nothing here decides an outcome.
 *
 * The die is a real 3D CSS cube: landing on a face is a single `transform`
 * transition the compositor animates on its own thread, smooth even on the
 * cheap Android phones most of this traffic is on.
 */

const money = value => Number(value || 0).toFixed(2);
const CHIPS = [10, 50, 100, 500];
const ROLL_MS = 2000;
const POLL_MS = 1000;

const FACE_ROTATION = {
  1: [0, 0], 2: [0, -90], 3: [-90, 0], 4: [90, 0], 5: [0, 90], 6: [0, 180]
};

export class DiceEngine {
  constructor(options) {
    Object.assign(this, options);
    this.chip = 10;
    this.bets = [];          // pending slip, not yet placed
    this.myBets = [];        // bets already placed this round (from server)
    this.busy = false;
    this.spinX = 0;
    this.spinY = 0;
    this.lastSeenResult = null;
    this.pollTimer = null;
    this.bettingOpen = false;
  }

  el(name) { return document.getElementById(`dice-${name}`); }

  init() {
    this.cube = this.el('cube');
    this.numbersEl = this.el('numbers');
    this.outsideEl = this.el('outside');
    this.chipsEl = this.el('chips');
    this.slipEl = this.el('slip');
    this.stakeEl = this.el('stake');
    this.rollBtn = this.el('roll');
    this.clearBtn = this.el('clear');
    this.resultEl = this.el('result');
    this.historyEl = this.el('history');
    this.walletEl = this.el('wallet');
    if (!this.cube) return;

    this.rollBtn?.addEventListener('click', () => this.placeBets());
    this.clearBtn?.addEventListener('click', () => { this.bets = []; this.render(); });

    this.buildCube();
    this.buildBoard();
    this.buildChips();
    this.showFace(1, false);
    this.render();

    this.poll();
  }

  isVisible() {
    return document.getElementById('page-dice')?.classList.contains('active');
  }

  buildCube() {
    const pips = {
      1: [5], 2: [1, 9], 3: [1, 5, 9],
      4: [1, 3, 7, 9], 5: [1, 3, 5, 7, 9], 6: [1, 3, 4, 6, 7, 9]
    };
    this.cube.innerHTML = Object.entries(pips).map(([face, cells]) => `
      <div class="dice-face dice-face-${face}">
        ${Array.from({ length: 9 }, (_, i) =>
          `<span class="${cells.includes(i + 1) ? 'dice-pip' : ''}"></span>`).join('')}
      </div>`).join('');
  }

  buildBoard() {
    this.numbersEl.innerHTML = [1, 2, 3, 4, 5, 6].map(face => `
      <button type="button" class="dice-num" data-bet="number" data-value="${face}">
        <span class="dice-mini">${'●'.repeat(face)}</span>
        <b>${face}</b><small>5.88x</small>
      </button>`).join('');

    const outside = [
      ['parity', 'odd', 'ODD', '1,3,5'], ['parity', 'even', 'EVEN', '2,4,6'],
      ['half', 'low', '1–3', 'LOW'], ['half', 'high', '4–6', 'HIGH']
    ];
    this.outsideEl.innerHTML = outside.map(([type, value, label, hint]) => `
      <button type="button" class="dice-out" data-bet="${type}" data-value="${value}">
        <b>${label}</b><small>${hint} · 1.96x</small>
      </button>`).join('');

    [this.numbersEl, this.outsideEl].forEach(container =>
      container.addEventListener('click', event => {
        const button = event.target.closest('[data-bet]');
        if (button) this.addBet(button.dataset.bet, button.dataset.value);
      }));
  }

  buildChips() {
    this.chipsEl.innerHTML = CHIPS.map(value =>
      `<button type="button" class="rc-chip" data-chip="${value}">₹${value}</button>`).join('');
    this.chipsEl.addEventListener('click', event => {
      const button = event.target.closest('[data-chip]');
      if (!button) return;
      this.chip = Number(button.dataset.chip);
      this.render();
    });
  }

  addBet(betType, value) {
    if (this.busy || !this.bettingOpen) {
      if (!this.bettingOpen) this.toast('Betting is closed — wait for the next round.', 'error');
      return;
    }
    const existing = this.bets.find(bet => bet.bet_type === betType && bet.value === value);
    if (existing) existing.amount += this.chip;
    else this.bets.push({ bet_type: betType, value, amount: this.chip });
    this.render();
  }

  removeBet(index) {
    if (this.busy) return;
    this.bets.splice(index, 1);
    this.render();
  }

  totalStake() {
    return this.bets.reduce((sum, bet) => sum + bet.amount, 0);
  }

  // --------------------------------------------------------------- server

  async poll() {
    clearTimeout(this.pollTimer);
    if (this.isVisible() && this.canPlay()) {
      try { await this.syncState(); } catch (error) { /* next poll recovers */ }
    }
    this.pollTimer = setTimeout(() => this.poll(), POLL_MS);
  }

  async syncState() {
    const data = await this.api('/api/games/dice/state', 'GET');
    this.bettingOpen = data.betting_open;
    this.secondsLeft = data.seconds_left;
    this.period = data.period;
    this.myBets = data.my_bets || [];
    if (typeof data.balance === 'number') this.setBalance(data.balance);

    // History strip.
    if (this.historyEl && Array.isArray(data.history)) {
      this.historyEl.innerHTML = data.history
        .map(h => `<span class="dice-hist">${h.face}</span>`).join('');
    }

    // A new settled round: tumble the die to its face and show the outcome.
    const result = data.last_result;
    if (result && result.period !== this.lastSeenResult) {
      this.lastSeenResult = result.period;
      this.revealRoll(result);
    } else if (!this.busy) {
      this.renderCountdown();
    }
    this.render();
  }

  revealRoll(result) {
    this.busy = true;
    this.cube.classList.add('is-rolling');
    this.spinX = (this.spinX % 360) + 360 * 3;
    this.spinY = (this.spinY % 360) + 360 * 4;
    this.releaseShake();
    this.showFace(result.face);
    setTimeout(async () => {
      this.busy = false;
      await this.showRoundOutcome(result);
      this.render();
    }, ROLL_MS + 100);
  }

  /** After a roll, tell the player how their bets on that round did. */
  async showRoundOutcome(result) {
    let payout = 0;
    let staked = 0;
    try {
      const { bets } = await this.api('/api/games/dice/my-bets', 'GET');
      (bets || []).filter(b => b.period === result.period).forEach(b => {
        staked += Number(b.amount);
        payout += Number(b.payout || 0);
      });
    } catch (error) { /* fall back to just the face */ }

    const faceLine = `${result.face} · ${result.parity.toUpperCase()} · ${result.half.toUpperCase()}`;
    if (staked > 0) {
      const won = payout > 0;
      this.resultEl.textContent = `${faceLine} — ${won ? `WIN ₹${money(payout)}` : 'No win'}`;
      this.resultEl.className = `dice-result${won ? ' is-win' : ''}`;
      if (won) this.toast(`Jeet gaye ₹${money(payout)}!`, 'success');
    } else {
      this.resultEl.textContent = `Rolled ${faceLine}`;
      this.resultEl.className = 'dice-result';
    }
  }

  renderCountdown() {
    if (!this.resultEl || this.busy) return;
    if (this.bettingOpen) {
      this.resultEl.textContent = `Betting open — rolls in ${this.secondsLeft}s`;
    } else {
      this.resultEl.textContent = 'Rolling…';
    }
    this.resultEl.className = 'dice-result';
  }

  async placeBets() {
    if (this.busy || !this.bets.length) return;
    if (!this.canPlay()) return this.denyPlay();
    if (!this.bettingOpen) return this.toast('Betting is closed — wait for the next round.', 'error');
    if (this.totalStake() > this.getBalance()) return this.toast('Wallet balance kam hai.', 'error');

    this.rollBtn.disabled = true;
    const slip = this.bets.slice();
    let placed = 0;
    try {
      for (const bet of slip) {
        const res = await this.api('/api/games/dice/bet', 'POST', {
          bet_type: bet.bet_type, selection: bet.value, amount: bet.amount
        });
        this.setBalance(res.balance);
        placed += 1;
      }
      this.bets = [];
      this.toast(`${placed} bet${placed === 1 ? '' : 's'} placed for this round.`, 'success');
    } catch (error) {
      this.toast(error.message || 'Bet nahi lag paya.', 'error');
    }
    await this.syncState().catch(() => {});
    this.render();
  }

  showFace(face, animate = true) {
    const [x, y] = FACE_ROTATION[face] || FACE_ROTATION[1];
    this.cube.style.transition = animate
      ? `transform ${ROLL_MS}ms cubic-bezier(.16,.86,.24,1)`
      : 'none';
    this.cube.style.transform = `rotateX(${this.spinX + x}deg) rotateY(${this.spinY + y}deg)`;
  }

  releaseShake() {
    this.cube.classList.remove('is-rolling');
    void this.cube.offsetHeight;
  }

  render() {
    if (this.walletEl) this.walletEl.textContent = money(this.getBalance());
    this.chipsEl?.querySelectorAll('[data-chip]').forEach(button =>
      button.classList.toggle('is-active', Number(button.dataset.chip) === this.chip));

    // Board shows both this round's placed bets and the pending slip.
    const staked = new Map();
    this.myBets.forEach(b => {
      const key = `${b.bet_type}:${b.selection}`;
      staked.set(key, (staked.get(key) || 0) + Number(b.amount));
    });
    this.bets.forEach(b => {
      const key = `${b.bet_type}:${b.value}`;
      staked.set(key, (staked.get(key) || 0) + Number(b.amount));
    });
    [this.numbersEl, this.outsideEl].forEach(container =>
      container?.querySelectorAll('[data-bet]').forEach(button => {
        const amount = staked.get(`${button.dataset.bet}:${button.dataset.value}`);
        button.classList.toggle('has-bet', Boolean(amount));
        button.dataset.staked = amount ? `₹${amount}` : '';
      }));

    if (this.slipEl) {
      this.slipEl.innerHTML = this.bets.length
        ? this.bets.map((bet, index) =>
            `<li><b>${bet.bet_type}</b><span>${bet.value}</span><em>₹${money(bet.amount)}</em>
               <button type="button" data-remove="${index}" aria-label="Remove bet">×</button></li>`).join('')
        : '<li class="is-empty">Number ya odd/even pe chip lagao</li>';
      this.slipEl.querySelectorAll('[data-remove]').forEach(button =>
        button.addEventListener('click', () => this.removeBet(Number(button.dataset.remove))));
    }
    if (this.stakeEl) this.stakeEl.textContent = money(this.totalStake());
    if (this.rollBtn) {
      this.rollBtn.disabled = this.busy || !this.bets.length || !this.bettingOpen;
      this.rollBtn.textContent = !this.bettingOpen ? 'CLOSED' : (this.busy ? 'ROLLING…' : 'PLACE BET');
    }
  }

  destroy() {
    clearTimeout(this.pollTimer);
  }
}
