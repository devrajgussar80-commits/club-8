/**
 * Main Application Coordinator & REST API Client for Python Backend
 */

import { appState } from './state.js?v=rooms-1';
import { sound } from './sound.js';
import { getRoomClock } from './game-clock.js?v=1';
import { AviatorEngine } from './aviator-engine.js?v=server-rounds-1';
import { ChickenRoadEngine } from './chicken-road-engine.js?v=road-1';
import { SlotEngine } from './slot-engine.js?v=1';
import { RouletteEngine } from './roulette-engine.js?v=1';
import { DiceEngine } from './dice-engine.js?v=1';
import { LotteryEngine } from './lottery.js?v=1';
import { VisitorTracker } from './tracker.js?v=1';
import { initInteractions } from './interactions.js?v=1';
import { AppShare } from './app-share.js?v=1';

class App {
  constructor() {
    this.currentBetSelection = null;
    this.selectedContractMultiplier = 1;
    this.selectedContractBase = 1;
    this.selectedQuantity = 1;
    this.authToken = localStorage.getItem('PREDICT_AUTH_TOKEN') || null;
    this.referralCode = localStorage.getItem('PREDICT_REFERRAL_CODE') || '';
    this.generatedOtp = '';
    // Cached from the last /api/auth/me so a page refresh restores the unlocked
    // state immediately instead of flashing the "locked" modal before the sync.
    this.gameAccessEnabled = localStorage.getItem('PREDICT_GAME_ACCESS') === '1';
    this.approvedDepositTotal = Number(localStorage.getItem('PREDICT_APPROVED_TOTAL') || 0);
    // Mirrors config.GAME_ACCESS_MIN_DEPOSIT. Refreshed from /api/game/status,
    // which is the server's own value, so changing it there is enough.
    this.gameAccessMinDeposit = Number(localStorage.getItem('PREDICT_ACCESS_MIN') || 300);
    this.lastAccessSync = 0;
    this.apiBaseUrl = String(window.APP_CONFIG?.API_BASE_URL || '').replace(/\/+$/, '');
    this.adminApiKey = localStorage.getItem('PREDICT_ADMIN_API_KEY')
      || sessionStorage.getItem('PREDICT_ADMIN_API_KEY') || '';
    this.adminToken = localStorage.getItem('PREDICT_ADMIN_TOKEN')
      || sessionStorage.getItem('PREDICT_ADMIN_TOKEN') || '';
    this.adminData = null;
    // Started as early as possible: the visits worth measuring are the ones
    // that leave before anything else on this page has finished loading.
    this.tracker = new VisitorTracker(this.apiBaseUrl);
    this.adminQueueFilter = 'all';
    this.adminLoadInFlight = false;
    this.adminDashboardUnavailable = false;
    this.adminFailStreak = 0;
    this.adminMutationsInFlight = 0;
    this.pollInterval = null;
    this.localGameClockInterval = null;
    this.historyPage = 0;
    this.historyPageSize = 10;
    this.pageStack = [];
    this.currentPage = 'home';
    this.pageScrollPositions = {};
    this.activeQR = null;
    this.pendingDeposit = null;
    this.walletSettings = { deposits_enabled: true, withdrawals_enabled: true, withdrawal_min: 200 };
    this.lastAdminTablesSync = 0;
    this.lastQrSync = 0;
    this.backendSyncInFlight = false;
    this.homeBannerIndex = 0;
    this.homeBannerTimer = null;
    this.recommendedPage = 0;
    this.recommendedTimer = null;
    this.homeRotatorTimers = [];
    this.winnerFeedTimer = null;
    this.withdrawMethod = 'bank';
    this.selectedBank = '';
    this.bankAccount = null;
    this.upiAccount = null;
    this.minesRound = null;
    this.minesMultiplier = 1;
    this.bankNames = [
      'Standard Chartered Bank', 'IDBI Bank', 'Bank of India', 'Punjab National Bank',
      'ICICI Bank', 'Canara Bank', 'Kotak Mahindra Bank', 'State Bank of India',
      'Axis Bank', 'FEDERAL BANK', 'Syndicate Bank', 'Citibank India', 'Bandhan Bank',
      'Indusind Bank', 'India Post Payments Bank', 'Corporation Bank',
      'State Bank Of Hyderabad', 'Gp parsik bank', 'Kerala Gramin Bank', 'RBL Bank',
      'Dhanlaxmi Bank', 'TJSB Bank', 'Purvanchal bank', 'Sarva Haryana Gramin Bank',
      'Saraswat Cooperative Bank', 'Telangana Grameena Bank',
      'andhra pragathi grameena bank', 'rajasthan marudhara gramin bank',
      'Abhyudaya bank', 'ujjivan small finance bank', 'capital small finance bank',
      'Mizoram Rural Bank', 'HDFC Bank', 'Bank of Baroda', 'Union Bank of India',
      'Indian Bank', 'Central Bank of India', 'Bank of Maharashtra', 'Yes Bank'
    ];
  }

  // A leftover local "arcade balance ledger" used to be layered on top of the
  // real balance; it is gone now that all games settle on the server. Wipe any
  // stale copy so a returning player is not shown inflated money one last time.
  clearLegacyArcadeLedger() {
    try { localStorage.removeItem('CLUB8_ARCADE_BALANCE_LEDGER'); } catch {}
  }

  async readUpiIdFromQrFile(file) {
    if (!file || !('BarcodeDetector' in window) || !window.createImageBitmap) return '';
    try {
      const detector = new BarcodeDetector({ formats: ['qr_code'] });
      const bitmap = await createImageBitmap(file);
      const results = await detector.detect(bitmap);
      bitmap.close?.();
      const rawValue = results?.[0]?.rawValue || '';
      const payeeMatch = rawValue.match(/[?&]pa=([^&]+)/i);
      return payeeMatch ? decodeURIComponent(payeeMatch[1].replace(/\+/g, ' ')).trim() : '';
    } catch {
      return '';
    }
  }

  initBankBinding() {
    try {
      this.bankAccount = JSON.parse(localStorage.getItem('PREDICT_BANK_ACCOUNT') || 'null');
    } catch {
      this.bankAccount = null;
    }
    this.selectedBank = this.bankAccount?.bank || '';

    const bankList = document.getElementById('bank-list');
    const searchInput = document.getElementById('bank-search-input');
    const renderBankList = (query = '') => {
      if (!bankList) return;
      const normalized = query.trim().toLowerCase();
      const names = this.bankNames.filter(name => name.toLowerCase().includes(normalized));
      bankList.innerHTML = names.length
        ? names.map(name => `<button type="button" role="option" data-bank-name="${name.replace(/"/g, '&quot;')}">${name}</button>`).join('')
        : '<div class="bank-list-empty">No bank found</div>';
    };
    renderBankList();
    searchInput?.addEventListener('input', () => renderBankList(searchInput.value));
    bankList?.addEventListener('click', (event) => {
      const item = event.target.closest('[data-bank-name]');
      if (!item) return;
      this.selectedBank = item.dataset.bankName || '';
      const selectedName = document.getElementById('selected-bank-name');
      if (selectedName) selectedName.textContent = this.selectedBank;
      this.updateBankSaveState();
      this.goBack();
    });

    document.getElementById('withdraw-bank-bind-card')?.addEventListener('click', () => {
      this.populateBankForm();
      this.switchSubPage('bank-account-add');
    });
    document.getElementById('choose-bank-button')?.addEventListener('click', () => {
      if (searchInput) searchInput.value = '';
      renderBankList();
      this.switchSubPage('bank-choose');
    });

    const form = document.getElementById('bank-account-form');
    form?.querySelectorAll('input').forEach(input => {
      input.addEventListener('input', () => {
        if (input.id === 'bank-account-number' || input.id === 'bank-phone-number') {
          input.value = input.value.replace(/\D/g, '');
        }
        if (input.id === 'bank-ifsc-code') input.value = input.value.toUpperCase().replace(/[^A-Z0-9]/g, '');
        this.updateBankSaveState();
      });
    });
    form?.addEventListener('submit', (event) => {
      event.preventDefault();
      if (!this.isBankFormValid()) return;
      this.bankAccount = {
        bank: this.selectedBank,
        recipient: document.getElementById('bank-recipient-name')?.value.trim() || '',
        account: document.getElementById('bank-account-number')?.value.trim() || '',
        phone: document.getElementById('bank-phone-number')?.value.trim() || '',
        ifsc: document.getElementById('bank-ifsc-code')?.value.trim().toUpperCase() || ''
      };
      localStorage.setItem('PREDICT_BANK_ACCOUNT', JSON.stringify(this.bankAccount));
      this.renderBankAccount();
      this.showToast('Bank account saved successfully!', 'success');
      this.goBack();
    });

    document.querySelectorAll('[data-withdraw-method]').forEach(button => {
      button.addEventListener('click', () => {
        const method = button.dataset.withdrawMethod;
        if (method === 'usdt') {
          this.showToast('USDT payout will be available soon.', 'error');
          this.setWithdrawMethod(this.withdrawMethod);
          return;
        }
        this.setWithdrawMethod(method);
      });
    });

    this.renderBankAccount();
    this.populateBankForm();
    this.setWithdrawMethod('upi');
  }

  initUpiBinding() {
    try {
      this.upiAccount = JSON.parse(localStorage.getItem('PREDICT_UPI_ACCOUNT') || 'null');
    } catch {
      this.upiAccount = null;
    }

    document.getElementById('withdraw-upi-bind-card')?.addEventListener('click', () => {
      this.renderUpiAccount();
      this.pageScrollPositions['upi-methods'] = 0;
      this.switchSubPage('upi-methods');
    });
    document.getElementById('add-upi-method-button')?.addEventListener('click', () => {
      this.populateUpiForm();
      this.pageScrollPositions['upi-account-add'] = 0;
      this.switchSubPage('upi-account-add');
    });
    document.getElementById('upi-saved-method-card')?.addEventListener('click', () => {
      this.setWithdrawMethod('upi');
      this.goBack();
    });

    const form = document.getElementById('upi-account-form');
    form?.querySelectorAll('input').forEach(input => {
      input.addEventListener('input', () => {
        if (input.id === 'upi-phone-number') input.value = input.value.replace(/\D/g, '');
        if (input.id === 'upi-account-id' || input.id === 'upi-confirm-id') {
          input.value = input.value.replace(/\s/g, '').toLowerCase();
        }
        this.updateUpiSaveState();
      });
    });
    form?.addEventListener('submit', (event) => {
      event.preventDefault();
      if (!this.isUpiFormValid()) return;
      this.upiAccount = {
        name: document.getElementById('upi-account-name')?.value.trim() || '',
        phone: document.getElementById('upi-phone-number')?.value.trim() || '',
        upiId: document.getElementById('upi-account-id')?.value.trim().toLowerCase() || ''
      };
      localStorage.setItem('PREDICT_UPI_ACCOUNT', JSON.stringify(this.upiAccount));
      this.renderUpiAccount();
      this.showToast('UPI payment method saved successfully!', 'success');
      this.goBack();
    });

    this.populateUpiForm();
    this.renderUpiAccount();
  }

  populateUpiForm() {
    const account = this.upiAccount || {};
    const values = {
      'upi-account-name': account.name || '',
      'upi-phone-number': account.phone || '',
      'upi-account-id': account.upiId || '',
      'upi-confirm-id': account.upiId || ''
    };
    Object.entries(values).forEach(([id, value]) => {
      const input = document.getElementById(id);
      if (input) input.value = value;
    });
    this.updateUpiSaveState();
  }

  isUpiFormValid() {
    const name = document.getElementById('upi-account-name')?.value.trim() || '';
    const phone = document.getElementById('upi-phone-number')?.value.trim() || '';
    const upiId = document.getElementById('upi-account-id')?.value.trim().toLowerCase() || '';
    const confirmUpiId = document.getElementById('upi-confirm-id')?.value.trim().toLowerCase() || '';
    return name.length >= 3
      && /^\d{10}$/.test(phone)
      && /^[a-z0-9._-]{2,}@[a-z0-9.-]{2,}$/.test(upiId)
      && upiId === confirmUpiId;
  }

  updateUpiSaveState() {
    const save = document.getElementById('save-upi-account');
    if (save) save.disabled = !this.isUpiFormValid();
  }

  renderUpiAccount() {
    const hasUpi = Boolean(this.upiAccount?.upiId);
    const empty = document.getElementById('upi-method-empty');
    const methodCard = document.getElementById('upi-saved-method-card');
    const saved = document.querySelector('.withdraw-upi-saved');
    const plus = document.querySelector('.withdraw-upi-plus');
    const emptyLabel = document.querySelector('.withdraw-upi-empty-label');
    if (empty) empty.hidden = hasUpi;
    if (methodCard) methodCard.hidden = !hasUpi;
    if (saved) saved.hidden = !hasUpi;
    if (plus) plus.hidden = hasUpi;
    if (emptyLabel) emptyLabel.hidden = hasUpi;
    if (!hasUpi) return;
    const values = {
      'upi-method-name': this.upiAccount.name,
      'upi-method-id': this.upiAccount.upiId,
      'withdraw-saved-upi-name': this.upiAccount.name,
      'withdraw-saved-upi-id': this.upiAccount.upiId
    };
    Object.entries(values).forEach(([id, value]) => {
      const element = document.getElementById(id);
      if (element) element.textContent = value;
    });
  }

  populateBankForm() {
    const account = this.bankAccount || {};
    this.selectedBank = account.bank || this.selectedBank || '';
    const values = {
      'selected-bank-name': this.selectedBank || 'Please select a bank',
      'bank-recipient-name': account.recipient || '',
      'bank-account-number': account.account || '',
      'bank-phone-number': account.phone || '',
      'bank-ifsc-code': account.ifsc || ''
    };
    Object.entries(values).forEach(([id, value]) => {
      const element = document.getElementById(id);
      if (!element) return;
      if ('value' in element) element.value = value;
      else element.textContent = value;
    });
    this.updateBankSaveState();
  }

  isBankFormValid() {
    const recipient = document.getElementById('bank-recipient-name')?.value.trim() || '';
    const account = document.getElementById('bank-account-number')?.value.trim() || '';
    const phone = document.getElementById('bank-phone-number')?.value.trim() || '';
    const ifsc = document.getElementById('bank-ifsc-code')?.value.trim().toUpperCase() || '';
    return Boolean(this.selectedBank)
      && recipient.length >= 3
      && /^\d{8,20}$/.test(account)
      && /^\d{10}$/.test(phone)
      && /^[A-Z]{4}0[A-Z0-9]{6}$/.test(ifsc);
  }

  updateBankSaveState() {
    const save = document.getElementById('save-bank-account');
    if (save) save.disabled = !this.isBankFormValid();
  }

  renderBankAccount() {
    const saved = document.querySelector('.withdraw-bank-saved');
    const plus = document.querySelector('.withdraw-bank-plus');
    const empty = document.querySelector('.withdraw-bank-empty-label');
    const warning = document.getElementById('withdraw-bank-warning');
    const hasAccount = Boolean(this.bankAccount?.account);
    if (saved) saved.hidden = !hasAccount;
    if (plus) plus.hidden = hasAccount;
    if (empty) empty.hidden = hasAccount;
    if (warning) warning.hidden = hasAccount;
    if (!hasAccount) return;
    const bankName = document.getElementById('withdraw-saved-bank-name');
    const bankNumber = document.getElementById('withdraw-saved-bank-number');
    if (bankName) bankName.textContent = this.bankAccount.bank;
    if (bankNumber) bankNumber.textContent = `•••• ${this.bankAccount.account.slice(-4)}`;
  }

  setWithdrawMethod(method) {
    this.withdrawMethod = method === 'upi' ? 'upi' : 'bank';
    const withdrawPage = document.getElementById('page-withdraw');
    withdrawPage?.classList.toggle('bank-mode', this.withdrawMethod === 'bank');
    document.querySelectorAll('[data-withdraw-method]').forEach(button => {
      button.classList.toggle('active', button.dataset.withdrawMethod === this.withdrawMethod);
    });
    const submit = document.getElementById('withdraw-submit-button');
    if (submit) submit.textContent = this.withdrawMethod === 'upi' ? 'Request UPI Payout' : 'Withdraw';
  }

  startHomeBannerCarousel() {
    const slides = [...document.querySelectorAll('.club-hero-slide')];
    const dots = [...document.querySelectorAll('.club-hero-dots i')];
    const notice = document.querySelector('.club-marquee span');
    if (slides.length < 2) return;
    const notices = [
      'Welcome to Club 8. Play responsibly and keep your account details secure.',
      'Fast deposits and withdrawals are available through the official wallet.',
      'Complete your deposit order and enter the correct 12-digit UTR for verification.',
      'Check the latest rewards, promotions and member benefits in your account.',
      'For account safety, never share your password or payment OTP with anyone.'
    ];
    const showSlide = (index) => {
      this.homeBannerIndex = index % slides.length;
      slides.forEach((slide, slideIndex) => slide.classList.toggle('active', slideIndex === this.homeBannerIndex));
      dots.forEach((dot, dotIndex) => dot.classList.toggle('active', dotIndex === this.homeBannerIndex));
      if (notice) notice.textContent = notices[this.homeBannerIndex % notices.length];
    };
    showSlide(this.homeBannerIndex);
    if (this.homeBannerTimer) clearInterval(this.homeBannerTimer);
    this.homeBannerTimer = setInterval(() => showSlide(this.homeBannerIndex + 1), 3500);
  }

  startRecommendedCarousel() {
    const carousel = document.getElementById('recommended-carousel');
    const track = carousel?.querySelector('.recommended-track');
    const slides = [...(carousel?.querySelectorAll('.recommended-slide') || [])];
    const dots = [...(carousel?.querySelectorAll('.recommended-dots i') || [])];
    const pageCount = document.getElementById('recommended-page-count');
    if (!track || slides.length < 2) return;
    const showPage = (index) => {
      this.recommendedPage = (index + slides.length) % slides.length;
      track.style.transform = `translateX(-${this.recommendedPage * (100 / slides.length)}%)`;
      dots.forEach((dot, dotIndex) => dot.classList.toggle('active', dotIndex === this.recommendedPage));
      if (pageCount) pageCount.textContent = `${this.recommendedPage + 1}/${slides.length}`;
    };
    const restartTimer = () => {
      if (this.recommendedTimer) clearInterval(this.recommendedTimer);
      this.recommendedTimer = setInterval(() => showPage(this.recommendedPage + 1), 4200);
    };
    document.getElementById('recommended-prev')?.addEventListener('click', () => {
      showPage(this.recommendedPage - 1);
      restartTimer();
    });
    document.getElementById('recommended-next')?.addEventListener('click', () => {
      showPage(this.recommendedPage + 1);
      restartTimer();
    });
    showPage(this.recommendedPage);
    restartTimer();
  }

