/**
 * Chicken Road — a scrolling-road crossing game with a wallet stake.
 *
 * FAIRNESS NOTE, read before changing the jump logic:
 * the RNG decides safe/hit BEFORE the chicken leaves the ground, and the traffic
 * is then steered to show that result. The player is never killed by a car that
 * happened to be there, and can never survive by timing the tap well. If the
 * collision were decided by the animation instead, a player who learnt the car
 * rhythm would beat the payout table and the house edge would vanish.
 */

const money = value => Number(value || 0).toFixed(2);

const secureUnit = () => {
  const bytes = new Uint32Array(1);
  crypto.getRandomValues(bytes);
  return (bytes[0] + 1) / 4294967297;
};

/**
 * `safe` is the chance of surviving one lane; `growth` is the multiplier step.
 * growth is deliberately below 1/safe — that gap is the house edge.
 * easy 1.18 vs 1.22 fair, medium 1.34 vs 1.43, hard 1.58 vs 1.72, hardcore 1.92 vs 2.17.
 */
const MODES = {
  easy: { safe: .82, growth: 1.18 },
  medium: { safe: .70, growth: 1.34 },
  hard: { safe: .58, growth: 1.58 },
  hardcore: { safe: .46, growth: 1.92 }
};

const NS = 'http://www.w3.org/2000/svg';

// World geometry, in viewBox units. The kerb ends at ROAD_START.
const LANES = 9;
const LANE_WIDTH = 245;
const ROAD_START = 266;
const LANDING_Y = 1020;
const START_X = 175;
// Once past the second lane the chicken stops moving across the screen and the
// road slides under it instead, so the run can be longer than the viewport.
const ANCHOR_X = 610;
const ANCHOR_FROM_LANE = 2;
const CAR_W = 102;
const CAR_H = 184;
const CAR_COLORS = ['#e34b4b', '#3da7ea', '#efc94b', '#8b62d2', '#32b17c', '#ef7c39'];

const laneCenter = index => ROAD_START + LANE_WIDTH * index + LANE_WIDTH / 2;

export class ChickenRoadEngine {
  constructor({ getBalance, setBalance, api, toast, canPlay, denyPlay }) {
    this.getBalance = getBalance;
    // Server-authoritative now: the backend debits the stake, draws the bust
    // lane and reveals each jump. This engine only animates the verdict and
    // stores the real balance the server returns -- it never moves money
    // itself, which is what let the old build bet with money that wasn't there.
    this.setBalance = setBalance;
    this.api = api;
    this.toast = toast;
    this.canPlay = canPlay || (() => true);
    this.denyPlay = denyPlay || (() => {});
    this.roundId = null;
    this._pending = null;
    this.amount = 1;
    this.active = false;
    this.busy = false;
    this.lane = 0;
    this.multiplier = 1;
    this.roadOffset = 0;
    this.cars = [];
    this.lastFrame = 0;
    this.loopId = null;
  }

  init() {
    this.stage = document.querySelector('.chicken-stage');
    this.amountEl = document.getElementById('chicken-amount');
    this.multiplierEl = document.getElementById('chicken-road-multiplier');
    this.message = document.getElementById('chicken-message');
    this.play = document.getElementById('chicken-play');
    this.cashout = document.getElementById('chicken-cashout');
    this.mode = document.getElementById('chicken-difficulty');
    this.badge = document.getElementById('chicken-result-badge');

    this.world = document.getElementById('chicken-road-world');
    this.laneLines = document.getElementById('chicken-lane-lines');
    this.targets = document.getElementById('chicken-targets');
    this.carLayer = document.getElementById('chicken-cars');
    this.runner = document.getElementById('chicken-runner');
    this.shadow = document.getElementById('chicken-shadow');

    document.getElementById('chicken-minus').addEventListener('click', () => this.setAmount(this.amount - 1));
    document.getElementById('chicken-plus').addEventListener('click', () => this.setAmount(this.amount + 1));
    document.querySelectorAll('[data-chicken-multiplier]').forEach(button =>
      button.addEventListener('click', () => this.setAmount(this.amount * Number(button.dataset.chickenMultiplier))));
    this.play.addEventListener('click', () => (this.active ? this.jump() : this.start()));
    this.cashout.addEventListener('click', () => this.finish(true));
    this.mode.addEventListener('change', () => this.buildTargets());

    this.buildLaneLines();
    this.buildTargets();
    this.seedCars();
    this.resetChicken();
    this.startLoop();
    this.render();
  }

