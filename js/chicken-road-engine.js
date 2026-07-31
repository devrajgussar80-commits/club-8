const money = value => Number(value || 0).toFixed(2);
const secureUnit = () => {
  const bytes = new Uint32Array(1);
  crypto.getRandomValues(bytes);
  return (bytes[0] + 1) / 4294967297;
};
const MODES = {
  easy: { safe: .82, growth: 1.18 },
  medium: { safe: .70, growth: 1.34 },
  hard: { safe: .58, growth: 1.58 },
  hardcore: { safe: .46, growth: 1.92 }
};

export class ChickenRoadEngine {
  constructor({ getBalance, changeBalance, toast, canPlay, denyPlay }) {
    this.getBalance = getBalance;
    this.changeBalance = changeBalance;
    this.toast = toast;
    this.canPlay = canPlay || (() => true);
    this.denyPlay = denyPlay || (() => {});
    this.amount = 1;
    this.active = false;
    this.busy = false;
    this.step = 0;
    this.multiplier = 1;
  }

  init() {
    this.stage = document.querySelector('.chicken-stage');
    this.amountEl = document.getElementById('chicken-amount');
    this.multiplierEl = document.getElementById('chicken-road-multiplier');
    this.message = document.getElementById('chicken-message');
    this.play = document.getElementById('chicken-play');
    this.cashout = document.getElementById('chicken-cashout');
    this.mode = document.getElementById('chicken-difficulty');
    this.track = document.getElementById('chicken-step-track');
    this.badge = document.getElementById('chicken-result-badge');
    this.runner = document.getElementById('chicken-runner');
    this.car = document.getElementById('chicken-car');
    document.getElementById('chicken-minus').addEventListener('click', () => this.setAmount(this.amount - 1));
    document.getElementById('chicken-plus').addEventListener('click', () => this.setAmount(this.amount + 1));
    document.querySelectorAll('[data-chicken-multiplier]').forEach(button => button.addEventListener('click', () => this.setAmount(this.amount * Number(button.dataset.chickenMultiplier))));
    this.play.addEventListener('click', () => this.active ? this.takeStep() : this.start());
    this.cashout.addEventListener('click', () => this.finish(true));
    this.mode.addEventListener('change', () => this.renderTrack());
    this.renderTrack();
    this.render();
  }

  setAmount(value) {
    if (this.active || this.busy) return;
    this.amount = Math.max(1, Math.min(100000, Number(value) || 1));
    this.render();
  }

  start() {
    if (!this.canPlay()) {
      this.denyPlay();
      return;
    }
    if (this.amount > this.getBalance()) return this.toast('Wallet balance kam hai.', 'error');
    this.changeBalance(-this.amount);
    this.active = true;
    this.step = 0;
    this.multiplier = 1;
    document.getElementById('chicken-bet-id').textContent = `CR${Date.now().toString().slice(-8)}`;
    this.message.textContent = 'Road ready — GO dabao ya stake cash out karo.';
    this.badge.textContent = '';
    this.stage.className = 'chicken-stage active';
    this.stage.style.setProperty('--runner-x', '18%');
    this.renderTrack();
    this.render();
  }

  takeStep() {
    if (!this.active || this.busy) return;
    this.busy = true;
    this.stage.classList.add('is-playing');
    this.stage.classList.remove('is-hit');
    this.car.classList.remove('will-hit');
    this.play.disabled = true;
    this.message.textContent = 'Chicken jumping…';
    const config = MODES[this.mode.value];
    const safe = secureUnit() < config.safe;
    this.car.classList.toggle('will-hit', !safe);
    requestAnimationFrame(() => this.car.classList.add('is-passing'));
    setTimeout(() => {
      this.busy = false;
      this.stage.classList.remove('is-playing');
      this.car.classList.remove('is-passing', 'will-hit');
      if (!safe) return this.finish(false);
      this.step += 1;
      this.multiplier = Number(Math.pow(config.growth, this.step).toFixed(2));
      this.stage.style.setProperty('--runner-x', `${18 + Math.min(5, this.step) * 14}%`);
      this.message.textContent = `Safe step ${this.step}! ₹${money(this.amount * this.multiplier)} available.`;
      this.badge.textContent = 'SAFE';
      setTimeout(() => { if (this.active) this.badge.textContent = ''; }, 700);
      this.renderTrack();
      this.render();
    }, 860);
  }

  finish(cashedOut) {
    if (!this.active) return;
    this.active = false;
    this.busy = false;
    this.stage.classList.remove('is-playing', 'active');
    if (cashedOut) {
      const payout = this.amount * this.multiplier;
      this.changeBalance(payout);
      this.message.textContent = `Cashed out ₹${money(payout)} at ${this.multiplier.toFixed(2)}x.`;
      this.badge.textContent = 'CASHED OUT';
      this.toast(`Chicken Road payout ₹${money(payout)}`, 'success');
    } else {
      this.message.textContent = `Chicken hit on step ${this.step + 1}. Bet lost.`;
      this.badge.textContent = 'HIT!';
      this.stage.classList.add('is-hit');
      this.toast('Chicken hit — active bet lost.', 'error');
      setTimeout(() => this.stage.classList.remove('is-hit'), 1100);
    }
    this.render();
  }

  renderTrack() {
    const config = MODES[this.mode.value];
    this.track.innerHTML = Array.from({ length: 5 }, (_, index) => {
      const value = Math.pow(config.growth, index + 1);
      return `<span class="${index < this.step ? 'passed' : index === this.step && this.active ? 'next' : ''}">${value.toFixed(2)}x</span>`;
    }).join('');
  }

  render() {
    this.amountEl.textContent = money(this.amount);
    this.multiplierEl.textContent = `${this.multiplier.toFixed(2)}x`;
    this.play.textContent = this.active ? 'GO — Next Step' : 'Play';
    this.play.disabled = this.busy;
    this.cashout.hidden = !this.active;
    this.cashout.querySelector('span').textContent = `${money(this.amount * this.multiplier)} INR`;
    this.mode.disabled = this.active;
    const value = money(this.getBalance());
    document.getElementById('chicken-wallet').textContent = value;
    document.getElementById('chicken-brand-balance').textContent = value;
  }
}
