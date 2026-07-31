/**
 * Chicken Road.
 *
 * The server owns every outcome: it deducts the bet, decides each lane from a
 * pre-committed seed, and credits the payout. This file only draws the result.
 *
 * If the backend does not expose /api/arcade/chicken yet, the game falls back to
 * a clearly labelled DEMO mode that never touches the real wallet — the old
 * build quietly wrote "winnings" into localStorage, so players saw a balance
 * they could never withdraw.
 */

const money = value => Number(value || 0).toFixed(2);

const RTP = 0.98;
const DIFFICULTIES = {
  easy: { lanes: 24, survival: 0.8752 },
  medium: { lanes: 22, survival: 0.7852 },
  hard: { lanes: 20, survival: 0.6930 },
  hardcore: { lanes: 15, survival: 0.5260 }
};

const multiplierAt = (difficulty, lane) =>
  lane <= 0 ? 1 : Number((RTP / Math.pow(DIFFICULTIES[difficulty].survival, lane)).toFixed(2));

const secureUnit = () => {
  const bytes = new Uint32Array(1);
  crypto.getRandomValues(bytes);
  return bytes[0] / 4294967296;
};

export class ChickenRoadEngine {
  constructor({ getBalance, setBalance, toast, canPlay, denyPlay, api }) {
    this.getBalance = getBalance;
    this.setBalance = setBalance;
    this.toast = toast;
    this.canPlay = canPlay || (() => true);
    this.denyPlay = denyPlay || (() => {});
    this.api = api;                       // (path, method, body) => Promise
    this.amount = 10;
    this.difficulty = 'easy';
    this.round = null;
    this.busy = false;
    this.serverMode = null;               // null = not probed yet
    this.demoBalance = 1000;
    this.config = null;
  }

  get lanes() {
    return DIFFICULTIES[this.difficulty].lanes;
  }

  get lane() {
    return this.round?.lane || 0;
  }

  get active() {
    return Boolean(this.round && this.round.status === 'active');
  }

  init() {
    this.stage = document.getElementById('cr-stage');
    this.laneHost = document.getElementById('cr-lanes');
    this.road = document.getElementById('cr-road');
    this.chicken = document.getElementById('cr-chicken');
    this.liveLabel = document.getElementById('cr-live-label');
    this.multiplierEl = document.getElementById('chicken-road-multiplier');
    this.badge = document.getElementById('chicken-result-badge');
    this.message = document.getElementById('chicken-message');
    this.amountEl = document.getElementById('chicken-amount');
    this.playBtn = document.getElementById('chicken-play');
    this.cashBtn = document.getElementById('chicken-cashout');
    this.modeSelect = document.getElementById('chicken-difficulty');
    this.modeTag = document.getElementById('cr-mode-tag');
    this.betIdEl = document.getElementById('chicken-bet-id');
    if (!this.stage) return;

    document.getElementById('chicken-minus')?.addEventListener('click', () => this.setAmount(this.amount - this.stepSize()));
    document.getElementById('chicken-plus')?.addEventListener('click', () => this.setAmount(this.amount + this.stepSize()));
    document.querySelectorAll('[data-chicken-multiplier]').forEach(button =>
      button.addEventListener('click', () => this.setAmount(this.amount * Number(button.dataset.chickenMultiplier))));

    this.playBtn?.addEventListener('click', () => (this.active ? this.takeStep() : this.start()));
    this.cashBtn?.addEventListener('click', () => this.cashout());
    this.modeSelect?.addEventListener('change', () => {
      if (this.active) return;
      this.difficulty = this.modeSelect.value;
      this.renderLanes();
      this.render();
    });

    document.getElementById('cr-fair-open')?.addEventListener('click', () => this.toggleFair(true));
    document.getElementById('cr-fair-close')?.addEventListener('click', () => this.toggleFair(false));

    this.renderLanes();
    this.render();
    void this.loadConfig();
  }

  stepSize() {
    return this.amount >= 500 ? 100 : this.amount >= 100 ? 50 : 10;
  }

  setAmount(value) {
    if (this.active || this.busy) return;
    this.amount = Math.max(1, Math.min(100000, Math.round(Number(value) || 1)));
    this.render();
  }

  async loadConfig() {
    if (!this.api) {
      this.serverMode = false;
      this.applyMode();
      return;
    }
    try {
      const data = await this.api('/api/arcade/chicken/config');
      this.config = data;
      this.serverMode = true;
      if (data.active_round) {
        this.round = data.active_round;
        this.difficulty = this.round.difficulty;
        if (this.modeSelect) this.modeSelect.value = this.difficulty;
        this.message.textContent = 'Round restored — keep going or cash out.';
      }
      this.renderLanes();
      this.render();
    } catch (error) {
      // 404 means this backend predates the arcade endpoints.
      this.serverMode = false;
    }
    this.applyMode();
  }

