// Plain browser module. No build, storage, customer identity or Shopify credentials.
export class ProxyError extends Error {
  constructor(status, code) { super(code); this.status = status; this.code = code; }
}
export function createProxy(base, fetcher = (...args) => fetch(...args)) {
  const url = new URL(base, location.origin);
  if (url.origin !== location.origin || url.search || url.hash) throw new Error('Invalid proxy path');
  const prefix = url.pathname.replace(/\/$/, '');
  return async (endpoint, { body, signal } = {}) => {
    if (!['program', 'me', 'rewards', 'redemptions', 'redeem', 'profile/birthday', 'referral', 'referral/code'].includes(endpoint)) throw new Error('Invalid endpoint');
    signal?.throwIfAborted();
    let response;
    try {
      response = await fetcher(`${prefix}/${endpoint}`, { method: body ? 'POST' : 'GET', signal,
        cache: 'no-store', redirect: 'error', headers: { Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}) },
        ...(body ? { body: JSON.stringify(body) } : {}) });
    } catch (error) {
      if (signal?.aborted || error?.name === 'AbortError') throw new DOMException('Cancelled', 'AbortError');
      throw new ProxyError(0, navigator.onLine === false ? 'OFFLINE' : 'NETWORK');
    }
    let data;
    try { data = await response.json(); } catch { signal?.throwIfAborted(); throw new ProxyError(response.status, 'UNAVAILABLE'); }
    if (!response.ok) throw new ProxyError(response.status, typeof data?.code === 'string' ? data.code : 'UNAVAILABLE');
    return { data, status: response.status };
  };
}
export const points = (value, locale) => new Intl.NumberFormat(locale).format(value);

