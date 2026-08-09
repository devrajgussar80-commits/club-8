/**
 * The staff portal at /employee.
 *
 * Its own script rather than a mode of the player app: the two share no
 * screens, and app.js is already five thousand lines that this page would
 * download to use none of. It talks only to /api/employee/*, every route of
 * which is scoped server-side to the signed-in employee.
 */

const API = (window.APP_CONFIG?.API_BASE_URL || '').replace(/\/+$/, '');
const TOKEN_KEY = 'CLUB8_EMPLOYEE_TOKEN';

const $ = id => document.getElementById(id);

class EmployeePortal {
  constructor() {
    // sessionStorage when "keep me signed in" is off, so a shared machine
    // does not hand the next person a live portal.
    this.token = localStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(TOKEN_KEY) || '';
    this.data = { me: null, referrals: [], chain: null, colleagues: [] };
    this.filter = 'all';
  }

  init() {
    this.wireGate();
    this.wireTabs();
    this.wireActions();
    if (this.token) void this.load();
    else this.showGate();
  }

  // ------------------------------------------------------------- plumbing

  async api(path, { method = 'GET', body = null } = {}) {
    let res;
    try {
      res = await fetch(`${API}/api/employee${path}`, {
        method,
        headers: {
          'Content-Type': 'application/json',
          ...(this.token ? { Authorization: `Bearer ${this.token}` } : {})
        },
        body: body ? JSON.stringify(body) : null
      });
    } catch (cause) {
      const error = new Error('Could not reach the server. Check your connection.');
      error.transient = true;
      throw error;
    }
    let payload = {};
    try { payload = await res.json(); } catch (_) { /* empty or not JSON */ }
    if (!res.ok) {
      const error = new Error(payload.detail || `Request failed (${res.status})`);
      error.status = res.status;
      throw error;
    }
    return payload;
  }

  signOut(message) {
    this.token = '';
    localStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(TOKEN_KEY);
    this.showGate(message);
  }

  showGate(message = '') {
    $('emp-app').hidden = true;
    $('emp-gate').style.display = 'flex';
    const box = $('emp-gate-error');
    box.textContent = message;
    box.hidden = !message;
  }

  hideGate() {
    $('emp-gate').style.display = 'none';
    $('emp-app').hidden = false;
  }

