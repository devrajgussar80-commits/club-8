/**
 * In-app sharing.
 *
 * A sheet with the apps people here actually use, because `navigator.share` is
 * missing on desktop browsers and on some Android WebViews, and a share button
 * that silently does nothing is worse than none.
 *
 * The card only appears once the backend reports an uploaded APK -- there is
 * no point inviting anyone to share a link to an app that does not exist.
 */

const money = n => Number(n || 0).toFixed(1);

export class AppShare {
  /**
   * `referralCode` is a getter, not a value: the code arrives from
   * /api/auth/me after this object is built, and a link shared before that
   * lands would otherwise carry no code at all — which is exactly the case
   * this is meant to cover.
   */
  constructor({ apiBaseUrl, toast, referralCode }) {
    this.base = String(apiBaseUrl || '').replace(/\/+$/, '');
    this.toast = toast || (() => {});
    this.getReferralCode = typeof referralCode === 'function' ? referralCode : () => referralCode;
  }

  /** Public page a recipient can open — never the raw .apk, which many
   *  messengers refuse to forward and some browsers block outright.
   *
   *  The sharer's own code rides along in `?ref=`, so whoever opens the link
   *  is credited to them without anyone having to read a code aloud and type
   *  it in. Both the download page and the app itself pick it up. */
  get shareUrl() {
    const code = String(this.getReferralCode() || '').trim().toUpperCase();
    const base = `${window.location.origin}/download`;
    return code ? `${base}?ref=${encodeURIComponent(code)}` : base;
  }

  get shareText() {
    const code = String(this.getReferralCode() || '').trim().toUpperCase();
    const invite = 'Play on Club 8 — WinGo, Aviator, Mines aur bahut kuch. App yahan se lo:';
    // Still spelled out, so a messenger that strips the query string from the
    // preview does not take the code with it.
    return code ? `${invite.slice(0, -1)} (invite code ${code}):` : invite;
  }

  init() {
    // Not on /admin: the dashboard is served from this same document, so the
    // share card and sheet would otherwise appear over it.
    if (location.pathname.startsWith('/admin')) return;

    this.card = document.getElementById('app-cta');
    this.meta = document.getElementById('app-cta-meta');
    if (!this.card) return;

    document.getElementById('app-cta-share')?.addEventListener('click', () => this.openSheet());
    document.getElementById('app-share-close')?.addEventListener('click', () => this.closeSheet());

    const sheet = document.getElementById('app-share-sheet');
    sheet?.addEventListener('click', event => {
      if (event.target === sheet) return this.closeSheet();
      const target = event.target.closest('[data-share]');
      if (target) this.share(target.dataset.share);
    });

    this.loadInfo();
  }

  async loadInfo() {
    try {
      const info = await (await fetch(`${this.base}/api/app/info`)).json();
      if (!info.available) return;
      this.card.hidden = false;
      const mb = info.size_bytes ? `${money(info.size_bytes / 1048576)} MB` : '';
      this.meta.textContent = [info.version && `v${info.version}`, mb]
        .filter(Boolean).join(' · ') || 'Android APK';
    } catch (error) {
      // No app uploaded, or the API is down: leave the card hidden rather than
      // offering a download that cannot work.
    }
  }

  openSheet() {
    const sheet = document.getElementById('app-share-sheet');
    if (sheet) sheet.hidden = false;
  }

  closeSheet() {
    const sheet = document.getElementById('app-share-sheet');
    if (sheet) sheet.hidden = true;
  }

  async share(channel) {
    const url = this.shareUrl;
    const message = `${this.shareText} ${url}`;

    if (channel === 'copy') {
      try {
        await navigator.clipboard.writeText(url);
        this.toast('Link copy ho gaya.', 'success');
      } catch (error) {
        this.toast(`Link: ${url}`, 'success');
      }
      return this.closeSheet();
    }

    if (channel === 'native') {
      if (navigator.share) {
        try {
          await navigator.share({ title: 'Club 8', text: this.shareText, url });
        } catch (error) {
          // Dismissing the sheet lands here; that is not a failure.
          if (error?.name !== 'AbortError') this.toast(`Link: ${url}`, 'success');
        }
      } else {
        await navigator.clipboard?.writeText(url).catch(() => {});
        this.toast('Is browser me share menu nahi hai — link copy kar diya.', 'success');
      }
      return this.closeSheet();
    }

    const targets = {
      whatsapp: `https://wa.me/?text=${encodeURIComponent(message)}`,
      telegram: `https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(this.shareText)}`,
      sms: `sms:?body=${encodeURIComponent(message)}`
    };
    const target = targets[channel];
    if (target) window.open(target, '_blank', 'noopener');
    this.closeSheet();
  }
}