  // ---------------- world construction ----------------

  buildLaneLines() {
    const dashes = [];
    for (let i = 1; i < LANES; i++) {
      const x = ROAD_START + LANE_WIDTH * i;
      for (let y = -40; y < 1740; y += 134) {
        dashes.push(`<rect x="${x - 4.5}" y="${y}" width="9" height="74" rx="2" fill="#f2f2f2"/>`);
      }
    }
    this.laneLines.innerHTML = dashes.join('');
  }

  /** Multipliers are the payout ladder, so they follow difficulty, not fixed art values. */
  laneMultiplier(laneIndex) {
    return Math.pow(MODES[this.mode.value].growth, laneIndex + 1);
  }

  buildTargets() {
    this.targets.innerHTML = Array.from({ length: LANES }, (_, i) => {
      const value = this.laneMultiplier(i);
      const label = value >= 100 ? value.toFixed(0) : value.toFixed(2);
      return `<g class="cr-target" data-lane="${i}" transform="translate(${laneCenter(i)} ${LANDING_Y})">
          <circle r="70"></circle>
          <circle class="cr-ring" r="50"></circle>
          <text y="3">${label}x</text>
        </g>`;
    }).join('');
    this.paintTargets();
  }

  paintTargets() {
    this.targets.querySelectorAll('.cr-target').forEach(node => {
      const index = Number(node.dataset.lane);
      node.classList.toggle('is-passed', index < this.lane);
      node.classList.toggle('is-next', this.active && index === this.lane);
    });
  }

  seedCars() {
    this.carLayer.innerHTML = '';
    this.cars = [];
    for (let i = 0; i < LANES; i++) {
      const speed = 220 + i * 18;
      this.addCar(i, -180 - ((i * 190) % 1200), speed, CAR_COLORS[i % CAR_COLORS.length]);
      this.addCar(i, -980 - ((i * 145) % 900), speed + 35, CAR_COLORS[(i + 2) % CAR_COLORS.length]);
    }
    this.updateCarVisibility();
  }

  addCar(laneIndex, y, speed, color) {
    const g = document.createElementNS(NS, 'g');
    g.setAttribute('class', 'cr-car');
    g.innerHTML = `
      <rect x="${-CAR_W / 2}" y="${-CAR_H / 2}" width="${CAR_W}" height="${CAR_H}" rx="28" fill="${color}" stroke="#292929" stroke-width="6"/>
      <rect x="${-CAR_W / 2 + 15}" y="${-CAR_H / 2 + 34}" width="${CAR_W - 30}" height="50" rx="13" fill="#bde2ef" stroke="#3d535b" stroke-width="4"/>
      <rect x="${-CAR_W / 2 + 15}" y="${CAR_H / 2 - 79}" width="${CAR_W - 30}" height="45" rx="12" fill="#9cc8d7" stroke="#3d535b" stroke-width="4"/>
      <rect x="${-CAR_W / 2 - 8}" y="${-CAR_H / 2 + 36}" width="12" height="43" rx="5" fill="#222"/>
      <rect x="${CAR_W / 2 - 4}" y="${-CAR_H / 2 + 36}" width="12" height="43" rx="5" fill="#222"/>
      <rect x="${-CAR_W / 2 - 8}" y="${CAR_H / 2 - 79}" width="12" height="43" rx="5" fill="#222"/>
      <rect x="${CAR_W / 2 - 4}" y="${CAR_H / 2 - 79}" width="12" height="43" rx="5" fill="#222"/>
      <circle cx="${-CAR_W / 2 + 19}" cy="${-CAR_H / 2 + 16}" r="7" fill="#fff3a6"/>
      <circle cx="${CAR_W / 2 - 19}" cy="${-CAR_H / 2 + 16}" r="7" fill="#fff3a6"/>`;
    this.carLayer.appendChild(g);
    this.cars.push({ g, laneIndex, y, speed });
  }

  /** Lanes already crossed keep no traffic, so the road behind reads as cleared. */
  updateCarVisibility() {
    this.cars.forEach(car => {
      car.g.style.display = car.laneIndex >= this.lane ? '' : 'none';
    });
  }

