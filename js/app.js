/**
 * Main Application Coordinator & REST API Client for Python Backend
 */

import { appState } from './state.js?v=rooms-1';
import { sound } from './sound.js';
import { getRoomClock } from './game-clock.js?v=1';
import { AviatorEngine } from './aviator-engine.js?v=3';
import { ChickenRoadEngine } from './chicken-road-engine.js?v=3';

class App {
  constructor() {
    this.currentBetSelection = null;
    this.selectedContractMultiplier = 1;
    this.selectedContractBase = 1;
    this.selectedQuantity = 1;
    this.authToken = localStorage.getItem('PREDICT_AUTH_TOKEN') || null;
    this.generatedOtp = '';
    // Cached from the last /api/auth/me so a page refresh restores the unlocked
    // state immediately instead of flashing the "locked" modal before the sync.
    this.gameAccessEnabled = localStorage.getItem('PREDICT_GAME_ACCESS') === '1';
    this.approvedDepositTotal = Number(localStorage.getItem('PREDICT_APPROVED_TOTAL') || 0);
    this.lastAccessSync = 0;
    this.apiBaseUrl = String(window.APP_CONFIG?.API_BASE_URL || '').replace(/\/+$/, '');
    this.adminApiKey = sessionStorage.getItem('PREDICT_ADMIN_API_KEY') || '';
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

  getArcadeBalanceDelta(userId = appState.getState().user.id) {
    try {
      const ledger = JSON.parse(localStorage.getItem('CLUB8_ARCADE_BALANCE_LEDGER') || '{}');
      return Number(ledger[userId] || 0);
    } catch {
      return 0;
    }
  }

  addArcadeBalanceDelta(amount, userId = appState.getState().user.id) {
    let ledger = {};
    try {
      ledger = JSON.parse(localStorage.getItem('CLUB8_ARCADE_BALANCE_LEDGER') || '{}');
    } catch {
      ledger = {};
    }
    ledger[userId] = Number((Number(ledger[userId] || 0) + Number(amount || 0)).toFixed(2));
    localStorage.setItem('CLUB8_ARCADE_BALANCE_LEDGER', JSON.stringify(ledger));
    return ledger[userId];
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
    this.startHomeBannerCarousel();
    this.startRecommendedCarousel();
    this.startHomeGameRotators();
    this.initMinesGame();
    this.initArcadeGames();
    appState.subscribe((state) => this.render(state));
    this.startBackendSync();
    this.render(appState.getState());
    if (this.authToken) void this.syncUserAccess();
    if (appState.getState().viewMode === 'admin') {
      if (this.adminApiKey) {
        void this.syncAdminMetrics();
      } else {
        this.showAdminGate();
      }
    }
  }

  initMinesGame() {
    const grid = document.getElementById('mines-grid');
    const betButton = document.getElementById('mines-bet-button');
    const betInput = document.getElementById('mines-bet-amount');
    const mineCount = document.getElementById('mines-count');
    if (!grid || !betButton || !betInput || !mineCount) return;

    const renderGrid = () => {
      grid.innerHTML = Array.from({ length: 25 }, (_, index) =>
        `<button type="button" class="mine-tile" data-mine-tile="${index}" aria-label="Tile ${index + 1}"><span></span></button>`
      ).join('');
    };
    const setMessage = (message) => {
      const output = document.getElementById('mines-message');
      if (output) output.textContent = message;
    };
    const setGameId = () => {
      const output = document.getElementById('mines-game-id');
      if (output) output.textContent = String(Date.now()).slice(-8);
    };
    const updateNext = () => {
      const bet = Math.max(1, Number(betInput.value) || 1);
      const next = document.getElementById('mines-next-win');
      if (next) next.textContent = `${(bet * this.minesMultiplier * 1.102).toFixed(2)} INR`;
    };
    const finishRound = (lost = false) => {
      if (!this.minesRound) return;
      grid.querySelectorAll('.mine-tile').forEach((tile, index) => {
        if (this.minesRound.mines.has(index)) tile.classList.add('mine-reveal');
      });
      betButton.classList.remove('cashout');
      betButton.querySelector('span').textContent = 'BET';
      setMessage(lost ? 'Boom! Mine found. Start a new round.' : 'Round cashed out successfully.');
      this.minesRound = null;
      this.minesMultiplier = 1;
      updateNext();
    };
    const startRound = () => {
      if (!this.canEnterPremiumGames()) {
        this.showGameAccessModal();
        return;
      }
      const count = Math.max(1, Math.min(10, Number(mineCount.value) || 3));
      const mines = new Set();
      while (mines.size < count) mines.add(Math.floor(Math.random() * 25));
      this.minesRound = { mines, opened: new Set() };
      this.minesMultiplier = 1;
      renderGrid();
      setGameId();
      betButton.classList.add('cashout');
      betButton.querySelector('span').textContent = 'CASH OUT';
      setMessage('Round active — reveal a safe tile or cash out.');
      updateNext();
    };

    renderGrid();
    setGameId();
    updateNext();
    grid.addEventListener('click', event => {
      const tile = event.target.closest('[data-mine-tile]');
      if (!tile || !this.minesRound || tile.classList.contains('revealed')) return;
      const index = Number(tile.dataset.mineTile);
      tile.classList.add('revealed');
      if (this.minesRound.mines.has(index)) {
        tile.classList.add('mine-hit');
        finishRound(true);
        return;
      }
      tile.classList.add('safe-hit');
      this.minesRound.opened.add(index);
      this.minesMultiplier = Number((this.minesMultiplier + 0.12 + Number(mineCount.value) * 0.025).toFixed(3));
      setMessage(`Safe! ${this.minesRound.opened.size} tile${this.minesRound.opened.size === 1 ? '' : 's'} opened · ${this.minesMultiplier.toFixed(2)}×`);
      updateNext();
    });
    betButton.addEventListener('click', () => this.minesRound ? finishRound(false) : startRound());
    document.getElementById('mines-minus')?.addEventListener('click', () => {
      betInput.value = Math.max(1, (Number(betInput.value) || 10) - 1).toFixed(2);
      updateNext();
    });
    document.getElementById('mines-plus')?.addEventListener('click', () => {
      betInput.value = ((Number(betInput.value) || 10) + 1).toFixed(2);
      updateNext();
    });
    document.getElementById('mines-random')?.addEventListener('click', () => {
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
        if (!game.includes('wingo') && !this.canEnterPremiumGames()) {
          this.showGameAccessModal();
          return;
        }
        if (game.includes('chicken road')) {
          this.switchSubPage('chicken-road');
        } else if (game.includes('aviator')) {
          this.switchSubPage('aviator');
        } else if (game.includes('wingo')) {
          this.switchSubPage('game');
        } else {
          this.showToast(`${card.dataset.miniGame} is coming soon.`, 'success');
        }
      });
    });

