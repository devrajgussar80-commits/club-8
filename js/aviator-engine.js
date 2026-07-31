/**
 * Aviator, driven by the server's shared round.
 *
 * The important change from the old build: this file decides nothing. It does
 * not draw the crash point, it does not hold the wallet, and it does not run
 * its own round timer. It polls /api/games/aviator/state, syncs to the server
 * clock, and animates whatever round every other player is already watching.
 *
 * Two details worth keeping if this is edited:
 *
 * 1. RENDER_LAG. The client learns about the crash on the next poll, so if it
 *    drew the multiplier at the true current time it would sail past the crash
 *    for a moment and then jump backwards. Drawing it ~one poll behind hides
 *    that. A manual cash-out still settles at the server's real (slightly
 *    higher) multiplier, so the lag can only ever pay the player more.
 *
 * 2. Auto cash-out is sent with the bet and executed server-side. Doing it in
 *    the browser would mean a closed tab or a stalled rAF loses a bet the
 *    player had already set a target for.
 */

import { sound } from './sound.js';

const money = value => Number(value || 0).toFixed(2);
const POLL_FLYING_MS = 250;
const POLL_IDLE_MS = 900;
const RENDER_LAG = 0.28;

export class AviatorEngine {
  constructor(options) {
    Object.assign(this, options);
    this.phase = 'betting';
    this.round = 0;
    this.crash = null;
    this.seedHash = '';
    this.multiplier = 1;
    this.history = [];
    this.bets = [];
    this.myBets = {};
    // server_time - our clock, in seconds. Every timestamp is compared in
    // server time so a device with a wrong clock still lands on the beat.
    this.clockOffset = 0;
    this.takeoffAt = 0;
    this.panels = [];
    this.listTab = 'all';
    this.totalWin = 0;
    this.frame = null;
    this.pollTimer = null;
    this.lastRoundSeen = 0;
  }

