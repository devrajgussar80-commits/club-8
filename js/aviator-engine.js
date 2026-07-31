const money = value => Number(value || 0).toFixed(2);
const secureUnit = () => {
  const bytes = new Uint32Array(1);
  crypto.getRandomValues(bytes);
  return (bytes[0] + 1) / 4294967297;
};

export class AviatorEngine {
  constructor({ getBalance, changeBalance, toast, canPlay, denyPlay }) {
    this.getBalance = getBalance;
    this.changeBalance = changeBalance;
    this.toast = toast;
    this.canPlay = canPlay || (() => true);
    this.denyPlay = denyPlay || (() => {});
    this.phase = 'betting';
    this.multiplier = 1;
    this.crashAt = 1;
    this.totalWin = 0;
    this.roundStartedAt = 0;
    this.phaseEndsAt = performance.now() + 4200;
    this.history = [1.17, 1.07, 1.07, 1.21, 5.47, 2.39, 11.03];
    this.panels = [];
    this.frame = null;
  }

  init() {
    this.stage = document.querySelector('.aviator-stage');
    this.live = document.getElementById('aviator-live-multiplier');
    this.status = document.getElementById('aviator-round-status');
    this.countdown = document.getElementById('aviator-countdown');
    this.recent = document.getElementById('aviator-recent');
    this.plane = document.getElementById('aviator-plane');
    this.trail = document.getElementById('aviator-trail-path');
    document.querySelectorAll('[data-aviator-panel]').forEach((panel, index) => this.setupPanel(panel, index));
    this.renderHistory();
    this.renderWallet();
    this.renderPlayers();
    this.tick = this.tick.bind(this);
    this.frame = requestAnimationFrame(this.tick);
  }

