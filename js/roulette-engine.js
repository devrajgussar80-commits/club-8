/**
 * European roulette, single zero.
 *
 * The wheel is a CSS rotation, not a physics simulation, and it is spun to an
 * angle *derived from* the pocket the server already picked. That ordering is
 * the whole point: POST /spin returns the pocket, and only then does the wheel
 * start turning towards it. A player who slows the animation down or edits the
 * transform sees the same result they were always going to get.
 *
 * Payouts come from the table odds (36 / covered pockets), so the 2.7% house
 * edge lives in the maths rather than in a fudge factor.
 */

const money = value => Number(value || 0).toFixed(2);
const CHIPS = [10, 50, 100, 500];
const SPIN_MS = 4200;

export class RouletteEngine {
  constructor(options) {
    Object.assign(this, options);
    this.chip = 10;
    this.bets = [];
    this.busy = false;
    this.rotation = 0;
    this.table = null;
  }

  el(name) {
    return document.getElementById(`roulette-${name}`);
  }

  init() {
    this.wheel = this.el('wheel');
    this.numbersEl = this.el('numbers');
    this.outsideEl = this.el('outside');
    this.chipsEl = this.el('chips');
    this.slipEl = this.el('slip');
    this.stakeEl = this.el('stake');
    this.spinBtn = this.el('spin');
    this.clearBtn = this.el('clear');
    this.resultEl = this.el('result');
    this.historyEl = this.el('history');
    this.walletEl = this.el('wallet');
    if (!this.wheel) return;

    this.spinBtn?.addEventListener('click', () => this.spin());
    this.clearBtn?.addEventListener('click', () => { this.bets = []; this.render(); });

    this.loadTable();
  }

  async loadTable() {
    try {
      this.table = await this.api('/api/games/roulette/table');
    } catch (error) {
      return this.toast('Roulette table load nahi hui.', 'error');
    }
    this.red = new Set(this.table.red_numbers);
    this.buildWheel();
    this.buildBoard();
    this.buildChips();
    this.render();
  }

  colourOf(number) {
    if (number === 0) return 'green';
    return this.red.has(number) ? 'red' : 'black';
  }

  buildWheel() {
    const order = this.table.wheel_order;
    const step = 360 / order.length;
    const pockets = order.map((number, index) => {
      const start = index * step;
      const end = start + step;
      const toXY = angle => {
        const rad = (angle - 90) * Math.PI / 180;
        return [`${(50 + 48 * Math.cos(rad)).toFixed(3)}`, `${(50 + 48 * Math.sin(rad)).toFixed(3)}`];
      };
      const [x1, y1] = toXY(start);
      const [x2, y2] = toXY(end);
      const mid = (start + end) / 2;
      const rad = (mid - 90) * Math.PI / 180;
      const lx = (50 + 39 * Math.cos(rad)).toFixed(2);
      const ly = (50 + 39 * Math.sin(rad)).toFixed(2);
      return `
        <path d="M50 50 L${x1} ${y1} A48 48 0 0 1 ${x2} ${y2} Z"
              class="rw-pocket rw-${this.colourOf(number)}"/>
        <text x="${lx}" y="${ly}" class="rw-label"
              transform="rotate(${mid} ${lx} ${ly})">${number}</text>`;
    }).join('');

    this.wheel.innerHTML = `
      <svg viewBox="0 0 100 100" class="rw-svg" aria-hidden="true">
        <circle cx="50" cy="50" r="49.5" class="rw-rim"/>
        <g id="roulette-rotor" class="rw-rotor">${pockets}</g>
        <circle cx="50" cy="50" r="17" class="rw-hub"/>
      </svg>
      <span class="rw-pointer" aria-hidden="true"></span>`;
    this.rotor = this.wheel.querySelector('#roulette-rotor');
  }

  buildBoard() {
    this.numbersEl.innerHTML = Array.from({ length: 37 }, (_, number) =>
      `<button type="button" class="rb-num rb-${this.colourOf(number)}"
               data-bet="straight" data-value="${number}">${number}</button>`).join('');

    const outside = [
      ['color', 'red', 'RED', '1:1'], ['color', 'black', 'BLACK', '1:1'],
      ['parity', 'odd', 'ODD', '1:1'], ['parity', 'even', 'EVEN', '1:1'],
      ['half', 'low', '1-18', '1:1'], ['half', 'high', '19-36', '1:1'],
      ['dozen', '1', '1st 12', '2:1'], ['dozen', '2', '2nd 12', '2:1'], ['dozen', '3', '3rd 12', '2:1'],
      ['column', '1', 'COL 1', '2:1'], ['column', '2', 'COL 2', '2:1'], ['column', '3', 'COL 3', '2:1']
    ];
    this.outsideEl.innerHTML = outside.map(([type, value, label, pays]) =>
      `<button type="button" class="rb-out rb-out-${value}" data-bet="${type}" data-value="${value}">
         <b>${label}</b><small>${pays}</small></button>`).join('');

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
        : '<li class="is-empty">Numbers ya outside bets pe chip lagao</li>';
      this.slipEl.querySelectorAll('[data-remove]').forEach(button =>
        button.addEventListener('click', () => this.removeBet(Number(button.dataset.remove))));
    }
    if (this.stakeEl) this.stakeEl.textContent = money(this.totalStake());
    if (this.spinBtn) {
      this.spinBtn.disabled = this.busy || !this.bets.length;
      this.spinBtn.textContent = this.busy ? 'SPINNING…' : 'SPIN';
    }
  }

  async spin() {
    if (this.busy || !this.bets.length) return;
    if (!this.canPlay()) return this.denyPlay();
    if (this.totalStake() > this.getBalance()) return this.toast('Wallet balance kam hai.', 'error');

    this.busy = true;
    this.resultEl.textContent = '';
    this.resultEl.className = 'roulette-result';
    this.render();

    let result;
    try {
      result = await this.api('/api/games/roulette/spin', 'POST', { bets: this.bets });
    } catch (error) {
      this.busy = false;
      this.render();
      return this.toast(error.message || 'Spin failed.', 'error');
    }

    await this.spinTo(result.pocket_index);

    this.busy = false;
    this.setBalance(result.balance);
    this.showResult(result);
    this.bets = [];
    this.render();
  }

  spinTo(pocketIndex) {
    const step = 360 / this.table.wheel_order.length;
    // Land the pocket's centre under the pointer at 12 o'clock, after six
    // full turns so the stop always looks like the end of a long spin.
    const target = -(pocketIndex * step + step / 2);
    this.rotation += 360 * 6 + ((target - this.rotation) % 360 + 360) % 360;
    this.rotor.style.transition = `transform ${SPIN_MS}ms cubic-bezier(.16,.84,.32,1)`;
    this.rotor.style.transform = `rotate(${this.rotation}deg)`;
    return new Promise(resolve => setTimeout(resolve, SPIN_MS + 120));
  }

  showResult(result) {
    this.resultEl.textContent = `${result.pocket} ${result.colour.toUpperCase()} · ${
      result.payout > 0 ? `WIN ₹${money(result.payout)}` : 'No win'}`;
    this.resultEl.className = `roulette-result is-${result.colour}${result.payout > 0 ? ' is-win' : ''}`;

    if (this.historyEl) {
      const chip = document.createElement('span');
      chip.className = `rh-chip rh-${result.colour}`;
      chip.textContent = result.pocket;
      this.historyEl.prepend(chip);
      while (this.historyEl.children.length > 12) this.historyEl.lastChild.remove();
    }
    if (result.payout > 0) this.toast(`Jeet gaye ₹${money(result.payout)}!`, 'success');
  }
}