  startHomeGameRotators() {
    this.homeRotatorTimers.forEach(timer => clearInterval(timer));
    this.homeRotatorTimers = [];

    const regularConfigs = {
      mini: {
        screens: [286, 287, 288, 289, 290, 291],
        y: 215,
        labels: [
          ['Chicken Road 2', 'Vortex', 'Ronaldinho da Sorte'],
          ['PUBG Mini', 'Mines', 'Mines Pro'],
          ['Cricket', 'Limbo', 'Javelin'],
          ['Dragon Tiger', 'Goal', 'Snakes'],
          ['Dice', 'King and Pauper', 'Hilo Wave'],
          ['Clash of Hands', 'Plinko', 'Bomb Wave']
        ]
      },
      slots: {
        screens: [286, 288, 289, 290, 287, 291],
        y: 462,
        labels: [
          ['Egypt Book', 'Fortune Teller', 'Fortune Panda'],
          ['Jili Gold', 'Royal Zeus', 'Money Bull'],
          ['Evolution', 'Chicken Road', 'Gaming'],
          ['Egypt Book', 'Fortune Teller', 'Fortune Panda'],
          ['CQ9 Game'],
          ['CQ9 Game']
        ]
      },
      fishing: {
        screens: [292, 293, 294, 295],
        y: 237,
        labels: [
          ['Dragon Fishing', 'Dragon Fishing 2', 'Cai Shen Fishing'],
          ['Shade Dragons Fishing', 'Fishing Yilufa', 'Dragon Master'],
          ['Fishing Disco', 'Royal Fishing', 'All-Star Fishing'],
          ['Bombing Fishing', 'Dinosaur Tycoon II', 'Dragon of Demons']
        ]
      },
      casino: {
        screens: [292, 293],
        y: 482,
        labels: [
          ['EVO Casino', 'Live Choice', 'WM Casino'],
          ['MG Live Grand']
        ]
      }
    };
    const cropXs = [616, 741, 865];
    const makeCrop = (screen, x, y, width = 118, height = 160) => `
      <svg viewBox="${x} ${y} ${width} ${height}" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <image href="assets/home-rotators/screen-${screen}.png" width="1366" height="768"></image>
      </svg>`;

    const setupRegularRotator = (name, config, delay) => {
      const container = document.getElementById(`home-rotator-${name}`);
      if (!container) return;
      let index = 0;
      const show = nextIndex => {
        index = (nextIndex + config.screens.length) % config.screens.length;
        const labels = config.labels[index];
        container.innerHTML = `<div class="home-game-slide ${labels.length === 1 ? 'single-card-slide' : ''}">
          ${labels.map((label, cardIndex) => `<article class="home-art-card" aria-label="${label}">
            ${makeCrop(config.screens[index], cropXs[cardIndex], config.y)}
          </article>`).join('')}
        </div>`;
      };
      const restart = () => {
        const previous = container.dataset.timerId;
        if (previous) clearInterval(Number(previous));
        const timer = setInterval(() => show(index + 1), delay);
        container.dataset.timerId = String(timer);
        this.homeRotatorTimers.push(timer);
      };
      document.querySelector(`[data-rotator-prev="${name}"]`)?.addEventListener('click', () => {
        show(index - 1);
        restart();
      });
      document.querySelector(`[data-rotator-next="${name}"]`)?.addEventListener('click', () => {
        show(index + 1);
        restart();
      });
      show(0);
      restart();
    };

    setupRegularRotator('mini', regularConfigs.mini, 3600);
    setupRegularRotator('slots', regularConfigs.slots, 4100);
    setupRegularRotator('fishing', regularConfigs.fishing, 4600);
    setupRegularRotator('casino', regularConfigs.casino, 5200);

    const jackpotContainer = document.getElementById('home-rotator-jackpot');
    const jackpotSlides = [
      { screen:296, labels:['Ways of the Qilin','Pirate Queen','Money Coming Expand Bets'], mult:['91.3X','13.9X','10.22X'], prices:['₹300.00','₹35.00','₹35.00'] },
      { screen:297, labels:['Fortune Gems','Jackpot Fishing','Fortune Coins'], mult:['14.4X','10X','20X'], prices:['₹35.00','₹35.00','₹70.00'] },
      { screen:298, labels:['Alibaba','Fortune Garuda 500','Circus Jackpot'], mult:['12.4X','18X','11.67X'], prices:['₹35.00','₹35.00','₹35.00'] },
      { screen:299, labels:['Fortune Garuda 500'], mult:['10X'], prices:['₹35.00'] },
      { screen:300, labels:['Ways of the Qilin','Pirate Queen','Money Coming Expand Bets'], mult:['91.3X','13.9X','10.22X'], prices:['₹300.00','₹35.00','₹35.00'] }
    ];
    let jackpotIndex = 0;
    const showJackpot = nextIndex => {
      if (!jackpotContainer) return;
      jackpotIndex = (nextIndex + jackpotSlides.length) % jackpotSlides.length;
      const slide = jackpotSlides[jackpotIndex];
      jackpotContainer.innerHTML = `<div class="home-jackpot-slide ${slide.labels.length === 1 ? 'single-card-slide' : ''}">
        ${slide.labels.map((label, cardIndex) => `<article class="home-jackpot-card">
          <div class="jackpot-art">${makeCrop(slide.screen, cropXs[cardIndex], 197)}</div>
          <em>${slide.mult[cardIndex]}</em>
          <span>${label}</span>
          <small>${slide.prices[cardIndex]}</small>
        </article>`).join('')}
      </div>`;
    };
    showJackpot(0);
    const jackpotTimer = setInterval(() => showJackpot(jackpotIndex + 1), 4300);
    this.homeRotatorTimers.push(jackpotTimer);

    const winnerSets = [
      [
        ['EVO Video','Mem***OZY','₹90.00'], ['EVO Video','Mem***BNV','₹60.00'],
        ['EVO Video','Mem***BKU','₹60.00'], ['EVO Video','Mem***AVX','₹1,600.00'],
        ['EVO Video','Mem***OLE','₹200.00'], ['EVO Video','Mem***USN','₹100.00']
      ],
      [
        ['AG Video','Mem***KWR','₹390.00'], ['5D 1 min','Mem***GIY','₹26.46'],
        ['5D 1 min','Mem***DLX','₹39.20'], ['EVO Video','Mem***AVX','₹100.00'],
        ['AG Video','Mem***BNV','₹390.00'], ['5D 1 min','Mem***USN','₹26.46']
      ]
    ];
    const winnerRows = document.getElementById('home-winning-rows');
    let winnerIndex = 0;
    const showWinners = () => {
      if (!winnerRows) return;
      const rows = winnerSets[winnerIndex % winnerSets.length];
      winnerRows.innerHTML = rows.map(row => `<div><span>🎰 &nbsp;${row[0]}</span><span>${row[1]}</span><b>${row[2]}</b></div>`).join('');
      winnerIndex += 1;
    };
    showWinners();
    if (this.winnerFeedTimer) clearInterval(this.winnerFeedTimer);
    this.winnerFeedTimer = setInterval(showWinners, 3800);

    const ranking = document.getElementById('earnings-ranking-list');
    if (ranking) {
      const rows = [
        ['4','Mem***WOJ','₹1,000,508.00'], ['5','Mem***PZV','₹990,976.00'],
        ['6','Mem***A1O','₹930,988.00'], ['7','Mem***KJ0','₹824,087.25'],
        ['8','Mem***TDA','₹779,540.00'], ['9','Mem***YPH','₹773,590.00'],
        ['10','Mem***R36','₹608,300.00']
      ];
      ranking.innerHTML = rows.map((row, index) => `<div><b>${row[0]}</b><span class="earning-avatar ${index === 1 ? 'avatar-boy' : 'avatar-girl'}"></span><span>${row[1]}</span><strong>${row[2]}</strong></div>`).join('');
    }
  }

  init() {
    // Before routing, so a visit still registers even if routing throws. The
    // landing path is the URL they actually opened -- using the router's
    // default screen id here recorded everyone as landing on "home" even when
    // what they saw was the login page. The first page_view below, emitted by
    // the router, is what names the real first screen.
    //
    // Not on /admin: the dashboard runs this same bundle, so every time an
    // admin opened or refreshed it, it counted itself as another unknown
    // visitor and inflated the very numbers being read on that screen.
    if (!location.pathname.startsWith('/admin')) {
      this.tracker.start(location.pathname);
      this.tracker.watchAuthScreen();
    }

    // One delegated listener, so buttons rendered later still get feedback.
    initInteractions();
    this.appShare = new AppShare({
      apiBaseUrl: this.apiBaseUrl,
      toast: (msg, type) => this.showToast(msg, type)
    });
    this.appShare.init();

    this.handleRouting();
    window.openClubPage = (page) => {
      this.closeClubBonus();
      if (this.isPremiumGamePage(page) && !this.canEnterPremiumGames()) {
        this.showGameAccessModal();
        return;
      }
      this.switchSubPage(page);
    };
    window.clubBack = () => this.goBack();
    window.closeClubBonus = () => this.closeClubBonus();
    this.initBankBinding();
    this.initUpiBinding();
    this.attachEventListeners();
    this.attachAdminListeners();
    this.startHomeBannerCarousel();
    this.startRecommendedCarousel();
    this.startHomeGameRotators();
    this.initMinesGame();
    this.initArcadeGames();
    appState.subscribe((state) => this.render(state));
    this.startBackendSync();
    this.render(appState.getState());
    // Sets the download button's href and shows/hides it based on whether an
    // APK has been uploaded. Public endpoint, so it runs whether or not the
    // user is signed in.
    void this.loadAppInfo();
    if (this.authToken) void this.syncUserAccess();
    if (appState.getState().viewMode === 'admin') {
      document.body.dataset.view = 'admin';
      if (this.hasAdminCredentials()) void this.loadAdmin();
      else this.showAdminGate();
    }
  }

  initMinesGame() {
    const grid = document.getElementById('mines-grid');
    const betButton = document.getElementById('mines-bet-button');
    const betInput = document.getElementById('mines-bet-amount');
    const mineCount = document.getElementById('mines-count');
    if (!grid || !betButton || !betInput || !mineCount) return;

    // Server-authoritative now: the backend debits the stake, hides the mines
    // and reveals each tile. This code only draws what the server returns and
    // stores the real balance -- the old build never touched the wallet, so it
    // was free money.
    const walletEl = document.getElementById('mines-balance');
    const applyBalance = value => {
      const state = appState.getState();
      state.user.balance = Number(Number(value).toFixed(2));
      appState.saveState();
      this.refreshArcadeWallets();
      if (walletEl) walletEl.textContent = Number(state.user.balance).toFixed(2);
    };

    const renderGrid = () => {
      grid.innerHTML = Array.from({ length: 25 }, (_, index) =>
        `<button type="button" class="mine-tile" data-mine-tile="${index}" aria-label="Tile ${index + 1}"><span></span></button>`
      ).join('');
    };
    const setMessage = (message) => {
      const output = document.getElementById('mines-message');
      if (output) output.textContent = message;
    };
    const setGameId = (id) => {
      const output = document.getElementById('mines-game-id');
      if (output) output.textContent = id || String(Date.now()).slice(-8);
    };
    const updateNext = () => {
      const bet = Math.max(1, Number(betInput.value) || 1);
      const next = document.getElementById('mines-next-win');
      if (next) next.textContent = `${(bet * this.minesMultiplier).toFixed(2)} INR`;
    };
    const revealLayout = (mines) => {
      if (!Array.isArray(mines)) return;
      grid.querySelectorAll('.mine-tile').forEach((tile, index) => {
        if (mines.includes(index)) tile.classList.add('mine-reveal');
      });
    };
    const endRound = (message) => {
      betButton.classList.remove('cashout');
      betButton.querySelector('span').textContent = 'BET';
      setMessage(message);
      this.minesRound = null;
      this.minesMultiplier = 1;
      updateNext();
    };

    const startRound = async () => {
      if (!this.canEnterPremiumGames()) return this.showGameAccessModal();
      const amount = Math.max(1, Number(betInput.value) || 1);
      if (amount > Number(appState.getState().user.balance || 0)) {
        return this.showToast('Wallet balance kam hai.', 'error');
      }
      betButton.disabled = true;
      try {
        const res = await this.fetchApi('/api/games/mines/bet', 'POST', {
          amount, mines: Math.max(1, Math.min(10, Number(mineCount.value) || 3))
        });
        this.minesRound = { id: res.round_id };
        this.minesMultiplier = 1;
        applyBalance(res.balance);
        renderGrid();
        setGameId(res.round_id);
        betButton.classList.add('cashout');
        betButton.querySelector('span').textContent = 'CASH OUT';
        setMessage('Round active — reveal a safe tile or cash out.');
        updateNext();
      } catch (error) {
        this.showToast(error.message || 'Bet nahi lag paya.', 'error');
      } finally {
        betButton.disabled = false;
      }
    };

    const cashOut = async () => {
      if (!this.minesRound) return;
      betButton.disabled = true;
      try {
        const res = await this.fetchApi('/api/games/mines/cashout', 'POST', { round_id: this.minesRound.id });
        applyBalance(res.balance);
        revealLayout(res.mines);
        endRound(`Cashed out ₹${Number(res.payout).toFixed(2)} at ${Number(res.multiplier).toFixed(2)}×.`);
        this.showToast(`Mines payout ₹${Number(res.payout).toFixed(2)}`, 'success');
      } catch (error) {
        this.showToast(error.message || 'Cash out fail ho gaya.', 'error');
      } finally {
        betButton.disabled = false;
      }
    };

    renderGrid();
    setGameId();
    updateNext();

    // Restore a round the server still holds. Reloading the page used to lose
    // the round id here while the stake stayed debited, leaving the player
    // unable to continue OR start a new round.
    this.resumeMinesRound = async () => {
      if (!this.authToken || this.minesRound) return;
      try {
        const s = await this.fetchApi('/api/games/mines/state');
        if (!s.active) return;
        this.minesRound = { id: s.round_id };
        this.minesMultiplier = Number(s.multiplier) || 1;
        renderGrid();
        setGameId(s.round_id);
        grid.querySelectorAll('.mine-tile').forEach((tile, index) => {
          if (s.opened.includes(index)) tile.classList.add('revealed', 'safe-hit');
        });
        betInput.value = Number(s.stake).toFixed(2);
        betButton.classList.add('cashout');
        betButton.querySelector('span').textContent = 'CASH OUT';
        setMessage(`Round resumed — ${s.opened.length} tile${s.opened.length === 1 ? '' : 's'} open · ${this.minesMultiplier.toFixed(2)}×`);
        updateNext();
      } catch (error) { /* no round to restore */ }
    };
    void this.resumeMinesRound();

    grid.addEventListener('click', async event => {
      const tile = event.target.closest('[data-mine-tile]');
      if (!tile || !this.minesRound || tile.classList.contains('revealed') || tile.dataset.busy) return;
      const index = Number(tile.dataset.mineTile);
      tile.dataset.busy = '1';
      tile.classList.add('revealing');
      try {
        const res = await this.fetchApi('/api/games/mines/reveal', 'POST', {
          round_id: this.minesRound.id, tile: index
        });
        tile.classList.remove('revealing');
        tile.classList.add('revealed');
        if (res.result === 'boom') {
          tile.classList.add('mine-hit');
          applyBalance(res.balance);
          revealLayout(res.mines);
          endRound('Boom! Mine found. Start a new round.');
          this.showToast('Mine hit — bet lost.', 'error');
          return;
        }
        tile.classList.add('safe-hit');
        this.minesMultiplier = Number(res.multiplier);
        if (res.result === 'cleared') {
          applyBalance(res.balance);
          revealLayout(res.mines);
          endRound(`All safe tiles cleared — ₹${Number(res.payout).toFixed(2)} paid!`);
          this.showToast(`Mines payout ₹${Number(res.payout).toFixed(2)}`, 'success');
          return;
        }
        setMessage(`Safe! ${res.opened} tile${res.opened === 1 ? '' : 's'} opened · ${this.minesMultiplier.toFixed(2)}×`);
        updateNext();
      } catch (error) {
        tile.classList.remove('revealing');
        this.showToast(error.message || 'Reveal fail ho gaya.', 'error');
      } finally {
        delete tile.dataset.busy;
      }
    });

    betButton.addEventListener('click', () => this.minesRound ? cashOut() : startRound());
    document.getElementById('mines-minus')?.addEventListener('click', () => {
      betInput.value = Math.max(1, (Number(betInput.value) || 10) - 1).toFixed(2);
      updateNext();
    });
    document.getElementById('mines-plus')?.addEventListener('click', () => {
      betInput.value = ((Number(betInput.value) || 10) + 1).toFixed(2);
      updateNext();
    });
    document.getElementById('mines-random')?.addEventListener('click', () => {
      if (this.minesRound) return;
      mineCount.value = String(1 + Math.floor(Math.random() * 5));
      setMessage(`${mineCount.value} mines selected randomly.`);
      updateNext();
    });
    document.getElementById('mines-help')?.addEventListener('click', () => {
      this.showToast('Place a bet, open safe tiles, then cash out before finding a mine.', 'success');
    });
    betInput.addEventListener('input', updateNext);
    mineCount.addEventListener('change', updateNext);
  }

  initArcadeGames() {
    document.querySelectorAll('.lobby-mini-card:not(.mines-launch-card)').forEach(card => {
      card.addEventListener('click', () => {
        const game = (card.dataset.miniGame || '').toLowerCase();
        // WinGo and the lottery stay open to everyone: the lottery is paid by
        // UPI, not from the wallet, so gating it behind a ₹300 deposit would
        // lock out exactly the new players it is meant to bring in.
        const isOpenGame = game.includes('wingo') || game.includes('lottery');
        if (!isOpenGame && !this.canEnterPremiumGames()) {
          this.showGameAccessModal();
          return;
        }
        if (game.includes('chicken road')) {
          this.switchSubPage('chicken-road');
        } else if (game.includes('lucky reels')) {
          this.switchSubPage('slots');
        } else if (game.includes('mega slots')) {
          this.switchSubPage('megaslots');
        } else if (game.includes('roulette')) {
          this.switchSubPage('roulette');
        } else if (game.includes('dice')) {
          this.switchSubPage('dice');
        } else if (game.includes('lottery')) {
          this.switchSubPage('lottery');
          this.lotteryEngine?.refresh();
        } else if (game.includes('aviator')) {
          this.switchSubPage('aviator');
        } else if (game.includes('wingo')) {
          this.switchSubPage('game');
        } else {
          this.showToast(`${card.dataset.miniGame} is coming soon.`, 'success');
        }
      });
    });

    this.clearLegacyArcadeLedger();
    const gameOptions = {
      getBalance: () => appState.getState().user.balance,
      toast: (message, type) => this.showToast(message, type),
      canPlay: () => this.canEnterPremiumGames(),
      denyPlay: () => this.showGameAccessModal(),

      // Every game is server-settled: it posts a stake, and the response
      // carries the authoritative balance, which the client just stores. No
      // client-side balance arithmetic, so nothing can drift or inflate.
      api: (url, method, body) => this.fetchApi(url, method, body),
      setBalance: value => {
        const state = appState.getState();
        state.user.balance = Number(Number(value).toFixed(2));
        appState.saveState();
        this.refreshArcadeWallets();
      }
    };
    this.chickenRoadEngine = new ChickenRoadEngine(gameOptions);
    this.chickenRoadEngine.init();
    this.aviatorEngine = new AviatorEngine(gameOptions);
    this.aviatorEngine.init();

    this.slotsEngine = new SlotEngine(gameOptions, { game: 'slots', prefix: 'slots', reels: 3, rows: 3 });
    this.slotsEngine.init();
    this.megaSlotsEngine = new SlotEngine(gameOptions, { game: 'megaslots', prefix: 'megaslots', reels: 5, rows: 3 });
    this.megaSlotsEngine.init();
    this.rouletteEngine = new RouletteEngine(gameOptions);
    this.rouletteEngine.init();
    this.diceEngine = new DiceEngine(gameOptions);
    this.diceEngine.init();
    this.lotteryEngine = new LotteryEngine(gameOptions);
    this.lotteryEngine.init();
  }

  /** Push the current balance into every arcade screen's wallet readout. */
  refreshArcadeWallets() {
    this.aviatorEngine?.renderWallet();
    this.chickenRoadEngine?.render();
    this.slotsEngine?.render();
    this.megaSlotsEngine?.render();
    this.rouletteEngine?.render();
    this.diceEngine?.render();
    const lotteryWallet = document.getElementById('lottery-wallet');
    if (lotteryWallet) {
      lotteryWallet.textContent = Number(appState.getState().user.balance || 0).toFixed(2);
    }
  }

  handleRouting() {
    const path = window.location.pathname;
    const search = window.location.search;
    const state = appState.getState();

    if (path.startsWith('/admin') || search.includes('view=admin')) {
      state.viewMode = 'admin';
    } else if (path.startsWith('/login') || !this.authToken) {
      state.viewMode = 'user';
      this.switchSubPage('auth');
    } else if (path.startsWith('/game')) {
      state.viewMode = 'user';
      this.switchSubPage('game', { record: false });
    } else {
      state.viewMode = 'user';
      this.switchSubPage('home', { record: false });
    }

    appState.saveState();
  }

  // WinGo ('game') stays open to everyone. One admin switch unlocks all the
  // premium arcade games together.
  isPremiumGamePage(page) {
    return new Set(['aviator', 'chicken-road', 'mines']).has(page);
  }

  // Two ways in, matching the server's rule in games_core.require_game_access:
  // the admin's manual switch, or enough approved recharge. The deposit path
  // has to be here -- approving a deposit only credits the balance, it never
  // flips game_access_enabled, so gating on the switch alone left players who
  // had recharged well past the minimum still locked out.
  canEnterPremiumGames() {
    if (!this.authToken) return false;
    return this.gameAccessEnabled || this.hasAccessDeposit();
  }

  hasAccessDeposit() {
    return Number(this.approvedDepositTotal) >= Number(this.gameAccessMinDeposit);
  }

  showGameAccessModal() {
    const modal = document.getElementById('game-access-modal');
    modal?.classList.add('active');
    modal?.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
  }

  closeGameAccessModal() {
    const modal = document.getElementById('game-access-modal');
    modal?.classList.remove('active');
    modal?.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
  }

  async syncUserAccess() {
    if (!this.authToken) return;
    this.lastAccessSync = Date.now();
    try {
      const data = await this.fetchApi('/api/auth/me');
      const user = data.user || {};
      const wasEnabled = this.gameAccessEnabled;
      this.gameAccessEnabled = Boolean(user.game_access_enabled);
      this.approvedDepositTotal = Number(user.approved_deposit_total || 0);
      localStorage.setItem('PREDICT_GAME_ACCESS', this.gameAccessEnabled ? '1' : '0');
      localStorage.setItem('PREDICT_APPROVED_TOTAL', String(this.approvedDepositTotal));

      if (this.gameAccessEnabled && !wasEnabled) {
        this.closeGameAccessModal();
        this.showToast('Game access unlocked by Admin. All games are open now.', 'success');
      } else if (!this.gameAccessEnabled && wasEnabled && this.isPremiumGamePage(this.currentPage)) {
        this.showToast('Game access was turned off by Admin.', 'error');
        this.switchSubPage('home', { record: false });
      }

      const state = appState.getState();
      state.user.id = user.id || state.user.id;
      state.user.name = user.username || user.name || state.user.name;
      state.user.phone = user.phone || state.user.phone;
      state.user.balance = Number(user.balance ?? state.user.balance);
      if (user.referral_code) {
        this.referralCode = user.referral_code;
        localStorage.setItem('PREDICT_REFERRAL_CODE', user.referral_code);
        const codeEl = document.getElementById('agency-invite-code');
        if (codeEl) codeEl.textContent = user.referral_code;
      }
      appState.saveState();
    } catch (error) {
      if (/token|credentials|unauthorized/i.test(error.message)) {
        this.authToken = null;
        this.gameAccessEnabled = false;
        this.approvedDepositTotal = 0;
        localStorage.removeItem('PREDICT_AUTH_TOKEN');
        localStorage.removeItem('PREDICT_GAME_ACCESS');
        localStorage.removeItem('PREDICT_APPROVED_TOTAL');
        this.switchSubPage('auth', { record: false });
      }
    }
  }