  // ---------------- placement helpers ----------------

  chickenScreenX(laneIndex = this.lane) {
    if (laneIndex === 0) return START_X;
    return laneIndex < ANCHOR_FROM_LANE ? laneCenter(laneIndex - 1) - this.roadOffset : ANCHOR_X;
  }

  setRoadOffset(value) {
    this.roadOffset = value;
    this.world.setAttribute('transform', `translate(${-value} 0)`);
  }

  placeChicken(x, y, scaleX = 1, scaleY = 1, rotation = 0) {
    this.runner.setAttribute('transform',
      `translate(${x} ${y}) rotate(${rotation}) scale(${scaleX} ${scaleY})`);
  }

  placeShadow(x, scale = 1, opacity = .28) {
    this.shadow.setAttribute('cx', x);
    this.shadow.setAttribute('cy', LANDING_Y + 51);
    this.shadow.setAttribute('rx', 47 * scale);
    this.shadow.setAttribute('ry', 15 * scale);
    this.shadow.setAttribute('opacity', opacity);
  }

  resetChicken() {
    this.setRoadOffset(0);
    this.placeChicken(START_X, LANDING_Y);
    this.placeShadow(START_X);
  }

  // ---------------- traffic loop ----------------

  startLoop() {
    if (this.loopId) return;
    this.lastFrame = performance.now();
    const step = now => {
      const dt = Math.min((now - this.lastFrame) / 1000, .04);
      this.lastFrame = now;
      const pace = 1 + this.lane * .13;
      this.cars.forEach(car => {
        car.y += car.speed * pace * dt;
        if (car.y > 1810) car.y = -180 - Math.random() * 480;
        // rotate 180 so the cars face down the screen, towards the player
        car.g.setAttribute('transform', `translate(${laneCenter(car.laneIndex)} ${car.y}) rotate(180)`);
      });
      this.loopId = requestAnimationFrame(step);
    };
    this.loopId = requestAnimationFrame(step);
  }

  /**
   * Make the traffic agree with the RNG verdict for the lane being entered.
   * `willHit` true  -> park the nearest car so it arrives exactly on the landing beat.
   * `willHit` false -> shove any car that would overlap the landing point clear of it.
   */
  stageTrafficFor(laneIndex, willHit, flightMs) {
    const laneCars = this.cars.filter(car => car.laneIndex === laneIndex);
    if (!laneCars.length) return;
    const pace = 1 + this.lane * .13;
    const seconds = flightMs / 1000;

    if (willHit) {
      const car = laneCars.reduce((a, b) => (a.y < b.y ? a : b));
      // travel backwards from the impact point so it lands on the chicken
      car.y = LANDING_Y - car.speed * pace * seconds;
      laneCars.filter(other => other !== car).forEach(other => {
        if (Math.abs(other.y - LANDING_Y) < CAR_H) other.y = LANDING_Y - 1500;
      });
      return;
    }

    laneCars.forEach(car => {
      const landingY = car.y + car.speed * pace * seconds;
      const clearance = CAR_H / 2 + 90;
      if (Math.abs(landingY - LANDING_Y) < clearance) car.y -= clearance * 2.4;
    });
  }

  carOverlaps(screenX, y) {
    return this.cars.some(car => {
      if (car.g.style.display === 'none') return false;
      const carX = laneCenter(car.laneIndex) - this.roadOffset;
      return Math.abs(carX - screenX) < CAR_W / 2 + 39 && Math.abs(car.y - y) < CAR_H / 2 + 44;
    });
  }

  // ---------------- betting ----------------

  setAmount(value) {
    if (this.active || this.busy) return;
    this.amount = Math.max(1, Math.min(100000, Number(value) || 1));
    this.render();
  }

