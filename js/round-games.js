/**
 * Fish vs Tiger and Vortex — the two shared-round table games.
 *
 * Both are the same interaction: a countdown, pick a side, the server settles
 * the round for everyone at once and the client replays the result. Only the
 * reveal differs (two cards vs a wheel), so that is the one method each
 * subclass overrides.
 *
 * The server owns the clock and the outcome. This polls /state, shows what is
 * already decided, and animates it. Nothing here can change a result.
 */

const money = value => Number(value || 0).toFixed(2);
const POLL_MS = 1000;

class RoundGameUI {
  constructor(options, config) {
    Object.assign(this, options);
    this.config = config;          // { game, prefix }
    this.chip = 10;
    this.selection = null;
    this.busy = false;
    this.lastSeen = null;
    this.timer = null;
  }

  el(name) {
    return document.getElementById(`${this.config.prefix}-${name}`);
  }

  init() {
    this.stage = this.el('stage');
    this.picksEl = this.el('picks');
    this.chipsEl = this.el('chips');
    this.betBtn = this.el('bet');
    this.statusEl = this.el('status');
    this.clockEl = this.el('clock');
    this.historyEl = this.el('history');
    this.walletEl = this.el('wallet');
    this.slipEl = this.el('slip');
    if (!this.stage) return;

    this.buildChips();
    this.betBtn?.addEventListener('click', () => this.placeBet());
    this.poll();
  }

  isVisible() {
    return document.getElementById(`page-${this.config.prefix}`)?.classList.contains('active');
  }

  buildChips() {
    if (!this.chipsEl) return;
    this.chipsEl.innerHTML = [10, 50, 100, 500]
      .map(value => `<button type="button" class="rc-chip" data-chip="${value}">₹${value}</button>`)
      .join('');
    this.chipsEl.addEventListener('click', event => {
      const button = event.target.closest('[data-chip]');
      if (!button) return;
      this.chip = Number(button.dataset.chip);
      this.render();
    });
  }

  buildPicks(selections, pays) {
    if (!this.picksEl || this.picksEl.dataset.built) return;
    this.picksEl.dataset.built = '1';
    this.picksEl.innerHTML = Object.entries(selections).map(([key, label]) => `
      <button type="button" class="rg-pick rg-pick-${key.replace('.', '-')}" data-pick="${key}">
        <b>${label}</b><small>${pays?.[key] ? `${pays[key]}x` : `${key}x`}</small>
      </button>`).join('');
    this.picksEl.addEventListener('click', event => {
      const button = event.target.closest('[data-pick]');
      if (!button) return;
      // Tapping the chosen side again clears it, so a mis-tap costs nothing.
      this.selection = this.selection === button.dataset.pick ? null : button.dataset.pick;
      this.render();
    });
  }

  async poll() {
    clearTimeout(this.timer);
    if (this.isVisible() && this.canPlay()) {
      try { await this.sync(); } catch (error) { /* next poll recovers */ }
    }
    this.timer = setTimeout(() => this.poll(), POLL_MS);
  }

  async sync() {
    const data = await this.api(`/api/games/${this.config.game}/state`);
    this.state = data;
    // Settlement credits winners without this client asking for anything, so
    // the authoritative balance comes back with every poll.
    if (typeof data.balance === 'number') this.setBalance(data.balance);
    this.buildPicks(data.selections, data.pays);

    if (this.historyEl) {
      this.historyEl.innerHTML = (data.history || [])
        .map(item => this.historyChip(item.outcome)).join('');
    }

    const last = data.last_result;
    if (last && last.period !== this.lastSeen) {
      this.lastSeen = last.period;
      // First poll of a session: show where the wheel or the cards are
      // without replaying a round the player never saw.
      if (this.seenOnce) this.revealRound(last);
      else this.reveal(last.outcome, false);
      this.seenOnce = true;
    }
    this.render();
  }