  async loadReferrals() {
    if (!this.authToken) return;
    const money = value => '₹' + Number(value || 0).toFixed(0);
    try {
      const data = await this.fetchApi('/api/referrals/mine');

      const code = data.referral_code || this.referralCode || '—';
      const codeEl = document.getElementById('agency-invite-code');
      if (codeEl) codeEl.textContent = code;

      const set = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
      set('promo-reward-each', money(data.reward_per_referral));
      set('promo-signups', data.total_signups);
      set('promo-deposited', data.total_deposited);
      set('promo-pending', money(data.pending));
      set('promo-earned', money(data.earned));
      set('promo-earned-2', money(data.earned));

      const list = document.getElementById('referral-list');
      if (!list) return;
      if (!data.referrals.length) {
        list.innerHTML = '<p class="referral-empty">No referrals yet. Share your code to start earning.</p>';
        return;
      }
      // Colour the status the same way the wallet colours amounts: green once
      // the reward is credited, amber while it waits on admin approval.
      const badge = r => {
        const cls = r.status === 'approved' ? 'ok' : r.status === 'deposited' ? 'pending' : r.status === 'rejected' ? 'bad' : 'muted';
        return `<span class="referral-badge ${cls}">${r.status_label}</span>`;
      };
      list.innerHTML = data.referrals.map(r => `
        <div class="referral-row">
          <div>
            <b>${this.escapeHtml(r.name)}</b>
            <small>${this.escapeHtml(r.phone)}</small>
          </div>
          <div class="referral-row-right">
            ${badge(r)}
            ${r.status === 'approved' ? `<em>+${money(r.reward)}</em>` : ''}
          </div>
        </div>`).join('');
    } catch (error) {
      // A failed load just leaves the placeholder; no toast for a background sync.
    }
  }

  startBackendSync() {
    if (this.pollInterval) clearInterval(this.pollInterval);
    if (this.localGameClockInterval) clearInterval(this.localGameClockInterval);

    if (this.authToken) this.syncGameStatus();
    this.syncActiveQR();
    this.lastQrSync = Date.now();
    this.localGameClockInterval = setInterval(() => {
      this.tickLocalGameClock();
    }, 1000);
    this.pollInterval = setInterval(async () => {
      if (this.backendSyncInFlight) return;
      this.backendSyncInFlight = true;
      // Background upkeep only. Anything the user is waiting on runs on its own
      // and must never queue behind these, so they go out in parallel and the
      // slower ones are rate-limited.
      const jobs = [];
      const isAdmin = appState.getState().viewMode === 'admin';
      try {
        if (this.authToken && !isAdmin) jobs.push(this.syncGameStatus());
        // Picks up an admin access grant without needing a re-login.
        if (this.authToken && Date.now() - this.lastAccessSync >= 20000) {
          jobs.push(this.syncUserAccess());
        }
        if (!isAdmin && Date.now() - this.lastQrSync >= 30000) {
          this.lastQrSync = Date.now();
          jobs.push(this.syncActiveQR());
        }
        if (isAdmin && this.hasAdminCredentials()) jobs.push(this.loadAdmin({ silent: true }));
        await Promise.allSettled(jobs);
      } finally {
        this.backendSyncInFlight = false;
      }
    }, 5000);

    // Re-check access the moment the app is reopened or brought back to front,
    // so a grant made while the tab was backgrounded applies straight away.
    if (!this.accessFocusHookAttached) {
      this.accessFocusHookAttached = true;
      const refreshOnReturn = () => {
        if (document.visibilityState !== 'visible' || !this.authToken) return;
        void this.syncUserAccess();
        if (appState.getState().viewMode === 'admin' && this.hasAdminCredentials()) {
          void this.loadAdmin({ silent: true });
        }
      };
      document.addEventListener('visibilitychange', refreshOnReturn);
      window.addEventListener('focus', refreshOnReturn);
      window.addEventListener('pageshow', refreshOnReturn);
    }
  }

  tickLocalGameClock() {
    const state = appState.getState();
    const nowMs = Date.now();

    Object.entries(state.rounds || {}).forEach(([room, roomState]) => {
      if (!roomState) return;
      const clock = getRoomClock(room, nowMs);
      roomState.currentPeriod = clock.period;
      roomState.timeRemaining = clock.timeRemaining;
      roomState.isFrozen = clock.timeRemaining <= 5;
    });

    appState.saveState();
  }

  async fetchApi(url, method = 'GET', body = null) {
    const headers = { 'Content-Type': 'application/json' };
    if (this.authToken) {
      headers['Authorization'] = `Bearer ${this.authToken}`;
    }
    if (url.startsWith('/api/admin/') && this.adminApiKey) {
      headers['X-Admin-Key'] = this.adminApiKey;
    }

    try {
      const res = await fetch(`${this.apiBaseUrl}${url}`, {
        method,
        headers,
        body: body ? JSON.stringify(body) : null
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'API Request Failed');
      }
      return data;
    } catch (err) {
      console.warn('API fetch warning:', err.message);
      throw err;
    }
  }

  async syncGameStatus() {
    try {
      const state = appState.getState();
      const requestedRoom = state.activeRoom;
      const data = await this.fetchApi(`/api/game/status?room=${encodeURIComponent(requestedRoom)}`);
      if (appState.getState().activeRoom !== requestedRoom) return;

      if (!state.rounds[requestedRoom]) {
        state.rounds[requestedRoom] = {
          currentPeriod: '',
          timeRemaining: data.duration,
          isFrozen: false,
          activeBets: []
        };
      }
      const roomState = state.rounds[requestedRoom];

      if (roomState) {
        const clock = getRoomClock(requestedRoom);
        roomState.currentPeriod = clock.period;
        roomState.timeRemaining = clock.timeRemaining;
        roomState.isFrozen = clock.timeRemaining <= 5;
      }
      // The server balance is the whole truth now that every arcade game
      // settles on the backend. It used to have a local "arcade delta" added
      // on top, which is exactly how chicken-road/mines wins inflated the
      // wallet with money that was never really there.
      state.user.balance = Number(Number(data.user_balance).toFixed(2));

      appState.saveState();
      this.syncHistoryAndOrders(requestedRoom);
    } catch (e) {}
  }