    const gameOptions = {
      getBalance: () => appState.getState().user.balance,
      changeBalance: delta => {
        const state = appState.getState();
        this.addArcadeBalanceDelta(delta, state.user.id);
        state.user.balance = Math.max(0, Number((state.user.balance + delta).toFixed(2)));
        appState.saveState();
        this.aviatorEngine?.renderWallet();
        this.chickenRoadEngine?.render();
      },
      toast: (message, type) => this.showToast(message, type),
      canPlay: () => this.canEnterPremiumGames(),
      denyPlay: () => this.showGameAccessModal()
    };
    this.chickenRoadEngine = new ChickenRoadEngine(gameOptions);
    this.chickenRoadEngine.init();
    this.aviatorEngine = new AviatorEngine(gameOptions);
    this.aviatorEngine.init();
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

  // The server already refuses to enable access below the ₹300 approved
  // recharge, so the admin toggle alone is the authority here.
  canEnterPremiumGames() {
    return Boolean(this.authToken && this.gameAccessEnabled);
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

  showAdminGate(message = '') {
    const gate = document.getElementById('admin-unlock-overlay');
    const error = document.getElementById('admin-unlock-error');
    gate?.classList.add('active');
    gate?.setAttribute('aria-hidden', 'false');
    if (error) {
      error.textContent = message;
      error.hidden = !message;
    }
  }

  hideAdminGate() {
    const gate = document.getElementById('admin-unlock-overlay');
    gate?.classList.remove('active');
    gate?.setAttribute('aria-hidden', 'true');
    const input = document.getElementById('admin-access-key');
    if (input) input.value = '';
  }

  lockAdmin(message = '') {
    this.adminApiKey = '';
    sessionStorage.removeItem('PREDICT_ADMIN_API_KEY');
    this.showAdminGate(message);
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
      try {
        if (this.authToken) await this.syncGameStatus();
        // Picks up an admin access grant without needing a re-login.
        if (this.authToken && Date.now() - this.lastAccessSync >= 10000) {
          await this.syncUserAccess();
        }
        if (Date.now() - this.lastQrSync >= 15000) {
          await this.syncActiveQR();
          this.lastQrSync = Date.now();
        }
        if (appState.getState().viewMode === 'admin' && this.adminApiKey) {
          await this.syncAdminMetrics();
        }
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
        if (appState.getState().viewMode === 'admin' && this.adminApiKey) {
          void this.refreshAdminMetricsFast();
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
      state.user.balance = Number((data.user_balance + this.getArcadeBalanceDelta(state.user.id)).toFixed(2));

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

  async syncAdminMetrics() {
    try {
      const metrics = await this.fetchApi('/api/admin/metrics');
      const state = appState.getState();
      state.admin.predictionMode = metrics.prediction_mode;
      
      const totalPoolEl = document.getElementById('admin-kpi-total-pool');
      const countEl = document.getElementById('admin-kpi-active-bets');
      const modeEl = document.getElementById('admin-kpi-current-mode');

      if (totalPoolEl) totalPoolEl.innerText = `₹${metrics.total_active_stake}`;
      if (countEl) countEl.innerText = `${metrics.active_bets_count} Active Bets`;
      if (modeEl) modeEl.innerText = metrics.prediction_mode.replace('_', ' ');
      const financeValues = {
        'admin-pending-deposits': String(metrics.pending_deposits || 0),
        'admin-approved-deposit-total': `₹${Number(metrics.approved_deposit_total || 0).toFixed(2)}`,
        'admin-pending-withdrawals': String(metrics.pending_withdrawals || 0),
        'admin-paid-withdrawal-total': `₹${Number(metrics.paid_withdrawal_total || 0).toFixed(2)}`
      };
      Object.entries(financeValues).forEach(([id, value]) => {
        const element = document.getElementById(id);
        if (element) element.textContent = value;
      });
      const apiStatus = document.getElementById('admin-api-status');
      apiStatus?.classList.remove('offline');
      if (apiStatus) apiStatus.innerHTML = '<i class="bi bi-circle-fill"></i> API connected';
      this.hideAdminGate();

      const grandTotal = (metrics.green_stake + metrics.red_stake + metrics.violet_stake) || 1;
      const gPct = Math.round((metrics.green_stake / grandTotal) * 100);
      const rPct = Math.round((metrics.red_stake / grandTotal) * 100);
      const vPct = Math.round((metrics.violet_stake / grandTotal) * 100);

      const gFill = document.getElementById('admin-pool-green-fill');
      const rFill = document.getElementById('admin-pool-red-fill');
      const vFill = document.getElementById('admin-pool-violet-fill');

      if (gFill) {
        gFill.style.width = `${gPct}%`;
        document.getElementById('admin-pool-green-val').innerText = `₹${metrics.green_stake} (${gPct}%)`;
      }
      if (rFill) {
        rFill.style.width = `${rPct}%`;
        document.getElementById('admin-pool-red-val').innerText = `₹${metrics.red_stake} (${rPct}%)`;
      }
      if (vFill) {
        vFill.style.width = `${vPct}%`;
        document.getElementById('admin-pool-violet-val').innerText = `₹${metrics.violet_stake} (${vPct}%)`;
      }

      await this.syncAdminTables();
    } catch (e) {
      const apiStatus = document.getElementById('admin-api-status');
      apiStatus?.classList.add('offline');
      if (apiStatus) apiStatus.innerHTML = '<i class="bi bi-circle-fill"></i> API offline / key invalid';
      if (appState.getState().viewMode === 'admin') {
        this.lockAdmin(e.message === 'Invalid admin access key' ? 'Incorrect admin access key.' : e.message);
      }
    }
  }

  async syncAdminTables(force = false) {
    if (!force && Date.now() - this.lastAdminTablesSync < 5000) return;
    this.lastAdminTablesSync = Date.now();
    try {
      const [platformSettings, usersData, qrData, depData, wthData] = await Promise.all([
        this.fetchApi('/api/admin/platform-settings'),
        this.fetchApi('/api/admin/users'),
        this.fetchApi('/api/admin/qr-codes'),
        this.fetchApi('/api/admin/deposits'),
        this.fetchApi('/api/admin/withdrawals')
      ]);
      const depositsEnabled = document.getElementById('admin-deposits-enabled');
      const withdrawalsEnabled = document.getElementById('admin-withdrawals-enabled');
      const withdrawalMin = document.getElementById('admin-withdrawal-min');
      if (depositsEnabled) depositsEnabled.checked = Boolean(platformSettings.deposits_enabled);
      if (withdrawalsEnabled) withdrawalsEnabled.checked = Boolean(platformSettings.withdrawals_enabled);
      if (withdrawalMin) withdrawalMin.value = String(platformSettings.withdrawal_min || 200);

      // 1. Users Table
      const usersBody = document.getElementById('admin-users-table-body');
      if (usersBody && usersData.users) {
        usersBody.innerHTML = usersData.users.map(u => `
          <tr>
            <td><code>${this.escapeHtml(u.id)}</code></td>
            <td><strong>${this.escapeHtml(u.username)}</strong></td>
            <td>${this.escapeHtml(u.phone)}</td>
            <td>₹${u.balance.toFixed(2)}</td>
            <td>
              <span class="admin-game-access">
                <small>Recharge ₹${Number(u.approved_deposit_total || 0).toFixed(2)}</small>
                <button class="btn-sm-access ${u.game_access_enabled ? 'enabled' : Number(u.approved_deposit_total || 0) >= 300 ? 'eligible' : ''}"
                  ${!u.game_access_enabled && Number(u.approved_deposit_total || 0) < 300 ? 'disabled' : ''}
                  onclick="window.adminToggleGameAccess('${u.id}', ${u.game_access_enabled ? 'false' : 'true'})">
                  ${u.game_access_enabled ? 'Disable Access' : Number(u.approved_deposit_total || 0) >= 300 ? 'Enable Access' : '₹300 Required'}
                </button>
              </span>
            </td>
            <td><span class="tag-badge ${u.status === 'active' ? 'tag-win' : 'tag-loss'}">${u.status.toUpperCase()}</span></td>
            <td>
              <button class="btn-sm-approve" style="background:${u.status === 'active' ? 'var(--color-red)' : 'var(--color-green)'}; color:#fff;" onclick="window.adminToggleUser('${u.id}', '${u.status === 'active' ? 'disabled' : 'active'}')">
                ${u.status === 'active' ? 'Disable' : 'Enable'}
              </button>
              <button class="btn-sm-reject" onclick="window.adminDeleteUser('${u.id}')">Delete</button>
            </td>
          </tr>
        `).join('');
      }

      // 2. QR Codes Table
      const qrBody = document.getElementById('admin-qr-table-body');
      if (qrBody && qrData.qr_codes) {
        qrBody.innerHTML = qrData.qr_codes.map(q => `
          <tr data-qr-id="${this.escapeHtml(q.id)}">
            <td><img class="admin-qr-thumb" src="${this.escapeHtml(this.resolveApiUrl(q.qr_url))}" alt="${this.escapeHtml(q.name)} QR"></td>
            <td><strong>${this.escapeHtml(q.name)}</strong><br><small>${this.escapeHtml(q.note || '-')}</small></td>
            <td><code>${this.escapeHtml(q.upi_id || 'Static QR')}</code><br><small>₹${q.min_amount || 100} - ₹${q.max_amount || 50000}</small></td>
            <td><span class="${q.is_active ? 'admin-active-pill' : 'admin-inactive-pill'}">${q.is_active ? 'ACTIVE' : 'INACTIVE'}</span></td>
            <td>
              ${q.is_active ? '' : `<button class="btn-sm-approve" onclick="window.adminActivateQR('${q.id}')">Set Active</button>`}
              <button class="btn-sm-reject" onclick="window.adminDeleteQR('${q.id}')">Delete</button>
            </td>
          </tr>
        `).join('');
      }

      // 3. Deposits Table
      const depBody = document.getElementById('admin-deposits-table-body');
      if (depBody && depData.deposits) {
        depBody.innerHTML = depData.deposits.map(d => `
          <tr data-deposit-id="${this.escapeHtml(d.id)}">
            <td><strong>${this.escapeHtml(d.user_name)}</strong></td>
            <td>₹${d.amount}</td>
            <td><code style="color:var(--color-green);">${this.escapeHtml(d.order_id || d.utr)}</code><br><small>${this.escapeHtml(d.qr_id || 'Legacy deposit')} · Ref ${this.escapeHtml(d.utr)}</small></td>
            <td>${this.escapeHtml(d.timestamp)}</td>
            <td><span class="tag-badge ${d.status === 'approved' ? 'tag-win' : d.status === 'rejected' ? 'tag-loss' : 'tag-pending'}">${d.status.toUpperCase()}</span></td>
            <td>
              ${d.status === 'pending' ? `
                <button class="btn-sm-approve" onclick="window.adminApproveDep('${d.id}')">Approve & Unlock</button>
                <button class="btn-sm-reject" onclick="window.adminRejectDep('${d.id}')">Reject</button>
              ` : '-'}
            </td>
          </tr>
        `).join('');
      }

      // 4. Withdrawals Table
      const wthBody = document.getElementById('admin-withdrawals-table-body');
      if (wthBody && wthData.withdrawals) {
        wthBody.innerHTML = wthData.withdrawals.map(w => `
          <tr data-withdrawal-id="${this.escapeHtml(w.id)}">
            <td><strong>${this.escapeHtml(w.user_name)}</strong></td>
            <td>₹${w.amount}</td>
            <td><code>${this.escapeHtml(w.upi_id)}</code></td>
            <td>${this.escapeHtml(w.timestamp)}</td>
            <td><span class="tag-badge ${w.status === 'paid' ? 'tag-win' : w.status === 'rejected' ? 'tag-loss' : 'tag-pending'}">${w.status.toUpperCase()}</span></td>
            <td>
              ${w.status === 'pending' ? `
                <button class="btn-sm-approve" onclick="window.adminApproveWth('${w.id}')">Mark Paid</button>
                <button class="btn-sm-reject" onclick="window.adminRejectWth('${w.id}')">Reject & Refund</button>
              ` : '-'}
            </td>
          </tr>
        `).join('');
      }

    } catch (e) {}
  }

  attachEventListeners() {
    // Admin Section Tabs Navigation
    document.querySelectorAll('.admin-tab').forEach(tab => {
      tab.addEventListener('click', (e) => {
        document.querySelectorAll('.admin-tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.admin-panel-section').forEach(s => s.classList.remove('active'));

        const targetSection = e.currentTarget.dataset.section;
        e.currentTarget.classList.add('active');
        document.getElementById(`admin-section-${targetSection}`)?.classList.add('active');
      });
    });

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
      const code = document.getElementById('agency-invite-code')?.textContent?.trim() || '';
      try {
        await navigator.clipboard.writeText(code);
        this.showToast('Invitation code copied', 'success');
      } catch {
        this.showToast(`Invitation code: ${code}`, 'success');
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
      button.disabled = true;
      try {
        await this.fetchApi('/api/wallet/deposit', 'POST', {
          amount: this.pendingDeposit.amount,
          utr,
          qr_id: this.pendingDeposit.qr.id,
          order_id: this.pendingDeposit.orderId
        });
        this.showToast('Payment submitted. Waiting for Admin approval.', 'success');
        document.getElementById('form-deposit')?.reset();
        if (utrInput) utrInput.value = '';
        document.querySelectorAll('.chip-amount').forEach(item => item.classList.remove('active'));
        updateDepositSubmit();
        this.pendingDeposit = null;
        closeDepositPayment();
        await this.syncWalletHistory();
      } catch (err) {
        this.showToast(err.message, 'error');
      } finally {
        button.disabled = false;
      }
    });

    const qrFileInput = document.getElementById('new-qr-file');
    qrFileInput?.addEventListener('change', async () => {
      const file = qrFileInput.files?.[0];
      const preview = document.getElementById('new-qr-preview');
      const dropzone = document.querySelector('.admin-qr-dropzone');
      if (!file || !preview) return;
      preview.src = URL.createObjectURL(file);
      dropzone?.classList.add('has-preview');
      const upiInput = document.getElementById('new-qr-upi');
      if (upiInput && !upiInput.value.trim()) {
        const detectedUpiId = await this.readUpiIdFromQrFile(file);
        if (detectedUpiId) {
          upiInput.value = detectedUpiId;
          this.showToast(`UPI ID detected: ${detectedUpiId}`, 'success');
        }
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
        await this.syncUserAccess();
        this.showToast(`Welcome back, ${res.user.name}!`, 'success');
        this.switchSubPage('home', { record: false });
      } catch (err) {
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

      try {
        if (phone.length !== 10) throw new Error('Please enter a valid 10-digit phone number.');
        if (!this.generatedOtp || otp !== this.generatedOtp) throw new Error('Send and verify the 6-digit code first.');
        if (password !== confirmPassword) throw new Error('Passwords do not match.');
        const res = await this.fetchApi('/api/auth/register', 'POST', { username, phone, password, referral_code: ref });
        this.authToken = res.token;
        localStorage.setItem('PREDICT_AUTH_TOKEN', res.token);
        this.gameAccessEnabled = false;
        this.approvedDepositTotal = 0;
        await this.syncUserAccess();
        this.showToast(`Account created! ₹100 signup bonus added for WinGo.`, 'success');
        this.switchSubPage('home', { record: false });
      } catch (err) {
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
      if (!this.activeQR?.qr_url) await this.syncActiveQR();
      const qr = this.activeQR;
      if (!qr?.qr_url) return this.showToast('Admin has not uploaded an active QR yet.', 'error');

      const minimum = Number(qr.min_amount || 100);
      const maximum = Number(qr.max_amount || 50000);
      if (amount < minimum || amount > maximum) {
        return this.showToast(`Enter an amount between ₹${minimum} and ₹${maximum}.`, 'error');
      }

      const randomPart = crypto.getRandomValues(new Uint32Array(1))[0].toString(36).toUpperCase().slice(-4).padStart(4, '0');
      const orderId = `ORD${String(Date.now()).slice(-8)}${randomPart}`;
      this.pendingDeposit = { amount, qr, orderId };
      document.getElementById('deposit-payment-amount').innerText = `₹${amount.toFixed(2)}`;
      let paymentQrUrl = this.resolveApiUrl(qr.qr_url);
      const intentLink = document.getElementById('deposit-upi-intent');
      if (qr.upi_id) {
        const upiPayment = `upi://pay?pa=${encodeURIComponent(qr.upi_id)}&pn=UPI%20Payment&am=${amount.toFixed(2)}&cu=INR&tr=${orderId}&tn=${orderId}`;
        paymentQrUrl = `${this.apiBaseUrl}/api/wallet/payment-qr?amount=${encodeURIComponent(amount.toFixed(2))}&qr_id=${encodeURIComponent(qr.id)}&order_id=${encodeURIComponent(orderId)}`;
        if (intentLink) {
          intentLink.href = upiPayment;
          intentLink.hidden = false;
        }
      } else if (intentLink) {
        intentLink.hidden = true;
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
        await this.syncGameStatus();
        await this.syncWalletHistory();
      } catch (err) {
        this.showToast(err.message, 'error');
      }
    };
    document.getElementById('form-withdrawal')?.addEventListener('submit', submitWithdrawal);
    document.getElementById('withdraw-submit-button')?.addEventListener('click', submitWithdrawal);

    // Admin Add QR Form Submit
    document.getElementById('form-add-qr')?.addEventListener('submit', async (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      const uploadButton = form.querySelector('button[type="submit"]');
      const payload = new FormData();
      const qrFile = document.getElementById('new-qr-file')?.files?.[0];
      if (!qrFile) return this.showToast('Choose a QR image first.', 'error');
      const upiInput = document.getElementById('new-qr-upi');
      let upiId = upiInput?.value?.trim() || '';
      if (!upiId) {
        upiId = await this.readUpiIdFromQrFile(qrFile);
        if (upiInput && upiId) upiInput.value = upiId;
      }
      if (!upiId) {
        return this.showToast('UPI ID could not be read automatically. Enter the UPI ID once to create clean changing QR codes.', 'error');
      }
      payload.append('name', document.getElementById('new-qr-name')?.value || '');
      payload.append('note', document.getElementById('new-qr-note')?.value || '');
      payload.append('upi_id', upiId);
      payload.append('min_amount', document.getElementById('new-qr-min')?.value || '100');
      payload.append('max_amount', document.getElementById('new-qr-max')?.value || '50000');
      payload.append('qr_file', qrFile);
      uploadButton.disabled = true;
      try {
        const headers = {};
        if (this.adminApiKey) headers['X-Admin-Key'] = this.adminApiKey;
        const response = await fetch(`${this.apiBaseUrl}/api/admin/qr-codes/upload`, {
          method: 'POST',
          headers,
          body: payload
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || 'QR upload failed');
        this.showToast('QR uploaded and activated for deposits.', 'success');
        form.reset();
        document.querySelector('.admin-qr-dropzone')?.classList.remove('has-preview');
        void Promise.all([
          this.syncAdminTables(true),
          this.syncActiveQR()
        ]);
      } catch (err) {
        this.showToast(err.message, 'error');
      } finally {
        uploadButton.disabled = false;
      }
    });

    // Streamlined Admin Mode Cards
    document.querySelectorAll('.mode-card-btn').forEach(card => {
      card.addEventListener('click', async (e) => {
        const mode = e.currentTarget.dataset.mode;
        try {
          await this.fetchApi('/api/admin/prediction-mode', 'POST', { mode });
          this.showToast(`Prediction Mode set to ${mode.toUpperCase()}`, 'success');
          await this.syncAdminMetrics();
        } catch (err) {
          this.showToast(err.message, 'error');
        }
      });
    });

    // Admin Forced Number Grid
    document.querySelectorAll('.manual-num-btn').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        const number = parseInt(e.currentTarget.dataset.num, 10);
        try {
          await this.fetchApi('/api/admin/force-result', 'POST', { number });
          this.showToast(`Manual result forced to Number ${number}`, 'success');
          await this.syncAdminMetrics();
        } catch (err) {
          this.showToast(err.message, 'error');
        }
      });
    });

    document.getElementById('admin-refresh')?.addEventListener('click', async () => {
      if (!this.adminApiKey) {
        this.showAdminGate('Enter the admin access key to load requests.');
        return;
      }
      await this.syncAdminMetrics();
      await this.syncAdminTables(true);
      this.showToast('Admin dashboard refreshed.', 'success');
    });

    document.getElementById('admin-lock')?.addEventListener('click', () => {
      this.lockAdmin();
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
      try {
        await this.fetchApi('/api/admin/metrics');
        sessionStorage.setItem('PREDICT_ADMIN_API_KEY', key);
        this.hideAdminGate();
        await this.syncAdminMetrics();
        await this.syncAdminTables(true);
        this.showToast('Admin dashboard connected.', 'success');
      } catch (error) {
        this.lockAdmin(error.message === 'Invalid admin access key' ? 'Incorrect admin access key.' : error.message);
        input?.focus();
      } finally {
        button.disabled = false;
      }
    });

    document.getElementById('admin-payment-settings')?.addEventListener('submit', async event => {
      event.preventDefault();
      const button = event.currentTarget.querySelector('button[type="submit"]');
      button.disabled = true;
      try {
        await this.fetchApi('/api/admin/platform-settings', 'PUT', {
          deposits_enabled: document.getElementById('admin-deposits-enabled').checked,
          withdrawals_enabled: document.getElementById('admin-withdrawals-enabled').checked,
          withdrawal_min: Number(document.getElementById('admin-withdrawal-min').value)
        });
        await this.syncActiveQR();
        this.showToast('Payment settings saved.', 'success');
      } catch (error) {
        this.showToast(error.message, 'error');
      } finally {
        button.disabled = false;
      }
    });
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
      await this.syncGameStatus();
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
    if (pageId === 'deposit-history' || pageId === 'withdraw-history') {
      this.syncWalletHistory();
    }

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

  findAdminRow(kind, id) {
    const attribute = {
      deposit: 'data-deposit-id',
      withdrawal: 'data-withdrawal-id',
      qr: 'data-qr-id'
    }[kind];
    if (!attribute) return null;
    return [...document.querySelectorAll(`[${attribute}]`)]
      .find(row => row.getAttribute(attribute) === String(id)) || null;
  }

  setAdminRowBusy(kind, id, busy) {
    this.findAdminRow(kind, id)?.querySelectorAll('button').forEach(button => {
      button.disabled = busy;
    });
  }

  updateAdminRequestRow(kind, id, status) {
    const row = this.findAdminRow(kind, id);
    if (!row) return;
    const badge = row.querySelector('.tag-badge');
    if (badge) {
      badge.className = `tag-badge ${status === 'approved' || status === 'paid' ? 'tag-win' : status === 'rejected' ? 'tag-loss' : 'tag-pending'}`;
      badge.textContent = status.toUpperCase();
    }
    const actions = row.querySelector('td:last-child');
    if (actions && status !== 'pending') actions.textContent = '-';
  }

  updateActiveQrRow(id) {
    document.querySelectorAll('[data-qr-id]').forEach(row => {
      const qrId = row.getAttribute('data-qr-id');
      const active = qrId === String(id);
      const pill = row.querySelector('.admin-active-pill, .admin-inactive-pill');
      if (pill) {
        pill.className = active ? 'admin-active-pill' : 'admin-inactive-pill';
        pill.textContent = active ? 'ACTIVE' : 'INACTIVE';
      }
      const actions = row.querySelector('td:last-child');
      if (actions) {
        actions.innerHTML = `${active ? '' : `<button class="btn-sm-approve" onclick="window.adminActivateQR('${this.escapeHtml(qrId)}')">Set Active</button>`}
          <button class="btn-sm-reject" onclick="window.adminDeleteQR('${this.escapeHtml(qrId)}')">Delete</button>`;
      }
    });
  }

  // Approving/rejecting money changes a user's approved-recharge total, which is
  // what unlocks the "Enable Access" button. The optimistic row patch cannot know
  // that, so always reconcile the real tables instead of suppressing the sync.
  async refreshAdminMetricsFast() {
    this.lastAdminTablesSync = 0;
    await this.syncAdminMetrics();
  }
}

const app = new App();
document.addEventListener('DOMContentLoaded', () => { app.init(); });

// Global exposed Admin action handlers
window.adminToggleUser = async (id, status) => {
  await app.fetchApi(`/api/admin/users/${id}/status`, 'PUT', { status });
  app.showToast(`User ${id} status set to ${status.toUpperCase()}`, 'success');
  await app.syncAdminTables(true);
};

window.adminToggleGameAccess = async (id, enabled) => {
  try {
    await app.fetchApi(`/api/admin/users/${id}/game-access`, 'PUT', { enabled });
    app.showToast(`Game access ${enabled ? 'enabled' : 'disabled'} for ${id}`, 'success');
    await app.syncAdminTables(true);
  } catch (error) {
    app.showToast(error.message, 'error');
  }
};

window.adminDeleteUser = async (id) => {
  if (confirm(`Are you sure you want to delete user ${id}?`)) {
    await app.fetchApi(`/api/admin/users/${id}`, 'DELETE');
    app.showToast(`User ${id} deleted`, 'success');
    await app.syncAdminTables(true);
  }
};

window.adminDeleteQR = async (id) => {
  await app.fetchApi(`/api/admin/qr-codes/${id}`, 'DELETE');
  app.showToast(`QR Code ${id} deleted`, 'success');
  await app.syncAdminTables(true);
};

window.adminActivateQR = async (id) => {
  app.setAdminRowBusy('qr', id, true);
  try {
    await app.fetchApi(`/api/admin/qr-codes/${id}/activate`, 'POST');
    app.updateActiveQrRow(id);
    app.showToast('Active deposit QR changed', 'success');
    void app.syncActiveQR();
  } catch (error) {
    app.showToast(error.message, 'error');
    app.setAdminRowBusy('qr', id, false);
  }
};

window.adminApproveDep = async (id) => {
  app.setAdminRowBusy('deposit', id, true);
  try {
    await app.fetchApi(`/api/admin/deposits/${id}/approve`, 'POST');
    app.updateAdminRequestRow('deposit', id, 'approved');
    app.showToast(`Deposit ${id} approved. Enable game access from User Directory after ₹300 eligibility.`, 'success');
    await app.refreshAdminMetricsFast();
  } catch (error) {
    app.showToast(error.message, 'error');
    app.setAdminRowBusy('deposit', id, false);
  }
};

window.adminRejectDep = async (id) => {
  app.setAdminRowBusy('deposit', id, true);
  try {
    await app.fetchApi(`/api/admin/deposits/${id}/reject`, 'POST');
    app.updateAdminRequestRow('deposit', id, 'rejected');
    app.showToast(`Deposit ${id} Rejected`, 'error');
    await app.refreshAdminMetricsFast();
  } catch (error) {
    app.showToast(error.message, 'error');
    app.setAdminRowBusy('deposit', id, false);
  }
};

window.adminApproveWth = async (id) => {
  app.setAdminRowBusy('withdrawal', id, true);
  try {
    await app.fetchApi(`/api/admin/withdrawals/${id}/approve`, 'POST');
    app.updateAdminRequestRow('withdrawal', id, 'paid');
    app.showToast(`Withdrawal ${id} Marked Paid`, 'success');
    await app.refreshAdminMetricsFast();
  } catch (error) {
    app.showToast(error.message, 'error');
    app.setAdminRowBusy('withdrawal', id, false);
  }
};

window.adminRejectWth = async (id) => {
  app.setAdminRowBusy('withdrawal', id, true);
  try {
    await app.fetchApi(`/api/admin/withdrawals/${id}/reject`, 'POST');
    app.updateAdminRequestRow('withdrawal', id, 'rejected');
    app.showToast(`Withdrawal ${id} Rejected & Balance Refunded`, 'error');
    await app.refreshAdminMetricsFast();
  } catch (error) {
    app.showToast(error.message, 'error');
    app.setAdminRowBusy('withdrawal', id, false);
  }
};