  applyMode() {
    if (this.modeTag) this.modeTag.hidden = this.serverMode !== false;
    const rtpBox = document.getElementById('cr-fair-rtp');
    if (rtpBox) {
      rtpBox.innerHTML = Object.entries(DIFFICULTIES).map(([name, cfg]) =>
        `<span><b>${name}</b> ${cfg.lanes} lanes · max ${multiplierAt(name, cfg.lanes).toLocaleString('en-IN')}x</span>`
      ).join('') + `<span class="cr-rtp-line">RTP ${(RTP * 100).toFixed(0)}% on every difficulty</span>`;
    }
    if (this.serverMode === false) {
      this.message.textContent = 'Demo mode — the server has no arcade endpoints yet, so nothing is charged or paid.';
    }
    this.render();
  }

  // ---------- rendering ----------

  renderLanes() {
    if (!this.laneHost) return;
    const total = this.lanes;
    this.laneHost.style.setProperty('--lane-count', String(total));
    this.laneHost.innerHTML = Array.from({ length: total }, (_, index) => {
      const lane = index + 1;
      const value = multiplierAt(this.difficulty, lane);
      const done = lane <= this.lane;
      const next = lane === this.lane + 1 && this.active;
      return `
        <div class="cr-lane ${done ? 'done' : ''} ${next ? 'next' : ''}" data-lane="${lane}">
          <span class="cr-lane-mult">${value >= 1000 ? Math.round(value).toLocaleString('en-IN') : value.toFixed(2)}x</span>
          <span class="cr-lane-road"></span>
        </div>`;
    }).join('');
    this.positionChicken();
  }

  positionChicken() {
    if (!this.road) return;
    const laneEl = this.laneHost.querySelector(`[data-lane="${Math.max(1, this.lane)}"]`);
    if (!laneEl) return;
    const offset = this.lane === 0 ? 0 : laneEl.offsetLeft + laneEl.offsetWidth / 2;
    this.chicken.style.transform = `translateX(${offset}px)`;
    // Keep the chicken roughly centred as the road scrolls forward.
    const target = Math.max(0, offset - this.road.clientWidth / 2);
    this.road.scrollTo({ left: target, behavior: 'smooth' });
  }

  render() {
    const multiplier = this.active ? multiplierAt(this.difficulty, this.lane) : (this.round?.multiplier ?? 1);
    if (this.amountEl) this.amountEl.textContent = money(this.amount);
    if (this.multiplierEl) this.multiplierEl.textContent = `${Number(multiplier || 1).toFixed(2)}x`;
    if (this.liveLabel) {
      this.liveLabel.textContent = this.active
        ? `Lane ${this.lane} of ${this.lanes} · next ${multiplierAt(this.difficulty, this.lane + 1).toFixed(2)}x`
        : 'Place a bet to start';
    }
    if (this.playBtn) {
      this.playBtn.textContent = this.active ? 'Go' : 'Play';
      this.playBtn.disabled = this.busy;
    }
    if (this.cashBtn) {
      this.cashBtn.hidden = !this.active || this.lane === 0;
      this.cashBtn.disabled = this.busy;
      const span = this.cashBtn.querySelector('span');
      if (span) span.textContent = `${money(this.amount * multiplierAt(this.difficulty, this.lane))} INR`;
    }
    if (this.modeSelect) this.modeSelect.disabled = this.active;

    const balance = this.serverMode === false ? this.demoBalance : this.getBalance();
    const shown = money(balance);
    const wallet = document.getElementById('chicken-wallet');
    const brand = document.getElementById('chicken-brand-balance');
    if (wallet) wallet.textContent = shown;
    if (brand) brand.textContent = shown;
  }

  flashBadge(text, tone) {
    if (!this.badge) return;
    this.badge.textContent = text;
    this.badge.className = `cr-badge show ${tone}`;
    setTimeout(() => { this.badge.className = 'cr-badge'; }, 1200);
  }

  toggleFair(open) {
    const sheet = document.getElementById('cr-fair-sheet');
    sheet?.classList.toggle('open', open);
    sheet?.setAttribute('aria-hidden', open ? 'false' : 'true');
  }