const referralAttribution = new Map();
export function captureReferral(root = '/', fetcher = (...args) => fetch(...args)) {
  const values = new URL(location.href).searchParams.getAll('ref');
  const code = values.length === 1 ? values[0].trim().toUpperCase() : '';
  if (!/^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{8}$/.test(code)) return Promise.resolve(false);
  const url = new URL(`${root.replace(/\/$/, '')}/cart/update.js`, location.origin);
  if (url.origin !== location.origin || url.search || url.hash) return Promise.resolve(false);
  const key = `${url.href}:${code}`;
  if (!referralAttribution.has(key)) referralAttribution.set(key, Promise.resolve().then(async () => {
    try {
      const response = await fetcher(url.pathname, {method:'POST',credentials:'same-origin',redirect:'error',cache:'no-store',
        headers:{'Content-Type':'application/json',Accept:'application/json'},body:JSON.stringify({attributes:{loyalty_ref:code}}),
        signal:AbortSignal.timeout(10000)});
      if (!response.ok) return false;
      return (await response.json()).attributes?.loyalty_ref === code;
    } catch { return false; }
  }));
  return referralAttribution.get(key);
}
export function money(value, currency, locale) {
  return new Intl.NumberFormat(locale, { style: 'currency', currency, currencyDisplay: 'code' }).format(String(value));
}
export function date(value, locale) {
  return value ? new Intl.DateTimeFormat(locale, { dateStyle: 'medium' }).format(new Date(value)) : '';
}
export function translate(strings, key, values = {}) {
  return String(strings[key] ?? '').replace(/%([a-z_]+)%/g, (_match, name) => String(values[name] ?? ''));
}
export function expiry(value, strings, locale, now = Date.now()) {
  if (!value) return translate(strings, 'no_expiry');
  const days = Math.ceil((new Date(value).getTime() - now) / 86400000);
  return days <= 0 ? translate(strings, 'expired') : days === 1 ? translate(strings, 'expires_one')
    : translate(strings, 'expires_days', { days: points(days, locale) });
}
export function estimate(priceInHundredths, spend) {
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(String(spend));
  if (!match || !/^\d+$/.test(String(priceInHundredths))) return '0';
  const divisor = BigInt(match[1] + (match[2] ?? '').padEnd(2, '0'));
  return divisor > 0n ? (BigInt(priceInHundredths) / divisor).toString() : '0';
}
export function newKey() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
}
export function pause(ms, signal) {
  return new Promise((resolve, reject) => {
    signal?.throwIfAborted();
    const cancel = () => { clearTimeout(timer); reject(new DOMException('Cancelled', 'AbortError')); };
    const timer = setTimeout(() => { signal?.removeEventListener('abort', cancel); resolve(); }, ms);
    signal?.addEventListener('abort', cancel, { once: true });
  });
}
export const rejectionKeys = {
  INSUFFICIENT_POINTS: 'error_points', DAILY_LIMIT: 'error_daily', UNUSED_CODES_LIMIT: 'error_unused',
  STOCK_EXHAUSTED: 'error_stock', CUSTOMER_LIMIT: 'error_customer_limit', REWARD_INACTIVE: 'error_inactive',
  PROGRAM_DISABLED: 'disabled', CUSTOMER_BLOCKED: 'error_blocked', CUSTOMER_ANONYMIZED: 'error_blocked',
  UNSUPPORTED_REWARD: 'error_unsupported', KEY_REUSED: 'error_key', ISSUE_FAILED: 'error_issue',
  CUSTOMER_NOT_FOUND: 'error_new', RATE_LIMITED: 'error_rate', OFFLINE: 'offline', NETWORK: 'error_network',
};
export function friendlyError(error, strings) {
  return translate(strings, error.status === 429 ? 'error_rate' : rejectionKeys[error.code] ?? 'error_generic');
}
export class RedeemFlow {
  constructor(api, changed = () => {}, refresh = async () => {}) {
    this.api = api; this.changed = changed; this.refresh = refresh; this.state = 'idle'; this.busy = false;
  }
  open(reward, previousCodes = []) {
    if (this.busy || this.state === 'pending') return;
    this.controller?.abort(); this.reward = reward; this.key = newKey(); this.result = null; this.error = null;
    this.baseline = new Set(previousCodes.map(row => row.reference)); this.state = 'confirm'; this.changed();
  }
  cancel() { if (!this.busy && this.state !== 'pending') { this.state = 'idle'; this.changed(); } }
  async confirm() {
    if (this.busy || !['confirm', 'error'].includes(this.state)) return;
    this.busy = true; this.error = null; this.controller = new AbortController(); this.changed();
    try {
      const response = await this.api('redeem', { body: { reward_id: this.reward.id, client_key: this.key }, signal: this.controller.signal });
      if (response.status === 202) {
        this.state = 'pending'; this.changed(); void this.refresh().catch(() => {}); await this.poll(this.controller.signal);
      } else { this.result = response.data; this.state = 'success'; await this.refresh(); }
    } catch (error) {
      if (error.name !== 'AbortError') {
        this.error = error; this.state = 'error';
        if ([409, 422].includes(error.status)) await this.refresh();
      }
    } finally { this.busy = false; this.changed(); }
  }
  async poll(signal) {
    signal.throwIfAborted();
    const bounded = new AbortController(), abort = () => bounded.abort();
    signal.addEventListener('abort', abort, { once: true });
    const timeout = setTimeout(abort, 30000);
    try { await this.pollBatch(bounded.signal); }
    catch (error) {
      if (signal.aborted || error.name !== 'AbortError') throw error;
      this.state = 'delayed';
    } finally { clearTimeout(timeout); signal.removeEventListener('abort', abort); }
  }
  async pollBatch(signal) {
    let reconciled = false;
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      await pause(Math.min(2000, deadline - Date.now()), signal);
      if (Date.now() >= deadline) break;
      const { data } = await this.api('redemptions', { signal });
      this.codes = data.results;
      // GET has no client-key reference. A single idempotent POST verifies ownership
      // of this exact attempt before displaying a candidate code as its result.
      const candidate = data.results.some(row => !this.baseline.has(row.reference)
        && row.reward_id === this.reward.id && row.status === 'ISSUED');
      if (candidate && !reconciled) {
        reconciled = true;
        const response = await this.api('redeem', { body: { reward_id: this.reward.id, client_key: this.key }, signal });
        if (response.status === 200) { this.result = response.data; this.state = 'success'; await this.refresh(); return; }
      }
    }
    this.state = 'delayed';
  }
  stop() { this.controller?.abort(); }
}
const programs = new Map();
export function getProgram(base, api) {
  if (!programs.has(base)) programs.set(base, api('program').then(response => response.data).catch(error => { programs.delete(base); throw error; }));
  return programs.get(base);
}
export function discountLink(code, root = '/', destination = 'cart') {
  const path = new URL(root, location.origin);
  if (path.origin !== location.origin || path.search || path.hash || !['cart', 'checkout'].includes(destination)) throw new Error('Invalid root or destination');
  return `${path.pathname.replace(/\/$/, '')}/discount/${encodeURIComponent(code)}?redirect=${encodeURIComponent(`${path.pathname.replace(/\/$/, '')}/${destination}`)}`;
}