  setupPanel(element, index) {
    const panel = {
      element, index, amount: 10, queued: false, active: false, cashed: false, payout: 0, cashedAt: 0,
      output: element.querySelector('[data-aviator-amount]'),
      action: element.querySelector('[data-aviator-action="bet"]'),
      target: element.querySelector('.aviator-auto-target input'),
      targetWrap: element.querySelector('.aviator-auto-target')
    };
    this.panels.push(panel);
    element.querySelector('[data-aviator-action="minus"]')?.addEventListener('click', () => this.setAmount(panel, panel.amount - 10));
    element.querySelector('[data-aviator-action="plus"]')?.addEventListener('click', () => this.setAmount(panel, panel.amount + 10));
    element.querySelectorAll('[data-value]').forEach(button => button.addEventListener('click', () => this.setAmount(panel, button.dataset.value)));
    element.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click', () => {
      element.querySelectorAll('[data-mode]').forEach(item => item.classList.toggle('active', item === button));
      panel.targetWrap.hidden = button.dataset.mode !== 'auto';
    }));
    panel.action.addEventListener('click', () => this.handleAction(panel));
    this.renderPanel(panel);
  }

  setAmount(panel, raw) {
    if (panel.queued || panel.active) return;
    panel.amount = Math.max(1, Math.min(100000, Number(raw) || 10));
    this.renderPanel(panel);
  }

  handleAction(panel) {
    if (panel.active) return this.cashOut(panel);
    if (panel.queued) {
      panel.queued = false;
      this.changeBalance(panel.amount);
      this.toast('Bet cancelled and refunded.', 'success');
      return this.renderPanel(panel);
    }
    if (!this.canPlay()) {
      this.denyPlay();
      return;
    }
    if (this.phase !== 'betting') {
      this.toast('Next round ke liye betting window ka wait karein.', 'error');
      return;
    }
    if (panel.amount > this.getBalance()) {
      this.toast('Wallet balance kam hai.', 'error');
      return;
    }
    this.changeBalance(-panel.amount);
    panel.queued = true;
    this.renderPanel(panel);
  }

  cashOut(panel, automatic = false) {
    if (!panel.active || panel.cashed || this.phase !== 'flying') return;
    panel.active = false;
    panel.cashed = true;
    const payout = panel.amount * this.multiplier;
    panel.payout = payout;
    panel.cashedAt = this.multiplier;
    this.totalWin += payout;
    this.changeBalance(payout);
    document.getElementById('aviator-total-win').innerHTML = `${money(this.totalWin)}<small>Total win INR</small>`;
    this.toast(`${automatic ? 'Auto c' : 'C'}ash out ₹${money(payout)} at ${this.multiplier.toFixed(2)}x`, 'success');
    this.renderPanel(panel);
  }

  startFlight(now) {
    this.phase = 'flying';
    this.roundStartedAt = now;
    const raw = 0.97 / secureUnit();
    this.crashAt = Math.min(100, Math.max(1, Math.floor(raw * 100) / 100));
    this.multiplier = 1;
    this.panels.forEach(panel => {
      panel.active = panel.queued;
      panel.queued = false;
      panel.cashed = false;
      panel.payout = 0;
      panel.cashedAt = 0;
      this.renderPanel(panel);
    });
    this.stage.classList.add('is-flying');
    this.stage.classList.remove('is-crashed');
    this.stage.style.setProperty('--flight-left', '3%');
    this.stage.style.setProperty('--flight-bottom', '3%');
    this.trail.style.strokeDashoffset = '1';
  }

  crash(now) {
    this.phase = 'crashed';
    this.phaseEndsAt = now + 2600;
    this.multiplier = this.crashAt;
    this.history.unshift(this.crashAt);
    this.history = this.history.slice(0, 12);
    this.panels.forEach(panel => {
      if (panel.active) {
        panel.active = false;
        this.toast(`Bet ${panel.index + 1} lost at ${this.crashAt.toFixed(2)}x`, 'error');
      }
      this.renderPanel(panel);
    });
    this.stage.classList.remove('is-flying');
    this.stage.classList.add('is-crashed');
    this.trail.style.strokeDashoffset = '0';
    this.renderHistory();
    this.renderPlayers();
  }

  resetBetting(now) {
    this.phase = 'betting';
    this.phaseEndsAt = now + 4200;
    this.multiplier = 1;
    this.stage.classList.remove('is-crashed');
    this.panels.forEach(panel => {
      panel.cashed = false;
      this.renderPanel(panel);
    });
  }

  tick(now) {
    if (this.phase === 'betting' && now >= this.phaseEndsAt) this.startFlight(now);
    if (this.phase === 'flying') {
      const elapsed = (now - this.roundStartedAt) / 1000;
      this.multiplier = Math.min(this.crashAt, Math.exp(elapsed * 0.28));
      const progress = Math.min(.96, elapsed / 8);
      this.stage.style.setProperty('--flight-left', `${3 + progress * 89}%`);
      this.stage.style.setProperty('--flight-bottom', `${3 + Math.pow(progress, .72) * 78}%`);
      this.trail.style.strokeDashoffset = String(1 - progress);
      this.panels.forEach(panel => {
        if (panel.active && Number(panel.target?.value) <= this.multiplier && !panel.targetWrap.hidden) this.cashOut(panel, true);
        else if (panel.active) this.renderPanel(panel);
      });
      if (this.multiplier >= this.crashAt) this.crash(now);
    } else if (this.phase === 'crashed' && now >= this.phaseEndsAt) {
      this.resetBetting(now);
    }
    this.renderStage(now);
    this.frame = requestAnimationFrame(this.tick);
  }

  renderStage(now) {
    this.live.textContent = `${this.multiplier.toFixed(2)}x`;
    this.live.classList.toggle('crashed', this.phase === 'crashed');
    if (this.phase === 'betting') {
      this.status.textContent = 'WAITING FOR NEXT ROUND';
      this.countdown.hidden = false;
      this.countdown.textContent = Math.max(0, (this.phaseEndsAt - now) / 1000).toFixed(1);
    } else if (this.phase === 'flying') {
      this.status.textContent = 'PLANE IS FLYING';
      this.countdown.hidden = true;
    } else {
      this.status.textContent = `FLEW AWAY AT ${this.crashAt.toFixed(2)}x`;
      this.countdown.hidden = true;
    }
  }

  renderPanel(panel) {
    panel.output.textContent = money(panel.amount);
    panel.element.classList.toggle('locked', panel.queued || panel.active);
    panel.action.className = 'aviator-bet-button';
    if (panel.active) {
      panel.action.classList.add('cashout');
      panel.action.innerHTML = `Cash Out <span>${money(panel.amount * this.multiplier)} INR</span>`;
    } else if (panel.queued) {
      panel.action.classList.add('waiting');
      panel.action.innerHTML = `Cancel <span>${money(panel.amount)} INR</span>`;
    } else if (panel.cashed) {
      panel.action.classList.add('done');
      panel.action.innerHTML = `Cashed Out ${panel.cashedAt.toFixed(2)}x <span>${money(panel.payout)} INR</span>`;
    } else {
      panel.action.innerHTML = `Bet <span>${money(panel.amount)} INR</span>`;
    }
  }

  renderHistory() {
    this.recent.innerHTML = this.history.map(value => `<span class="${value >= 10 ? 'hot' : value >= 2 ? 'high' : ''}">${value.toFixed(2)}x</span>`).join('');
  }

  renderWallet() {
    const value = money(this.getBalance());
    document.getElementById('aviator-wallet').textContent = value;
    document.getElementById('aviator-brand-balance').textContent = value;
  }

  renderPlayers() {
    const names = ['1***6','1***2','1***e','1***3','1***2','1***0','1***d','1***5','1***8','1***9'];
    const icons = ['🦅','🌙','🐺','🐱','✈️','🐺','🐒','🦅','🎲','🥷'];
    const list = document.getElementById('aviator-player-list');
    const base = 3000 + Math.floor(secureUnit() * 5000);
    list.innerHTML = names.map((name, index) => `<div><span class="aviator-player-avatar">${icons[index]}</span><b>${name}</b><span>${(base - index * 250).toLocaleString('en-IN')}.00</span><span>${index < 4 ? this.history[index].toFixed(2) + 'x' : ''}</span><span>${index < 4 ? money((base - index * 250) * this.history[index]) : ''}</span></div>`).join('');
    const count = 815 + Math.floor(secureUnit() * 420);
    document.getElementById('aviator-bet-count').textContent = `${count}/${count} Bets`;
  }
}