  syncFair() {
    const set = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.textContent = value || '—';
    };
    set('cr-fair-hash', this.round?.server_seed_hash);
    set('cr-fair-seed', this.round?.server_seed);
    set('cr-fair-nonce', this.round?.nonce != null ? String(this.round.nonce) : '');
  }

  // ---------- round flow ----------

  async start() {
    if (this.busy || this.active) return;
    if (!this.canPlay()) return this.denyPlay();

    const balance = this.serverMode === false ? this.demoBalance : this.getBalance();
    if (this.amount > balance) return this.toast('Wallet balance is not enough.', 'error');

    this.busy = true;
    this.render();
    try {
      if (this.serverMode) {
        const clientSeed = document.getElementById('cr-fair-client')?.value?.trim() || undefined;
        this.round = await this.api('/api/arcade/chicken/start', 'POST', {
          bet: this.amount, difficulty: this.difficulty, client_seed: clientSeed
        });
        this.setBalance?.(this.round.balance);
      } else {
        this.demoBalance -= this.amount;
        this.round = {
          round_id: `DEMO-${Date.now().toString().slice(-8)}`,
          difficulty: this.difficulty, bet: this.amount, lane: 0, status: 'active'
        };
      }
      if (this.betIdEl) this.betIdEl.textContent = this.round.round_id;
      this.stage?.classList.add('running');
      this.message.textContent = 'Tap Go to cross a lane, or cash out anytime.';
      this.syncFair();
      this.renderLanes();
    } catch (error) {
      this.toast(error.message, 'error');
    } finally {
      this.busy = false;
      this.render();
    }
  }

  async takeStep() {
    if (!this.active || this.busy) return;
    this.busy = true;
    this.render();
    this.stage?.classList.add('hopping');

    try {
      let result;
      if (this.serverMode) {
        result = await this.api('/api/arcade/chicken/step', 'POST', { round_id: this.round.round_id });
      } else {
        const lane = this.lane + 1;
        const safe = secureUnit() < DIFFICULTIES[this.difficulty].survival;
        const done = safe && lane >= this.lanes;
        result = {
          ...this.round, lane, safe,
          status: !safe ? 'lost' : done ? 'cashed' : 'active',
          payout: done ? this.amount * multiplierAt(this.difficulty, lane) : 0,
          completed: done
        };
        if (done) this.demoBalance += result.payout;
      }

      await new Promise(resolve => setTimeout(resolve, 380));   // let the hop play
      this.round = result;
      if (typeof result.balance === 'number') this.setBalance?.(result.balance);

      if (!result.safe) {
        this.stage?.classList.add('crashed');
        this.flashBadge('HIT!', 'bad');
        this.message.textContent = `A car got the chicken on lane ${result.lane}. Bet lost.`;
        this.toast('Chicken hit — bet lost.', 'error');
        setTimeout(() => this.stage?.classList.remove('crashed', 'running'), 900);
      } else if (result.completed) {
        this.flashBadge('ROAD CLEARED!', 'good');
        this.message.textContent = `Crossed every lane — ₹${money(result.payout)} paid out.`;
        this.toast(`Road cleared! ₹${money(result.payout)}`, 'success');
        this.stage?.classList.remove('running');
      } else {
        this.flashBadge('SAFE', 'good');
        this.message.textContent = `Lane ${result.lane} cleared · ₹${money(this.amount * multiplierAt(this.difficulty, result.lane))} available.`;
      }
      this.syncFair();
      this.renderLanes();
    } catch (error) {
      this.toast(error.message, 'error');
    } finally {
      this.stage?.classList.remove('hopping');
      this.busy = false;
      this.render();
    }
  }

  async cashout() {
    if (!this.active || this.busy || this.lane === 0) return;
    this.busy = true;
    this.render();
    try {
      let result;
      if (this.serverMode) {
        result = await this.api('/api/arcade/chicken/cashout', 'POST', { round_id: this.round.round_id });
        this.setBalance?.(result.balance);
      } else {
        const payout = this.amount * multiplierAt(this.difficulty, this.lane);
        this.demoBalance += payout;
        result = { ...this.round, status: 'cashed', payout };
      }
      this.round = result;
      this.stage?.classList.remove('running');
      this.flashBadge('CASHED OUT', 'good');
      this.message.textContent = `Cashed out ₹${money(result.payout)}.`;
      this.toast(`Chicken Road payout ₹${money(result.payout)}`, 'success');
      this.syncFair();
      this.renderLanes();
    } catch (error) {
      this.toast(error.message, 'error');
    } finally {
      this.busy = false;
      this.render();
    }
  }
}
