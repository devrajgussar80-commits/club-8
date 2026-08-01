/**
 * Dice Roll — one die, six faces, server-decided.
 *
 * The die is a real CSS cube, not a sprite swap: six faces placed with 3D
 * transforms and a parent that rotates. Landing on a face is therefore a
 * single `transform` transition, which the compositor animates on its own
 * thread — smooth even on the cheap Android phones most of this traffic is
 * on, and it keeps running when JS is busy.
 *
 * The tumble starts only AFTER the roll comes back, and it is aimed at the
 * face the server already picked. Nothing the player does to the animation
 * can change the result; slowing it down or editing the transform just shows
 * the same number arriving differently.
 */

const money = value => Number(value || 0).toFixed(2);
const CHIPS = [10, 50, 100, 500];
const ROLL_MS = 2200;

// Rotation that brings each face to the front, given how the faces are placed
// in CSS. Getting these wrong is the classic dice bug: the animation lands on
// a face that is not the one the server rolled, and the game looks rigged.
const FACE_ROTATION = {
  1: [0, 0],
  2: [0, -90],
  3: [-90, 0],
  4: [90, 0],
  5: [0, 90],
  6: [0, 180]
};

export class DiceEngine {
  constructor(options) {
    Object.assign(this, options);
    this.chip = 10;
    this.bets = [];
    this.busy = false;
    // Kept climbing so every roll spins forward rather than unwinding.
    this.spinX = 0;
    this.spinY = 0;
  }

  el(name) {
    return document.getElementById(`dice-${name}`);
  }

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

    this.rollBtn?.addEventListener('click', () => this.roll());
    this.clearBtn?.addEventListener('click', () => { this.bets = []; this.render(); });

    this.buildCube();
    this.buildBoard();
    this.buildChips();
    this.showFace(1, false);
    this.render();
  }

  /** Six faces of pips. Pip positions come from CSS grid areas, not markup. */
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
    if (this.busy) return;
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

  render() {
    if (this.walletEl) this.walletEl.textContent = money(this.getBalance());
    this.chipsEl?.querySelectorAll('[data-chip]').forEach(button =>
      button.classList.toggle('is-active', Number(button.dataset.chip) === this.chip));

    const staked = new Map(this.bets.map(bet => [`${bet.bet_type}:${bet.value}`, bet.amount]));
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
      this.rollBtn.disabled = this.busy || !this.bets.length;
      this.rollBtn.textContent = this.busy ? 'ROLLING…' : 'ROLL';
    }
  }

  /** Point the cube at `face`. `animate` false snaps, for the idle state. */
  showFace(face, animate = true) {
    const [x, y] = FACE_ROTATION[face] || FACE_ROTATION[1];
    this.cube.style.transition = animate
      ? `transform ${ROLL_MS}ms cubic-bezier(.16,.86,.24,1)`
      : 'none';
    this.cube.style.transform = `rotateX(${this.spinX + x}deg) rotateY(${this.spinY + y}deg)`;
  }

  /**
   * Hand control back from the shake animation to the inline transform.
   *
   * Dropping `.is-rolling` and setting the landing transform in the same task
   * batches into one style recalculation, and the transition can be skipped
   * entirely -- the die then sits on the PREVIOUS roll's number while the
   * result text shows the new one, which reads exactly like a rigged game.
   * Reading a layout property forces the browser to commit the pre-landing
   * state first, so the transition always has something to animate from.
   */
  releaseShake() {
    this.cube.classList.remove('is-rolling');
    void this.cube.offsetHeight;
  }

  async roll() {
    if (this.busy || !this.bets.length) return;
    if (!this.canPlay()) return this.denyPlay();
    if (this.totalStake() > this.getBalance()) return this.toast('Wallet balance kam hai.', 'error');

    this.busy = true;
    this.resultEl.textContent = '';
    this.resultEl.className = 'dice-result';
    this.cube.classList.add('is-rolling');
    this.render();

    let result;
    try {
      result = await this.api('/api/games/dice/roll', 'POST', { bets: this.bets });
    } catch (error) {
      this.busy = false;
      this.releaseShake();
      this.render();
      return this.toast(error.message || 'Roll failed.', 'error');
    }

    // Whole extra turns on both axes, so the die tumbles rather than pivots.
    // Normalised first: without this the angle grows by 1080deg every roll and
    // after a long session the numbers get large enough to lose precision.
    this.spinX = (this.spinX % 360) + 360 * 3;
    this.spinY = (this.spinY % 360) + 360 * 4;
    this.releaseShake();
    this.showFace(result.face);
    await new Promise(resolve => setTimeout(resolve, ROLL_MS + 120));

    this.busy = false;
    this.setBalance(result.balance);
    this.showResult(result);
    this.bets = [];
    this.render();
  }

  showResult(result) {
    const won = result.payout > 0;
    this.resultEl.textContent = `${result.face} · ${result.parity.toUpperCase()} · ${
      result.half.toUpperCase()} — ${won ? `WIN ₹${money(result.payout)}` : 'No win'}`;
    this.resultEl.className = `dice-result${won ? ' is-win' : ''}`;

    if (this.historyEl) {
      const chip = document.createElement('span');
      chip.className = `dice-hist${won ? ' is-win' : ''}`;
      chip.textContent = result.face;
      this.historyEl.prepend(chip);
      while (this.historyEl.children.length > 12) this.historyEl.lastChild.remove();
    }
    if (won) this.toast(`Jeet gaye ₹${money(result.payout)}!`, 'success');
  }
}
