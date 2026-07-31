/**
 * Daily lottery ticket counter.
 *
 * A ticket is not bought with wallet balance -- it is paid by UPI, exactly
 * like a deposit, and the admin confirms the payment. So this screen books a
 * *pending* ticket and says so plainly; the number is only really the
 * player's once the dashboard approves it. Showing it as owned before then
 * would let two players believe they hold the same number.
 */

const money = value => Number(value || 0).toFixed(2);
const pad = number => String(number).padStart(2, '0');

export class LotteryEngine {
  constructor(options) {
    Object.assign(this, options);
    this.data = null;
    this.selected = null;
    this.busy = false;
  }

  el(name) {
    return document.getElementById(`lottery-${name}`);
  }

  init() {
    this.gridEl = this.el('grid');
    this.formEl = this.el('form');
    this.utrEl = this.el('utr');
    this.buyBtn = this.el('buy');
    this.pickedEl = this.el('picked');
    this.qrEl = this.el('qr');
    this.priceEl = this.el('price');
    this.prizeEl = this.el('prize');
    this.dateEl = this.el('date');
    this.myEl = this.el('my-tickets');
    this.resultsEl = this.el('results');
    if (!this.gridEl) return;

    this.gridEl.addEventListener('click', event => {
      const button = event.target.closest('[data-ticket]');
      if (button && !button.disabled) this.select(Number(button.dataset.ticket));
    });
    this.buyBtn?.addEventListener('click', () => this.buy());
  }

  /** Called every time the page is opened, so counts are never stale. */
  async refresh() {
    try {
      this.data = await this.api('/api/games/lottery/today');
    } catch (error) {
      return this.toast(error.message || 'Lottery load nahi hui.', 'error');
    }
    this.render();
    this.loadResults();
  }

  async loadResults() {
    try {
      const { results } = await this.api('/api/games/lottery/results?limit=10');
      if (!this.resultsEl) return;
      this.resultsEl.innerHTML = results.length
        ? results.map(row => `<li><b>${row.draw_date}</b>
            <span class="lot-ball">${pad(row.winning_ticket)}</span>
            <em>${row.user_name || 'No winner'}</em></li>`).join('')
        : '<li class="is-empty">Abhi tak koi draw nahi hua</li>';
    } catch (error) {
      console.warn('lottery results unavailable', error.message);
    }
  }

  select(number) {
    this.selected = number;
    this.render();
  }

  render() {
    if (!this.data) return;
    const [min, max] = this.data.range;
    const taken = new Set(this.data.taken);
    const mine = new Map(this.data.my_tickets.map(ticket => [ticket.ticket_number, ticket.status]));

    if (this.dateEl) this.dateEl.textContent = this.data.draw_date;
    if (this.priceEl) this.priceEl.textContent = money(this.data.ticket_price);
    if (this.prizeEl) this.prizeEl.textContent = money(this.data.prize_amount);

    const tiles = [];
    for (let number = min; number <= max; number += 1) {
      const own = mine.get(number);
      const isTaken = taken.has(number) && !own;
      const classes = ['lot-tile'];
      if (own === 'approved') classes.push('is-mine');
      if (own === 'pending') classes.push('is-pending');
      if (isTaken) classes.push('is-taken');
      if (this.selected === number) classes.push('is-selected');
      tiles.push(`<button type="button" class="${classes.join(' ')}" data-ticket="${number}"
                    ${isTaken || own ? 'disabled' : ''}>${pad(number)}</button>`);
    }
    this.gridEl.innerHTML = tiles.join('');

    const qr = this.data.payment_qr;
    if (this.qrEl) {
      this.qrEl.innerHTML = qr
        ? `<img src="${qr.qr_url}" alt="UPI QR for lottery payment" loading="lazy">
           ${qr.upi_id ? `<code>${qr.upi_id}</code>` : ''}`
        : '<p class="is-empty">Admin ne abhi payment QR set nahi kiya.</p>';
    }
    if (this.pickedEl) {
      this.pickedEl.textContent = this.selected === null ? '--' : pad(this.selected);
    }
    if (this.formEl) this.formEl.hidden = this.selected === null;
    if (this.buyBtn) {
      this.buyBtn.disabled = this.busy || this.selected === null;
      this.buyBtn.textContent = this.busy ? 'Booking…' : `Book ticket · ₹${money(this.data.ticket_price)}`;
    }

    if (this.myEl) {
      this.myEl.innerHTML = this.data.my_tickets.length
        ? this.data.my_tickets.map(ticket =>
            `<li><span class="lot-ball">${pad(ticket.ticket_number)}</span>
               <b class="lot-status is-${ticket.status}">${ticket.status}</b></li>`).join('')
        : '<li class="is-empty">Aaj ka koi ticket nahi</li>';
    }
  }

  async buy() {
    if (this.busy || this.selected === null) return;
    const utr = (this.utrEl?.value || '').trim();
    if (utr.length < 6) return this.toast('Payment ka UTR daalein.', 'error');

    this.busy = true;
    this.render();
    try {
      const result = await this.api('/api/games/lottery/buy', 'POST', {
        ticket_number: this.selected,
        utr,
        qr_id: this.data.payment_qr?.id || null
      });
      this.toast(result.message, 'success');
      if (this.utrEl) this.utrEl.value = '';
      this.selected = null;
    } catch (error) {
      this.toast(error.message || 'Ticket book nahi hua.', 'error');
    } finally {
      this.busy = false;
      await this.refresh();
    }
  }
}