  async syncActiveQR() {
    try {
      const [data, settings] = await Promise.all([
        this.fetchApi('/api/wallet/active-qr'),
        this.fetchApi('/api/wallet/settings').catch(() => this.walletSettings)
      ]);
      this.activeQR = data;
      this.walletSettings = settings;

      const amountInput = document.getElementById('deposit-amount-input');
      const minimum = Number(data.min_amount || 100);
      const maximum = Number(data.max_amount || 50000);
      if (amountInput) {
        amountInput.min = String(minimum);
        amountInput.max = String(maximum);
        amountInput.placeholder = `₹${minimum.toFixed(2)} - ₹${maximum.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
      }
      const channel = document.querySelector('.deposit-channel-card .selected-channel');
      if (channel) {
        channel.innerHTML = `${data.name || 'Active UPI QR'}<br><small>Balance:${minimum} - ${maximum >= 1000 ? `${maximum / 1000}K` : maximum}</small>`;
      }
      const methodLabel = document.getElementById('selected-recharge-method');
      if (methodLabel) methodLabel.textContent = data.name || 'Active UPI QR';
      const depositButton = document.getElementById('deposit-sticky-submit');
      if (depositButton) {
        const amount = Number(amountInput?.value || 0);
        depositButton.disabled = !settings.deposits_enabled || amount < minimum || amount > maximum;
        depositButton.textContent = settings.deposits_enabled ? 'Deposit' : 'Deposits paused';
      }
      const withdrawInput = document.getElementById('withdraw-amount-input');
      if (withdrawInput) {
        withdrawInput.min = String(settings.withdrawal_min || 200);
        withdrawInput.placeholder = `Minimum ₹${Number(settings.withdrawal_min || 200).toFixed(0)}`;
      }
      const withdrawButton = document.getElementById('withdraw-submit-button');
      if (withdrawButton) {
        withdrawButton.disabled = !settings.withdrawals_enabled;
        withdrawButton.innerText = settings.withdrawals_enabled
          ? (this.withdrawMethod === 'upi' ? 'Request UPI Payout' : 'Withdraw')
          : 'Withdrawals paused';
      }
    } catch (e) {}
  }

  resolveApiUrl(url) {
    if (!url || /^(?:https?:|data:|blob:)/i.test(url)) return url || '';
    return `${this.apiBaseUrl}${url.startsWith('/') ? url : `/${url}`}`;
  }

  escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[character]);
  }

  async syncWalletHistory() {
    try {
      const [depositData, withdrawalData] = await Promise.all([
        this.fetchApi('/api/wallet/deposits'),
        this.fetchApi('/api/wallet/withdrawals')
      ]);
      const depositList = document.getElementById('user-deposit-history-list');
      if (depositList) {
        depositList.innerHTML = depositData.deposits?.length
          ? depositData.deposits.map(item => `
            <article class="wallet-history-row">
              <strong>${this.escapeHtml(item.order_id || item.id)}</strong><b>₹${Number(item.amount).toFixed(2)}</b>
              <small>UTR ${this.escapeHtml(item.utr)} · ${this.escapeHtml(item.timestamp)}</small>
              <span class="wallet-history-status ${this.escapeHtml(item.status)}">${this.escapeHtml(item.status)}</span>
            </article>`).join('')
          : '<div class="club-no-data"><i class="bi bi-file-earmark-x-fill"></i><span>No deposits yet</span></div>';
      }
      const withdrawalList = document.getElementById('user-withdraw-history-list');
      if (withdrawalList) {
        withdrawalList.innerHTML = withdrawalData.withdrawals?.length
          ? withdrawalData.withdrawals.map(item => `
            <article class="wallet-history-row">
              <strong>${this.escapeHtml(item.id)}</strong><b>₹${Number(item.amount).toFixed(2)}</b>
              <small>${this.escapeHtml(item.upi_id)} · ${this.escapeHtml(item.timestamp)}</small>
              <span class="wallet-history-status ${this.escapeHtml(item.status)}">${this.escapeHtml(item.status)}</span>
            </article>`).join('')
          : '<div class="club-no-data"><i class="bi bi-file-earmark-x-fill"></i><span>No withdrawals yet</span></div>';
      }
    } catch {}
  }

  async syncHistoryAndOrders(room = appState.getState().activeRoom) {
    try {
      const histData = await this.fetchApi(`/api/game/history?room=${encodeURIComponent(room)}`);
      const state = appState.getState();
      if (state.activeRoom !== room) return;
      if (histData.history) {
        state.history = histData.history.map(h => ({
          period: h.period,
          number: h.winning_number,
          color: h.winning_color,
          size: h.winning_size,
          room: h.room
        }));
      }

      const betsData = await this.fetchApi('/api/game/my-bets');
      if (betsData.bets) {
        state.userBets = betsData.bets.map(b => ({
          period: b.period,
          selection: b.selection,
          totalStake: b.total_stake,
          status: b.status,
          payout: b.payout
        }));
      }
      appState.saveState();
    } catch (e) {}
  }

  // ===================== ADMIN DASHBOARD =====================
  // One fetch fills the whole panel; every action repaints its own row first and
  // reconciles in the background, so nothing waits on the network to feel done.

  showAdminGate(message = '') {
    const gate = document.getElementById('admin-unlock-overlay');
    gate?.classList.add('active');
    gate?.setAttribute('aria-hidden', 'false');
    const error = document.getElementById('admin-unlock-error');
    if (error) {
      error.textContent = message;
      error.hidden = !message;
    }
  }

  hideAdminGate() {
    const gate = document.getElementById('admin-unlock-overlay');
    gate?.classList.remove('active');
    gate?.setAttribute('aria-hidden', 'true');
    const error = document.getElementById('admin-unlock-error');
    if (error) error.hidden = true;
  }

  lockAdmin(message = '') {
    this.adminApiKey = '';
    this.adminToken = '';
    sessionStorage.removeItem('PREDICT_ADMIN_API_KEY');
    localStorage.removeItem('PREDICT_ADMIN_API_KEY');
    localStorage.removeItem('PREDICT_ADMIN_TOKEN');
    this.showAdminGate(message);
  }

  hasAdminCredentials() {
    return Boolean(this.adminToken || this.adminApiKey);
  }

  adminHeaders() {
    const headers = { 'Content-Type': 'application/json' };
    if (this.adminToken) headers['Authorization'] = `Bearer ${this.adminToken}`;
    else if (this.adminApiKey) headers['X-Admin-Key'] = this.adminApiKey;
    return headers;
  }

  async adminApi(path, method = 'GET', body = null, { timeout = 20000 } = {}) {
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), timeout);
    let res;
    try {
      res = await fetch(`${this.apiBaseUrl}${path}`, {
        method,
        headers: this.adminHeaders(),
        body: body ? JSON.stringify(body) : null,
        signal: abort.signal
      });
    } catch (cause) {
      // A dropped connection is not a credentials problem; tag it so the caller
      // can retry instead of signing the admin out.
      const error = new Error('network');
      error.transient = true;
      throw error;
    } finally {
      clearTimeout(timer);
    }

    let data = {};
    try { data = await res.json(); } catch (_) {}
    if (!res.ok) {
      const error = new Error(data.detail || `Request failed (${res.status})`);
      error.status = res.status;
      // 401 is the only answer that actually means "these credentials are wrong".
      // 403/503/502/504 are server-side states that come and go on their own.
      error.transient = res.status !== 401;
      throw error;
    }
    return data;
  }

  setAdminStatus(state, label) {
    const el = document.getElementById('admin-api-status');
    if (!el) return;
    el.classList.toggle('offline', state === 'offline');
    el.classList.toggle('retrying', state === 'retrying');
    el.innerHTML = `<i class="bi bi-circle-fill"></i> ${label}`;
  }

  // Single round trip. Falls back to the older per-table endpoints so the panel
  // still works against a backend that predates /api/admin/dashboard.
  async loadAdmin({ silent = false } = {}) {
    if (!this.hasAdminCredentials()) {
      this.showAdminGate();
      return;
    }
    if (this.adminLoadInFlight) return;
    this.adminLoadInFlight = true;
    if (!silent) document.getElementById('admin-refresh')?.classList.add('spin');
    try {
      let data;
      if (this.adminDashboardUnavailable) {
        data = await this.loadAdminLegacy();
      } else {
        try {
          data = await this.adminApi('/api/admin/dashboard');
        } catch (error) {
          if (/404|not found/i.test(error.message)) {
            this.adminDashboardUnavailable = true;
            data = await this.loadAdminLegacy();
          } else {
            throw error;
          }
        }
      }
      this.adminData = data;
      this.adminFailStreak = 0;
      this.hideAdminGate();
      this.setAdminStatus('live', 'live');
      // A poll that was already in flight when the admin changed something
      // carries pre-change data; repainting it would flick the row back.
      if (this.adminMutationsInFlight === 0) this.renderAdmin(data);
    } catch (error) {
      this.adminFailStreak = (this.adminFailStreak || 0) + 1;

      // Only a real 401 means the credentials are wrong, and even then one
      // stray response should not throw the admin out mid-approval.
      if (!error.transient && this.adminFailStreak >= 2) {
        this.adminFailStreak = 0;
        this.lockAdmin('Sign in again to continue.');
        return;
      }

      // Everything else: keep the dashboard on screen with its last good data
      // and just say we are retrying. The poll will recover on its own.
      if (this.adminFailStreak < 3) {
        this.setAdminStatus('retrying', 'reconnecting…');
      } else {
        this.setAdminStatus('offline', 'offline — retrying');
      }
      if (!silent && this.adminFailStreak === 1) {
        this.showToast('Connection hiccup — retrying in the background.', 'error');
      }
    } finally {
      this.adminLoadInFlight = false;
      document.getElementById('admin-refresh')?.classList.remove('spin');
    }
  }

  async loadAdminLegacy() {
    const [metrics, settings, users, qr, deposits, withdrawals] = await Promise.all([
      this.adminApi('/api/admin/metrics'),
      this.adminApi('/api/admin/platform-settings'),
      this.adminApi('/api/admin/users'),
      this.adminApi('/api/admin/qr-codes'),
      this.adminApi('/api/admin/deposits'),
      this.adminApi('/api/admin/withdrawals')
    ]);
    return {
      metrics,
      platform_settings: settings,
      users: users.users || [],
      qr_codes: qr.qr_codes || [],
      deposits: deposits.deposits || [],
      withdrawals: withdrawals.withdrawals || []
    };
  }

  renderAdmin(data) {
    this.renderAdminStats(data.metrics || {});
    this.renderAdminQueue();
    this.renderAdminUsers(data.users || []);
    void this.loadUsersDaily();
    this.renderAdminHistory();
    this.renderAdminQrCodes(data.qr_codes || []);
    this.renderAdminControls(data);
    this.loadAdminGames();
    this.loadAdminLottery();
    this.loadAdminVisitors();
    this.loadFlushScopes();
  }

  // ----------------------------------------------------- database flushing

  async loadFlushScopes() {
    const host = document.getElementById('nd-flush-scopes');
    if (!host) return;
    let data;
    try {
      data = await this.adminApi('/api/admin/maintenance/flush-scopes');
    } catch (error) {
      host.innerHTML = `<p class="ng-hint">${error.message}</p>`;
      return;
    }
    this.flushPhrase = data.confirm_phrase;

    host.innerHTML = data.scopes.map(scope => `
      <label class="nd-flush-scope">
        <input type="checkbox" value="${scope.key}">
        <span>
          <b>${scope.label}</b>
          <em>${scope.rows.toLocaleString()} rows</em>
          <small>${scope.detail}</small>
        </span>
      </label>`).join('');

    // The button stays disabled until something is selected AND the phrase is
    // typed: two independent actions, so a single stray tap cannot wipe data.
    const sync = () => {
      const picked = host.querySelectorAll('input:checked').length;
      const typed = document.getElementById('nd-flush-phrase').value.trim();
      document.getElementById('nd-flush-run').disabled =
        !picked || typed !== this.flushPhrase;
    };
    host.querySelectorAll('input').forEach(box => box.addEventListener('change', sync));
    document.getElementById('nd-flush-phrase')?.addEventListener('input', sync);
    sync();
  }

  async runFlush() {
    const host = document.getElementById('nd-flush-scopes');
    const scopes = [...host.querySelectorAll('input:checked')].map(box => box.value);
    const phrase = document.getElementById('nd-flush-phrase').value.trim();
    const labels = [...host.querySelectorAll('input:checked')]
      .map(box => box.closest('label').querySelector('b').textContent);

    if (!window.confirm(`Permanently delete: ${labels.join(', ')}?\n\nThis cannot be undone.`)) return;

    const button = document.getElementById('nd-flush-run');
    button.disabled = true;
    try {
      const result = await this.adminApi('/api/admin/maintenance/flush', 'POST',
        { scopes, confirm: phrase });
      document.getElementById('nd-flush-result').textContent =
        `Deleted ${result.total.toLocaleString()} rows: ` +
        Object.entries(result.deleted).map(([t, n]) => `${t} ${n}`).join(' · ');
      this.showToast(`Flushed ${result.total} rows.`, 'success');
      document.getElementById('nd-flush-phrase').value = '';
    } catch (error) {
      this.showToast(error.message, 'error');
    }
    this.loadFlushScopes();
  }

  // -------------------------------------------------------------- visitors

  /** Everyone who landed on the site, including those who never signed up. */
  async loadAdminVisitors() {
    const body = document.getElementById('nv-sessions-body');
    if (!body) return;
    const hours = this.adminVisitorHours || 24;
    const outcome = this.adminVisitorOutcome || '';

    const set = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
    };

    let summary;
    try {
      summary = await this.adminApi(`/api/admin/visitors/summary?hours=${hours}`);
    } catch (error) {
      body.innerHTML = `<tr><td colspan="7" class="nd-empty">${error.message}</td></tr>`;
      return;
    }

    set('nv-visitors', summary.visitors);
    set('nv-anonymous', summary.still_anonymous);
    set('nv-converted', summary.registered + summary.logged_in);
    set('nv-avg-time', this.formatDuration(summary.avg_seconds));

    let sessions;
    try {
      const query = `hours=${hours}${outcome ? `&outcome=${outcome}` : ''}`;
      ({ sessions } = await this.adminApi(`/api/admin/visitors?${query}`));
    } catch (error) {
      body.innerHTML = `<tr><td colspan="7" class="nd-empty">${error.message}</td></tr>`;
      return;
    }

    const labels = { browsing: 'left without signing up', registered: 'registered', logged_in: 'logged in' };
    body.innerHTML = sessions.length
      ? sessions.map(s => `
          <tr class="nv-row" data-session="${s.id}">
            <td>${new Date(s.started_at).toLocaleString()}</td>
            <td><code>${s.visitor_id.slice(0, 8)}</code>${s.username ? `<br><small>${s.username}</small>` : ''}</td>
            <td><code>${s.ip || '—'}</code></td>
            <td>${s.device || '—'}<br><small>${s.browser || ''} · ${s.os || ''}</small></td>
            <td>${s.landing_path || '—'} → <b>${s.last_path || '—'}</b><br><small>${s.page_views} screens</small></td>
            <td>${this.formatDuration(s.active_seconds)}</td>
            <td class="nv-outcome is-${s.outcome}">${labels[s.outcome] || s.outcome}</td>
          </tr>`).join('')
      : '<tr><td colspan="7" class="nd-empty">No visits in this window</td></tr>';

    body.querySelectorAll('.nv-row').forEach(row =>
      row.addEventListener('click', () => this.openVisitorTimeline(row.dataset.session)));

    void this.loadVisitorsDaily();
  }

  /** One dated table per day of visitor traffic. */
  async loadVisitorsDaily() {
    const host = document.getElementById('nv-daily');
    if (!host) return;
    let days;
    try {
      ({ days } = await this.adminApi('/api/admin/visitors/daily?days=60'));
    } catch (error) {
      host.innerHTML = `<p class="nd-empty">${this.escapeHtml(error.message)}</p>`;
      return;
    }
    if (!days.length) {
      host.innerHTML = '<p class="nd-empty">No visitor data yet.</p>';
      return;
    }
    const dur = s => this.formatDuration(s);
    const labels = { browsing: 'anon', registered: 'registered', logged_in: 'logged in' };
    // Preserve which days are expanded across the background refresh (see the
    // same note in loadUsersDaily).
    const openDates = new Set([...host.querySelectorAll('.nd-day[open]')].map(d => d.dataset.date));
    const firstRender = host.dataset.rendered !== '1';
    host.innerHTML = days.map((day, i) => `
      <details class="nd-day" data-date="${day.date}"${(firstRender ? i === 0 : openDates.has(day.date)) ? ' open' : ''}>
        <summary class="nd-day-head">
          <h4>${this.escapeHtml(day.label)}</h4>
          <span class="nd-day-sum">
            ${day.visitors} visitors · ${day.sessions} visits · ${day.registered + day.logged_in} joined · ${day.bounced} bounced · avg ${dur(day.avg_seconds)}
          </span>
        </summary>
        <div class="nd-table-wrap">
          <table class="nd-table">
            <thead><tr><th>Time</th><th>Visitor</th><th>IP</th><th>Device</th><th>Journey</th><th>Time on site</th><th>Outcome</th></tr></thead>
            <tbody>
              ${day.rows.map(r => `
                <tr>
                  <td>${this.escapeHtml(r.time)}</td>
                  <td>${this.escapeHtml(r.visitor)}${r.phone ? `<br><small>${this.escapeHtml(r.phone)}</small>` : ''}</td>
                  <td><code>${this.escapeHtml(r.ip || '—')}</code></td>
                  <td>${this.escapeHtml(r.device)}</td>
                  <td>${this.escapeHtml(r.journey)}</td>
                  <td>${dur(r.seconds)}</td>
                  <td class="nv-outcome is-${r.outcome}">${labels[r.outcome] || r.outcome}</td>
                </tr>`).join('')}
            </tbody>
          </table>
        </div>
      </details>`).join('');
    host.dataset.rendered = '1';
  }

  formatDuration(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    if (total < 60) return `${total}s`;
    const minutes = Math.floor(total / 60);
    return `${minutes}m ${total % 60}s`;
  }

  async openVisitorTimeline(sessionId) {
    const panel = document.getElementById('nv-timeline-panel');
    if (!panel) return;
    panel.hidden = false;
    document.getElementById('nv-timeline').innerHTML = '<li class="nd-empty">Loading…</li>';

    let data;
    try {
      data = await this.adminApi(`/api/admin/visitors/${sessionId}`);
    } catch (error) {
      document.getElementById('nv-timeline').innerHTML = `<li class="nd-empty">${error.message}</li>`;
      return;
    }

    const s = data.session;
    document.getElementById('nv-timeline-title').textContent =
      s.username ? `${s.username} (was anonymous)` : `Anonymous ${s.visitor_id.slice(0, 8)}`;
    document.getElementById('nv-timeline-meta').textContent =
      [`IP ${s.ip || 'unknown'}`, `${s.browser} on ${s.os} (${s.device})`,
       `from ${s.referrer || 'direct'}`, `${this.formatDuration(s.active_seconds)} on site`,
       `${s.page_views} screens`].join(' · ');

    const start = new Date(s.started_at).getTime();
    document.getElementById('nv-timeline').innerHTML = data.events.map(event => {
      const offset = Math.max(0, Math.round((new Date(event.created_at).getTime() - start) / 1000));
      const meta = event.meta && Object.keys(event.meta).length
        ? `<small>${Object.entries(event.meta).map(([k, v]) => `${k}: ${v}`).join(' · ')}</small>` : '';
      return `<li class="nv-ev is-${event.name}">
          <span class="nv-ev-at">+${this.formatDuration(offset)}</span>
          <b>${event.name.replace(/_/g, ' ')}</b>
          ${event.path ? `<code>${event.path}</code>` : ''}${meta}
        </li>`;
    }).join('') || '<li class="nd-empty">No events</li>';

    const other = document.getElementById('nv-other-visits');
    other.innerHTML = data.other_visits.length
      ? `<h4>Same browser, other visits</h4>` + data.other_visits.map(v =>
          `<button type="button" class="nv-other-visit" data-session="${v.id}">
             ${new Date(v.started_at).toLocaleString()} · ${v.outcome} ·
             ${this.formatDuration(v.active_seconds)}
           </button>`).join('')
      : '';
    other.querySelectorAll('[data-session]').forEach(button =>
      button.addEventListener('click', () => this.openVisitorTimeline(button.dataset.session)));
  }

  // ---------------------------------------------------------------- games

  /** Analytics for every game, live and historical. */
  async loadAdminGames() {
    const body = document.getElementById('ng-games-body');
    if (!body) return;
    const days = this.adminGamesRange || 1;

    let data;
    try {
      data = await this.adminApi(`/api/admin/games/overview?days=${days}`);
    } catch (error) {
      body.innerHTML = `<tr><td colspan="7" class="nd-empty">${error.message}</td></tr>`;
      return;
    }

    const money = value => `₹${Number(value || 0).toFixed(2)}`;
    const set = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
    };
    set('ng-total-stake', money(data.totals.stake));
    set('ng-total-payout', money(data.totals.payout));
    set('ng-total-ggr', money(data.totals.ggr));
    set('ng-total-rounds', data.totals.rounds);

    body.innerHTML = data.games.length
      ? data.games.map(game => `
          <tr>
            <td><b>${game.label}</b></td>
            <td>${game.rounds}</td>
            <td>${game.players}</td>
            <td>${money(game.stake)}</td>
            <td>${money(game.payout)}</td>
            <td class="${game.ggr >= 0 ? 'ng-up' : 'ng-down'}">${money(game.ggr)}</td>
            <td>${game.rtp === null ? '—' : `${game.rtp}%`}</td>
          </tr>`).join('')
      : '<tr><td colspan="7" class="nd-empty">No rounds in this window</td></tr>';

    this.loadAdminGameFeed();
    this.loadAdminGameList();
  }

  /** The game picker: one tab per game, each showing whether it is live. */
  async loadAdminGameList() {
    const list = document.getElementById('ng-game-list');
    if (!list) return;
    let games;
    try {
      ({ games } = await this.adminApi('/api/admin/games/controls'));
    } catch (error) {
      list.innerHTML = `<div class="nd-empty">${error.message}</div>`;
      return;
    }
    this.adminGameControls = games;

    list.innerHTML = games.map(game => `
      <button type="button" role="tab" class="ng-game-tab${game.enabled ? '' : ' is-off'}"
              data-game="${game.game}" aria-selected="false">
        <i class="ng-tab-dot${game.live_players ? ' is-live' : ''}"></i>
        <b>${game.label}</b>
        ${game.live_players
          ? `<em class="ng-tab-live" title="${game.live_players} playing now">${game.live_players}</em>`
          : ''}
        ${game.mode === 'manual' ? '<em class="ng-tab-manual">MANUAL</em>' : ''}
        ${game.enabled ? '' : '<em class="ng-tab-off">OFF</em>'}
      </button>`).join('');

    list.querySelectorAll('[data-game]').forEach(tab =>
      tab.addEventListener('click', () => this.openAdminGame(tab.dataset.game)));

    // Land on a game rather than an empty panel: without this the section
    // opens with tabs and nothing under them, which reads as still loading.
    if (!this.adminGameOpen && games.length) this.adminGameOpen = games[0].game;

    if (this.adminGameOpen) {
      this.markAdminGameTab(this.adminGameOpen);
      // Painted from the list data that just arrived -- no second request.
      const fresh = games.find(entry => entry.game === this.adminGameOpen);
      if (fresh) this.paintAdminGame(fresh);
    }
  }

  markAdminGameTab(game) {
    document.querySelectorAll('#ng-game-list .ng-game-tab').forEach(tab => {
      const selected = tab.dataset.game === game;
      tab.classList.toggle('is-active', selected);
      tab.setAttribute('aria-selected', String(selected));
    });
  }

  /**
   * Open a game's panel.
   *
   * Paints immediately from the list response that is already in memory, then
   * fills in the recent-rounds table when it arrives. Waiting for the round
   * trip before showing anything made a click feel like the dashboard had
   * hung, because the database is remote and the panel needs several queries.
   */
  async openAdminGame(game) {
    const panel = document.getElementById('ng-game-detail');
    if (!panel) return;
    this.adminGameOpen = game;
    this.markAdminGameTab(game);

    const cached = this.adminGameControls?.find(entry => entry.game === game);
    if (cached) {
      this.paintAdminGame(cached);
      const body = document.getElementById('ng-detail-rounds');
      if (body) body.innerHTML = '<tr><td colspan="5" class="nd-empty">Loading…</td></tr>';
    }

    let data;
    try {
      data = await this.adminApi(`/api/admin/games/controls/${game}`);
    } catch (error) {
      if (!cached) this.showToast(error.message, 'error');
      return;
    }
    // A slow reply must not overwrite a panel the admin has since switched to.
    if (this.adminGameOpen !== game) return;
    this.paintAdminGame(data);
  }

  paintAdminGame(data) {
    const panel = document.getElementById('ng-game-detail');
    if (!panel) return;
    panel.hidden = false;

    const money = value => `₹${Number(value || 0).toFixed(2)}`;
    const set = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
    };
    set('ng-detail-title', data.label);
    set('ng-detail-live-players', data.live_players);
    set('ng-detail-live-rounds', data.live_rounds);
    set('ng-detail-live-stake', money(data.live_stake));
    set('ng-detail-hour-stake', money(data.hour_stake));

    document.getElementById('ng-ctl-enabled').checked = data.enabled;
    document.getElementById('ng-ctl-forced').value = data.forced || '';
    document.getElementById('ng-ctl-bias').value = data.house_bias;
    set('ng-bias-value', `${data.house_bias}%`);
    document.getElementById('ng-ctl-min').value = data.min_stake;
    document.getElementById('ng-ctl-max').value = data.max_stake;

    document.querySelectorAll('.nd-seg-btn.ng-mode').forEach(button => {
      button.classList.toggle('active', button.dataset.mode === data.mode);
      // A game the server cannot steer gets its result controls locked, not
      // hidden behind a switch that would silently do nothing.
      button.disabled = !data.can_force;
    });
    document.getElementById('ng-ctl-bias').disabled = !data.can_force;
    document.getElementById('ng-manual-block').hidden = data.mode !== 'manual' || !data.can_force;

    const note = document.getElementById('ng-ctl-note');
    if (note) {
      note.textContent = data.can_force ? '' : data.note;
      note.hidden = data.can_force;
    }

    const choices = document.getElementById('ng-forced-choices');
    choices.innerHTML = data.forced_choices.map(choice =>
      `<button type="button" class="ng-choice${choice === data.forced ? ' is-active' : ''}"
               data-forced="${choice}">${choice.replace(/_/g, ' ')}</button>`).join('')
      || '<small class="ng-hint">This game has no forced results.</small>';
    choices.querySelectorAll('[data-forced]').forEach(button =>
      button.addEventListener('click', () => {
        document.getElementById('ng-ctl-forced').value = button.dataset.forced;
        choices.querySelectorAll('[data-forced]').forEach(b => b.classList.toggle('is-active', b === button));
      }));

    this.paintLiveBets(data.live_bets);

    // Absent when painting from the cached list entry -- leave whatever the
    // table already shows rather than blanking it.
    if (!data.recent) return;
    const body = document.getElementById('ng-detail-rounds');
    body.innerHTML = data.recent.length
      ? data.recent.map(round => `
          <tr>
            <td>${new Date(round.created_at).toLocaleTimeString()}</td>
            <td>${round.username}</td>
            <td>${money(round.stake)}</td>
            <td>${money(round.payout)}</td>
            <td class="${round.profit <= 0 ? 'ng-up' : 'ng-down'}">${money(-round.profit)}</td>
          </tr>`).join('')
      : '<tr><td colspan="5" class="nd-empty">No rounds yet</td></tr>';
  }


  /**
   * The open round of a shared-round game: what everyone has backed so far.
   *
   * Only WinGo and Dice have anything to show -- the one-shot games settle
   * inside the request, so by the time a round reaches the database it is
   * already over and there is no "live" table to read.
   */
  paintLiveBets(live) {
    const panel = document.getElementById('ng-live-bets');
    if (!panel) return;
    if (!live || !live.supported) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;

    const money = value => `₹${Number(value || 0).toFixed(2)}`;
    document.getElementById('ng-live-summary').textContent =
      `${live.total_players} player${live.total_players === 1 ? '' : 's'} · ${money(live.total_staked)}`;

    const set = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
    };
    set('ng-live-total-players', live.total_players);
    set('ng-live-total-bets', live.total_bets);
    set('ng-live-total-staked', money(live.total_staked));

    document.getElementById('ng-live-selections').innerHTML = live.selections.length
      ? live.selections.map(sel => `
          <article class="ng-sel-card">
            <span class="ng-sel-fill" style="--w:${sel.share}%"></span>
            <b>${sel.selection}</b>
            <strong>${sel.players}</strong>
            <em>${sel.players === 1 ? 'player' : 'players'} · ${sel.share}%</em>
            <span class="ng-sel-stake">${money(sel.staked)}</span>
          </article>`).join('')
      : '<p class="ng-hint">No bets on the current round yet.</p>';
  }

  async saveAdminGameControls() {
    const game = this.adminGameOpen;
    if (!game) return;
    const canForce = this.adminGameControls?.find(g => g.game === game)?.can_force;
    const body = {
      enabled: document.getElementById('ng-ctl-enabled').checked,
      min_stake: Number(document.getElementById('ng-ctl-min').value),
      max_stake: Number(document.getElementById('ng-ctl-max').value)
    };
    if (canForce) {
      body.mode = document.querySelector('.nd-seg-btn.ng-mode.active')?.dataset.mode || 'auto';
      body.forced = document.getElementById('ng-ctl-forced').value.trim();
      body.house_bias = Number(document.getElementById('ng-ctl-bias').value);
    }
    try {
      await this.adminApi(`/api/admin/games/controls/${game}`, 'PUT', body);
      this.showToast('Game controls saved.', 'success');
    } catch (error) {
      this.showToast(error.message, 'error');
    }
    this.loadAdminGameList();
  }

  async loadAdminGameFeed() {
    const body = document.getElementById('ng-live-body');
    if (!body) return;
    let rounds;
    try {
      ({ rounds } = await this.adminApi('/api/admin/games/rounds?limit=40'));
    } catch (error) {
      body.innerHTML = `<tr><td colspan="6" class="nd-empty">${error.message}</td></tr>`;
      return;
    }
    const money = value => `₹${Number(value || 0).toFixed(2)}`;
    body.innerHTML = rounds.length
      ? rounds.map(round => `
          <tr>
            <td>${new Date(round.created_at).toLocaleTimeString()}</td>
            <td>${round.label}</td>
            <td>${round.username}</td>
            <td>${money(round.stake)}</td>
            <td>${money(round.payout)}</td>
            <td class="${round.profit <= 0 ? 'ng-up' : 'ng-down'}">${money(-round.profit)}</td>
          </tr>`).join('')
      : '<tr><td colspan="6" class="nd-empty">No rounds yet</td></tr>';
  }

  /** Ticket list, approvals and the winner desk for one draw date. */
  async loadAdminLottery() {
    const body = document.getElementById('ng-lottery-body');
    if (!body) return;
    const dateInput = document.getElementById('ng-lottery-date');
    const query = dateInput?.value ? `?draw_date=${dateInput.value}` : '';

    let data;
    try {
      data = await this.adminApi(`/api/admin/lottery/tickets${query}`);
    } catch (error) {
      body.innerHTML = `<tr><td colspan="6" class="nd-empty">${error.message}</td></tr>`;
      return;
    }
    if (dateInput && !dateInput.value) dateInput.value = data.draw_date;

    const set = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
    };
    set('ng-lot-total', data.summary.total);
    set('ng-lot-pending', data.summary.pending);
    set('ng-lot-approved', data.summary.approved);
    set('ng-lot-collected', `₹${Number(data.summary.collected).toFixed(2)}`);

    const winning = data.draw?.winning_ticket;
    body.innerHTML = data.tickets.length
      ? data.tickets.map(ticket => {
          const isWinner = winning !== null && winning !== undefined
            && ticket.ticket_number === winning && ticket.status === 'approved';
          const action = ticket.status === 'pending'
            ? `<button type="button" class="nd-mini ok" data-lot-approve="${ticket.id}">Approve</button>
               <button type="button" class="nd-mini no" data-lot-reject="${ticket.id}">Reject</button>`
            : (isWinner && !ticket.paid_at
                ? `<button type="button" class="nd-mini pay" data-lot-pay="${ticket.id}">Pay ₹1000</button>`
                : (ticket.paid_at ? '<span class="ng-paid">paid</span>' : ''));
          return `
            <tr class="${isWinner ? 'ng-winner-row' : ''}">
              <td><b>${String(ticket.ticket_number).padStart(2, '0')}</b></td>
              <td>${ticket.user_name}</td>
              <td>${ticket.phone}</td>
              <td><code>${ticket.utr || '—'}</code></td>
              <td class="lot-status is-${ticket.status}">${ticket.status}</td>
              <td>${action}</td>
            </tr>`;
        }).join('')
      : '<tr><td colspan="6" class="nd-empty">No tickets for this draw</td></tr>';

    body.querySelectorAll('[data-lot-approve]').forEach(button =>
      button.addEventListener('click', () => this.reviewLotteryTicket(button.dataset.lotApprove, 'approve')));
    body.querySelectorAll('[data-lot-reject]').forEach(button =>
      button.addEventListener('click', () => this.reviewLotteryTicket(button.dataset.lotReject, 'reject')));
    body.querySelectorAll('[data-lot-pay]').forEach(button =>
      button.addEventListener('click', () => this.payLotteryPrize(button.dataset.lotPay)));

    this.renderLotteryWinnerCard(data, winning);
  }

  renderLotteryWinnerCard(data, winning) {
    const card = document.getElementById('ng-winner-card');
    if (!card) return;
    if (winning === null || winning === undefined) {
      card.hidden = true;
      return;
    }
    const holders = data.tickets.filter(
      ticket => ticket.ticket_number === winning && ticket.status === 'approved');
    card.hidden = false;
    card.innerHTML = holders.length
      ? `<h4>Winning ticket ${String(winning).padStart(2, '0')}</h4>` + holders.map(ticket => `
          <div class="ng-winner-row-card">
            <span><b>${ticket.user_name}</b><small>${ticket.phone} · balance ₹${Number(ticket.balance).toFixed(2)}</small></span>
            ${ticket.paid_at
              ? '<em class="ng-paid">prize paid</em>'
              : `<button type="button" class="btn-primary" data-lot-pay-card="${ticket.id}">Top up ₹1000</button>`}
          </div>`).join('')
      : `<h4>Winning ticket ${String(winning).padStart(2, '0')}</h4>
         <p class="nd-empty">Is number pe koi approved ticket nahi — prize rollover.</p>`;

    card.querySelectorAll('[data-lot-pay-card]').forEach(button =>
      button.addEventListener('click', () => this.payLotteryPrize(button.dataset.lotPayCard)));
  }

  async reviewLotteryTicket(ticketId, action) {
    try {
      await this.adminApi(`/api/admin/lottery/tickets/${ticketId}/review`, 'POST', { action });
      this.showToast(`Ticket ${action}d.`, 'success');
    } catch (error) {
      this.showToast(error.message, 'error');
    }
    this.loadAdminLottery();
  }

  async payLotteryPrize(ticketId) {
    try {
      const result = await this.adminApi(`/api/admin/lottery/tickets/${ticketId}/pay`, 'POST');
      this.showToast(`Prize ₹${result.prize} credited. New balance ₹${result.balance}.`, 'success');
    } catch (error) {
      this.showToast(error.message, 'error');
    }
    this.loadAdminLottery();
    this.loadAdminGames();
  }

  async loadAdminReferrals() {
    const body = document.getElementById('admin-referrals-table-body');
    if (!body) return;
    let data;
    try {
      data = await this.adminApi('/api/admin/referrals');
    } catch (error) {
      body.innerHTML = `<tr><td colspan="6" class="nd-empty">${this.escapeHtml(error.message)}</td></tr>`;
      return;
    }

    const note = document.getElementById('nd-referral-note');
    if (note) {
      note.textContent = data.pending_approval
        ? `${data.pending_approval} reward${data.pending_approval > 1 ? 's' : ''} waiting for approval`
        : 'No rewards pending';
    }

    const money = v => `₹${Number(v || 0).toFixed(0)}`;
    const statusCell = s => {
      const map = { signed_up: 'muted', deposited: 'pending', approved: 'ok', rejected: 'bad' };
      const label = { signed_up: 'Signed up', deposited: 'Deposit done', approved: 'Reward paid', rejected: 'Rejected' };
      return `<span class="referral-badge ${map[s] || 'muted'}">${label[s] || s}</span>`;
    };

    body.innerHTML = data.referrals.length
      ? data.referrals.map(r => {
          const deposited = Number(r.referred_deposit_total) > 0;
          // The reward is only actionable once the invited player has a
          // qualifying (approved) deposit, i.e. status === 'deposited'.
          const action = r.status === 'deposited'
            ? `<button type="button" class="nd-mini ok" data-ref-approve="${r.id}">Approve ${money(r.reward)}</button>
               <button type="button" class="nd-mini no" data-ref-reject="${r.id}">Reject</button>`
            : '';
          return `
            <tr>
              <td><b>${this.escapeHtml(r.referrer_name || '—')}</b><small>${this.escapeHtml(r.referrer_code || '')}</small></td>
              <td>${this.escapeHtml(r.referred_name || '—')}<small>${this.escapeHtml(r.referred_phone || '')}</small></td>
              <td>${deposited ? `<span class="ref-yes">₹${Number(r.referred_deposit_total).toFixed(0)}</span>` : '<span class="ref-no">Not yet</span>'}</td>
              <td>${money(r.reward)}</td>
              <td>${statusCell(r.status)}</td>
              <td>${action}</td>
            </tr>`;
        }).join('')
      : '<tr><td colspan="6" class="nd-empty">No referrals yet</td></tr>';

    body.querySelectorAll('[data-ref-approve]').forEach(button =>
      button.addEventListener('click', () => this.reviewReferral(button.dataset.refApprove, 'approve')));
    body.querySelectorAll('[data-ref-reject]').forEach(button =>
      button.addEventListener('click', () => this.reviewReferral(button.dataset.refReject, 'reject')));
  }

  async reviewReferral(referralId, action) {
    try {
      const result = await this.adminApi(`/api/admin/referrals/${referralId}/${action}`, 'POST');
      this.showToast(
        action === 'approve'
          ? `Reward ₹${result.reward} credited to the referrer.`
          : 'Referral rejected.',
        'success'
      );
    } catch (error) {
      this.showToast(error.message, 'error');
    }
    this.loadAdminReferrals();
  }

  // ------------------------------------------------------------- team accounts

  async createTeamUser() {
    const username = document.getElementById('team-username')?.value.trim();
    const phone = document.getElementById('team-phone')?.value.trim();
    const password = document.getElementById('team-password')?.value;
    const winRate = Number(document.getElementById('team-winrate')?.value || 80);
    if (!username || !phone || !password) return this.showToast('Name, phone aur password zaroori hain.', 'error');
    if (password.length < 6) return this.showToast('Password kam se kam 6 characters ka ho.', 'error');
    try {
      const res = await this.adminApi('/api/admin/team/create', 'POST', {
        username, phone, password, win_rate: winRate
      });
      this.showToast(`Team account bana: ${res.username} (${res.win_rate}% win). Code ${res.referral_code}.`, 'success');
      document.getElementById('admin-team-form')?.reset();
      const wr = document.getElementById('team-winrate'); if (wr) wr.value = '80';
      this.loadTeam();
    } catch (error) {
      this.showToast(error.message, 'error');
    }
  }

  async loadTeam() {
    const host = document.getElementById('admin-team-list');
    if (!host) return;
    let team;
    try {
      ({ team } = await this.adminApi('/api/admin/team'));
    } catch (error) {
      host.innerHTML = `<p class="nd-empty">${this.escapeHtml(error.message)}</p>`;
      return;
    }
    if (!team.length) {
      host.innerHTML = '<p class="nd-empty">No team accounts yet. Create one above.</p>';
      return;
    }
    const money = v => `₹${Number(v || 0).toFixed(2)}`;
    const openIds = new Set([...host.querySelectorAll('.nd-day[open]')].map(d => d.dataset.date));
    host.innerHTML = team.map(m => `
      <details class="nd-day" data-date="${m.id}"${openIds.has(m.id) ? ' open' : ''}>
        <summary class="nd-day-head">
          <h4>${this.escapeHtml(m.username)} <small style="color:#8a93ab">${this.escapeHtml(m.phone)}</small></h4>
          <span class="nd-day-sum">
            win ${Math.round(m.win_rate)}% · bal ${money(m.balance)} · code ${this.escapeHtml(m.referral_code || '—')} · ${m.referral_count} referrals · their deposits ${money(m.referred_deposit_total)}
          </span>
        </summary>
        <div style="padding:12px 14px; display:flex; flex-wrap:wrap; gap:8px; align-items:center; border-bottom:1px solid var(--border-color)">
          <label class="nd-hint" style="display:flex; align-items:center; gap:6px">
            Win rate %
            <input type="number" min="0" max="100" value="${Math.round(m.win_rate)}" data-team-rate="${m.id}" class="form-control" style="width:80px">
          </label>
          <button type="button" class="nd-mini ok" data-team-save="${m.id}">Save</button>
          <span class="nd-hint">Own deposits: ${money(m.own_deposits)}</span>
        </div>
        <div class="nd-table-wrap">
          <table class="nd-table">
            <thead><tr><th>Referred player</th><th>Phone</th><th>Status</th><th>Their deposits</th></tr></thead>
            <tbody>
              ${m.referrals.length ? m.referrals.map(r => `
                <tr>
                  <td>${this.escapeHtml(r.name || '—')}</td>
                  <td><code>${this.escapeHtml(r.phone || '—')}</code></td>
                  <td>${this.escapeHtml(r.status)}</td>
                  <td>${r.deposit_total > 0 ? `<span class="ref-yes">${money(r.deposit_total)}</span>` : '<span class="ref-no">—</span>'}</td>
                </tr>`).join('') : '<tr><td colspan="4" class="nd-empty">No referrals yet</td></tr>'}
            </tbody>
          </table>
        </div>
      </details>`).join('');

    host.querySelectorAll('[data-team-save]').forEach(button =>
      button.addEventListener('click', () => {
        const id = button.dataset.teamSave;
        const input = host.querySelector(`[data-team-rate="${id}"]`);
        this.updateTeamRate(id, Number(input?.value || 0));
      }));
  }

  async updateTeamRate(userId, winRate) {
    try {
      await this.adminApi(`/api/admin/team/${userId}`, 'PUT', { win_rate: winRate });
      this.showToast(`Win rate updated to ${Math.round(winRate)}%.`, 'success');
      this.loadTeam();
    } catch (error) {
      this.showToast(error.message, 'error');
    }
  }

  async submitAdminCredentials() {
    const phone = document.getElementById('admin-sec-phone')?.value.trim();
    const password = document.getElementById('admin-sec-password')?.value;
    const current = document.getElementById('admin-sec-current')?.value;
    if (!current) return this.showToast('Current password daalna zaroori hai.', 'error');
    if (!phone && !password) return this.showToast('Naya phone ya password to daalo.', 'error');
    try {
      const result = await this.adminApi('/api/admin/security/credentials', 'POST', {
        current_password: current,
        new_phone: phone || null,
        new_password: password || null
      });
      this.showToast('Admin login updated. Please sign in again with the new details.', 'success');
      ['admin-sec-phone', 'admin-sec-password', 'admin-sec-current'].forEach(id => {
        const el = document.getElementById(id); if (el) el.value = '';
      });
      // Phone or password changed means the old session is stale; force a fresh
      // admin login rather than leaving a token that may soon be invalid.
      void result;
      this.lockAdmin('Login updated — sign in again with your new details.');
    } catch (error) {
      this.showToast(error.message, 'error');
    }
  }

  async loadDeployState() {
    // The one-click commit+push only works on the dev machine, where this
    // backend has the git repo. Reveal it there; on the live site show the
    // note instead (a push already redeploys, so there's nothing to click).
    const box = document.getElementById('admin-local-deploy');
    const note = document.getElementById('admin-deploy-live-note');
    let available = false;
    try {
      ({ available } = await this.adminApi('/api/admin/deploy/local-available'));
    } catch (error) { /* treat as not available */ }
    if (box) box.hidden = !available;
    if (note) note.hidden = available;
  }

  async commitAndDeploy() {
    const button = document.getElementById('admin-commit-push');
    const out = document.getElementById('admin-local-deploy-result');
    const msg = document.getElementById('admin-commit-msg')?.value.trim() || '';
    if (button) button.disabled = true;
    if (out) out.textContent = 'Committing and pushing…';
    try {
      const data = await this.adminApi('/api/admin/deploy/local-push', 'POST', { message: msg }, { timeout: 200000 });
      if (out) out.textContent = data.detail || 'Done.';
      if (data.status === 'noop') {
        this.showToast('Kuch naya nahi tha — already up to date.', 'success');
      } else {
        this.showToast('Pushed! Vercel + Render redeploy ho rahe hain.', 'success');
        const field = document.getElementById('admin-commit-msg');
        if (field) field.value = '';
      }
    } catch (error) {
      if (out) out.textContent = error.message;
      this.showToast(error.message, 'error');
    } finally {
      if (button) button.disabled = false;
    }
  }

  async submitAdminKeyRotation() {
    const key = document.getElementById('admin-sec-key')?.value.trim();
    if (!key || key.length < 24) return this.showToast('Access key kam se kam 24 characters ka ho.', 'error');
    try {
      await this.adminApi('/api/admin/rotate-access-key', 'POST', { api_key: key });
      this.showToast('Access key rotated. Update it wherever tooling uses it.', 'success');
      const el = document.getElementById('admin-sec-key'); if (el) el.value = '';
    } catch (error) {
      this.showToast(error.message, 'error');
    }
  }

  renderAdminStats(m) {
    const money = value => `₹${Number(value || 0).toFixed(2)}`;
    const set = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
    };
    set('admin-pending-deposits', String(m.pending_deposits || 0));
    set('admin-pending-withdrawals', String(m.pending_withdrawals || 0));
    set('admin-approved-deposit-total', money(m.approved_deposit_total));
    set('admin-paid-withdrawal-total', money(m.paid_withdrawal_total));
    set('nd-users-count', String(m.users_count || 0));
    set('admin-kpi-total-pool', `₹${Number(m.total_active_stake || 0).toFixed(0)}`);
    set('admin-kpi-active-bets', `${m.active_bets_count || 0} Active Bets`);

    document.querySelectorAll('.nd-stat-warn').forEach((card, index) => {
      const count = index === 0 ? m.pending_deposits : m.pending_withdrawals;
      card.classList.toggle('has-pending', Number(count || 0) > 0);
    });

    const total = (Number(m.green_stake) + Number(m.red_stake) + Number(m.violet_stake)) || 1;
    [['green', m.green_stake], ['red', m.red_stake], ['violet', m.violet_stake]].forEach(([name, stake]) => {
      const pct = Math.round((Number(stake || 0) / total) * 100);
      const fill = document.getElementById(`admin-pool-${name}-fill`);
      const val = document.getElementById(`admin-pool-${name}-val`);
      if (fill) fill.style.width = `${pct}%`;
      if (val) val.textContent = `₹${Number(stake || 0).toFixed(0)} (${pct}%)`;
    });
  }

  pendingRequests() {
    const data = this.adminData || {};
    const deposits = (data.deposits || [])
      .filter(d => d.status === 'pending')
      .map(d => ({ kind: 'deposit', id: d.id, amount: Number(d.amount) || 0, who: d.user_name, userId: d.user_id, meta: `Order ${d.order_id || '-'} · UTR ${d.utr}`, at: d.timestamp }));
    const withdrawals = (data.withdrawals || [])
      .filter(w => w.status === 'pending')
      .map(w => ({ kind: 'withdrawal', id: w.id, amount: Number(w.amount) || 0, who: w.user_name, userId: w.user_id, meta: `To ${w.upi_id}`, at: w.timestamp }));
    return [...deposits, ...withdrawals].sort((a, b) => String(b.at || '').localeCompare(String(a.at || '')));
  }

  renderAdminQueue() {
    const host = document.getElementById('nd-queue');
    if (!host) return;
    const filter = this.adminQueueFilter || 'all';
    const all = this.pendingRequests();
    const items = filter === 'all' ? all : all.filter(item => item.kind === filter);

    const badge = document.getElementById('nd-queue-count');
    if (badge) {
      badge.textContent = String(all.length);
      badge.classList.toggle('zero', all.length === 0);
    }

    if (!items.length) {
      host.innerHTML = `<div class="nd-empty"><i class="bi bi-check2-circle"></i>Nothing waiting. You are all caught up.</div>`;
      return;
    }

    host.innerHTML = items.map(item => {
      const isDeposit = item.kind === 'deposit';
      const okLabel = isDeposit ? 'Approve' : 'Mark paid';
      const noLabel = isDeposit ? 'Reject' : 'Reject &amp; refund';
      const okFn = isDeposit ? 'adminApproveDep' : 'adminApproveWth';
      const noFn = isDeposit ? 'adminRejectDep' : 'adminRejectWth';
      return `
        <article class="nd-req" data-kind="${item.kind}" data-req-id="${this.escapeHtml(item.id)}"
                 data-user-id="${this.escapeHtml(item.userId || '')}" data-amount="${item.amount}">
          <div class="nd-req-main">
            <div class="nd-req-top">
              <span class="nd-req-kind">${isDeposit ? 'Deposit' : 'Withdrawal'}</span>
              <span class="nd-req-amount">₹${item.amount.toFixed(2)}</span>
              <span class="nd-req-who">${this.escapeHtml(item.who || 'Unknown')}</span>
            </div>
            <div class="nd-req-meta">${this.escapeHtml(item.meta)} · ${this.escapeHtml(String(item.at || ''))}</div>
          </div>
          <div class="nd-req-actions">
            <button class="nd-btn-ok" onclick="window.${okFn}('${this.escapeHtml(item.id)}')">${okLabel}</button>
            <button class="nd-btn-no" onclick="window.${noFn}('${this.escapeHtml(item.id)}')">${noLabel}</button>
          </div>
        </article>`;
    }).join('');
  }

  /** One dated table per day of signups. */
  async loadUsersDaily() {
    const host = document.getElementById('admin-users-daily');
    if (!host) return;
    let days;
    try {
      ({ days } = await this.adminApi('/api/admin/users/daily?days=60'));
    } catch (error) {
      host.innerHTML = `<p class="nd-empty">${this.escapeHtml(error.message)}</p>`;
      return;
    }
    if (!days.length) {
      host.innerHTML = '<p class="nd-empty">No signups yet.</p>';
      return;
    }
    const money = v => `₹${Number(v || 0).toFixed(2)}`;
    // The dashboard refreshes in the background, which re-renders this list.
    // Remember which days the admin had expanded (by date) so a refresh does
    // not fold or unfold them under the cursor; only the very first render
    // defaults the newest day open.
    const openDates = new Set([...host.querySelectorAll('.nd-day[open]')].map(d => d.dataset.date));
    const firstRender = host.dataset.rendered !== '1';
    host.innerHTML = days.map((day, i) => `
      <details class="nd-day" data-date="${day.date}"${(firstRender ? i === 0 : openDates.has(day.date)) ? ' open' : ''}>
        <summary class="nd-day-head">
          <h4>${this.escapeHtml(day.label)}</h4>
          <span class="nd-day-sum">
            ${day.signups} signups · ${day.with_deposit} deposited · recharge ${money(day.total_deposits)} · balance ${money(day.total_balance)}
          </span>
        </summary>
        <div class="nd-table-wrap">
          <table class="nd-table">
            <thead><tr><th>Time</th><th>Player</th><th>Phone</th><th>Balance</th><th>Recharge</th><th>Access</th><th>Status</th></tr></thead>
            <tbody>
              ${day.users.map(u => `
                <tr>
                  <td>${this.escapeHtml(u.time)}</td>
                  <td>${this.escapeHtml(u.username || '—')}</td>
                  <td><code>${this.escapeHtml(u.phone || '—')}</code></td>
                  <td>${money(u.balance)}</td>
                  <td>${u.approved_deposit_total > 0 ? `<span class="ref-yes">${money(u.approved_deposit_total)}</span>` : '<span class="ref-no">—</span>'}</td>
                  <td>${u.game_access_enabled ? 'yes' : 'no'}</td>
                  <td>${this.escapeHtml(u.status || 'active')}</td>
                </tr>`).join('')}
            </tbody>
          </table>
        </div>
      </details>`).join('');
    host.dataset.rendered = '1';
  }

  renderAdminUsers(users) {
    const body = document.getElementById('admin-users-table-body');
    if (!body) return;
    if (!users.length) {
      body.innerHTML = `<tr><td colspan="6" class="nd-empty">No players yet</td></tr>`;
      return;
    }
    body.innerHTML = users.map(u => {
      const approved = Number(u.approved_deposit_total || 0);
      const active = u.status === 'active';
      return `
        <tr data-user-id="${this.escapeHtml(u.id)}"
            data-search="${this.escapeHtml(`${u.username || ''} ${u.phone || ''} ${u.id}`.toLowerCase())}">
          <td>
            <span class="nd-player-name">${this.escapeHtml(u.username || '-')}</span>
            <span class="nd-player-sub">${this.escapeHtml(u.phone || '')} · ${this.escapeHtml(u.id)}</span>
          </td>
          <td data-user-balance>₹${Number(u.balance || 0).toFixed(2)}</td>
          <td class="nd-recharge" data-approved-total="${approved}">₹${approved.toFixed(2)}</td>
          <td class="admin-game-access">${this.renderUserAccessCell(u.id, approved, Boolean(u.game_access_enabled))}</td>
          <td><span class="nd-badge ${active ? 'ok' : 'bad'}">${active ? 'Active' : 'Disabled'}</span></td>
          <td>
            <button class="nd-row-btn" onclick="window.adminToggleUser('${this.escapeHtml(u.id)}', '${active ? 'disabled' : 'active'}')">${active ? 'Disable' : 'Enable'}</button>
            <button class="nd-row-btn danger" onclick="window.adminDeleteUser('${this.escapeHtml(u.id)}')">Delete</button>
          </td>
        </tr>`;
    }).join('');
    this.applyAdminSearch('nd-user-search', 'admin-users-table-body');
  }

  renderAdminHistory() {
    const body = document.getElementById('nd-history-body');
    if (!body) return;
    const data = this.adminData || {};
    const rows = [
      ...(data.deposits || []).filter(d => d.status !== 'pending').map(d => ({
        kind: 'Deposit', who: d.user_name, amount: d.amount, ref: d.order_id || d.utr, at: d.timestamp, status: d.status
      })),
      ...(data.withdrawals || []).filter(w => w.status !== 'pending').map(w => ({
        kind: 'Withdrawal', who: w.user_name, amount: w.amount, ref: w.upi_id, at: w.timestamp, status: w.status
      }))
    ].sort((a, b) => String(b.at || '').localeCompare(String(a.at || ''))).slice(0, 200);

    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="6" class="nd-empty">Nothing processed yet</td></tr>`;
      return;
    }
    body.innerHTML = rows.map(r => {
      const tone = (r.status === 'approved' || r.status === 'paid') ? 'ok' : 'bad';
      return `
        <tr data-search="${this.escapeHtml(`${r.who || ''} ${r.ref || ''}`.toLowerCase())}">
          <td>${r.kind}</td>
          <td>${this.escapeHtml(r.who || '-')}</td>
          <td>₹${Number(r.amount || 0).toFixed(2)}</td>
          <td><code>${this.escapeHtml(String(r.ref || '-'))}</code></td>
          <td>${this.escapeHtml(String(r.at || ''))}</td>
          <td><span class="nd-badge ${tone}">${this.escapeHtml(String(r.status).toUpperCase())}</span></td>
        </tr>`;
    }).join('');
    this.applyAdminSearch('nd-history-search', 'nd-history-body');
  }

  renderAdminQrCodes(codes) {
    const body = document.getElementById('admin-qr-table-body');
    if (!body) return;
    if (!codes.length) {
      body.innerHTML = `<tr><td colspan="5" class="nd-empty">No QR codes uploaded</td></tr>`;
      return;
    }
    body.innerHTML = codes.map(q => `
      <tr data-qr-id="${this.escapeHtml(q.id)}">
        <td><img class="nd-qr-thumb" src="${this.escapeHtml(this.resolveApiUrl(q.qr_url))}" alt=""></td>
        <td><span class="nd-player-name">${this.escapeHtml(q.name)}</span><span class="nd-player-sub">${this.escapeHtml(q.note || '')}</span></td>
        <td><code>${this.escapeHtml(q.upi_id || 'Static QR')}</code><br><span class="nd-player-sub">₹${q.min_amount || 100} – ₹${q.max_amount || 50000}</span></td>
        <td class="nd-qr-state">${this.renderQrStateCell(q.id, Boolean(q.is_active))}</td>
        <td>
          <button class="nd-row-btn danger" onclick="window.adminDeleteQR('${this.escapeHtml(q.id)}')">Delete</button>
        </td>
      </tr>`).join('');
    this.renderQrPoolNote();
  }

  // Several QRs can be live at once; each deposit order is handed the
  // least-recently-used one.
  renderQrStateCell(qrId, enabled) {
    return `
      <span class="nd-badge ${enabled ? 'ok' : 'bad'}">${enabled ? 'In rotation' : 'Off'}</span>
      <button class="nd-row-btn nd-qr-toggle" onclick="window.adminToggleQR('${this.escapeHtml(qrId)}', ${enabled ? 'false' : 'true'})">
        ${enabled ? 'Turn off' : 'Turn on'}
      </button>`;
  }

  setQrStateCell(qrId, enabled) {
    const row = document.querySelector(`#admin-qr-table-body tr[data-qr-id="${CSS.escape(String(qrId))}"]`);
    const cell = row?.querySelector('.nd-qr-state');
    if (cell) cell.innerHTML = this.renderQrStateCell(qrId, enabled);
    const record = ((this.adminData || {}).qr_codes || []).find(q => q.id === qrId);
    if (record) record.is_active = enabled ? 1 : 0;
    this.renderQrPoolNote();
  }

  renderQrPoolNote() {
    const note = document.getElementById('nd-qr-pool-note');
    if (!note) return;
    const live = ((this.adminData || {}).qr_codes || []).filter(q => q.is_active).length;
    note.textContent = live === 0
      ? 'No QR in rotation — players cannot deposit.'
      : `${live} QR${live > 1 ? 's' : ''} in rotation · each new deposit order gets the next one`;
    note.classList.toggle('warn', live === 0);
  }

  renderAdminControls(data) {
    const settings = data.platform_settings || {};
    const deposits = document.getElementById('admin-deposits-enabled');
    const withdrawals = document.getElementById('admin-withdrawals-enabled');
    const min = document.getElementById('admin-withdrawal-min');
    if (deposits) deposits.checked = Boolean(settings.deposits_enabled);
    if (withdrawals) withdrawals.checked = Boolean(settings.withdrawals_enabled);
    if (min && document.activeElement !== min) min.value = String(settings.withdrawal_min || 200);

    const mode = (data.metrics || {}).prediction_mode || 'auto_least';
    const modeLabel = document.getElementById('admin-kpi-current-mode');
    if (modeLabel) modeLabel.textContent = String(mode).replace('_', ' ');
    document.querySelectorAll('.nd-mode').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.mode === mode);
    });
  }

  applyAdminSearch(inputId, bodyId) {
    const term = (document.getElementById(inputId)?.value || '').trim().toLowerCase();
    document.querySelectorAll(`#${bodyId} tr[data-search]`).forEach(row => {
      row.hidden = Boolean(term) && !row.dataset.search.includes(term);
    });
  }

  // ---- optimistic row helpers ----

  renderUserAccessCell(userId, approvedTotal, accessEnabled) {
    const eligible = approvedTotal >= 300;
    const label = accessEnabled ? 'Disable' : eligible ? 'Enable all games' : '₹300 required';
    const tone = accessEnabled ? 'enabled' : eligible ? 'eligible' : '';
    return `<button class="nd-access-btn ${tone}" ${!accessEnabled && !eligible ? 'disabled' : ''}
      onclick="window.adminToggleGameAccess('${this.escapeHtml(userId)}', ${accessEnabled ? 'false' : 'true'})">${label}</button>`;
  }

  findUserRow(userId) {
    if (!userId) return null;
    return document.querySelector(`#admin-users-table-body tr[data-user-id="${CSS.escape(String(userId))}"]`);
  }

  findRequestCard(id) {
    if (!id) return null;
    return document.querySelector(`#nd-queue [data-req-id="${CSS.escape(String(id))}"]`);
  }

  setRequestBusy(id, busy) {
    this.findRequestCard(id)?.classList.toggle('is-busy', busy);
  }

  // Drop a handled request straight out of the queue and refresh the counter.
  clearRequest(id) {
    const card = this.findRequestCard(id);
    if (!card) return;
    const kind = card.dataset.kind;
    const store = kind === 'deposit' ? 'deposits' : 'withdrawals';
    const list = (this.adminData || {})[store] || [];
    const record = list.find(r => r.id === id);
    if (record) record.status = kind === 'deposit' ? 'approved' : 'paid';
    card.remove();
    const remaining = this.pendingRequests();
    const badge = document.getElementById('nd-queue-count');
    if (badge) {
      badge.textContent = String(remaining.length);
      badge.classList.toggle('zero', remaining.length === 0);
    }
    if (!document.querySelector('#nd-queue .nd-req')) this.renderAdminQueue();
  }

  bumpUserRecharge(userId, delta) {
    const row = this.findUserRow(userId);
    const cell = row?.querySelector('.nd-recharge');
    if (!cell) return;
    const next = Math.max(0, Number(cell.dataset.approvedTotal || 0) + delta);
    cell.dataset.approvedTotal = String(next);
    cell.textContent = `₹${next.toFixed(2)}`;
    const accessCell = row.querySelector('.admin-game-access');
    if (accessCell) {
      const enabled = accessCell.querySelector('.nd-access-btn')?.classList.contains('enabled') || false;
      accessCell.innerHTML = this.renderUserAccessCell(userId, next, enabled);
    }
    const record = ((this.adminData || {}).users || []).find(u => u.id === userId);
    if (record) record.approved_deposit_total = next;
  }

  setUserAccessCell(userId, accessEnabled) {
    const row = this.findUserRow(userId);
    const accessCell = row?.querySelector('.admin-game-access');
    const approved = Number(row?.querySelector('.nd-recharge')?.dataset.approvedTotal || 0);
    if (accessCell) accessCell.innerHTML = this.renderUserAccessCell(userId, approved, accessEnabled);
    const record = ((this.adminData || {}).users || []).find(u => u.id === userId);
    if (record) record.game_access_enabled = accessEnabled ? 1 : 0;
  }

  refreshAdminMetricsFast() {
    void this.loadAdmin({ silent: true });
  }

  // Holds off background repaints while a local change is being confirmed.
  async adminMutate(work) {
    this.adminMutationsInFlight += 1;
    try {
      return await work();
    } finally {
      this.adminMutationsInFlight -= 1;
    }
  }

  attachAdminListeners() {
    const gate = document.getElementById('admin-unlock-overlay');
    gate?.querySelectorAll('.nd-gate-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        gate.querySelectorAll('.nd-gate-tab').forEach(t => t.classList.toggle('active', t === tab));
        gate.querySelectorAll('.nd-gate-form').forEach(form => {
          form.classList.toggle('active', form.dataset.gatePanel === tab.dataset.gate);
        });
      });
    });

    const persist = (storageKey, value) => {
      const remember = document.getElementById('admin-remember')?.checked !== false;
      (remember ? localStorage : sessionStorage).setItem(storageKey, value);
    };

    document.getElementById('admin-login-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      const button = event.currentTarget.querySelector('button[type="submit"]');
      const phone = (document.getElementById('admin-login-phone')?.value || '').replace(/\D/g, '').slice(-10);
      const password = document.getElementById('admin-login-password')?.value || '';
      if (phone.length !== 10 || !password) {
        this.showAdminGate('Enter your 10-digit phone number and password.');
        return;
      }
      button.disabled = true;
      try {
        let result;
        let lastError;
        for (const candidate of [phone, `+91${phone}`, `91${phone}`]) {
          try {
            result = await this.adminApi('/api/admin/login', 'POST', { phone: candidate, password });
            break;
          } catch (error) {
            lastError = error;
            if (!/invalid phone or password/i.test(error.message)) throw error;
          }
        }
        if (!result) throw lastError || new Error('Invalid phone or password!');
        this.adminToken = result.token;
        persist('PREDICT_ADMIN_TOKEN', result.token);
        this.hideAdminGate();
        await this.loadAdmin();
        this.showToast(`Welcome, ${result.admin?.name || 'Admin'}.`, 'success');
      } catch (error) {
        const notFound = /404|not found/i.test(error.message);
        this.showAdminGate(notFound
          ? 'Password sign-in is not enabled on the server yet. Use the access key tab.'
          : error.message);
      } finally {
        button.disabled = false;
      }
    });

    document.getElementById('admin-unlock-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      const input = document.getElementById('admin-access-key');
      const button = event.currentTarget.querySelector('button[type="submit"]');
      const key = input?.value?.trim() || '';
      if (!key) {
        this.showAdminGate('Admin access key is required.');
        input?.focus();
        return;
      }
      button.disabled = true;
      this.adminApiKey = key;
      this.adminToken = '';
      try {
        await this.adminApi('/api/admin/metrics');
        persist('PREDICT_ADMIN_API_KEY', key);
        this.hideAdminGate();
        if (input) input.value = '';
        await this.loadAdmin();
        this.showToast('Dashboard connected.', 'success');
      } catch (error) {
        this.adminApiKey = '';
        this.showAdminGate(/invalid admin/i.test(error.message) ? 'That access key is not valid.' : error.message);
        input?.focus();
      } finally {
        button.disabled = false;
      }
    });

    document.getElementById('admin-refresh')?.addEventListener('click', () => {
      void this.loadAdmin();
    });
    document.getElementById('admin-lock')?.addEventListener('click', () => {
      this.lockAdmin();
      this.showToast('Signed out of admin.', 'success');
    });

    document.querySelectorAll('.nd-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        // Matched on the section, not on the clicked element: the same
        // section appears twice in the DOM -- once in the desktop rail,
        // once in the mobile bottom bar -- and marking only the button
        // that was clicked would leave the other shell highlighting the
        // wrong tab after a rotate or resize.
        document.querySelectorAll('.nd-tab').forEach(t =>
          t.classList.toggle('active', t.dataset.section === tab.dataset.section));
        document.querySelectorAll('.nd-section').forEach(section => {
          section.classList.toggle('active', section.id === `nd-section-${tab.dataset.section}`);
        });
        // Referrals are their own endpoint, not part of the dashboard payload,
        // so they load when the tab is opened rather than on every refresh.
        if (tab.dataset.section === 'referrals') void this.loadAdminReferrals();
        if (tab.dataset.section === 'team') void this.loadTeam();
        if (tab.dataset.section === 'security') { void this.loadAppInfo(); void this.loadDeployState(); }
      });
    });

    document.getElementById('btn-share-app')?.addEventListener('click', async () => {
      // Share the /download landing page, not the raw .apk, so the recipient
      // gets install instructions rather than a file their browser may block.
      const url = `${window.location.origin}/download`;
      const shareData = { title: 'Club 8 App', text: 'Download the Club 8 Android app:', url };
      try {
        if (navigator.share) {
          await navigator.share(shareData);
        } else {
          await navigator.clipboard.writeText(url);
          this.showToast('Download link copied — ab kisi ko bhi bhej do.', 'success');
        }
      } catch (error) {
        // User dismissing the share sheet lands here too; only toast on a real
        // copy fallback failure.
        if (error?.name !== 'AbortError') this.showToast(`Download link: ${url}`, 'success');
      }
    });

    document.getElementById('admin-credentials-form')?.addEventListener('submit', e => {
      e.preventDefault();
      void this.submitAdminCredentials();
    });
    document.getElementById('admin-key-form')?.addEventListener('submit', e => {
      e.preventDefault();
      void this.submitAdminKeyRotation();
    });
    document.getElementById('admin-commit-push')?.addEventListener('click', () => {
      void this.commitAndDeploy();
    });
    document.getElementById('admin-team-form')?.addEventListener('submit', e => {
      e.preventDefault();
      void this.createTeamUser();
    });

    // The queue filter and the games range share the .nd-seg-btn look, so each
    // handler only reacts to the data attribute it owns.
    document.querySelectorAll('.nd-seg-btn[data-queue]').forEach(button => {
      button.addEventListener('click', () => {
        document.querySelectorAll('.nd-seg-btn[data-queue]')
          .forEach(b => b.classList.toggle('active', b === button));
        this.adminQueueFilter = button.dataset.queue;
        this.renderAdminQueue();
      });
    });

    document.querySelectorAll('.nd-seg-btn.ng-range').forEach(button => {
      button.addEventListener('click', () => {
        document.querySelectorAll('.nd-seg-btn.ng-range')
          .forEach(b => b.classList.toggle('active', b === button));
        this.adminGamesRange = Number(button.dataset.days);
        this.loadAdminGames();
      });
    });

    document.getElementById('ng-lottery-date')?.addEventListener('change', () => this.loadAdminLottery());

    document.getElementById('nd-flush-run')?.addEventListener('click', () => this.runFlush());

    document.querySelectorAll('.nd-seg-btn.nv-range').forEach(button => {
      button.addEventListener('click', () => {
        document.querySelectorAll('.nd-seg-btn.nv-range')
          .forEach(b => b.classList.toggle('active', b === button));
        this.adminVisitorHours = Number(button.dataset.hours);
        this.loadAdminVisitors();
      });
    });

    document.querySelectorAll('.nd-seg-btn.nv-filter').forEach(button => {
      button.addEventListener('click', () => {
        document.querySelectorAll('.nd-seg-btn.nv-filter')
          .forEach(b => b.classList.toggle('active', b === button));
        this.adminVisitorOutcome = button.dataset.outcome;
        this.loadAdminVisitors();
      });
    });

    document.getElementById('nv-timeline-close')?.addEventListener('click', () => {
      document.getElementById('nv-timeline-panel').hidden = true;
    });

    document.querySelectorAll('.nd-seg-btn.ng-mode').forEach(button => {
      button.addEventListener('click', () => {
        document.querySelectorAll('.nd-seg-btn.ng-mode')
          .forEach(b => b.classList.toggle('active', b === button));
        document.getElementById('ng-manual-block').hidden = button.dataset.mode !== 'manual';
      });
    });

    document.getElementById('ng-ctl-bias')?.addEventListener('input', event => {
      document.getElementById('ng-bias-value').textContent = `${event.target.value}%`;
    });

    document.getElementById('ng-controls-form')?.addEventListener('submit', event => {
      event.preventDefault();
      this.saveAdminGameControls();
    });

    document.getElementById('ng-draw-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      const input = document.getElementById('ng-winning-ticket');
      const winning = Number(input.value);
      if (!Number.isInteger(winning) || winning < 0 || winning > 99) {
        return this.showToast('Winning ticket 00-99 ke beech hona chahiye.', 'error');
      }
      try {
        const result = await this.adminApi('/api/admin/lottery/draw', 'POST', {
          draw_date: document.getElementById('ng-lottery-date')?.value || null,
          winning_ticket: winning
        });
        this.showToast(result.winners.length
          ? `Winner: ${result.winners.map(w => w.user_name).join(', ')}`
          : 'Is number pe koi approved ticket nahi tha.', 'success');
      } catch (error) {
        this.showToast(error.message, 'error');
      }
      this.loadAdminLottery();
    });

    document.getElementById('nd-user-search')?.addEventListener('input', () => {
      this.applyAdminSearch('nd-user-search', 'admin-users-table-body');
    });
    document.getElementById('nd-history-search')?.addEventListener('input', () => {
      this.applyAdminSearch('nd-history-search', 'nd-history-body');
    });

    document.querySelectorAll('.nd-mode').forEach(button => {
      button.addEventListener('click', async () => {
        const mode = button.dataset.mode;
        document.querySelectorAll('.nd-mode').forEach(b => b.classList.toggle('active', b === button));
        try {
          await this.adminApi('/api/admin/prediction-mode', 'POST', { mode });
          this.showToast(`Result mode set to ${mode.replace('_', ' ')}.`, 'success');
        } catch (error) {
          this.showToast(error.message, 'error');
        }
      });
    });

    document.getElementById('nd-force-apply')?.addEventListener('click', async () => {
      const input = document.getElementById('nd-force-number');
      const number = parseInt(input?.value, 10);
      if (!Number.isInteger(number) || number < 0 || number > 9) {
        this.showToast('Enter a number between 0 and 9.', 'error');
        return;
      }
      try {
        await this.adminApi('/api/admin/force-result', 'POST', { number });
        this.showToast(`Next result forced to ${number}.`, 'success');
        if (input) input.value = '';
        void this.loadAdmin({ silent: true });
      } catch (error) {
        this.showToast(error.message, 'error');
      }
    });

    document.getElementById('admin-payment-settings')?.addEventListener('submit', async event => {
      event.preventDefault();
      const button = event.currentTarget.querySelector('button[type="submit"]');
      button.disabled = true;
      try {
        await this.adminApi('/api/admin/platform-settings', 'PUT', {
          deposits_enabled: document.getElementById('admin-deposits-enabled')?.checked,
          withdrawals_enabled: document.getElementById('admin-withdrawals-enabled')?.checked,
          withdrawal_min: Number(document.getElementById('admin-withdrawal-min')?.value || 200)
        });
        this.showToast('Payment settings saved.', 'success');
      } catch (error) {
        this.showToast(error.message, 'error');
      } finally {
        button.disabled = false;
      }
    });

    document.getElementById('admin-qr-upload-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      const form = event.currentTarget;
      const button = form.querySelector('button[type="submit"]');
      const file = document.getElementById('admin-qr-file')?.files?.[0];
      if (!file) {
        this.showToast('Choose a QR image first.', 'error');
        return;
      }
      const payload = new FormData();
      payload.append('name', document.getElementById('admin-qr-name')?.value || 'UPI QR');
      payload.append('upi_id', document.getElementById('admin-qr-upi')?.value || '');
      payload.append('min_amount', document.getElementById('admin-qr-min')?.value || '100');
      payload.append('max_amount', document.getElementById('admin-qr-max')?.value || '50000');
      payload.append('qr_file', file);
      button.disabled = true;
      try {
        const headers = this.adminHeaders();
        delete headers['Content-Type'];   // let the browser set the multipart boundary
        const response = await fetch(`${this.apiBaseUrl}/api/admin/qr-codes/upload`, { method: 'POST', headers, body: payload });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || 'QR upload failed');
        this.showToast('QR uploaded and set active.', 'success');
        form.reset();
        void this.loadAdmin({ silent: true });
        void this.syncActiveQR();
      } catch (error) {
        this.showToast(error.message, 'error');
      } finally {
        button.disabled = false;
      }
    });

    document.getElementById('admin-app-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      const form = event.currentTarget;
      const button = form.querySelector('button[type="submit"]');
      const file = document.getElementById('admin-app-file')?.files?.[0];
      if (!file) return this.showToast('Choose an APK file first.', 'error');
      if (!file.name.toLowerCase().endsWith('.apk')) return this.showToast('That is not a .apk file.', 'error');

      const payload = new FormData();
      payload.append('version', document.getElementById('admin-app-version')?.value || '');
      payload.append('apk_file', file);

      const progress = document.getElementById('admin-app-progress');
      button.disabled = true;
      if (progress) progress.hidden = false;
      try {
        const headers = this.adminHeaders();
        delete headers['Content-Type'];
        const response = await fetch(`${this.apiBaseUrl}/api/admin/app/upload`, { method: 'POST', headers, body: payload });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || 'APK upload failed');
        this.showToast(`App uploaded (${(result.size_bytes / 1048576).toFixed(1)} MB). Download is live.`, 'success');
        form.reset();
        void this.loadAppInfo();
      } catch (error) {
        this.showToast(error.message, 'error');
      } finally {
        button.disabled = false;
        if (progress) progress.hidden = true;
      }
    });

    document.getElementById('admin-app-delete')?.addEventListener('click', async () => {
      if (!window.confirm('Remove the current app? The download button will hide until you upload again.')) return;
      try {
        await this.adminApi('/api/admin/app', 'DELETE');
        this.showToast('App removed.', 'success');
        void this.loadAppInfo();
      } catch (error) {
        this.showToast(error.message, 'error');
      }
    });
  }

  /** Refresh the admin app card and the public download button from /api/app/info. */
  async loadAppInfo() {
    let info;
    try {
      info = await this.fetchApi('/api/app/info');
    } catch (error) {
      return;
    }
    this.appInfo = info;

    // Admin card state (only present on the dashboard).
    const status = document.getElementById('admin-app-status');
    const del = document.getElementById('admin-app-delete');
    if (status) {
      if (info.available) {
        const mb = (info.size_bytes / 1048576).toFixed(1);
        const when = info.uploaded_at ? new Date(info.uploaded_at).toLocaleString() : '';
        status.textContent = `Live: ${info.filename}${info.version ? ' v' + info.version : ''} · ${mb} MB · ${when}`;
      } else {
        status.textContent = 'No app uploaded yet.';
      }
    }
    if (del) del.hidden = !info.available;

    // Public download entry points: point at the backend and hide when absent.
    const href = `${this.apiBaseUrl}/api/app/download`;
    const link = document.getElementById('app-download-link');
    if (link) {
      link.href = href;
      link.style.display = info.available ? '' : 'none';
    }
    const share = document.getElementById('btn-share-app');
    if (share) share.style.display = info.available ? '' : 'none';
  }

  attachEventListeners() {

    // Sound Toggle
    document.getElementById('toggle-sound')?.addEventListener('click', () => {
      sound.enabled = !sound.enabled;
      this.showToast(sound.enabled ? 'Sound FX Enabled' : 'Sound FX Muted', 'success');
    });

    document.getElementById('header-back')?.addEventListener('click', () => {
      this.goBack();
    });

    // Room Tabs
    document.querySelectorAll('.room-tab').forEach(tab => {
      tab.addEventListener('click', (e) => {
        const room = e.currentTarget.dataset.room;
        if (room) {
          const state = appState.getState();
          state.activeRoom = room;
          document.querySelectorAll('.room-tab').forEach(t => t.classList.toggle('active', t === e.currentTarget));
          const roomLabel = e.currentTarget.querySelector('.time-sub')?.textContent?.trim() || '';
          const title = document.getElementById('active-room-title');
          if (title) title.innerText = `WinGo ${roomLabel}`;
          this.historyPage = 0;
          appState.saveState();
          this.syncGameStatus();
        }
      });
    });

    // Bottom Nav Sub-Pages
    document.querySelectorAll('.nav-item').forEach(nav => {
      nav.addEventListener('click', (e) => {
        const page = e.currentTarget.dataset.page;
        if (page === 'bonus') {
          this.showClubBonus();
          return;
        }
        if (page) {
          this.switchSubPage(page, { record: false });
        }
      });
    });

    document.querySelectorAll('.deposit-methods, .history-kind-tabs, .notification-tabs').forEach(group => {
      group.querySelectorAll('button').forEach(button => {
        button.addEventListener('click', () => {
          group.querySelectorAll('button').forEach(item => item.classList.remove('active'));
          button.classList.add('active');
        });
      });
    });

    // Screenshot-style deposit controls
    const depositAmountInput = document.getElementById('deposit-amount-input');
    const depositSubmit = document.getElementById('deposit-sticky-submit');
    const updateDepositSubmit = () => {
      const amount = Number(depositAmountInput?.value || 0);
      const minimum = Number(this.activeQR?.min_amount || 100);
      const maximum = Number(this.activeQR?.max_amount || 50000);
      if (depositSubmit) {
        depositSubmit.disabled = !this.walletSettings.deposits_enabled || amount < minimum || amount > maximum;
        depositSubmit.textContent = this.walletSettings.deposits_enabled ? 'Deposit' : 'Deposits paused';
      }
    };

    document.querySelectorAll('.chip-amount').forEach(chip => {
      chip.addEventListener('click', () => {
        document.querySelectorAll('.chip-amount').forEach(item => item.classList.remove('active'));
        chip.classList.add('active');
        if (depositAmountInput) depositAmountInput.value = chip.dataset.amount || '';
        updateDepositSubmit();
      });
    });

    depositAmountInput?.addEventListener('input', () => {
      document.querySelectorAll('.chip-amount').forEach(item => {
        item.classList.toggle('active', item.dataset.amount === depositAmountInput.value);
      });
      updateDepositSubmit();
    });

    document.getElementById('clear-deposit-amount')?.addEventListener('click', () => {
      if (depositAmountInput) depositAmountInput.value = '';
      document.querySelectorAll('.chip-amount').forEach(item => item.classList.remove('active'));
      updateDepositSubmit();
    });

    document.getElementById('copy-invite-code')?.addEventListener('click', async () => {
      const code = this.referralCode
        || localStorage.getItem('PREDICT_REFERRAL_CODE')
        || document.getElementById('agency-invite-code')?.textContent?.trim() || '';
      if (!code || code === '—') return this.showToast('Referral code load ho raha hai, ek pal ruko.', 'error');
      try {
        await navigator.clipboard.writeText(code);
        this.showToast('Referral code copied', 'success');
      } catch {
        this.showToast(`Your referral code: ${code}`, 'success');
      }
    });

    const depositMethodNames = ['Phonepe_QR', 'Innate UPI-QR', 'Expert Paytm-QR', 'UPI-QR PAY', 'USDT', 'ARPay'];
    document.querySelectorAll('.deposit-methods button').forEach((button, index) => {
      button.addEventListener('click', () => {
        const method = index === 0 && this.activeQR?.name
          ? this.activeQR.name
          : (depositMethodNames[index] || 'Phonepe_QR');
        const methodLabel = document.getElementById('selected-recharge-method');
        const channel = document.querySelector('.deposit-channel-card .selected-channel');
        if (methodLabel) methodLabel.textContent = method;
        if (channel) channel.innerHTML = `${method}<br><small>Balance:100 - 50K</small>`;
      });
    });
    updateDepositSubmit();

    const closeDepositPayment = () => {
      const modal = document.getElementById('deposit-payment-modal');
      modal?.classList.remove('active');
      modal?.setAttribute('aria-hidden', 'true');
      document.body.style.overflow = '';
    };
    document.getElementById('close-deposit-payment')?.addEventListener('click', closeDepositPayment);
    document.querySelector('.deposit-payment-backdrop')?.addEventListener('click', closeDepositPayment);
    document.getElementById('deposit-payment-utr')?.addEventListener('input', (e) => {
      e.currentTarget.value = e.currentTarget.value.replace(/\D/g, '').slice(0, 12);
      e.currentTarget.closest('.deposit-utr-field')?.classList.remove('invalid');
    });

    document.getElementById('confirm-deposit-paid')?.addEventListener('click', async (e) => {
      if (!this.pendingDeposit) return;
      if (!this.authToken) {
        closeDepositPayment();
        this.showToast('Please log in before submitting a payment.', 'error');
        this.switchSubPage('auth');
        return;
      }
      const utrInput = document.getElementById('deposit-payment-utr');
      const utr = utrInput?.value?.trim() || '';
      if (!/^\d{12}$/.test(utr)) {
        utrInput?.closest('.deposit-utr-field')?.classList.add('invalid');
        utrInput?.focus();
        return this.showToast('Please enter the valid 12-digit UTR number.', 'error');
      }
      const button = e.currentTarget;
      const pending = this.pendingDeposit;
      button.disabled = true;

      // Close and confirm straight away — the request is already valid at this
      // point, so making the player watch a spinner adds nothing.
      this.showToast('Payment submitted. Waiting for Admin approval.', 'success');
      document.getElementById('form-deposit')?.reset();
      if (utrInput) utrInput.value = '';
      document.querySelectorAll('.chip-amount').forEach(item => item.classList.remove('active'));
      updateDepositSubmit();
      this.pendingDeposit = null;
      closeDepositPayment();

      try {
        await this.fetchApi('/api/wallet/deposit', 'POST', {
          amount: pending.amount,
          utr,
          qr_id: pending.qr.id,
          order_id: pending.orderId
        });
        void this.syncWalletHistory();
      } catch (err) {
        this.showToast(err.message, 'error');
      } finally {
        button.disabled = false;
      }
    });


    // Color Bets
    document.querySelectorAll('.btn-color').forEach(btn => {
      btn.addEventListener('click', (e) => {
        this.openBetModal('color', e.currentTarget.dataset.color);
      });
    });

    // Number Bets
    document.querySelectorAll('.btn-number').forEach(btn => {
      btn.addEventListener('click', (e) => {
        this.openBetModal('number', e.currentTarget.dataset.number);
      });
    });

    // Big/Small Bets
    document.querySelectorAll('.btn-big-small').forEach(btn => {
      btn.addEventListener('click', (e) => {
        this.openBetModal('size', e.currentTarget.dataset.size);
      });
    });

    // Multipliers
    document.querySelectorAll('.mult-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        document.querySelectorAll('.mult-btn').forEach(b => b.classList.remove('active'));
        e.currentTarget.classList.add('active');
        this.selectedContractMultiplier = parseInt(e.currentTarget.dataset.mult, 10) || 1;
        this.updateBetModalTotal();
      });
    });

    document.querySelectorAll('.balance-chip').forEach(btn => {
      btn.addEventListener('click', (e) => {
        document.querySelectorAll('.balance-chip').forEach(b => b.classList.remove('active'));
        e.currentTarget.classList.add('active');
        this.selectedContractBase = parseInt(e.currentTarget.dataset.base, 10) || 1;
        this.updateBetModalTotal();
      });
    });

    document.getElementById('quantity-minus')?.addEventListener('click', () => {
      this.selectedQuantity = Math.max(1, this.selectedQuantity - 1);
      this.updateBetModalTotal();
    });

    document.getElementById('quantity-plus')?.addEventListener('click', () => {
      this.selectedQuantity = Math.min(999, this.selectedQuantity + 1);
      this.updateBetModalTotal();
    });

    document.querySelectorAll('.quick-mult').forEach(btn => {
      btn.addEventListener('click', (e) => {
        document.querySelectorAll('.quick-mult').forEach(b => b.classList.remove('active'));
        e.currentTarget.classList.add('active');
        this.selectedContractMultiplier = parseInt(e.currentTarget.dataset.mult, 10) || 1;
      });
    });

    document.getElementById('random-number')?.addEventListener('click', () => {
      const number = Math.floor(Math.random() * 10);
      this.openBetModal('number', String(number));
    });

    const rulesModal = document.getElementById('how-play-modal');
    document.getElementById('btn-how-play')?.addEventListener('click', () => {
      rulesModal?.classList.add('active');
      rulesModal?.setAttribute('aria-hidden', 'false');
    });
    document.getElementById('btn-close-rules')?.addEventListener('click', () => {
      rulesModal?.classList.remove('active');
      rulesModal?.setAttribute('aria-hidden', 'true');
    });
    rulesModal?.addEventListener('click', (e) => {
      if (e.target === rulesModal) {
        rulesModal.classList.remove('active');
        rulesModal.setAttribute('aria-hidden', 'true');
      }
    });

    document.querySelectorAll('.history-tab').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const selected = e.currentTarget.dataset.historyTab;
        document.querySelectorAll('.history-tab').forEach(b => b.classList.toggle('active', b === e.currentTarget));
        document.querySelectorAll('.history-panel').forEach(panel => panel.classList.remove('active'));
        document.getElementById(`history-panel-${selected}`)?.classList.add('active');
      });
    });

    document.getElementById('history-prev')?.addEventListener('click', () => {
      if (this.historyPage > 0) {
        this.historyPage -= 1;
        this.renderGameHistory(appState.getState());
      }
    });

    document.getElementById('history-next')?.addEventListener('click', () => {
      const displayHistory = this.buildDisplayHistory(appState.getState().history);
      const totalPages = Math.max(1, Math.ceil(displayHistory.length / this.historyPageSize));
      if (this.historyPage < totalPages - 1) {
        this.historyPage += 1;
        this.renderGameHistory(appState.getState());
      }
    });

    // Confirm Bet
    document.getElementById('btn-confirm-bet')?.addEventListener('click', () => {
      this.placeBet();
    });

    document.getElementById('btn-close-modal')?.addEventListener('click', () => {
      this.closeBetModal();
    });

    document.querySelectorAll('[data-toggle-password]').forEach(button => {
      button.addEventListener('click', () => {
        const input = document.getElementById(button.dataset.togglePassword);
        if (!input) return;
        input.type = input.type === 'password' ? 'text' : 'password';
        button.innerHTML = `<i class="bi ${input.type === 'password' ? 'bi-eye-slash' : 'bi-eye'}"></i>`;
      });
    });

    document.getElementById('send-reg-otp')?.addEventListener('click', () => {
      const phoneInput = document.getElementById('reg-phone');
      const phone = (phoneInput?.value || '').replace(/\D/g, '').slice(-10);
      if (phone.length !== 10) {
        phoneInput?.focus();
        return this.showToast('Please enter a valid 10-digit phone number.', 'error');
      }
      const random = crypto.getRandomValues(new Uint32Array(1))[0] % 1000000;
      this.generatedOtp = String(random).padStart(6, '0');
      const otpInput = document.getElementById('reg-otp');
      const note = document.getElementById('generated-otp-note');
      if (otpInput) otpInput.value = this.generatedOtp;
      if (note) {
        note.hidden = false;
        note.textContent = `Verification code ${this.generatedOtp} generated and filled automatically.`;
      }
      this.showToast(`Code ${this.generatedOtp} verified automatically.`, 'success');
    });

    document.getElementById('cancel-game-access')?.addEventListener('click', () => this.closeGameAccessModal());
    document.querySelector('.game-access-backdrop')?.addEventListener('click', () => this.closeGameAccessModal());
    document.getElementById('confirm-game-access')?.addEventListener('click', () => {
      this.closeGameAccessModal();
      this.switchSubPage('recharge');
    });

    // Login Form Submit
    document.getElementById('form-login')?.addEventListener('submit', async (e) => {
      e.preventDefault();
      const phone = (document.getElementById('login-phone')?.value || '').replace(/\D/g, '').slice(-10);
      const password = document.getElementById('login-password')?.value;

      this.tracker?.event('login_submit');
      try {
        if (phone.length !== 10) throw new Error('Please enter a valid 10-digit phone number.');
        let res;
        let lastError;
        for (const candidate of [phone, `+91${phone}`, `91${phone}`]) {
          try {
            res = await this.fetchApi('/api/auth/login', 'POST', { phone: candidate, password });
            break;
          } catch (error) {
            lastError = error;
            if (!/invalid phone or password/i.test(error.message)) throw error;
          }
        }
        if (!res) throw lastError || new Error('Invalid phone or password!');
        this.authToken = res.token;
        localStorage.setItem('PREDICT_AUTH_TOKEN', res.token);
        this.tracker?.event('login_success');
        this.tracker?.identify(res.user?.id);
        await this.syncUserAccess();
        this.showToast(`Welcome back, ${res.user.name}!`, 'success');
        this.switchSubPage('home', { record: false });
      } catch (err) {
        // The reason matters: "wrong password" and "invalid phone" are very
        // different stories about why people are not getting in.
        this.tracker?.event('login_failed', { reason: err.message.slice(0, 120) });
        this.showToast(err.message, 'error');
      }
    });

    // Register Form Submit
    document.getElementById('form-register')?.addEventListener('submit', async (e) => {
      e.preventDefault();
      const phone = (document.getElementById('reg-phone')?.value || '').replace(/\D/g, '').slice(-10);
      const username = `Member${phone.slice(-4)}`;
      const password = document.getElementById('reg-password')?.value;
      const confirmPassword = document.getElementById('reg-confirm-password')?.value;
      const otp = document.getElementById('reg-otp')?.value;
      const ref = document.getElementById('reg-referral')?.value;

      this.tracker?.event('register_submit', { has_referral: Boolean(ref) });
      try {
        if (phone.length !== 10) throw new Error('Please enter a valid 10-digit phone number.');
        if (!this.generatedOtp || otp !== this.generatedOtp) throw new Error('Send and verify the 6-digit code first.');
        if (password !== confirmPassword) throw new Error('Passwords do not match.');
        const res = await this.fetchApi('/api/auth/register', 'POST', { username, phone, password, referral_code: ref });
        this.authToken = res.token;
        localStorage.setItem('PREDICT_AUTH_TOKEN', res.token);
        this.tracker?.event('register_success');
        this.tracker?.identify(res.user?.id);
        this.gameAccessEnabled = false;
        this.approvedDepositTotal = 0;
        await this.syncUserAccess();
        this.showToast(`Account created! ₹100 signup bonus added for WinGo.`, 'success');
        this.switchSubPage('home', { record: false });
      } catch (err) {
        // Records the drop-offs that never reach the server at all: a failed
        // OTP or a password mismatch throws before the register call.
        this.tracker?.event('register_failed', { reason: err.message.slice(0, 120) });
        this.showToast(err.message, 'error');
      }
    });

    // Logout
    document.getElementById('btn-logout-user')?.addEventListener('click', () => {
      this.authToken = null;
      this.gameAccessEnabled = false;
      this.approvedDepositTotal = 0;
      localStorage.removeItem('PREDICT_AUTH_TOKEN');
      this.showToast('Logged out successfully', 'success');
      this.switchSubPage('auth');
    });

    // Deposit Submit
    const openDepositPayment = async (e) => {
      e?.preventDefault();
      if (!this.authToken) {
        this.showToast('Please log in before creating a deposit order.', 'error');
        this.switchSubPage('auth');
        return;
      }
      const amount = Number(document.getElementById('deposit-amount-input')?.value);

      // Ask the server for the next QR in the rotation. Abandoning an order and
      // starting again therefore lands on a different account each time.
      let qr = null;
      let orderId = null;
      try {
        const order = await this.fetchApi('/api/wallet/deposit-order', 'POST', { amount });
        qr = order.qr;
        orderId = order.order_id;
      } catch (error) {
        if (!/404|not found/i.test(error.message)) {
          return this.showToast(error.message, 'error');
        }
        // Older backend without rotation — fall back to the single active QR.
        if (!this.activeQR?.qr_url) await this.syncActiveQR();
        qr = this.activeQR;
        const randomPart = crypto.getRandomValues(new Uint32Array(1))[0].toString(36).toUpperCase().slice(-4).padStart(4, '0');
        orderId = `ORD${String(Date.now()).slice(-8)}${randomPart}`;
      }

      if (!qr?.qr_url) return this.showToast('Admin has not enabled a deposit QR yet.', 'error');
      const minimum = Number(qr.min_amount || 100);
      const maximum = Number(qr.max_amount || 50000);
      if (amount < minimum || amount > maximum) {
        return this.showToast(`Enter an amount between ₹${minimum} and ₹${maximum}.`, 'error');
      }

      this.pendingDeposit = { amount, qr, orderId };
      document.getElementById('deposit-payment-amount').innerText = `₹${amount.toFixed(2)}`;

      // Always the image the admin uploaded. A QR generated here from the UPI
      // id is an unsigned intent with the amount baked in, and UPI apps
      // decline those for merchant-style payments -- that is the "transaction
      // failed" players were hitting. A PSP-issued QR is signed by the bank,
      // so it is the only one that reliably completes.
      const paymentQrUrl = this.resolveApiUrl(qr.qr_url);

      // The deep link still carries the amount and order reference, which the
      // static QR cannot. It is offered alongside, not instead: if it fails on
      // a given app the player can still scan the image.
      const intentLink = document.getElementById('deposit-upi-intent');
      if (intentLink) {
        if (qr.upi_id) {
          intentLink.href = `upi://pay?pa=${encodeURIComponent(qr.upi_id)}`
            + `&pn=${encodeURIComponent(qr.name || 'Club 8')}`
            + `&am=${amount.toFixed(2)}&cu=INR&tr=${orderId}&tn=${orderId}`;
          intentLink.hidden = false;
        } else {
          intentLink.hidden = true;
        }
      }
      document.getElementById('deposit-payment-qr').src = paymentQrUrl;
      document.getElementById('deposit-payment-order').innerText = orderId;
      const utrInput = document.getElementById('deposit-payment-utr');
      if (utrInput) utrInput.value = '';
      utrInput?.closest('.deposit-utr-field')?.classList.remove('invalid');
      const modal = document.getElementById('deposit-payment-modal');
      modal?.classList.add('active');
      modal?.setAttribute('aria-hidden', 'false');
      document.body.style.overflow = 'hidden';
    };
    document.getElementById('form-deposit')?.addEventListener('submit', openDepositPayment);
    depositSubmit?.addEventListener('click', openDepositPayment);

    // Withdraw Submit
    const submitWithdrawal = async (e) => {
      e?.preventDefault();
      if (!this.authToken) {
        this.showToast('Please log in before requesting a withdrawal.', 'error');
        this.switchSubPage('auth');
        return;
      }
      const amount = Number(document.getElementById('withdraw-amount-input')?.value);
      let upi_id = document.getElementById('withdraw-upi-input')?.value?.trim() || '';

      if (this.withdrawMethod === 'bank') {
        if (!this.bankAccount?.account) {
          this.showToast('Please add a bank account first.', 'error');
          this.populateBankForm();
          this.switchSubPage('bank-account-add');
          return;
        }
        upi_id = [
          'BANK',
          this.bankAccount.bank,
          this.bankAccount.recipient,
          `A/C ${this.bankAccount.account}`,
          `IFSC ${this.bankAccount.ifsc}`,
          `PHONE ${this.bankAccount.phone}`
        ].join(' | ');
      } else {
        upi_id = upi_id || this.upiAccount?.upiId || '';
        if (!upi_id) {
          this.showToast('Please add a UPI payment method first.', 'error');
          this.renderUpiAccount();
          this.switchSubPage('upi-methods');
          return;
        }
      }

      try {
        await this.fetchApi('/api/wallet/withdraw', 'POST', { amount, upi_id });
        this.showToast(`${this.withdrawMethod === 'bank' ? 'Bank' : 'UPI'} withdrawal request submitted!`, 'success');
        document.getElementById('form-withdrawal')?.reset();
        void this.syncGameStatus();
        void this.syncWalletHistory();
      } catch (err) {
        this.showToast(err.message, 'error');
      }
    };
    document.getElementById('form-withdrawal')?.addEventListener('submit', submitWithdrawal);
    document.getElementById('withdraw-submit-button')?.addEventListener('click', submitWithdrawal);

    // Admin Add QR Form Submit
  }

  openBetModal(selectType, selection) {
    const state = appState.getState();
    const roomState = state.rounds[state.activeRoom];
    if (roomState?.isFrozen || Number(roomState?.timeRemaining) <= 5) {
      this.showToast('Betting is locked for the final 5 seconds.', 'error');
      return;
    }

    this.currentBetSelection = { selectType, selection };
    this.selectedContractBase = 1;
    this.selectedContractMultiplier = 1;
    this.selectedQuantity = 1;

    const modal = document.getElementById('bet-modal');
    const title = document.getElementById('modal-selection-title');
    const roomLabel = document.getElementById('modal-room-label');
    const activeRoomLabel = document.querySelector('.room-tab.active .time-sub')?.textContent?.trim() || '30sec';
    if (roomLabel) roomLabel.innerText = `WinGo ${activeRoomLabel}`;

    const displaySelection = String(selection).charAt(0).toUpperCase() + String(selection).slice(1);
    if (title) title.innerText = `Select ${displaySelection}`;

    const sheet = modal?.querySelector('.bet-contract-sheet');
    if (sheet) {
      sheet.classList.remove('accent-green', 'accent-red', 'accent-violet', 'accent-orange', 'accent-blue');
      sheet.classList.add(`accent-${this.getSelectionAccent(selectType, selection)}`);
    }

    document.querySelectorAll('.mult-btn').forEach(b => {
      b.classList.toggle('active', Number(b.dataset.mult) === 1);
    });
    document.querySelectorAll('.balance-chip').forEach(b => {
      b.classList.toggle('active', Number(b.dataset.base) === 1);
    });
    const agreement = document.getElementById('contract-agreement');
    if (agreement) agreement.checked = true;

    this.updateBetModalTotal();
    modal?.classList.add('active');
  }

  getSelectionAccent(selectType, selection) {
    if (selectType === 'size') return selection === 'Big' ? 'orange' : 'blue';
    if (selectType === 'color') return selection;
    const number = Number(selection);
    if (number === 0 || number === 5) return 'violet';
    return number % 2 === 0 ? 'red' : 'green';
  }

  closeBetModal() {
    document.getElementById('bet-modal')?.classList.remove('active');
    this.currentBetSelection = null;
  }

  updateBetModalTotal() {
    const total = this.selectedContractBase * this.selectedQuantity * this.selectedContractMultiplier;
    const totalEl = document.getElementById('modal-total-stake');
    if (totalEl) totalEl.innerText = `₹${total.toFixed(2)}`;
    const quantityEl = document.getElementById('modal-quantity');
    if (quantityEl) quantityEl.innerText = String(this.selectedQuantity);
    const returnEl = document.getElementById('modal-potential-return');
    if (returnEl) returnEl.innerText = `₹${(total * this.getCurrentPayoutRate()).toFixed(2)}`;
  }

  getCurrentPayoutRate() {
    if (!this.currentBetSelection) return 1.96;
    if (this.currentBetSelection.selectType === 'number') return 8.82;
    if (
      this.currentBetSelection.selectType === 'color' &&
      this.currentBetSelection.selection === 'violet'
    ) return 4.5;
    return 1.96;
  }

  async placeBet() {
    if (!this.currentBetSelection) return;

    const state = appState.getState();
    const agreement = document.getElementById('contract-agreement');
    if (agreement && !agreement.checked) {
      this.showToast('Please agree to the pre-sale rules.', 'error');
      return;
    }

    const betAmount = this.selectedContractBase * this.selectedQuantity;
    const totalStake = betAmount * this.selectedContractMultiplier;
    const potentialReturn = totalStake * this.getCurrentPayoutRate();
    try {
      await this.fetchApi('/api/game/bet', 'POST', {
        select_type: this.currentBetSelection.selectType,
        selection: this.currentBetSelection.selection,
        amount: betAmount,
        multiplier: this.selectedContractMultiplier,
        room: state.activeRoom,
        period: state.rounds[state.activeRoom]?.currentPeriod
      });

      sound.playBetPlaced();
      this.showToast(
        `Bet placed: ₹${totalStake}. Possible return ₹${potentialReturn.toFixed(2)}.`,
        'success'
      );
      this.closeBetModal();
      void this.syncGameStatus();
    } catch (err) {
      this.showToast(err.message, 'error');
    }
  }

  switchSubPage(pageId, options = {}) {
    const { record = true } = options;
    const target = document.getElementById(`page-${pageId}`);
    if (!target) return;

    // Central gate: deep links like /game and every in-app navigation honour the
    // single admin access switch.
    if (this.isPremiumGamePage(pageId) && !this.canEnterPremiumGames()) {
      this.showGameAccessModal();
      return;
    }

    const activeId = document.querySelector('#user-app-view .sub-page.active')?.id?.replace('page-', '');
    if (activeId) this.pageScrollPositions[activeId] = window.scrollY;
    if (record && activeId && activeId !== pageId && activeId !== 'auth') {
      this.pageStack.push(activeId);
      if (this.pageStack.length > 20) this.pageStack.shift();
    }

    this.currentPage = pageId;
    document.body.dataset.clubPage = pageId;
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelector(`.nav-item[data-page="${pageId}"]`)?.classList.add('active');

    document.querySelectorAll('.sub-page').forEach(p => p.classList.remove('active'));
    target.classList.add('active');

    // This app never changes URL, so every screen change has to be reported
    // by hand -- otherwise "kis page tak aaya" would always read "/".
    this.tracker?.pageView(pageId);
    if (pageId === 'auth') this.tracker?.event('auth_view');

    if (pageId === 'deposit-history' || pageId === 'withdraw-history') {
      this.syncWalletHistory();
    }
    if (pageId === 'promotion') {
      void this.loadReferrals();
    }
    // Pick up a round the server still holds, so a reload or a trip to another
    // screen never strands a debited stake.
    if (pageId === 'mines') void this.resumeMinesRound?.();
    if (pageId === 'chicken-road') void this.chickenRoadEngine?.resumeRound?.();

    const rootPages = new Set(['home', 'activity', 'promotion', 'wallet', 'profile']);
    const bottomNav = document.querySelector('.club-bottom-nav');
    bottomNav?.classList.toggle('hidden-for-detail', !rootPages.has(pageId));
    const savedTop = this.pageScrollPositions[pageId] || 0;
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => window.scrollTo({ top: savedTop, behavior: 'auto' }));
    });
  }

  goBack() {
    this.closeClubBonus();
    const fallback = this.currentPage === 'game' ? 'home' : (this.currentPage === 'home' ? 'home' : 'profile');
    const previous = this.pageStack.pop() || fallback;
    this.switchSubPage(previous, { record: false });
    if (previous === 'profile' || previous === 'activity') {
      window.setTimeout(() => this.showClubBonus(), 180);
    }
  }

  showClubBonus() {
    const modal = document.getElementById('club-bonus-modal');
    if (!modal) return;
    modal.classList.add('active');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
  }

  closeClubBonus() {
    const modal = document.getElementById('club-bonus-modal');
    if (!modal) return;
    modal.classList.remove('active');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
  }

  showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `<i class="bi ${type === 'success' ? 'bi-check-circle-fill' : 'bi-exclamation-triangle-fill'}"></i> ${message}`;
    container.appendChild(toast);

    setTimeout(() => { toast.remove(); }, 3500);
  }

  render(state) {
    const userView = document.getElementById('user-app-view');
    const adminView = document.getElementById('admin-dashboard-view');
    const playerHeader = document.getElementById('player-header');

    if (state.viewMode === 'user') {
      document.body.classList.remove('admin-mode');
      document.documentElement.classList.remove('admin-mode');
      userView?.classList.add('active');
      adminView?.classList.remove('active');
      if (playerHeader) playerHeader.style.display = 'flex';
    } else {
      document.body.classList.add('admin-mode');
      document.documentElement.classList.add('admin-mode');
      userView?.classList.remove('active');
      adminView?.classList.add('active');
      if (playerHeader) playerHeader.style.display = 'none';
    }

    const balEls = document.querySelectorAll('.user-balance-display');
    balEls.forEach(el => { el.innerText = `₹${state.user.balance.toFixed(2)}`; });
    this.aviatorEngine?.renderWallet();
    if (this.chickenRoadEngine && !this.chickenRoadEngine.busy) this.chickenRoadEngine.render();

    const activeRoomTab = document.querySelector(`.room-tab[data-room="${state.activeRoom}"]`);
    document.querySelectorAll('.room-tab').forEach(tab => {
      tab.classList.toggle('active', tab === activeRoomTab);
    });
    const activeRoomLabel = activeRoomTab?.querySelector('.time-sub')?.textContent?.trim();
    const activeRoomTitle = document.getElementById('active-room-title');
    if (activeRoomTitle && activeRoomLabel) activeRoomTitle.innerText = `WinGo ${activeRoomLabel}`;

    const roomState = state.rounds[state.activeRoom];
    if (roomState) {
      const periodEl = document.getElementById('period-id-display');
      if (periodEl) periodEl.innerText = roomState.currentPeriod;

      const timerEl = document.getElementById('countdown-timer-display');
      const closingOverlay = document.getElementById('closing-countdown-overlay');
      const closingTens = document.getElementById('closing-countdown-tens');
      const closingOnes = document.getElementById('closing-countdown-ones');
      const bettingSection = document.querySelector('#page-game .betting-section');
      if (timerEl) {
        const secs = roomState.timeRemaining;
        const minsStr = String(Math.floor(secs / 60)).padStart(2, '0');
        const secsStr = String(secs % 60).padStart(2, '0');
        timerEl.innerHTML = `${minsStr.split('').map(d => `<span class="digit">${d}</span>`).join('')}<b>:</b>${secsStr.split('').map(d => `<span class="digit">${d}</span>`).join('')}`;

        if (secs <= 10) timerEl.classList.add('warning');
        else timerEl.classList.remove('warning');

        const closingSeconds = Math.max(0, Math.min(5, Number(secs) || 0));
        const isClosing = Number(secs) >= 0 && Number(secs) <= 5;
        if (closingTens) closingTens.innerText = String(Math.floor(closingSeconds / 10));
        if (closingOnes) closingOnes.innerText = String(closingSeconds % 10);
        closingOverlay?.classList.toggle('active', isClosing);
        closingOverlay?.setAttribute('aria-hidden', isClosing ? 'false' : 'true');
        bettingSection?.classList.toggle('closing-round', isClosing);
      }

      const freezeOverlay = document.getElementById('freeze-banner');
      const isBettingLocked = roomState.isFrozen || Number(roomState.timeRemaining) <= 5;
      if (isBettingLocked) {
        freezeOverlay?.classList.add('active');
        document.querySelectorAll('.btn-color, .btn-number, .btn-big-small, .quick-controls button').forEach(b => b.disabled = true);
      } else {
        freezeOverlay?.classList.remove('active');
        document.querySelectorAll('.btn-color, .btn-number, .btn-big-small, .quick-controls button').forEach(b => b.disabled = false);
      }
    }

    this.renderGameHistory(state);
    this.renderMyOrders(state);
  }

  buildDisplayHistory(history, currentPeriod) {
    const source = Array.isArray(history) ? history.slice(0, 500).map(item => ({ ...item })) : [];
    const knownByPeriod = new Map(source.map(item => [String(item.period), item]));
    const display = [];
    let nextPeriod;

    try {
      nextPeriod = BigInt(currentPeriod || source[0]?.period || Date.now()) - 1n;
    } catch (e) {
      nextPeriod = BigInt(Date.now()) - 1n;
    }

    while (display.length < 500) {
      const period = String(nextPeriod);
      const known = knownByPeriod.get(period);
      const number = known ? Number(known.number) : Number((nextPeriod * 7n + 3n) % 10n);
      const color = number === 0 || number === 5
        ? 'violet'
        : number % 2 === 0 ? 'red' : 'green';

      display.push({
        ...(known || {}),
        period,
        number,
        color: known?.color || color,
        size: known?.size || (number >= 5 ? 'Big' : 'Small'),
        room: known?.room || appState.getState().activeRoom
      });
      nextPeriod -= 1n;
    }

    return display;
  }

  renderGameHistory(state) {
    const tableBody = document.getElementById('history-table-body');
    const beadGrid = document.getElementById('bead-plate-grid');
    const currentPeriod = state.rounds[state.activeRoom]?.currentPeriod;
    const displayHistory = this.buildDisplayHistory(state.history, currentPeriod);
    const totalPages = Math.max(1, Math.ceil(displayHistory.length / this.historyPageSize));
    if (this.historyPage >= totalPages) this.historyPage = totalPages - 1;
    const start = this.historyPage * this.historyPageSize;
    const historyList = displayHistory.slice(start, start + this.historyPageSize);
    const recentBalls = document.getElementById('recent-result-balls');
    const pageLabel = document.getElementById('history-page-label');
    const prev = document.getElementById('history-prev');
    const next = document.getElementById('history-next');

    if (pageLabel) pageLabel.innerText = `${this.historyPage + 1}/${totalPages}`;
    if (prev) prev.disabled = this.historyPage === 0;
    if (next) next.disabled = this.historyPage >= totalPages - 1;

    if (recentBalls) {
      recentBalls.innerHTML = displayHistory.slice(0, 5).map(h => {
        const split = h.number === 0 ? 'split-red-violet' : h.number === 5 ? 'split-green-violet' : h.color;
        return `<span class="mini-result-ball ${split}" data-number="${h.number}" aria-label="${h.number}">${h.number}</span>`;
      }).join('');
    }

    if (tableBody) {
      tableBody.innerHTML = historyList.map(h => {
        const numberClass = h.number === 0
          ? 'history-number split-red-violet'
          : h.number === 5
            ? 'history-number split-green-violet'
            : `history-number ${h.color}`;
        const dots = h.number === 0
          ? '<i class="color-dot red"></i><i class="color-dot violet"></i>'
          : h.number === 5
            ? '<i class="color-dot green"></i><i class="color-dot violet"></i>'
            : `<i class="color-dot ${h.color}"></i>`;

        return `
          <tr>
            <td>${h.period}</td>
            <td><span class="${numberClass}">${h.number}</span></td>
            <td>${h.size}</td>
            <td><span class="history-color-dots">${dots}</span></td>
          </tr>
        `;
      }).join('');
    }

    if (beadGrid) {
      beadGrid.innerHTML = displayHistory.slice(0, 30).map(h => {
        let dotClass = `dot-${h.color}`;
        if (h.number === 0) dotClass = 'dot-split-red-violet';
        if (h.number === 5) dotClass = 'dot-split-green-violet';

        return `<div class="bead-cell ${dotClass}">${h.number}</div>`;
      }).join('');
    }

    this.renderTrendChart(displayHistory);
  }

  renderTrendChart(history) {
    const chart = document.getElementById('trend-chart');
    if (!chart) return;

    const sample = history.slice(0, 100);
    const frequency = Array(10).fill(0);
    const missing = Array(10).fill(100);
    const maxConsecutive = Array(10).fill(0);
    const run = Array(10).fill(0);

    sample.forEach((item, index) => {
      const number = Number(item.number);
      frequency[number] += 1;
      if (missing[number] === 100) missing[number] = index;

      for (let n = 0; n < 10; n += 1) {
        if (n === number) {
          run[n] += 1;
          maxConsecutive[n] = Math.max(maxConsecutive[n], run[n]);
        } else {
          run[n] = 0;
        }
      }
    });

    const averageMissing = frequency.map(count => Math.max(0, Math.round((sample.length - count) / Math.max(1, count))));
    const numberCells = values => values.map(value => `<span>${value}</span>`).join('');
    const winningCells = numberCells(Array.from({ length: 10 }, (_, i) => i));
    const rows = history.slice(0, 10);
    const points = rows.map((item, index) => `${10.5 + (Number(item.number) * 20.8)},${21.5 + (index * 43)}`).join(' ');

    chart.innerHTML = `
      <div class="trend-head"><span>Period</span><span>Number</span></div>
      <div class="trend-stats">
        <div class="trend-stat-row trend-stat-title"><strong>Statistic</strong><span>(last 100 Periods)</span></div>
        <div class="trend-stat-row trend-winning"><strong>Winning Numbers</strong><div class="trend-stat-numbers">${winningCells}</div></div>
        <div class="trend-stat-row"><strong>Missing</strong><div class="trend-stat-numbers">${numberCells(missing)}</div></div>
        <div class="trend-stat-row"><strong>Avg missing</strong><div class="trend-stat-numbers">${numberCells(averageMissing)}</div></div>
        <div class="trend-stat-row"><strong>Frequency</strong><div class="trend-stat-numbers">${numberCells(frequency)}</div></div>
        <div class="trend-stat-row"><strong>Max consecutive</strong><div class="trend-stat-numbers">${numberCells(maxConsecutive)}</div></div>
      </div>
      <div class="trend-rows">
        <svg class="trend-lines" viewBox="0 0 229 430" preserveAspectRatio="none" aria-hidden="true">
          <polyline points="${points}" fill="none" stroke="#ff7b7b" stroke-width="1.1"></polyline>
        </svg>
        ${rows.map(item => {
          const activeNumber = Number(item.number);
          const activeColor = activeNumber === 0 ? 'zero' : activeNumber === 5 ? 'five' : item.color;
          return `
            <div class="trend-row">
              <span class="trend-period">${item.period}</span>
              <div class="trend-number-track">
                ${Array.from({ length: 10 }, (_, number) => `<span class="trend-number ${number === activeNumber ? `active ${activeColor}` : ''}">${number}</span>`).join('')}
                <span class="trend-size ${item.size === 'Big' ? 'big' : 'small'}">${item.size === 'Big' ? 'B' : 'S'}</span>
              </div>
            </div>
          `;
        }).join('')}
      </div>
      <div class="trend-footer">
        <button type="button" disabled><i class="bi bi-chevron-left"></i></button>
        <span>1/50</span>
        <button type="button"><i class="bi bi-chevron-right"></i></button>
      </div>
    `;
  }

  renderMyOrders(state) {
    const ordersBody = document.getElementById('my-orders-body');
    if (!ordersBody) return;

    if (state.userBets.length === 0) {
      ordersBody.innerHTML = `<tr><td colspan="4" style="text-align:center; color: var(--text-muted);">No bets placed yet</td></tr>`;
      return;
    }

    ordersBody.innerHTML = state.userBets.slice(0, 10).map(b => {
      let statusBadge = `<span class="tag-badge tag-pending">Pending</span>`;
      if (b.status === 'win') statusBadge = `<span class="tag-badge tag-win">+₹${b.payout} (WIN)</span>`;
      if (b.status === 'loss') statusBadge = `<span class="tag-badge tag-loss">LOSS</span>`;

      return `
        <tr>
          <td>${b.period}</td>
          <td><strong style="text-transform:uppercase;">${b.selection}</strong></td>
          <td>₹${b.totalStake}</td>
          <td>${statusBadge}</td>
        </tr>
      `;
    }).join('');
  }

}