  async revealRound(last) {
    this.busy = true;
    this.render();
    await this.reveal(last.outcome, true);
    this.busy = false;

    const mine = last.my_bets || [];
    if (mine.length) {
      const payout = mine.reduce((sum, bet) => sum + Number(bet.payout || 0), 0);
      const staked = mine.reduce((sum, bet) => sum + Number(bet.amount || 0), 0);
      if (payout > staked) this.toast(`Jeet gaye ₹${money(payout)}!`, 'success');
      else if (payout > 0) this.toast(`Stake wapas: ₹${money(payout)}`, 'success');
    }
    this.render();
  }

  async placeBet() {
    if (this.busy || !this.selection) return;
    if (!this.canPlay()) return this.denyPlay();
    if (this.chip > this.getBalance()) return this.toast('Wallet balance kam hai.', 'error');
    if (!this.state?.betting_open) return this.toast('Betting band hai — agla round rukiye.', 'error');

    this.busy = true;
    this.render();
    try {
      const result = await this.api(`/api/games/${this.config.game}/bet`, 'POST',
        { selection: this.selection, amount: this.chip });
      this.setBalance(result.balance);
      this.toast(`₹${money(result.amount)} lagaya.`, 'success');
      await this.sync();
    } catch (error) {
      this.toast(error.message || 'Bet fail ho gaya.', 'error');
    } finally {
      this.busy = false;
      this.render();
    }
  }

  render() {
    if (this.walletEl) this.walletEl.textContent = money(this.getBalance());
    this.chipsEl?.querySelectorAll('[data-chip]').forEach(button =>
      button.classList.toggle('is-active', Number(button.dataset.chip) === this.chip));

    const staked = new Map();
    (this.state?.my_bets || []).forEach(bet =>
      staked.set(bet.selection, (staked.get(bet.selection) || 0) + Number(bet.amount)));

    this.picksEl?.querySelectorAll('[data-pick]').forEach(button => {
      const key = button.dataset.pick;
      button.classList.toggle('is-chosen', this.selection === key);
      button.classList.toggle('has-bet', staked.has(key));
      button.dataset.staked = staked.has(key) ? `₹${staked.get(key)}` : '';
    });

    if (this.clockEl) {
      const left = this.state?.seconds_left ?? 0;
      this.clockEl.textContent = `00:${String(left).padStart(2, '0')}`;
      this.clockEl.classList.toggle('is-closing', !this.state?.betting_open);
    }
    if (this.statusEl && !this.busy) {
      this.statusEl.textContent = this.state?.betting_open
        ? 'Betting open' : 'Round closing…';
    }
    if (this.slipEl) {
      this.slipEl.textContent = staked.size
        ? [...staked].map(([key, amount]) => `${key} ₹${money(amount)}`).join(' · ')
        : 'Is round mein koi bet nahi';
    }
    if (this.betBtn) {
      this.betBtn.disabled = this.busy || !this.selection || !this.state?.betting_open;
      this.betBtn.textContent = this.selection
        ? `Bet ₹${this.chip} on ${this.selection}` : 'Pick a side';
    }
  }

  // Subclasses implement these two.
  historyChip() { return ''; }
  async reveal() {}
}

/* ------------------------------------------------------------- Fish vs Tiger */

export class FishTigerEngine extends RoundGameUI {
  constructor(options) {
    super(options, { game: 'fishtiger', prefix: 'fishtiger' });
  }

  historyChip(outcome) {
    const winner = outcome?.winner || '';
    const letter = winner === 'fish' ? 'F' : winner === 'tiger' ? 'T' : 'D';
    return `<span class="ft-hist is-${winner}">${letter}</span>`;
  }

  card(side, data) {
    const red = data.suit === '♥' || data.suit === '♦';
    return `<div class="ft-card${red ? ' is-red' : ''}">
        <b>${data.name}</b><span>${data.suit}</span>
      </div>`;
  }

