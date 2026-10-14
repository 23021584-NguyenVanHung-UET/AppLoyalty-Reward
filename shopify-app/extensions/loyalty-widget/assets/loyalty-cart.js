import { createProxy, discountLink, translate, date, friendlyError } from './loyalty-core.js';

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}
function readConfig(host) {
  const source = host.querySelector('script[type="application/json"]')?.textContent ?? '{}';
  return JSON.parse(source.replace(/"(?:\\[\s\S]|[^"\\])*"|<!--[\s\S]*?-->/g,
    token => token.startsWith('<!--') ? '' : token));
}
function contrast(background, foreground) {
  const luminance = hex => {
    const c = hex.match(/[a-f\d]{2}/gi).map(v => parseInt(v, 16) / 255)
      .map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
    return c[0] * .2126 + c[1] * .7152 + c[2] * .0722;
  };
  const a = luminance(background), b = luminance(foreground);
  return (Math.max(a, b) + .05) / (Math.min(a, b) + .05) >= 4.5 ? foreground : a > .179 ? '#000000' : '#ffffff';
}
export function readyCodes(rows, now = Date.now()) {
  const seen = new Set(), expiry = row => row.expires_at === null ? Infinity : Date.parse(row.expires_at);
  return rows.filter(row => {
    if (row.status !== 'ISSUED' || typeof row.code !== 'string' || !row.code.trim()
      || row.code.length > 64 || expiry(row) <= now || Number.isNaN(expiry(row))) return false;
    const key = row.code.toUpperCase();
    if (seen.has(key)) return false;
    seen.add(key); return true;
  }).sort((a, b) => expiry(a) - expiry(b));
}
export class CartOffers {
  constructor(host, config) {
    this.host = host; this.config = config; this.preview = config.designMode || Boolean(window.Shopify?.designMode);
    this.loggedIn = config.loggedIn; this.codes = []; this.destroyed = false;
    this.root = host.shadowRoot ?? host.attachShadow({ mode: 'open' }); this.root.replaceChildren();
    const sheet = element('link'); sheet.rel = 'stylesheet'; sheet.href = host.dataset.css; this.root.append(sheet);
    this.api = createProxy(host.dataset.proxy);
    const root = new URL(config.rootUrl || '/', location.origin);
    if (root.origin !== location.origin || root.search || root.hash) throw new Error('Invalid root');
    this.rootPath = root.pathname.replace(/\/$/, '');
    const appearance = config.appearance ?? {}, hex = (v, fallback) => /^#[a-f\d]{6}$/i.test(v) ? v : fallback;
    const accent = hex(appearance.accent, '#5b37d4');
    host.style.setProperty('--lc-accent', accent); host.style.setProperty('--lc-on-accent', contrast(accent, '#ffffff'));
    const number = (v, fallback, min, max) => v == null || !Number.isFinite(Number(v)) ? fallback : Math.min(max, Math.max(min, Number(v)));
    host.style.setProperty('--lc-radius', `${number(appearance.radius, 16, 0, 24)}px`);
    host.style.setProperty('--lc-space', `${number(appearance.spacing, 16, 0, 32)}px`);
    host.dataset.layout = appearance.layout === 'grid' ? 'grid' : 'list';
    if (appearance.customColors === true) {
      const bg = hex(appearance.background, '#ffffff'), fg = contrast(bg, hex(appearance.foreground, '#242033'));
      host.style.setProperty('--lc-bg', bg); host.style.setProperty('--lc-text', fg); host.style.setProperty('--lc-muted', fg);
    } else for (const name of ['bg', 'text', 'muted']) host.style.removeProperty(`--lc-${name}`);
    this.limit = Math.trunc(number(appearance.limit, 3, 1, 6));
    this.box = element('section', 'cart-offers'); this.box.setAttribute('aria-label', this.t('cart_title'));
    const header = element('header', 'cart-header'), copy = element('div');
    copy.append(element('span', 'cart-eyebrow', this.t('cart_eyebrow')),
      element('h2', '', appearance.heading?.trim() || this.t('cart_title')));
    this.refreshButton = element('button', 'refresh', this.t('cart_refresh')); this.refreshButton.type = 'button';
    this.refreshButton.addEventListener('click', () => this.load()); header.append(copy, this.refreshButton);
    this.body = element('div', 'cart-body'); this.body.setAttribute('aria-live', 'polite');
    this.box.append(header, this.body); this.root.append(this.box);
    this.onRefresh = () => { if (!this.loading) void this.load(); };
    this.onVisible = () => { if (document.visibilityState === 'visible') this.onRefresh(); };
    this.events = ['pageshow', 'cart:updated', 'cart:change'];
    this.events.forEach(name => window.addEventListener(name, this.onRefresh));
    document.addEventListener('visibilitychange', this.onVisible);
    if (this.preview) {
      this.loggedIn = true; this.program = { enabled: true }; this.cartCount = 2;
      this.codes = config.previewData?.codes ?? [{status:'ISSUED',code:'PREVIEW-ONLY',expires_at:null}]; this.render();
    } else void this.load();
  }
  t(key, values) { return translate(this.config.strings, key, values); }
  async load() {
    if (this.destroyed || this.preview) return;
    this.reader?.abort(); clearTimeout(this.expiryTimer);
    const reader = this.reader = new AbortController();
    const timeout = setTimeout(() => reader.abort(), 10000);
    this.loading = true; this.error = null; this.codes = []; this.render();
    try {
      const program = (await this.api('program', { signal: reader.signal })).data;
      if (reader.signal.aborted || this.destroyed || this.reader !== reader) return;
      this.program = program;
      if (this.program.enabled && this.loggedIn) {
        const [codes, response] = await Promise.all([
          this.api('redemptions', { signal: reader.signal }),
          fetch(`${this.rootPath}/cart.js`, { signal: reader.signal, cache: 'no-store', redirect: 'error', credentials: 'same-origin', headers: { Accept: 'application/json' } }),
        ]);
        if (!response.ok) throw new Error('Cart unavailable');
        const cart = await response.json();
        if (!Number.isInteger(cart.item_count) || cart.item_count < 0 || !Array.isArray(codes.data.results)) throw new Error('Invalid cart');
        if (reader.signal.aborted || this.destroyed || this.reader !== reader) return;
        this.codes = codes.data.results; this.cartCount = cart.item_count;
      }
    } catch (error) {
      if (this.reader !== reader || this.destroyed) return;
      if (error.code === 'NOT_LOGGED_IN') this.loggedIn = false;
      else this.error = error;
    } finally {
      clearTimeout(timeout);
      if (!this.destroyed && this.reader === reader) { this.loading = false; this.render(); }
    }
  }
  render() {
    if (this.destroyed) return;
    clearTimeout(this.expiryTimer); this.body.replaceChildren(); this.host.hidden = false;
    this.refreshButton.hidden = this.preview || !this.loggedIn || this.program?.enabled === false;
    this.refreshButton.disabled = Boolean(this.loading);
    this.body.setAttribute('aria-busy', String(Boolean(this.loading)));
    if (this.preview) this.body.append(element('p', 'preview', this.t('preview')));
    if (this.loading) { this.body.append(element('p', 'muted', this.t('loading'))); return; }
    if (this.error) {
      const retry = element('button', 'action', this.t('retry')); retry.type = 'button';
      retry.addEventListener('click', () => this.load());
      this.body.append(element('p', 'notice', friendlyError(this.error, this.config.strings)), retry); return;
    }
    if (!this.loggedIn) {
      this.body.append(element('p', 'muted', this.t('cart_login')));
      const login = new URL(this.config.loginUrl || `${this.rootPath}/account/login`, location.origin);
      if (login.origin === location.origin) {
        login.searchParams.set('return_url', `${this.rootPath}/cart`);
        const link = element('a', 'action', this.t('login')); link.href = login.pathname + login.search; this.body.append(link);
      }
      return;
    }
    if (this.program?.enabled === false) { this.body.append(element('p', 'muted', this.t('disabled'))); return; }
    if (!this.cartCount) { this.body.append(element('p', 'muted', this.t('cart_empty'))); return; }
    const ready = readyCodes(this.codes);
    if (!ready.length) {
      this.host.hidden = this.config.appearance?.hideEmpty === true && !this.preview;
      this.body.append(element('p', 'muted', this.t('cart_no_codes'))); return;
    }
    const list = element('div', 'offers');
    ready.slice(0, this.limit).forEach((row, index) => {
      const card = element('article', 'coupon');
      if (index === 0 && row.expires_at) card.append(element('span', 'badge', this.t('cart_use_first')));
      card.append(element('code', 'coupon-code', row.code), element('p', 'expiry', row.expires_at
        ? this.t('valid_until', { date: date(row.expires_at, this.config.locale) }) : this.t('no_expiry')));
      const actions = element('div', 'actions');
      for (const [destination, label, className] of [['cart', 'apply', 'action'], ['checkout', 'cart_checkout', 'action secondary']]) {
        if (destination === 'checkout' && this.config.appearance?.showCheckout === false) continue;
        const link = element(this.preview ? 'button' : 'a', className, this.t(label));
        if (this.preview) { link.type = 'button'; link.disabled = true; }
        else link.href = discountLink(row.code, this.config.rootUrl, destination);
        actions.append(link);
      }
      card.append(actions); list.append(card);
    });
    this.body.append(list, element('p', 'terms', this.t('cart_terms')));
    if (ready.length > this.limit) this.body.append(element('p', 'terms', this.t('cart_more', { count: ready.length - this.limit })));
    const next = Math.min(...ready.filter(row => row.expires_at).map(row => Date.parse(row.expires_at)));
    if (Number.isFinite(next)) this.expiryTimer = setTimeout(() => this.render(), Math.min(2147483647, Math.max(1, next - Date.now())));
  }
  destroy() {
    this.destroyed = true; this.reader?.abort(); clearTimeout(this.expiryTimer);
    this.events.forEach(name => window.removeEventListener(name, this.onRefresh));
    document.removeEventListener('visibilitychange', this.onVisible);
  }
}
if (!customElements.get('lp-cart-offers')) customElements.define('lp-cart-offers', class extends HTMLElement {
  connectedCallback() { if (!this.instance) this.instance = new CartOffers(this, readConfig(this)); }
  disconnectedCallback() { this.instance?.destroy(); this.instance = null; }
});
