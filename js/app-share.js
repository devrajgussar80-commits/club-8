/**
 * In-app download and sharing.
 *
 * Download: fetched as a stream and saved from a blob, rather than pointing a
 * link at the APK. A plain download link navigates the page away in Android
 * WebView, which is exactly where most of these installs happen — the player
 * loses the game they were in. Streaming keeps them on the screen and lets the
 * button report progress, which matters because an APK on a phone connection
 * is a slow, silent wait otherwise.
 *
 * Share: a sheet with the apps people here actually use, because
 * `navigator.share` is missing on desktop browsers and on some Android
 * WebViews, and a share button that silently does nothing is worse than none.
 */

const money = n => Number(n || 0).toFixed(1);

export class AppShare {
  constructor({ apiBaseUrl, toast }) {
    this.base = String(apiBaseUrl || '').replace(/\/+$/, '');
    this.toast = toast || (() => {});
    this.downloading = false;
  }

  /** Public page a recipient can open — never the raw .apk, which many
   *  messengers refuse to forward and some browsers block outright. */
  get shareUrl() {
    return `${window.location.origin}/download`;
  }

  get shareText() {
    return 'Play on Club 8 — WinGo, Aviator, Mines aur bahut kuch. App yahan se lo:';
  }

  init() {
    // Not on /admin: the dashboard is served from this same document, so the
    // download card and share sheet would otherwise appear over it.
    if (location.pathname.startsWith('/admin')) return;

    this.card = document.getElementById('app-cta');
    this.meta = document.getElementById('app-cta-meta');
    this.progress = document.getElementById('app-cta-progress');
    if (!this.card) return;

    document.getElementById('app-cta-download')?.addEventListener('click', () => this.download());
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

  async download() {
    if (this.downloading) return;
    this.downloading = true;
    this.progress.hidden = false;
    const bar = this.progress.querySelector('i');
    const label = this.progress.querySelector('span');
    const setPct = pct => {
      bar.style.width = `${pct}%`;
      label.textContent = `${pct}%`;
    };
    setPct(0);

    try {
      const response = await fetch(`${this.base}/api/app/download`);
      if (!response.ok) throw new Error('Download unavailable right now.');

      const total = Number(response.headers.get('content-length')) || 0;
      const reader = response.body?.getReader();

      let blob;
      if (!reader) {
        // No streaming support: still works, just without a progress figure.
        blob = await response.blob();
        setPct(100);
      } else {
        const chunks = [];
        let received = 0;
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          chunks.push(value);
          received += value.length;
          if (total) setPct(Math.min(99, Math.round(received / total * 100)));
        }
        blob = new Blob(chunks, { type: 'application/vnd.android.package-archive' });
        setPct(100);
      }

      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'club8.apk';
      document.body.appendChild(link);
      link.click();
      link.remove();
      // Freed on the next tick: revoking immediately can cancel the save on
      // some Android browsers before they have read the blob.
      setTimeout(() => URL.revokeObjectURL(url), 60000);

      this.toast('App downloaded — open it to install.', 'success');
    } catch (error) {
      this.toast(error.message || 'Download fail ho gaya.', 'error');
    } finally {
      this.downloading = false;
      setTimeout(() => { this.progress.hidden = true; }, 2500);
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