  /**
   * Restore a round the server still holds.
   *
   * Reloading the page used to lose the round id while the stake stayed
   * debited, so the player could neither jump on nor cash out the round they
   * had paid for, and every new bet was refused.
   */
  async resumeRound() {
    if (!this.api || this.active || this.busy) return;
    let s;
    try {
      s = await this.api('/api/games/chicken/state', 'GET');
    } catch (error) {
      return;
    }
    if (!s || !s.active) return;

    this.roundId = s.round_id;
    this.amount = Number(s.stake) || this.amount;
    this.active = true;
    this.lane = Number(s.lane) || 0;
    this.multiplier = Number((s.multiplier || 1).toFixed(2));
    if (this.mode && s.mode) this.mode.value = s.mode;
    document.getElementById('chicken-bet-id').textContent = s.round_id;
    this.stage.className = 'chicken-stage active';
    this.message.textContent = this.lane
      ? `Round resumed at lane ${this.lane} — ₹${money(this.amount * this.multiplier)} available.`
      : 'Round resumed — jump ya stake cash out karo.';

    this.seedCars();
    this.buildTargets();
    this.resetChicken();
    // Put the chicken back where the player left it.
    for (let i = 0; i < this.lane; i++) this.updateCarVisibility();
    this.paintTargets();
    this.render();
  }

  async start() {
    if (!this.canPlay()) return this.denyPlay();
    if (this.busy) return;
    if (this.amount > this.getBalance()) return this.toast('Wallet balance kam hai.', 'error');

    this.busy = true;
    this.render();
    let res;
    try {
      res = await this.api('/api/games/chicken/bet', 'POST', {
        amount: this.amount,
        mode: this.mode.value
      });
    } catch (error) {
      this.busy = false;
      this.render();
      return this.toast(error.message || 'Bet nahi lag paya.', 'error');
    }

    this.roundId = res.round_id;
    this.setBalance(res.balance);   // real wallet, straight from the server
    this.active = true;
    this.busy = false;
    this.lane = 0;
    this.multiplier = 1;
    document.getElementById('chicken-bet-id').textContent = res.round_id;
    this.message.textContent = 'Road ready — jump ya stake cash out karo.';
    this.badge.textContent = '';
    this.stage.className = 'chicken-stage active';

    this.resetChicken();
    this.seedCars();
    this.buildTargets();
    this.render();
  }

  async jump() {
    if (!this.active || this.busy) return;
    this.busy = true;
    this.play.disabled = true;
    this.stage.classList.add('is-playing');
    this.stage.classList.remove('is-hit');
    this.message.textContent = 'Chicken jumping…';

    // The verdict comes from the server, before a single frame runs; the
    // animation is then steered to show whatever the server decided.
    let res;
    try {
      res = await this.api('/api/games/chicken/jump', 'POST', { round_id: this.roundId });
    } catch (error) {
      this.busy = false;
      this.play.disabled = false;
      this.stage.classList.remove('is-playing');
      return this.toast(error.message || 'Jump fail ho gaya.', 'error');
    }
    this._pending = res;

    const targetLane = this.lane;
    const flight = 760;
    const safe = res.result !== 'hit';
    this.stageTrafficFor(targetLane, !safe, flight);

    const fromX = this.chickenScreenX();
    const targetWorldX = laneCenter(targetLane);
    const scrolls = targetLane >= ANCHOR_FROM_LANE;
    const toX = scrolls ? ANCHOR_X : targetWorldX - this.roadOffset;
    const fromOffset = this.roadOffset;
    const toOffset = scrolls ? targetWorldX - ANCHOR_X : this.roadOffset;

    const startTime = performance.now();
    const animate = now => {
      const raw = Math.min((now - startTime) / flight, 1);
      const eased = raw < .5 ? 4 * raw ** 3 : 1 - Math.pow(-2 * raw + 2, 3) / 2;
      const hop = Math.sin(Math.PI * raw);
      const crouch = raw < .12 ? Math.sin((raw / .12) * Math.PI) : 0;
      const land = raw > .84 ? Math.sin(((raw - .84) / .16) * Math.PI) : 0;

      const x = fromX + (toX - fromX) * eased;
      const y = LANDING_Y - hop * 145 + crouch * 8 + land * 5;
      if (scrolls) this.setRoadOffset(fromOffset + (toOffset - fromOffset) * eased);

      this.placeChicken(x, y,
        1 - crouch * .13 + hop * .08 + land * .11,
        1 + crouch * .08 + hop * .13 - land * .15,
        -10 * hop + 4 * Math.sin(raw * Math.PI * 2));
      this.placeShadow(x, 1 - hop * .48, .28 - hop * .17);

      // Only cut the flight short once the chicken is low enough to be struck.
      if (!safe && hop < .48 && raw > .5 && this.carOverlaps(x, y)) {
        this.placeChicken(x, y, 1.14, .76, 24);
        this.placeShadow(x, 1.12, .34);
        return this.settleJump(false);
      }
      if (raw < 1) return requestAnimationFrame(animate);

      if (scrolls) this.setRoadOffset(toOffset);
      if (!safe) {
        this.placeChicken(toX, LANDING_Y, 1.14, .76, 24);
        this.placeShadow(toX, 1.12, .34);
        return this.settleJump(false);
      }
      this.placeChicken(toX, LANDING_Y);
      this.placeShadow(toX);
      this.settleJump(true);
    };
    requestAnimationFrame(animate);
  }