const app = new App();
document.addEventListener('DOMContentLoaded', () => { app.init(); });

// Global exposed Admin action handlers
// ---- Admin actions. Each repaints its own row first, then confirms. ----

// Paint the result immediately, then confirm. If the server disagrees we pull
// the real state back down, so an optimistic patch can never stick around wrong.
const settleRequest = async (id, path, onDone, successMessage) => {
  onDone?.();
  app.clearRequest(id);
  app.showToast(successMessage, 'success');
  try {
    await app.adminApi(path, 'POST');
    app.refreshAdminMetricsFast();
  } catch (error) {
    app.showToast(error.transient
      ? 'Could not reach the server — checking what actually happened.'
      : error.message, 'error');
    void app.loadAdmin({ silent: true });
  }
};

window.adminApproveDep = (id) => {
  const card = app.findRequestCard(id);
  const userId = card?.dataset.userId;
  const amount = Number(card?.dataset.amount || 0);
  return settleRequest(id, `/api/admin/deposits/${id}/approve`,
    () => app.bumpUserRecharge(userId, amount),
    `Deposit approved. ₹${amount.toFixed(2)} credited.`);
};

window.adminRejectDep = (id) =>
  settleRequest(id, `/api/admin/deposits/${id}/reject`, null, 'Deposit rejected.');

