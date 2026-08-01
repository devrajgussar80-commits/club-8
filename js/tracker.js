/**
 * Anonymous visitor tracker.
 *
 * Runs before anyone has an account — that is the whole point. Someone opens
 * the site, lands on the login/register screen, and either signs up or leaves;
 * none of that shows up in the users table, so it is reported from here.
 *
 * Two ids, doing different jobs:
 *   visitorId  localStorage, survives across visits  -> "is this the same
 *              browser as yesterday?"
 *   sessionId  sessionStorage, one per tab/visit     -> "what happened during
 *              this particular visit?"
 * Neither identifies a person. The server adds the IP and the User-Agent from
 * the request itself, because a client is free to lie about both.
 *
 * Dwell time is measured as VISIBLE time, not wall clock: the heartbeat is
 * paused whenever the tab is hidden. A tab left open in the background for an
 * hour is not an hour of interest, and reporting it as such would make every
 * engagement number meaningless.
 */

const ENDPOINT = '/api/track';
const HEARTBEAT_MS = 15000;
const VISITOR_KEY = 'CLUB8_VISITOR_ID';
const SESSION_KEY = 'CLUB8_SESSION_ID';

const newId = () => {
  // crypto.randomUUID is missing on older Android WebViews, which is exactly
  // the population this tracker exists to measure.
  if (crypto.randomUUID) return crypto.randomUUID().replace(/-/g, '');
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return [...bytes].map(b => b.toString(16).padStart(2, '0')).join('');
};

const readId = (store, key) => {
  try {
    let value = store.getItem(key);
    if (!value) {
      value = newId();
      store.setItem(key, value);
    }
    return value;
  } catch (error) {
    // Private mode or blocked storage: still track the visit, just without
    // being able to recognise this browser again.
    return newId();
  }
};

export class VisitorTracker {
  constructor(apiBaseUrl) {
    this.base = String(apiBaseUrl || '').replace(/\/+$/, '');
    this.visitorId = readId(localStorage, VISITOR_KEY);
    this.sessionId = readId(sessionStorage, SESSION_KEY);
    this.visibleSince = document.visibilityState === 'visible' ? Date.now() : null;
    this.pendingSeconds = 0;
    this.lastPath = null;
    this.started = false;
    // Events are posted one at a time, chained on this promise.
    //
    // Fire-and-forget looked fine and was not: the requests raced, so whichever
    // one the server happened to receive first created the session row and set
    // its landing path. A visit that started at "/" and walked to the login
    // screen could be recorded as landing on "auth" and ending at "/", i.e.
    // exactly backwards. Analytics that reorders itself is worse than none.
    this.chain = Promise.resolve();
  }

  /** Seconds the tab has been visible since the last report, then reset. */
  takeSeconds() {
    if (this.visibleSince !== null) {
      this.pendingSeconds += (Date.now() - this.visibleSince) / 1000;
      this.visibleSince = Date.now();
    }
    const seconds = Math.round(this.pendingSeconds);
    this.pendingSeconds -= seconds;
    return seconds;
  }

  send(event, { path = null, meta = null, beacon = false } = {}) {
    // Nothing is reported until start() runs. Callers all over the app fire
    // events blind, and on screens where tracking is deliberately off (the
    // admin dashboard) those would otherwise create a session with no
    // beginning.
    if (!this.started && event !== 'session_start') return;

    const body = JSON.stringify({
      visitor_id: this.visitorId,
      session_id: this.sessionId,
      event,
      path: path ?? this.lastPath,
      referrer: document.referrer || null,
      meta
    });

    // `keepalive` is what lets the exit report survive the page going away.
    // A normal fetch is cancelled on unload, which is why exits used to be
    // the one event that never arrived.
    const post = () => fetch(`${this.base}${ENDPOINT}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      keepalive: beacon
    }).catch(() => {
      // Analytics must never break the app or spam the console for a player.
    });

    // The exit report jumps the queue: the page is going away and anything
    // still waiting behind an earlier request would simply be lost.
    if (beacon) {
      post();
      return;
    }
    this.chain = this.chain.then(post, post);
  }

  start(path) {
    if (this.started) return;
    this.started = true;
    this.lastPath = path || location.pathname;

    this.send('session_start', {
      meta: {
        screen: `${window.screen?.width || 0}x${window.screen?.height || 0}`,
        language: navigator.language || '',
        // Distinguishes a first-time stranger from someone coming back.
        returning: this.isReturning()
      }
    });
    // No page_view here on purpose. session_start already carries the landing
    // path, and the router emits the first real screen a moment later; sending
    // both raced (neither call is awaited) and left the timeline out of order
    // while counting the landing twice in page_views.

    this.timer = setInterval(() => {
      if (document.visibilityState !== 'visible') return;
      const seconds = this.takeSeconds();
      if (seconds > 0) this.send('heartbeat', { meta: { seconds } });
    }, HEARTBEAT_MS);

    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') {
        const seconds = this.takeSeconds();
        this.visibleSince = null;
        this.send('exit', { meta: { seconds, reason: 'hidden' }, beacon: true });
      } else {
        this.visibleSince = Date.now();
      }
    });

    // pagehide, not unload: unload does not fire reliably on mobile Safari or
    // Android WebView, where most of these visitors are.
    window.addEventListener('pagehide', () => {
      const seconds = this.takeSeconds();
      this.send('exit', { meta: { seconds, reason: 'pagehide' }, beacon: true });
    });
  }

  isReturning() {
    try {
      const seen = localStorage.getItem(`${VISITOR_KEY}_SEEN`);
      localStorage.setItem(`${VISITOR_KEY}_SEEN`, '1');
      return Boolean(seen);
    } catch (error) {
      return false;
    }
  }

  pageView(path) {
    if (path === this.lastPath) return;
    this.lastPath = path;
    this.send('page_view', { path });
  }

  event(name, meta = null) {
    this.send(name, { meta });
  }

  /** Join this anonymous visit to the account it just became. */
  identify(userId) {
    if (!userId) return;
    fetch(`${this.base}/api/track/identify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        visitor_id: this.visitorId,
        session_id: this.sessionId,
        user_id: userId
      })
    }).catch(() => {});
  }

  /**
   * Watch the auth screen: which tab, which field they gave up on, and
   * whether the attempt succeeded. `field_focus` is the interesting one —
   * it separates "looked at the form" from "actually tried to sign up".
   */
  watchAuthScreen() {
    const page = document.getElementById('page-auth');
    if (!page) return;

    page.addEventListener('focusin', event => {
      const field = event.target.closest('input, select');
      if (!field) return;
      const name = field.id || field.name || field.type;
      if (this.seenFields?.has(name)) return;
      (this.seenFields ||= new Set()).add(name);
      this.event('field_focus', { field: name });
    });

    page.addEventListener('click', event => {
      const tab = event.target.closest('[data-auth-tab], .auth-tab');
      if (tab) this.event('auth_tab', { tab: (tab.textContent || '').trim().slice(0, 30) });
    });
  }
}