  toast(message, type = 'success') {
    const host = $('toast-container');
    if (!host) return;
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => el.remove(), 3200);
  }

  // ------------------------------------------------------------ sign in

  wireGate() {
    $('emp-login-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      const button = event.currentTarget.querySelector('button[type="submit"]');
      const phone = ($('emp-phone').value || '').replace(/\D/g, '').slice(-10);
      const password = $('emp-password').value || '';
      if (phone.length !== 10 || !password) {
        return this.showGate('Enter your 10-digit number and password.');
      }
      button.disabled = true;
      try {
        const result = await this.api('/login', { method: 'POST', body: { phone, password } });
        this.token = result.token;
        const store = $('emp-remember').checked ? localStorage : sessionStorage;
        store.setItem(TOKEN_KEY, result.token);
        $('emp-password').value = '';
        this.hideGate();
        await this.load();
      } catch (error) {
        this.showGate(error.message);
      } finally {
        button.disabled = false;
      }
    });
  }

  // ------------------------------------------------------------- loading

  async load() {
    this.setStatus('Loading…');
    try {
      // One round trip each, in parallel: the portal is read-only and these
      // four do not depend on one another.
      const [me, referrals, chain, colleagues] = await Promise.all([
        this.api('/me'),
        this.api('/referrals'),
        this.api('/chain'),
        this.api('/colleagues')
      ]);
      this.data = { me, referrals: referrals.referrals || [], chain, colleagues: colleagues.colleagues || [] };
      this.reward = referrals.reward_per_referral || 0;
      this.hideGate();
      this.render();
      this.setStatus(`Updated ${new Date().toLocaleTimeString()}`);
    } catch (error) {
      // 401/403 is the only answer that means "sign in again"; a dropped
      // connection must not throw someone out mid-shift.
      if (error.status === 401 || error.status === 403) this.signOut(error.message);
      else this.setStatus(error.message);
    }
  }

  setStatus(text) {
    const el = $('emp-status');
    if (el) el.textContent = text;
  }

  // ------------------------------------------------------------ rendering

  render() {
    this.renderHeader();
    this.renderKpis();
    this.renderReferrals();
    this.renderChain();
    this.renderEarnings();
    this.renderColleagues();
  }

  renderHeader() {
    const emp = this.data.me?.employee || {};
    const stats = this.data.me?.stats || {};
    $('emp-name').textContent = emp.name || '—';
    const joined = emp.joined ? new Date(emp.joined).toLocaleDateString() : '—';
    $('emp-meta').textContent =
      `${emp.phone || '—'} · joined ${joined} · #${stats.rank || 1} of ${stats.staff_count || 1}`;

    const avatar = $('emp-avatar');
    if (emp.has_photo) {
      // Fetched through the API so it carries the session header; a plain
      // <img src> could not authenticate and would 401.
      void this.photoInto(avatar, emp.id);
    }

    const code = emp.referral_code || '';
    $('emp-code').textContent = code || '—';
    this.inviteUrl = code ? `${window.location.origin}/register?ref=${encodeURIComponent(code)}` : '';
    $('emp-link').textContent = this.inviteUrl || '—';
  }

  /** Photos sit behind the session, so they are fetched and swapped in as blobs. */
  async photoInto(el, userId) {
    if (!userId) return;
    try {
      const res = await fetch(`${API}/api/employee/photo/${encodeURIComponent(userId)}`, {
        headers: { Authorization: `Bearer ${this.token}` }
      });
      if (!res.ok) return;
      const url = URL.createObjectURL(await res.blob());
      el.innerHTML = '';
      el.style.backgroundImage = `url(${url})`;
      el.classList.add('has-photo');
    } catch (_) { /* the initial icon stays; a missing photo is not an error */ }
  }

  renderKpis() {
    const s = this.data.me?.stats || {};
    const money = n => `₹${Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
    const tiles = [
      ['Invited', s.invited ?? 0, 'people who signed up with your code'],
      ['Deposited', s.deposited ?? 0, `${s.conversion ?? 0}% of your invites`],
      ['No deposit yet', s.not_deposited ?? 0, 'signed up but never paid in'],
      ['Earned', money(s.earned), `${s.paid ?? 0} referrals paid out`],
      ['Waiting', money(s.pending), 'deposited, reward not released'],
      ['They deposited', money(s.deposits_brought), 'total paid in by your invites']
    ];
    // <small> then <b> is what .ng-kpi styles, the same shape the dashboard's
    // own tiles use. The third line is the portal's addition.
    $('emp-kpis').innerHTML = tiles.map(([label, value, hint]) => `
      <div class="ng-kpi">
        <small>${this.esc(label)}</small>
        <b>${this.esc(String(value))}</b>
        <i class="emp-kpi-hint">${this.esc(hint)}</i>
      </div>`).join('');
  }

  matchesFilter(row) {
    if (this.filter === 'earned') return row.earned;
    if (this.filter === 'waiting') return row.status === 'deposited';
    if (this.filter === 'none') return !row.deposited;
    return true;
  }

  renderReferrals() {
    const rows = this.data.referrals.filter(r => this.matchesFilter(r));
    $('emp-ref-count').textContent =
      `${rows.length} of ${this.data.referrals.length}`;

    if (!rows.length) {
      $('emp-referrals-body').innerHTML =
        `<tr><td colspan="5" class="nd-empty">${this.data.referrals.length
          ? 'Nobody in this group yet.'
          : 'Nobody has signed up with your code yet. Share your link to start.'}</td></tr>`;
      return;
    }

    $('emp-referrals-body').innerHTML = rows.map(r => `
      <tr>
        <td><strong>${this.esc(r.name || '—')}</strong><br><small>${this.esc(r.phone)}</small></td>
        <td>${r.joined ? new Date(r.joined).toLocaleDateString() : '—'}</td>
        <td>${r.deposited
              ? `<span class="emp-yes">₹${r.deposits.toLocaleString('en-IN')}</span>`
              : '<span class="emp-no">No</span>'}</td>
        <td>${r.earned
              ? `<span class="emp-yes">₹${r.reward}</span>`
              : r.pending_reward
                ? `<span class="emp-wait">₹${r.pending_reward}</span>`
                : '—'}</td>
        <td><small>${this.esc(r.why)}</small></td>
      </tr>`).join('');
  }

  renderChain() {
    const chain = this.data.chain || { chain: [], total: 0 };
    $('emp-chain-count').textContent = `${chain.total} below you`;
    $('emp-chain').innerHTML = chain.chain?.length
      ? this.chainHtml(chain.chain)
      : '<p class="nd-empty">Nobody below you yet.</p>';
  }

  chainHtml(nodes) {
    return `<ul class="emp-chain-list">${nodes.map(n => `
      <li>
        <div class="emp-chain-node">
          <span class="emp-depth">L${n.depth}</span>
          <strong>${this.esc(n.name || '—')}</strong>
          <small>${this.esc(n.phone || '')}</small>
          ${n.deposits > 0
            ? `<span class="emp-yes">₹${Number(n.deposits).toLocaleString('en-IN')}</span>`
            : '<span class="emp-no">no deposit</span>'}
        </div>
        ${n.invited?.length ? this.chainHtml(n.invited) : ''}
      </li>`).join('')}</ul>`;
  }

  renderEarnings() {
    const s = this.data.me?.stats || {};
    const money = n => `₹${Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
    $('emp-earnings').innerHTML = `
      <div class="emp-earn-card emp-earn-paid">
        <span>Paid to you</span><strong>${money(s.earned)}</strong>
        <small>${s.paid ?? 0} referrals released by admin</small>
      </div>
      <div class="emp-earn-card emp-earn-wait">
        <span>Waiting on admin</span><strong>${money(s.pending)}</strong>
        <small>deposit approved, reward not released yet</small>
      </div>
      <div class="emp-earn-card">
        <span>Per referral</span><strong>${money(s.reward_per_referral)}</strong>
        <small>paid once their first deposit is approved</small>
      </div>`;

    // Sorted so the ones that paid are on top, then the ones that still might.
    const order = { approved: 0, deposited: 1, signed_up: 2, rejected: 3 };
    const rows = [...this.data.referrals]
      .sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9));
    $('emp-earnings-body').innerHTML = rows.length ? rows.map(r => `
      <tr>
        <td><strong>${this.esc(r.name || '—')}</strong><br><small>${this.esc(r.phone)}</small></td>
        <td><small>${this.esc(r.why)}</small></td>
        <td>${r.earned
              ? `<span class="emp-yes">+₹${r.reward}</span>`
              : r.pending_reward
                ? `<span class="emp-wait">₹${r.pending_reward}</span>`
                : '<span class="emp-no">₹0</span>'}</td>
      </tr>`).join('') : '<tr><td colspan="3" class="nd-empty">No referrals yet.</td></tr>';
  }

  renderColleagues() {
    const list = this.data.colleagues;
    const group = this.data.me?.employee?.group;
    const money = n => `₹${Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;

    // The group panel first: an employee's own group is what they are part of,
    // and the whole-staff list below is context for it.
    $('emp-group').innerHTML = group ? `
      <div class="emp-group">
        <div class="emp-group-name">
          ${this.esc(group.name)}
          ${group.note ? `<small>${this.esc(group.note)}</small>` : ''}
        </div>
        <div class="emp-group-nums">
          <div><b>${group.members}</b><span>in the group</span></div>
          <div><b>${group.invited}</b><span>invited</span></div>
          <div><b>${group.paid}</b><span>paid out</span></div>
          <div><b>${money(group.earned)}</b><span>earned</span></div>
        </div>
      </div>` : `<p class="nd-hint">You are not in a group yet — your admin can add you to one.</p>`;

    const mine = list.filter(c => c.same_group);
    const rest = list.filter(c => !c.same_group);
    const card = (c, i) => `
      <div class="emp-colleague${c.is_me ? ' is-me' : ''}${c.same_group ? ' is-group' : ''}">
        <span class="emp-rank">${i + 1}</span>
        <div class="emp-avatar emp-avatar-sm" data-photo-for="${this.esc(c.id)}">
          <i class="bi bi-person-fill"></i>
        </div>
        <div class="emp-colleague-who">
          <strong>${this.esc(c.name || '—')}${c.is_me ? ' <em>(you)</em>' : ''}</strong>
          <small>joined ${c.joined ? new Date(c.joined).toLocaleDateString() : '—'}</small>
        </div>
        <div class="emp-colleague-nums">
          <span><b>${c.invited}</b> invited</span>
          <span><b>${c.paid}</b> paid</span>
          <span><b>₹${Number(c.earned).toLocaleString('en-IN')}</b> earned</span>
        </div>
      </div>`;

    $('emp-colleagues').innerHTML = list.length ? `
      ${mine.length ? `<p class="emp-group-heading">Your group</p>${mine.map(card).join('')}` : ''}
      ${rest.length ? `<p class="emp-group-heading">${mine.length ? 'Everyone else' : 'All staff'}</p>${rest.map(card).join('')}` : ''}
    ` : '<p class="nd-empty">No other staff yet.</p>';

    list.filter(c => c.has_photo).forEach(c => {
      const el = document.querySelector(`[data-photo-for="${CSS.escape(c.id)}"]`);
      if (el) void this.photoInto(el, c.id);
    });
  }

  esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
    ));
  }

  // -------------------------------------------------------------- actions

  wireTabs() {
    document.querySelectorAll('[data-emp-tab]').forEach(button => {
      button.addEventListener('click', () => {
        document.querySelectorAll('[data-emp-tab]').forEach(b => b.classList.remove('active'));
        button.classList.add('active');
        const want = button.dataset.empTab;
        ['referrals', 'chain', 'earnings', 'team'].forEach(name => {
          $(`emp-panel-${name}`).hidden = name !== want;
        });
      });
    });

    document.querySelectorAll('[data-emp-filter]').forEach(button => {
      button.addEventListener('click', () => {
        document.querySelectorAll('[data-emp-filter]').forEach(b => b.classList.remove('active'));
        button.classList.add('active');
        this.filter = button.dataset.empFilter;
        this.renderReferrals();
      });
    });
  }

  wireActions() {
    $('emp-logout')?.addEventListener('click', () => this.signOut());
    $('emp-refresh')?.addEventListener('click', () => void this.load());

    $('emp-copy')?.addEventListener('click', async () => {
      if (!this.inviteUrl) return;
      try {
        await navigator.clipboard.writeText(this.inviteUrl);
        this.toast('Invite link copied.');
      } catch (_) {
        // clipboard is blocked outside a secure context; select it instead so
        // the link can still be copied by hand.
        const range = document.createRange();
        range.selectNodeContents($('emp-link'));
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        this.toast('Press Ctrl+C to copy.', 'error');
      }
    });

    $('emp-share')?.addEventListener('click', async () => {
      if (!this.inviteUrl) return;
      const text = 'Join me on Club 8 — WinGo, Aviator, Mines aur bahut kuch. ₹100 signup bonus:';
      if (navigator.share) {
        try { await navigator.share({ text, url: this.inviteUrl }); } catch (_) { /* dismissed */ }
      } else {
        window.open(`https://wa.me/?text=${encodeURIComponent(`${text} ${this.inviteUrl}`)}`, '_blank');
      }
    });
  }
}

const portal = new EmployeePortal();
portal.init();