  init() {
    this.stage = document.querySelector('.aviator-stage');
    this.live = document.getElementById('aviator-live-multiplier');
    this.status = document.getElementById('aviator-round-status');
    this.countdown = document.getElementById('aviator-countdown');
    this.progress = document.getElementById('aviator-countdown-bar');
    this.recent = document.getElementById('aviator-recent');
    this.trail = document.getElementById('aviator-trail-path');
    this.area = document.getElementById('aviator-area');
    this.listEl = document.getElementById('aviator-player-list');
    this.fairEl = document.getElementById('aviator-fair-hash');

    document.querySelectorAll('[data-aviator-panel]')
      .forEach((panel, index) => this.setupPanel(panel, index));

    document.querySelectorAll('[data-aviator-tab]').forEach(button =>
      button.addEventListener('click', () => {
        this.listTab = button.dataset.aviatorTab;
        document.querySelectorAll('[data-aviator-tab]')
          .forEach(item => item.classList.toggle('active', item === button));
        this.renderList();
      }));

    // Space is the market convention for "cash out now" -- but only while the
    // player is actually looking at the game, and never while typing into the
    // amount or auto-cashout inputs.
    this.onKey = event => {
      if (event.code !== 'Space' || !this.isVisible()) return;
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '')) return;
      const live = this.panels.find(panel => panel.state === 'active');
      if (!live) return;
      event.preventDefault();
      this.cashOut(live);
    };
    document.addEventListener('keydown', this.onKey);

    this.recent.addEventListener('click', event => {
      const chip = event.target.closest('[data-round]');
      if (chip) this.showFairness(Number(chip.dataset.round));
    });

    this.tick = this.tick.bind(this);
    this.frame = requestAnimationFrame(this.tick);
    this.poll();
  }

  isVisible() {
    return document.getElementById('page-aviator')?.classList.contains('active');
  }

  now() {
    return Date.now() / 1000 + this.clockOffset;
  }

  // ------------------------------------------------------------- panels

  setupPanel(element, index) {
    const panel = {
      element,
      index,
      amount: 10,
      // idle | queued (bet accepted, waiting for takeoff) | active | cashed
      state: 'idle',
      payout: 0,
      cashedAt: 0,
      busy: false,
      autoBet: false,
      output: element.querySelector('[data-aviator-amount]'),
      action: element.querySelector('[data-aviator-action="bet"]'),
      target: element.querySelector('.aviator-auto-target input'),
      targetWrap: element.querySelector('.aviator-auto-target'),
      autoBetBox: element.querySelector('[data-aviator-autobet]')
    };
    this.panels.push(panel);

    element.querySelector('[data-aviator-action="minus"]')
      ?.addEventListener('click', () => this.setAmount(panel, panel.amount - 10));
    element.querySelector('[data-aviator-action="plus"]')
      ?.addEventListener('click', () => this.setAmount(panel, panel.amount + 10));
    element.querySelectorAll('[data-value]').forEach(button =>
      button.addEventListener('click', () => this.setAmount(panel, button.dataset.value)));

    element.querySelectorAll('[data-mode]').forEach(button =>
      button.addEventListener('click', () => {
        element.querySelectorAll('[data-mode]')
          .forEach(item => item.classList.toggle('active', item === button));
        panel.targetWrap.hidden = button.dataset.mode !== 'auto';
        if (panel.autoBetBox) panel.autoBetBox.hidden = button.dataset.mode !== 'auto';
      }));

    panel.autoBetBox?.querySelector('input')?.addEventListener('change', event => {
      panel.autoBet = event.target.checked;
      this.toast(panel.autoBet
        ? `Auto bet on for panel ${index + 1}.`
        : `Auto bet off for panel ${index + 1}.`, 'success');
    });

    panel.action.addEventListener('click', () => this.handleAction(panel));
    this.renderPanel(panel);
  }

  setAmount(panel, raw) {
    if (panel.state !== 'idle' && panel.state !== 'cashed') return;
    panel.amount = Math.max(1, Math.min(100000, Math.round(Number(raw) || 10)));
    this.renderPanel(panel);
  }

  autoTarget(panel) {
    if (panel.targetWrap?.hidden) return null;
    const value = Number(panel.target?.value);
    return value >= 1.01 ? value : null;
  }

  handleAction(panel) {
    if (panel.busy) return;
    if (panel.state === 'active') return this.cashOut(panel);
    if (panel.state === 'queued') return this.cancelBet(panel);
    return this.placeBet(panel);
  }

  async placeBet(panel, silent = false) {
    if (!this.canPlay()) return this.denyPlay();
    if (this.phase !== 'betting') {
      if (!silent) this.toast('Next round ke liye betting window ka wait karein.', 'error');
      return;
    }
    if (panel.amount > this.getBalance()) {
      if (!silent) this.toast('Wallet balance kam hai.', 'error');
      return;
    }

    panel.busy = true;
    this.renderPanel(panel);
    try {
      const data = await this.api('/api/games/aviator/bet', 'POST', {
        amount: panel.amount,
        panel: panel.index,
        auto_cashout: this.autoTarget(panel)
      });
      this.setBalance(data.balance);
      panel.state = 'queued';
      panel.payout = 0;
      panel.cashedAt = 0;
      sound.playBetPlaced?.();
    } catch (error) {
      if (!silent) this.toast(error.message || 'Bet nahi lag paya.', 'error');
    } finally {
      panel.busy = false;
      this.renderPanel(panel);
    }
  }

  async cancelBet(panel) {
    panel.busy = true;
    this.renderPanel(panel);
    try {
      const data = await this.api('/api/games/aviator/cancel', 'POST', { panel: panel.index });
      this.setBalance(data.balance);
      panel.state = 'idle';
      this.toast('Bet cancelled and refunded.', 'success');
    } catch (error) {
      this.toast(error.message || 'Cancel nahi ho paya.', 'error');
    } finally {
      panel.busy = false;
      this.renderPanel(panel);
    }
  }

  async cashOut(panel) {
    if (panel.state !== 'active' || panel.busy) return;
    panel.busy = true;
    this.renderPanel(panel);
    try {
      const data = await this.api('/api/games/aviator/cashout', 'POST', { panel: panel.index });
      this.setBalance(data.balance);
      panel.state = 'cashed';
      panel.cashedAt = data.multiplier;
      panel.payout = data.payout;
      this.totalWin += data.payout;
      this.renderTotalWin();
      sound.playWin?.();
      this.toast(`Cash out ₹${money(data.payout)} at ${data.multiplier.toFixed(2)}x`, 'success');
    } catch (error) {
      // The usual failure here is a genuine loss: the plane flew away between
      // the tap and the request landing.
      panel.state = 'idle';
      this.toast(error.message || 'Cash out miss ho gaya.', 'error');
    } finally {
      panel.busy = false;
      this.renderPanel(panel);
    }
  }

  // --------------------------------------------------------------- server

  async poll() {
    clearTimeout(this.pollTimer);
    if (this.isVisible() && this.canPlay()) {
      try {
        await this.syncState();
      } catch (error) {
        // A dropped poll is not worth a toast; the next one recovers.
      }
    }
    const delay = this.phase === 'flying' ? POLL_FLYING_MS : POLL_IDLE_MS;
    this.pollTimer = setTimeout(() => this.poll(), delay);
  }

  async syncState() {
    const data = await this.api('/api/games/aviator/state', 'GET');
    this.clockOffset = data.server_time - Date.now() / 1000;
    this.takeoffAt = data.takeoff_at;
    this.seedHash = data.seed_hash;
    this.bets = data.bets || [];
    this.myBets = data.my_bets || {};
    this.history = data.history || [];

    const rolledOver = data.round !== this.round;
    this.round = data.round;
    this.phase = data.phase;
    this.crash = data.crash;

    this.syncPanels(rolledOver);
    if (rolledOver) {
      this.totalWin = 0;
      this.renderTotalWin();
    }
    this.renderHistory();
    this.renderList();
    if (this.fairEl) this.fairEl.textContent = `#${this.round} · ${this.seedHash.slice(0, 16)}…`;
  }

  /** Reconcile each panel against what the server says it holds. */
  syncPanels(rolledOver) {
    this.panels.forEach(panel => {
      if (panel.busy) return;
      const mine = this.myBets[String(panel.index)];

      if (!mine) {
        // No bet on the server. Anything the client thought was live is over.
        if (panel.state === 'queued' || panel.state === 'active') panel.state = 'idle';
        if (rolledOver && panel.autoBet && this.phase === 'betting') {
          panel.state = 'idle';
          this.placeBet(panel, true);
        }
        this.renderPanel(panel);
        return;
      }

      if (mine.cashed_at) {
        if (panel.state !== 'cashed') {
          panel.state = 'cashed';
          panel.cashedAt = mine.cashed_at;
          panel.payout = mine.payout;
          this.totalWin += mine.payout;
          this.renderTotalWin();
          this.toast(`Auto cash out ₹${money(mine.payout)} at ${mine.cashed_at.toFixed(2)}x`, 'success');
        }
      } else if (this.phase === 'betting') {
        panel.state = 'queued';
      } else if (this.phase === 'flying') {
        panel.state = 'active';
      } else if (panel.state === 'active' || panel.state === 'queued') {
        panel.state = 'idle';
        sound.playLoss?.();
        this.toast(`Bet ${panel.index + 1} lost at ${Number(this.crash).toFixed(2)}x`, 'error');
      }
      panel.renderAmount = mine.amount;
      this.renderPanel(panel);
    });
  }

  async showFairness(roundNo) {
    try {
      const data = await this.api(`/api/games/aviator/fairness/${roundNo}`, 'GET');
      this.toast(
        `Round #${data.round} crashed at ${data.crash.toFixed(2)}x — seed ${data.server_seed.slice(0, 20)}…`,
        'success'
      );
    } catch (error) {
      this.toast(error.message || 'Round abhi complete nahi hua.', 'error');
    }
  }

  // ------------------------------------------------------------ rendering

  tick() {
    if (this.isVisible()) this.renderStage();
    this.frame = requestAnimationFrame(this.tick);
  }

  renderStage() {
    const now = this.now();

    if (this.phase === 'betting') {
      this.multiplier = 1;
      const left = Math.max(0, this.takeoffAt - now);
      this.status.textContent = 'WAITING FOR NEXT ROUND';
      this.countdown.hidden = false;
      this.countdown.textContent = left.toFixed(1);
      if (this.progress) this.progress.style.width = `${Math.min(100, (1 - left / 6) * 100)}%`;
      this.stage.classList.remove('is-flying', 'is-crashed');
      this.setFlight(0);
    } else if (this.phase === 'flying') {
      const elapsed = Math.max(0, now - this.takeoffAt - RENDER_LAG);
      this.multiplier = Math.exp(elapsed * 0.28);
      this.status.textContent = 'PLANE IS FLYING';
      this.countdown.hidden = true;
      this.stage.classList.add('is-flying');
      this.stage.classList.remove('is-crashed');
      this.setFlight(Math.min(.96, elapsed / 8));
      this.panels.forEach(panel => panel.state === 'active' && this.renderPanel(panel));
    } else {
      this.multiplier = Number(this.crash || this.multiplier);
      this.status.textContent = `FLEW AWAY AT ${this.multiplier.toFixed(2)}x`;
      this.countdown.hidden = true;
      this.stage.classList.remove('is-flying');
      this.stage.classList.add('is-crashed');
    }

    this.live.textContent = `${this.multiplier.toFixed(2)}x`;
    this.live.classList.toggle('crashed', this.phase === 'crashed');
  }

  /**
   * Redraw the flight curve for `progress` (0..1 across the stage).
   *
   * The curve is rebuilt each frame rather than revealed with a dash offset,
   * because the market look is a *filled* area under a bending line whose tip
   * is the plane -- a fixed path can only be uncovered, it cannot bend.
   * Everything is in the SVG's 100x60 viewBox and the plane is placed with the
   * same numbers, so the nose sits exactly on the end of the line.
   */
  setFlight(progress) {
    const x = 4 + progress * 88;
    const y = 56 - Math.pow(progress, .72) * 50;
    // Control point on the baseline: gives the slow-then-steep climb.
    const curve = `M 4 56 Q ${(4 + (x - 4) * .58).toFixed(2)} 56 ${x.toFixed(2)} ${y.toFixed(2)}`;
    if (this.trail) this.trail.setAttribute('d', curve);
    if (this.area) this.area.setAttribute('d', `${curve} L ${x.toFixed(2)} 56 L 4 56 Z`);
    this.stage.style.setProperty('--flight-left', `${x}%`);
    this.stage.style.setProperty('--flight-bottom', `${((60 - y) / 60) * 100}%`);
  }

  renderPanel(panel) {
    panel.output.textContent = money(panel.amount);
    panel.element.classList.toggle('locked', panel.state === 'queued' || panel.state === 'active');
    panel.action.className = 'aviator-bet-button';
    panel.action.disabled = panel.busy;

    if (panel.busy) {
      panel.action.innerHTML = '<span>…</span>';
      return;
    }
    if (panel.state === 'active') {
      panel.action.classList.add('cashout');
      panel.action.innerHTML =
        `Cash Out <span>${money(panel.amount * this.multiplier)} INR</span>`;
    } else if (panel.state === 'queued') {
      panel.action.classList.add('waiting');
      panel.action.innerHTML = `Cancel <span>${money(panel.amount)} INR</span>`;
    } else if (panel.state === 'cashed') {
      panel.action.classList.add('done');
      panel.action.innerHTML =
        `Cashed Out ${Number(panel.cashedAt).toFixed(2)}x <span>${money(panel.payout)} INR</span>`;
    } else {
      panel.action.innerHTML = `Bet <span>${money(panel.amount)} INR</span>`;
    }
  }

  renderHistory() {
    if (!this.recent) return;
    this.recent.innerHTML = this.history.map(item => {
      const cls = item.crash >= 10 ? 'hot' : item.crash >= 2 ? 'high' : '';
      return `<span class="${cls}" data-round="${item.round}" role="button" tabindex="0"
        title="Tap to verify round #${item.round}">${item.crash.toFixed(2)}x</span>`;
    }).join('');
  }

  renderList() {
    if (!this.listEl) return;
    let rows = this.bets;
    if (this.listTab === 'mine') rows = rows.filter(row => row.mine);
    if (this.listTab === 'top') rows = [...rows].sort((a, b) => b.payout - a.payout).slice(0, 10);

    this.listEl.innerHTML = rows.length
      ? rows.map(row => `
        <div class="${row.mine ? 'is-mine' : ''}${row.cashed_at ? ' has-cashed' : ''}">
          <span class="aviator-player-avatar">${row.mine ? '⭐' : '👤'}</span>
          <b>${row.player}</b>
          <span>${money(row.amount)}</span>
          <span>${row.cashed_at ? row.cashed_at.toFixed(2) + 'x' : '—'}</span>
          <span>${row.cashed_at ? money(row.payout) : '—'}</span>
        </div>`).join('')
      : '<div class="aviator-empty">Is round me abhi koi bet nahi.</div>';

    const cashed = this.bets.filter(row => row.cashed_at).length;
    const count = document.getElementById('aviator-bet-count');
    if (count) count.textContent = `${cashed}/${this.bets.length} Bets`;
  }

  renderTotalWin() {
    const el = document.getElementById('aviator-total-win');
    if (el) el.innerHTML = `${money(this.totalWin)}<small>Total win INR</small>`;
  }

  renderWallet() {
    const value = money(this.getBalance());
    const wallet = document.getElementById('aviator-wallet');
    const brand = document.getElementById('aviator-brand-balance');
    if (wallet) wallet.textContent = value;
    if (brand) brand.textContent = value;
  }

  destroy() {
    cancelAnimationFrame(this.frame);
    clearTimeout(this.pollTimer);
    document.removeEventListener('keydown', this.onKey);
  }
}