window.adminApproveWth = (id) =>
  settleRequest(id, `/api/admin/withdrawals/${id}/approve`, null, 'Withdrawal marked paid.');

window.adminRejectWth = (id) =>
  settleRequest(id, `/api/admin/withdrawals/${id}/reject`, null, 'Withdrawal rejected and refunded.');

window.adminToggleGameAccess = (id, enabled) => app.adminMutate(async () => {
  app.setUserAccessCell(id, enabled);   // paint first, confirm after
  try {
    await app.adminApi(`/api/admin/users/${id}/game-access`, 'PUT', { enabled });
    app.showToast(`All games ${enabled ? 'unlocked' : 'locked'} for this player.`, 'success');
  } catch (error) {
    app.setUserAccessCell(id, !enabled);
    app.showToast(error.message, 'error');
  }
});

window.adminToggleUser = async (id, status) => {
  try {
    await app.adminApi(`/api/admin/users/${id}/status`, 'PUT', { status });
    app.showToast(`Player ${status === 'active' ? 'enabled' : 'disabled'}.`, 'success');
    void app.loadAdmin({ silent: true });
  } catch (error) {
    app.showToast(error.message, 'error');
  }
};

window.adminDeleteUser = async (id) => {
  if (!confirm(`Delete player ${id}? This cannot be undone.`)) return;
  const row = app.findUserRow(id);
  try {
    await app.adminApi(`/api/admin/users/${id}`, 'DELETE');
    row?.remove();
    app.showToast('Player deleted.', 'success');
    void app.loadAdmin({ silent: true });
  } catch (error) {
    app.showToast(error.message, 'error');
  }
};

window.adminToggleQR = (id, enabled) => app.adminMutate(async () => {
  app.setQrStateCell(id, enabled);       // paint first, confirm after
  try {
    await app.adminApi(`/api/admin/qr-codes/${id}/activate?enabled=${enabled}`, 'POST');
    app.showToast(enabled ? 'QR added to rotation.' : 'QR removed from rotation.', 'success');
    void app.syncActiveQR();
  } catch (error) {
    app.setQrStateCell(id, !enabled);
    app.showToast(error.message, 'error');
  }
});

// Kept for older markup that still calls the activate-only handler.
window.adminActivateQR = (id) => window.adminToggleQR(id, true);

window.adminDeleteQR = async (id) => {
  if (!confirm('Delete this QR code?')) return;
  try {
    await app.adminApi(`/api/admin/qr-codes/${id}`, 'DELETE');
    app.showToast('QR code deleted.', 'success');
    void app.loadAdmin({ silent: true });
    void app.syncActiveQR();
  } catch (error) {
    app.showToast(error.message, 'error');
  }
};