  async reveal(outcome, animate) {
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    const fish = this.el('fish-card');
    const tiger = this.el('tiger-card');
    if (!fish || !tiger) return;

    if (animate) {
      // Both cards flip face down, then turn over one at a time -- the pause
      // between them is the whole tension of the game.
      fish.classList.add('is-dealing');
      tiger.classList.add('is-dealing');
      this.statusEl.textContent = 'Dealing…';
      await wait(600);
    }
    fish.innerHTML = this.card('fish', outcome.fish);
    fish.classList.remove('is-dealing');
    if (animate) await wait(500);
    tiger.innerHTML = this.card('tiger', outcome.tiger);
    tiger.classList.remove('is-dealing');

    this.stage.classList.remove('win-fish', 'win-tiger', 'win-tie');
    this.stage.classList.add(`win-${outcome.winner}`);
    this.statusEl.textContent = outcome.winner === 'tie'
      ? 'Tie — side bets returned'
      : `${outcome.winner === 'fish' ? 'Fish' : 'Tiger'} wins`;
  }
}

/* -------------------------------------------------------------------- Vortex */

export class VortexEngine extends RoundGameUI {
  constructor(options) {
    super(options, { game: 'vortex', prefix: 'vortex' });
    this.spin = 0;
  }

  historyChip(outcome) {
    const value = outcome?.multiplier;
    return `<span class="vx-hist${value >= 7.84 ? ' is-big' : ''}">${value}x</span>`;
  }

  /**
   * Build the vertical strip.
   *
   * The wheel is repeated REPEATS times so the reel can travel a long way
   * before settling without ever running out of tiles -- a single copy would
   * reach the end of the strip after one spin and have to snap back visibly.
   */
  buildWheel(wheel) {
    const strip = this.el('wheel-face');
    if (!strip || strip.dataset.built) return;
    strip.dataset.built = '1';
    this.wheel = wheel;
    this.repeats = 6;
    const tier = value =>
      value >= 23 ? 'top' : value >= 11 ? 'high' : value >= 7 ? 'mid' : value >= 3 ? 'low' : 'base';
    const tiles = [];
    for (let copy = 0; copy < this.repeats; copy += 1) {
      wheel.forEach(value => tiles.push(
        `<span class="vx-tile vx-${tier(value)}"><b>${value}</b><i>x</i></span>`));
    }
    strip.innerHTML = tiles.join('');
  }

  /** Distance between two tiles, measured rather than assumed, so the CSS
   *  keeps control of the tile height at every screen size. */
  pitch() {
    const strip = this.el('wheel-face');
    const tiles = strip?.children;
    if (!tiles || tiles.length < 2) return 0;
    return tiles[1].getBoundingClientRect().top - tiles[0].getBoundingClientRect().top;
  }

  async reveal(outcome, animate) {
    if (this.state?.wheel) this.buildWheel(this.state.wheel);
    const strip = this.el('wheel-face');
    if (!strip || !this.wheel) return;

    const pitch = this.pitch();
    const window_ = strip.parentElement;
    const centre = window_.getBoundingClientRect().height / 2 - pitch / 2;
    // Land in a middle copy, so there is strip above and below the result and
    // the reel never shows its own edge.
    const copy = animate ? this.repeats - 2 : 2;
    const offset = (copy * this.wheel.length + outcome.index) * pitch - centre;

    if (animate) {
      // Start from an early copy so the travel is long, then ease into place.
      strip.style.transition = 'none';
      strip.style.transform = `translateY(${-(outcome.index * pitch - centre)}px)`;
      void strip.offsetHeight;
      strip.style.transition = 'transform 3400ms cubic-bezier(.12,.75,.16,1)';
      this.statusEl.textContent = 'Spinning…';
    } else {
      strip.style.transition = 'none';
    }
    strip.style.transform = `translateY(${-offset}px)`;

    strip.querySelectorAll('.is-landed').forEach(el => el.classList.remove('is-landed'));
    if (animate) await new Promise(resolve => setTimeout(resolve, 3500));
    strip.children[copy * this.wheel.length + outcome.index]?.classList.add('is-landed');
    this.statusEl.textContent = `Landed on ${outcome.multiplier}x`;
  }
}