  settleJump(safe) {
    this.busy = false;
    this.stage.classList.remove('is-playing');
    const res = this._pending || {};
    this._pending = null;

    if (!safe) {
      if (res.balance != null) this.setBalance(res.balance);
      return this.finish(false);
    }

    // Trust the server's lane and multiplier, not a local recompute.
    this.lane = res.lane != null ? res.lane : this.lane + 1;
    this.multiplier = Number((res.multiplier != null ? res.multiplier : this.laneMultiplier(this.lane - 1)).toFixed(2));
    this.updateCarVisibility();
    this.paintTargets();
    this.badge.textContent = 'SAFE';
    setTimeout(() => { if (this.active) this.badge.textContent = ''; }, 700);

    if (res.result === 'cleared' || this.lane >= LANES) {
      if (res.balance != null) this.setBalance(res.balance);
      this.message.textContent = 'Road cleared — full payout!';
      return this.finishWithPayout(res.payout != null ? res.payout : this.amount * this.multiplier);
    }
    this.message.textContent = `Safe lane ${this.lane}! ₹${money(this.amount * this.multiplier)} available.`;
    this.render();
  }

  /** Manual cash-out button: ask the server to pay out the current round. */
  async finish(cashedOut) {
    if (!this.active) return;

    // The hit path (cashedOut === false) is settled by the jump response; only
    // a manual cash-out needs its own server call.
    if (cashedOut) {
      if (this.busy) return;
      this.busy = true;
      this.render();
      let res;
      try {
        res = await this.api('/api/games/chicken/cashout', 'POST', { round_id: this.roundId });
      } catch (error) {
        this.busy = false;
        this.render();
        return this.toast(error.message || 'Cash out fail ho gaya.', 'error');
      }
      this.setBalance(res.balance);
      this.multiplier = Number((res.multiplier || this.multiplier).toFixed(2));
      return this.finishWithPayout(res.payout);
    }

    this.active = false;
    this.busy = false;
    this.roundId = null;
    this.stage.classList.remove('is-playing', 'active');
    this.paintTargets();
    this.message.textContent = `Chicken hit on lane ${this.lane + 1}. Bet lost.`;
    this.badge.textContent = 'HIT!';
    this.stage.classList.add('is-hit');
    this.toast('Chicken hit — active bet lost.', 'error');
    setTimeout(() => {
      this.stage.classList.remove('is-hit');
      this.lane = 0;
      this.updateCarVisibility();
      this.resetChicken();
      this.paintTargets();
    }, 1100);
    this.render();
  }

  finishWithPayout(payout) {
    this.active = false;
    this.busy = false;
    this.roundId = null;
    this.stage.classList.remove('is-playing', 'active');
    this.paintTargets();
    this.message.textContent = `Cashed out ₹${money(payout)} at ${this.multiplier.toFixed(2)}x.`;
    this.badge.textContent = 'CASHED OUT';
    this.toast(`Chicken Road payout ₹${money(payout)}`, 'success');
    this.render();
  }

  render() {
    this.amountEl.textContent = money(this.amount);
    this.multiplierEl.textContent = `${this.multiplier.toFixed(2)}x`;
    this.play.textContent = this.active ? 'JUMP' : 'Play';
    this.play.disabled = this.busy;
    this.cashout.hidden = !this.active;
    this.cashout.querySelector('span').textContent = `${money(this.amount * this.multiplier)} INR`;
    this.mode.disabled = this.active;
    const value = money(this.getBalance());
    document.getElementById('chicken-wallet').textContent = value;
    document.getElementById('chicken-brand-balance').textContent = value;
  }
}
